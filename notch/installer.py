"""Install / uninstall helpers for the packaged Notch.exe (run as `Notch.exe --post-install` / `--uninstall`)."""
import ctypes
import os
import subprocess
import sys
import winreg

from . import winbar
from .config import DATA_DIR, STARTUP_LNK, set_startup

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Notch"
START_MENU_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs", "Notch.lnk")
VERSION = "2.4.1"

MB_YESNO, MB_ICONQUESTION, MB_ICONINFO, IDYES = 0x4, 0x20, 0x40, 6


def _box(text, flags=MB_ICONINFO):
    return ctypes.windll.user32.MessageBoxW(None, text, "Notch", flags | 0x10000)   # MB_SETFOREGROUND


def _exe():
    return sys.executable


def _shortcut(path, target):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path)
    sc.TargetPath = target
    sc.WorkingDirectory = os.path.dirname(target)
    sc.IconLocation = target + ",0"
    sc.Description = "Notch"
    sc.WindowStyle = 1
    sc.Save()


def _folder_size_kb(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total // 1024


def post_install(startup=False):
    exe = _exe()
    folder = os.path.dirname(exe)
    _shortcut(START_MENU_LNK, exe)
    set_startup(startup)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in [("DisplayName", "Notch"), ("DisplayVersion", VERSION), ("Publisher", "Notch"),
                          ("DisplayIcon", f'"{exe}",0'), ("InstallLocation", folder),
                          ("UninstallString", f'"{exe}" --uninstall'),
                          ("QuietUninstallString", f'"{exe}" --uninstall --quiet')]:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, _folder_size_kb(folder))
    return 0


def stop_running(folder):
    """End every Notch process started from `folder` (the guard first, so it can't race the restore)."""
    import psutil
    me = os.getpid()
    folder = os.path.normcase(os.path.abspath(folder))
    procs = []
    for p in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            exe = p.info["exe"]
            if p.pid != me and exe and os.path.normcase(os.path.dirname(exe)) == folder:
                procs.append(p)
        except (psutil.Error, TypeError):
            pass
    procs.sort(key=lambda p: "--guard" not in (p.info["cmdline"] or []))
    for p in procs:
        try:
            p.terminate()
            p.wait(3)
        except psutil.Error:
            pass
    if winbar.is_hidden_by_us():      # Windows' taskbar was hidden by the notch taskbar: bring it back
        winbar.restore()


def uninstall(quiet=False):
    exe = _exe()
    folder = os.path.dirname(exe)
    if not quiet and _box("Remove Notch from this PC?", MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 1
    stop_running(folder)
    for lnk in (START_MENU_LNK, STARTUP_LNK):
        try:
            os.remove(lnk)
        except OSError:
            pass
    set_startup(False)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    if not quiet and _box("Also delete your Notch settings, reminders and clipboard history?",
                          MB_YESNO | MB_ICONQUESTION) == IDYES:
        if _same(DATA_DIR, DEFAULT_DATA):       # never a folder someone pointed NOTCH_DATA at
            _delete_later(DATA_DIR)
    if getattr(sys, "frozen", False) and _same(folder, INSTALL_DIR):
        # only the folder Setup installed to: a copy of Notch.exe run from Downloads must not
        # take Downloads with it
        _delete_later(folder)             # the running exe can't delete its own folder: let cmd do it after we exit
    if not quiet:
        _box("Notch has been removed.")
    return 0


INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Notch")
DEFAULT_DATA = os.path.join(os.environ.get("APPDATA", ""), "Notch")


def _same(a, b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _delete_later(folder):
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{folder}"',
                     cwd=os.environ.get("TEMP", "C:\\"), creationflags=0x08000000 | 0x00000008)  # no window, detached
