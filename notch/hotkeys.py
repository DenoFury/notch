"""Win+1…9 for the notch taskbar.

Windows keeps handling Win+number for its own (hidden) taskbar, whose order differs from ours,
so a low-level keyboard hook takes just those keys. It runs on its own thread with its own
message loop, so keyboard input never waits on the UI thread; every other key is passed on at once.
"""
import ctypes
import ctypes.wintypes as wt
import threading

from PySide6.QtCore import QObject, Signal

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_QUIT = 0x100, 0x101, 0x104, 0x105, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_DUMMY = 0xE8          # unassigned: tells Windows "a key was used with Win", so Start doesn't open
LLKHF_INJECTED = 0x10


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.restype = wt.HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.restype = wt.HMODULE


class WinNumberKeys(QObject):
    pressed = Signal(int)          # 1…9 (Win+0 = 10)

    def __init__(self):
        super().__init__()
        self._win = set()          # Win keys held right now
        self._eaten = set()        # digits whose key-up we must also swallow
        self._proc = HOOKPROC(self._hook)
        self._hook_h = None
        self._tid = None
        self._t = threading.Thread(target=self._run, name="win-number-keys", daemon=True)
        self._t.start()

    def _down(self, vk):
        return user32.GetAsyncKeyState(vk) & 0x8000

    def _hook(self, code, wparam, lparam):
        if code == 0:
            k = KBDLLHOOKSTRUCT.from_address(lparam)
            vk = k.vkCode
            if vk in (VK_LWIN, VK_RWIN):
                (self._win.add if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN) else self._win.discard)(vk)
            elif 0x30 <= vk <= 0x39:
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if self._win and not (self._down(VK_SHIFT) or self._down(VK_CONTROL) or self._down(VK_MENU)):
                        if vk not in self._eaten:          # not the auto-repeat of a held key
                            self._eaten.add(vk)
                            user32.keybd_event(VK_DUMMY, 0, 0, 0)
                            user32.keybd_event(VK_DUMMY, 0, 2, 0)
                            self.pressed.emit(10 if vk == 0x30 else vk - 0x30)   # queued to the UI thread
                        return 1
                elif vk in self._eaten:
                    self._eaten.discard(vk)
                    return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._hook_h = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        print("win-number keys:", "on" if self._hook_h else f"hook failed ({ctypes.get_last_error()})", flush=True)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook_h:
            user32.UnhookWindowsHookEx(self._hook_h)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
