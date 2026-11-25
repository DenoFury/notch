"""Mic / camera in use, and downloads, for the island.

Mic and camera: Windows records which apps use them under
HKCU\\...\\CapabilityAccessManager\\ConsentStore\\<microphone|webcam> (LastUsedTimeStop is 0
while an app is using it). A thread blocks on RegNotifyChangeKeyValue, so nothing is polled.

Downloads: browsers write a partial file (.crdownload, .part …) into the Downloads folder and
rename it when done. A folder watcher sees that; sizes are re-read once a second only while
something is downloading.
"""
import ctypes
import ctypes.wintypes as wt
import os
import threading
import time
import uuid
import winreg

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

advapi32 = ctypes.windll.advapi32
kernel32 = ctypes.windll.kernel32
advapi32.RegNotifyChangeKeyValue.argtypes = [wt.HKEY, wt.BOOL, wt.DWORD, wt.HANDLE, wt.BOOL]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
REG_NOTIFY_CHANGE_LAST_SET = 0x4


def _pretty(key_name):
    """'C:#Program Files#Discord#Discord.exe' or 'Microsoft.WindowsCamera_8wekyb3d8bbwe' -> a name."""
    if "#" in key_name:
        path = key_name.replace("#", "\\")
        from .taskbar_core import app_name
        return app_name(path) if os.path.exists(path) else os.path.splitext(os.path.basename(path))[0]
    base = key_name.split("_")[0]
    return base.split(".")[-1] or key_name


def _in_use(cap):
    """Names of apps using `cap` (microphone/webcam) right now."""
    names = []
    for sub in ("", "\\NonPackaged"):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{CONSENT}\\{cap}{sub}")
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    app = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if app == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(k, app) as a:
                        start = winreg.QueryValueEx(a, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(a, "LastUsedTimeStop")[0]
                except OSError:
                    continue
                if start and stop == 0:
                    names.append(_pretty(app))
    return sorted(set(names))


class PrivacyWatch(QObject):
    """mic / cam: lists of app names using them now."""
    changed = Signal(list, list)       # mic apps, camera apps
    _found = Signal(list, list)

    def __init__(self):
        super().__init__()
        self.mic, self.cam = _in_use("microphone"), _in_use("webcam")
        self._found.connect(self._on_found)
        threading.Thread(target=self._watch, daemon=True, name="privacy").start()

    def _watch(self):
        ev = kernel32.CreateEventW(None, False, False, None)
        keys = []
        for cap in ("microphone", "webcam"):
            try:
                keys.append(winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{CONSENT}\\{cap}", 0, winreg.KEY_NOTIFY))
            except OSError:
                pass
        if not keys:
            return
        while True:
            for k in keys:
                advapi32.RegNotifyChangeKeyValue(wt.HKEY(k.handle), True, REG_NOTIFY_CHANGE_LAST_SET, ev, True)
            kernel32.WaitForSingleObject(ev, 0xFFFFFFFF)     # sleeps until an app starts/stops using one
            time.sleep(0.15)                                   # let Windows finish writing both values
            self._found.emit(_in_use("microphone"), _in_use("webcam"))

    def _on_found(self, mic, cam):
        if (mic, cam) != (self.mic, self.cam):
            self.mic, self.cam = mic, cam
            self.changed.emit(mic, cam)


# ---------------------------------------------------------------- downloads
PARTIAL = (".crdownload", ".part", ".partial", ".download", ".opdownload")


def downloads_folder():
    try:
        g = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wt.DWORD, wt.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.create_string_buffer(g, 16), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


def _display_name(partial):
    name = partial
    for ext in PARTIAL:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    if name.lower().startswith("unconfirmed "):        # Chrome before the real name is known
        return "a file"
    return name


class DownloadWatch(QObject):
    started = Signal(str)                  # display name
    progress = Signal()                    # sizes changed (only while downloading)
    finished = Signal(str, str)            # display name, full path of the finished file

    def __init__(self):
        super().__init__()
        self.folder = downloads_folder()
        self.active = {}                   # partial file name -> [display name, size, bytes/s, t]
        self._w = QFileSystemWatcher(self)
        if os.path.isdir(self.folder):
            self._w.addPath(self.folder)
        self._w.directoryChanged.connect(lambda *_: self._debounce.start())
        self._debounce = QTimer(self, singleShot=True, interval=250, timeout=self._scan)
        self._tick = QTimer(self, interval=1000, timeout=self._sizes)
        self._scan(initial=True)

    def _scan(self, initial=False):
        try:
            names = os.listdir(self.folder)
        except OSError:
            return
        partial = {n for n in names if n.lower().endswith(PARTIAL)}
        now = time.monotonic()
        gone = set(self.active) - partial
        renamed = any(n.lower().startswith("unconfirmed ") for n in gone)   # same download, real name now
        for n in partial - set(self.active):
            self.active[n] = [_display_name(n), self._size(n), 0.0, now]
            if not initial and not renamed:
                self.started.emit(self.active[n][0])
        for n in set(self.active) - partial:
            disp = self.active.pop(n)[0]
            final = n
            for ext in PARTIAL:
                if final.lower().endswith(ext):
                    final = final[: -len(ext)]
                    break
            path = os.path.join(self.folder, final)
            if not final.lower().startswith("unconfirmed ") and os.path.exists(path):
                self.finished.emit(final, path)
            else:
                # Chrome renames "Unconfirmed 123.crdownload" to "name.crdownload" mid-way: not finished
                pass
        if self.active and not self._tick.isActive():
            self._tick.start()
        elif not self.active:
            self._tick.stop()
        self.progress.emit()

    def _size(self, n):
        try:
            return os.path.getsize(os.path.join(self.folder, n))
        except OSError:
            return 0

    def _sizes(self):
        now = time.monotonic()
        for n, v in self.active.items():
            size = self._size(n)
            dt = max(0.2, now - v[3])
            v[2] = v[2] * 0.5 + max(0, size - v[1]) / dt * 0.5
            v[1], v[3] = size, now
        self.progress.emit()

    def summary(self):
        """(name, bytes so far, bytes per second) of the newest download, and how many there are."""
        if not self.active:
            return None, 0
        n = max(self.active, key=lambda k: self.active[k][3])
        d, size, speed, _ = self.active[n]
        return (d, size, speed), len(self.active)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
