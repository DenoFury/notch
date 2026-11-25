"""Tiny guard: waits for the notch to exit, then makes sure Windows' taskbar is back.

Runs as its own process so it still works if the notch crashes. It blocks on the
process handle (no polling) and hands its memory back to Windows while waiting.
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from notch import winbar  # noqa: E402

k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = ctypes.c_void_p
k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
k32.GetCurrentProcess.restype = ctypes.c_void_p
k32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]


def main(pid=None):
    pid = int(sys.argv[1]) if pid is None else pid
    h = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if not h:
        winbar.restore() if winbar.is_hidden_by_us() else None
        return
    k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)
    k32.WaitForSingleObject(h, 0xFFFFFFFF)
    if winbar.is_hidden_by_us():
        winbar.restore()


if __name__ == "__main__":
    main()
