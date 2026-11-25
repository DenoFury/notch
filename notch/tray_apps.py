"""Apps that sit in the notification area (tray), for the notch taskbar.

Windows 11 records every tray icon under HKCU\\Control Panel\\NotifyIconSettings with the
program's path, a PNG snapshot of its icon and whether it is shown on the taskbar ("promoted").
We list the ones whose program is running right now. Opening one brings its window forward,
or asks the (single-instance) app to show itself.
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import uuid
import winreg

import psutil
from PySide6.QtGui import QPixmap

KEY = r"Control Panel\NotifyIconSettings"
# icons that are Windows itself, drivers, or already on the bar (volume, network, Notch)
SKIP = {"explorer.exe", "securityhealthsystray.exe", "monotificationux.exe", "python.exe", "pythonw.exe",
        "notch.exe", "taskmgr.exe"}
# background helpers: starting them again would not show anything
NO_RELAUNCH_DIRS = ("\\windows\\", "\\driverstore\\", "\\nvidia corporation\\", "\\riot vanguard\\")

shell32 = ctypes.windll.shell32
ole32 = ctypes.windll.ole32
kernel32 = ctypes.windll.kernel32
shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wt.DWORD, wt.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.GetApplicationUserModelId.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p]


def _known_folder(guid):
    g = uuid.UUID(guid).bytes_le
    buf = ctypes.create_string_buffer(g, 16)
    out = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(buf, 0, None, ctypes.byref(out)) != 0:
        return None
    path = out.value
    ole32.CoTaskMemFree(out)
    return path


_folders = {}


def expand(path):
    """'{6D809377-…}\\App\\x.exe' -> 'C:\\Program Files\\App\\x.exe'."""
    if path.startswith("{") and "}" in path:
        guid, rest = path[1:path.index("}")], path[path.index("}") + 1:]
        if guid not in _folders:
            _folders[guid] = _known_folder(guid)
        base = _folders[guid]
        return (base + rest) if base else path
    return path


class TrayApp:
    __slots__ = ("exe", "name", "png", "promoted", "pid", "_pm")

    def __init__(self, exe, name, png, promoted, pid):
        self.exe, self.name, self.png, self.promoted, self.pid = exe, name, png, promoted, pid
        self._pm = None

    @property
    def key(self):
        return self.exe.lower()

    def pixmap(self):
        if self._pm is None:
            # the program's own large icon is sharp; the tray's 32 px snapshot is the fallback
            from .taskbar_core import app_icon
            self._pm = app_icon(self.exe, pid=self.pid)
            if self._pm.isNull() or self._pm.width() < 48:
                pm = QPixmap()
                if self.png and pm.loadFromData(self.png, "PNG"):
                    self._pm = pm
        return self._pm


_pix_cache = {}


def running_tray_apps():
    running = {}
    for p in psutil.process_iter(["exe"]):
        exe = p.info["exe"]
        if exe:
            running.setdefault(exe.lower(), p.pid)
    out, seen = [], set()
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY)
    except OSError:
        return out
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, sub) as k:
                    vals = {}
                    for name in ("ExecutablePath", "InitialTooltip", "IconSnapshot", "IsPromoted"):
                        try:
                            vals[name] = winreg.QueryValueEx(k, name)[0]
                        except OSError:
                            pass
            except OSError:
                continue
            exe = expand(vals.get("ExecutablePath", ""))
            low = exe.lower()
            if not exe or low in seen or os.path.basename(low) in SKIP or low not in running:
                continue
            seen.add(low)
            tip = (vals.get("InitialTooltip") or "").split("\n")[0].strip()
            from .taskbar_core import app_name
            name = tip or app_name(exe)
            if name.lower().endswith(".root"):          # WhatsApp.Root -> WhatsApp
                name = name[:-5]
            png = vals.get("IconSnapshot") or b""
            app = TrayApp(exe, name, png, bool(vals.get("IsPromoted")), running[low])
            cached = _pix_cache.get((low, len(png)))
            if cached is not None:
                app._pm = cached
            else:
                _pix_cache[(low, len(png))] = app.pixmap()
            out.append(app)
    out.sort(key=lambda a: a.name.lower())
    return out


def _aumid(pid):
    """App ID of a Store (packaged) app's process, or None."""
    h = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        n = ctypes.c_uint32(256)
        buf = ctypes.create_unicode_buffer(256)
        return buf.value if kernel32.GetApplicationUserModelId(h, ctypes.byref(n), buf) == 0 else None
    finally:
        kernel32.CloseHandle(h)


def can_open(app):
    low = app.exe.lower()
    return not any(d in low for d in NO_RELAUNCH_DIRS)


def open_app(app, tracker=None):
    """Bring the app's window forward, or ask the running app to show itself. False if we can't."""
    if tracker is not None:
        for g in tracker.groups:
            if g.windows and (g.exe or g.path or "").lower() == app.key:
                tracker.activate(g.windows[0][0])
                return True
    if not can_open(app):
        return False
    aumid = _aumid(app.pid)
    try:
        if aumid:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
        else:
            os.startfile(app.exe)
        return True
    except OSError:
        return False
