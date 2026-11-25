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
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup",
                           "Notch.lnk")
MB_YESNO, MB_ICONQUESTION, MB_ICONINFO, MB_ICONERROR, IDYES = 0x4, 0x20, 0x40, 0x10, 6
NO_WINDOW = 0x08000000
# --silent: an automatic update started by Notch itself. No questions; keeps the start-with-Windows
# choice as it was; starts the new version when done.
SILENT = "--silent" in sys.argv


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


BLOCKED = {4551: "Smart App Control", 1260: "a Windows policy", 225: "Windows Security"}
FAILED_NOTE = os.path.join(os.environ.get("APPDATA", ""), "Notch", "update_failed.json")


def can_start(exe):
    """None if the new Notch.exe starts (it just prints its version), else a short reason."""
    try:
        r = subprocess.run([exe, "--version"], creationflags=NO_WINDOW, timeout=90, capture_output=True)
    except OSError as e:
        who = BLOCKED.get(getattr(e, "winerror", None))
        return f"{who} blocked it" if who else f"it couldn't start ({e})"
    except subprocess.TimeoutExpired:
        return "it didn't respond"
    return None if r.returncode == 0 else f"it stopped with code {r.returncode}"


def blocked(problem, update):
    """The new version can't run here: leave the installed one exactly as it was."""
    import json
    version = "the new version"
    try:
        with zipfile.ZipFile(os.path.join(getattr(sys, "_MEIPASS", ""), "notch_app.zip")) as z:
            version = z.read("version.txt").decode().strip()
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    if SILENT:
        try:
            os.makedirs(os.path.dirname(FAILED_NOTE), exist_ok=True)
            with open(FAILED_NOTE, "w", encoding="utf-8") as f:
                json.dump({"version": version, "reason": problem}, f)
        except OSError:
            pass
        if os.path.exists(EXE):
            subprocess.Popen([EXE], cwd=TARGET, creationflags=0x00000008)   # bring the old one back
        return 3
    box(f"Windows won't let Notch run: {problem}.\n\n"
        + ("Your installed version was left as it was.\n\n" if update else "")
        + "Notch isn't code-signed yet, and Smart App Control (Windows Security › App & browser control) "
          "blocks unsigned apps it doesn't know.", MB_ICONERROR)
    return 3


def main():
    payload = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "notch_app.zip")
    update = os.path.exists(EXE)
    q = ("Update Notch to the new version?" if update else
         "Install Notch?\n\nA free island for the top of your Windows screen: music, volume and battery popups, "
         "notifications, clipboard history, widgets and an optional dock-style taskbar.\n\n"
         f"It goes into:\n{TARGET}\n(no admin rights needed)")
    if SILENT:
        if not update:
            return 1                # silent mode only ever updates an existing install
        startup = os.path.exists(STARTUP_LNK)
    else:
        if box(q, MB_YESNO | MB_ICONQUESTION) != IDYES:
            return 1
        startup = box("Start Notch automatically when you sign in to Windows?", MB_YESNO | MB_ICONQUESTION) == IDYES
    try:
        # 1) unpack next to the installed copy, which keeps working meanwhile
        stage = TARGET + ".new"
        for attempt in range(4):     # antivirus scanners briefly lock files they've just seen
            try:
                if os.path.isdir(stage):
                    shutil.rmtree(stage, ignore_errors=True)
                os.makedirs(stage, exist_ok=True)
                with zipfile.ZipFile(payload) as z:
                    z.extractall(stage)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(2.0)
        # 2) make sure Windows lets the new version start before replacing anything. Smart App
        #    Control can block a new unsigned build even when the previous one ran fine.
        problem = can_start(os.path.join(stage, "Notch.exe"))
        if problem:
            shutil.rmtree(stage, ignore_errors=True)
            return blocked(problem, update)
        # 3) swap the folders, then finish setting up
        if stop_old():
            time.sleep(1.0)
        old = TARGET + ".old"
        shutil.rmtree(old, ignore_errors=True)
        for attempt in range(6):
            try:
                if os.path.isdir(TARGET):
                    os.rename(TARGET, old)
                os.rename(stage, TARGET)
                break
            except OSError:
                if attempt == 5:
                    if not os.path.isdir(TARGET) and os.path.isdir(old):
                        os.rename(old, TARGET)          # put the working version back
                    raise
                time.sleep(1.5)
        shutil.rmtree(old, ignore_errors=True)
        args = [EXE, "--post-install"] + (["--startup"] if startup else [])
        subprocess.run(args, creationflags=NO_WINDOW, timeout=60)
        subprocess.Popen([EXE], cwd=TARGET, creationflags=0x00000008)    # detached
    except Exception as e:   # noqa: BLE001 (show anything that went wrong)
        box(f"Setup could not finish:\n\n{e}", MB_ICONERROR)
        return 2
    if SILENT:
        return 0
    box("Notch is installed and running.\n\n"
        "• Move your mouse to the top-middle of the screen to open the island.\n"
        "• Settings: click the Notch icon in the system tray (bottom-right, maybe under ^).\n"
        "• The dock-style taskbar is off at first. Turn it on in Settings → Taskbar.\n"
        "• To remove it: Settings → Apps → Installed apps → Notch → Uninstall.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
