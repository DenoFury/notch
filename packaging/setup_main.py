"""Notch Setup: unpacks Notch into the user's Programs folder, adds shortcuts and starts it.

No admin rights needed: everything goes under the current user (%LOCALAPPDATA%\\Programs\\Notch).
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
import zipfile

TARGET = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Notch")
EXE = os.path.join(TARGET, "Notch.exe")
MB_YESNO, MB_ICONQUESTION, MB_ICONINFO, MB_ICONERROR, IDYES = 0x4, 0x20, 0x40, 0x10, 6
NO_WINDOW = 0x08000000


def box(text, flags=MB_ICONINFO):
    return ctypes.windll.user32.MessageBoxW(None, text, "Notch Setup", flags | 0x10000)


def stop_old():
    """Close a running copy of Notch from the install folder before replacing its files."""
    import psutil
    folder = os.path.normcase(TARGET)
    procs = []
    for p in psutil.process_iter(["exe", "cmdline"]):
        try:
            if p.info["exe"] and os.path.normcase(os.path.dirname(p.info["exe"])) == folder:
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
    return bool(procs)


def main():
    payload = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "notch_app.zip")
    update = os.path.exists(EXE)
    q = ("Update Notch to the new version?" if update else
         "Install Notch?\n\nA free island for the top of your Windows screen: music, volume and battery popups, "
         "notifications, clipboard history, widgets and an optional dock-style taskbar.\n\n"
         f"It goes into:\n{TARGET}\n(no admin rights needed)")
    if box(q, MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 1
    startup = box("Start Notch automatically when you sign in to Windows?", MB_YESNO | MB_ICONQUESTION) == IDYES
    try:
        if stop_old():
            time.sleep(1.0)
        # antivirus scanners briefly lock files they've just seen: try a few times before giving up
        for attempt in range(4):
            try:
                if os.path.isdir(TARGET):
                    shutil.rmtree(TARGET, ignore_errors=True)
                os.makedirs(TARGET, exist_ok=True)
                with zipfile.ZipFile(payload) as z:
                    z.extractall(TARGET)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(2.0)
        args = [EXE, "--post-install"] + (["--startup"] if startup else [])
        subprocess.run(args, creationflags=NO_WINDOW, timeout=60)
        subprocess.Popen([EXE], cwd=TARGET, creationflags=0x00000008)    # detached
    except Exception as e:   # noqa: BLE001 (show anything that went wrong)
        box(f"Setup could not finish:\n\n{e}", MB_ICONERROR)
        return 2
    box("Notch is installed and running.\n\n"
        "• Move your mouse to the top-middle of the screen to open the island.\n"
        "• Settings: click the Notch icon in the system tray (bottom-right, maybe under ^).\n"
        "• The dock-style taskbar is off at first. Turn it on in Settings → Taskbar.\n"
        "• To remove it: Settings → Apps → Installed apps → Notch → Uninstall.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
