"""Builds dist/Notch-Setup.exe.  Run:  .venv\\Scripts\\python.exe packaging\\build.py"""
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# scratch space outside the project: antivirus / indexers briefly lock fresh files in watched folders,
# which made PyInstaller's clean-up fail
WORK = os.path.join(os.environ.get("TEMP", HERE), "notch-build")
OUT = os.path.join(ROOT, "dist")
PY = sys.executable


def make_icon(path):
    from PIL import Image, ImageDraw
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((40, 40, 984, 984), 220, fill=(28, 28, 32, 255))
    d.rounded_rectangle((200, 230, 824, 420), 95, fill=(0, 0, 0, 255), outline=(70, 70, 78, 255), width=10)
    d.ellipse((260, 275, 360, 375), fill=(48, 209, 88, 255))
    for i, h in enumerate((50, 90, 64, 100, 44)):
        x = 560 + i * 44
        d.rounded_rectangle((x, 325 - h // 2, x + 24, 325 + h // 2), 12, fill=(48, 209, 88, 255))
    big.save(path, sizes=[(s, s) for s in sizes])


def pyinstaller(*args):
    subprocess.run([PY, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
                    "--workpath", os.path.join(WORK, "build"), "--specpath", WORK, *args], check=True, cwd=ROOT)


def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    icon = os.path.join(WORK, "notch.ico")
    make_icon(icon)

    # 1) the app itself: a folder with Notch.exe
    app_dist = os.path.join(WORK, "app")
    pyinstaller("run.py", "--name", "Notch", "--windowed", "--onedir", "--icon", icon,
                "--distpath", app_dist,
                "--collect-submodules", "winrt", "--collect-submodules", "pycaw",
                "--hidden-import", "win32timezone", "--hidden-import", "notch.installer",
                "--exclude-module", "tkinter", "--exclude-module", "PIL",
                "--exclude-module", "PySide6.QtQml",
                "--exclude-module", "PySide6.QtQuick", "--exclude-module", "PySide6.QtPdf",
                "--exclude-module", "PySide6.QtWebEngineCore",
                "--collect-submodules", "segno", "--collect-data", "tzdata",
                "--hidden-import", "truststore", "--hidden-import", "win32pdh", "--hidden-import", "notch.updater",
                "--collect-submodules", "cryptography.hazmat.primitives.asymmetric",
                "--hidden-import", "PySide6.QtMultimedia", "--hidden-import", "PySide6.QtMultimediaWidgets",
                "--exclude-module", "PySide6.QtWebEngineWidgets", "--exclude-module", "PySide6.QtWebEngineQuick")
    folder = os.path.join(app_dist, "Notch")
    # drop Qt parts the app never loads
    qt = os.path.join(folder, "_internal", "PySide6")
    for name in ("Qt6Pdf.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll", "Qt6QmlWorkerScript.dll",
                 "Qt6Quick.dll", "Qt6VirtualKeyboard.dll", "opengl32sw.dll",
                 "Qt6OpenGL.dll"):
        p = os.path.join(qt, name)
        if os.path.exists(p):
            os.remove(p)
    tr = os.path.join(qt, "translations")
    if os.path.isdir(tr):
        shutil.rmtree(tr)

    # 2) zip it as the installer's payload
    payload = os.path.join(WORK, "notch_app.zip")
    import re
    version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(ROOT, "notch", "installer.py"),
                                                    encoding="utf-8").read()).group(1)
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("version.txt", version)
        for root, _, files in os.walk(folder):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, folder))

    # 3) the one-file installer
    pyinstaller(os.path.join(HERE, "setup_main.py"), "--name", "Notch-Setup", "--windowed", "--onefile",
                "--icon", icon, "--distpath", OUT, "--add-data", f"{payload};.",
                "--exclude-module", "tkinter", "--exclude-module", "PySide6", "--exclude-module", "PIL")
    exe = os.path.join(OUT, "Notch-Setup.exe")
    print(f"built {exe}  ({os.path.getsize(exe) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
