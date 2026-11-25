"""Hide / restore Windows' own taskbar. No Qt here, so the guard process can use it too.

While hidden, Windows' taskbar is set to auto-hide (so it gives its screen space
back) and its windows are hidden. The previous auto-hide state is saved to a file
first; restore() puts it back and deletes the file. If the notch ever crashes, the
guard process (or the next start) sees the file and restores the taskbar.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
user32.FindWindowW.restype = wt.HWND
user32.FindWindowExW.restype = wt.HWND
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]

STATE_FILE = os.path.join(os.environ.get("NOTCH_DATA") or os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "Notch"), "taskbar_state.json")


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


shell32.SHAppBarMessage.argtypes = [wt.DWORD, ctypes.POINTER(APPBARDATA)]
shell32.SHAppBarMessage.restype = ctypes.c_size_t
ABM_GETSTATE, ABM_SETSTATE, ABS_AUTOHIDE = 4, 10, 1


def tray_windows():
    hs = []
    main = user32.FindWindowW("Shell_TrayWnd", None)
    if main:
        hs.append(main)
    h = None
    while True:
        h = user32.FindWindowExW(None, h, "Shell_SecondaryTrayWnd", None)
        if not h:
            break
        hs.append(h)
    return hs


def _state(hwnd):
    abd = APPBARDATA(ctypes.sizeof(APPBARDATA), hwnd)
    return int(shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(abd)))


def _set_state(hwnd, state):
    abd = APPBARDATA(ctypes.sizeof(APPBARDATA), hwnd)
    abd.lParam = state
    shell32.SHAppBarMessage(ABM_SETSTATE, ctypes.byref(abd))


def is_hidden_by_us():
    return os.path.exists(STATE_FILE)


def hide():
    hs = tray_windows()
    if not hs:
        return False
    before = _saved_state()
    if before is None:
        before = _state(hs[0])
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"state": before}, f)
        os.replace(tmp, STATE_FILE)   # all-or-nothing
    _set_state(hs[0], before | ABS_AUTOHIDE)
    for h in hs:
        user32.ShowWindow(h, 0)   # SW_HIDE
    _log("hidden")
    return True


def ensure_hidden():
    """Cheap check: Explorer sometimes shows its taskbar again by itself."""
    for h in tray_windows():
        if user32.IsWindowVisible(h):
            user32.ShowWindow(h, 0)


def show_temporarily():
    hs = tray_windows()
    if hs:
        user32.ShowWindow(hs[0], 5)   # SW_SHOW
        user32.SetForegroundWindow(hs[0])
    return hs[0] if hs else None


def _saved_state():
    try:
        with open(STATE_FILE) as f:
            return int(json.load(f)["state"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _log(msg):
    import time
    print(f"{time.strftime('%H:%M:%S')} taskbar: {msg}", flush=True)


def restore():
    hs = tray_windows()
    before = _saved_state()
    _log(f"restore (auto-hide back to {before})")
    if hs and before is not None:
        _set_state(hs[0], before)
    for h in hs:
        user32.ShowWindow(h, 5)   # SW_SHOW
    try:
        os.remove(STATE_FILE)
    except OSError as e:
        _log(f"could not remove state file: {e}")
