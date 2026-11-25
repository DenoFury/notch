"""Automatic updates from the project's GitHub releases.

A release is tagged like "v2.5.0" and carries two files:
  Notch-Setup.exe       the installer
  Notch-Setup.exe.sig   JSON: {"version", "sha256", "signature"}

Before anything is installed:
  1. the signature must verify with the public key built into this app (Ed25519). The private
     key never leaves the developer's PC, so a hacked GitHub account or a swapped download
     can't produce a valid update;
  2. the downloaded file's SHA-256 must equal the signed one;
  3. the signed version must be newer than the running one (no downgrades to old versions).
Only then is the installer started, silently, and it restarts Notch.

Checks happen a couple of minutes after start and then once a day; one small request each.
Updates only run in the installed app (Notch.exe), never when running from source.
"""
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

REPO = "DenoFury/notch"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "Notch-Setup.exe"
SIG_ASSET = ASSET + ".sig"
PUBLIC_KEY = "nMPfJxnLDitNRYB2XRRPUTMHOmpy9oHwoR8u1DX6jH8="      # Ed25519, raw, base64
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
UA = {"User-Agent": "Notch-updater", "Accept": "application/vnd.github+json"}
DAY = 24 * 3600


def parse_version(v):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def signed_message(version, sha256):
    return f"notch-update\n{version}\n{sha256}\n".encode()


def verify(sig_doc, public_key_b64=PUBLIC_KEY):
    """True if sig_doc (dict) carries a valid signature over its version + sha256."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(sig_doc["signature"]),
                   signed_message(sig_doc["version"], sig_doc["sha256"].lower()))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


def _host_ok(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return url.startswith("https://") and (host in ALLOWED_HOSTS or host.endswith(".githubusercontent.com"))



class Updater(QObject):
    available = Signal(str)            # version ready to install (downloaded and verified)
    progress = Signal(int)             # download percent
    status = Signal(str)               # "Up to date", "Checking…", errors (for Settings)
    _done = Signal(object)             # from the worker thread

    def __init__(self, app, current):
        super().__init__()
        self.app = app
        self.current = current
        self.ready = None              # (version, path to verified installer, its sha256)
        self.busy = False
        self._done.connect(self._finish)
        self.enabled = getattr(sys, "frozen", False) or bool(os.environ.get("NOTCH_UPDATER_TEST"))
        self._t = QTimer(self, interval=6 * 3600 * 1000, timeout=self._maybe_check)
        if self.enabled:
            QTimer.singleShot(120_000, self._maybe_check)
            self._t.start()

    # ------------------------------------------------------------ checking
    def _maybe_check(self):
        if self.app.settings["auto_update"] == "off":
            return
        if time.time() - float(self.app.settings["update_last_check"] or 0) >= DAY - 600:
            self.check()

    def check(self, manual=False):
        if self.busy or not self.enabled:
            if not self.enabled and manual:
                self.status.emit("Updates work in the installed app")
            return
        self.busy = True
        self.status.emit("Checking…")
        threading.Thread(target=self._work, args=(manual,), daemon=True, name="updater").start()

    def _get(self, url, binary=False):
        if not _host_ok(url) and not url.startswith(API):
            raise ValueError("unexpected address")
        req = urllib.request.Request(url, headers=UA)
        r = urllib.request.urlopen(req, timeout=30)
        if not _host_ok(r.geturl()) and not r.geturl().startswith(API):    # where redirects ended up
            raise ValueError("unexpected redirect")
        return r if binary else json.loads(r.read().decode("utf-8"))

    def _work(self, manual):
        try:
            rel = self._get(API)
            latest = parse_version(rel.get("tag_name"))
            if not latest or latest <= parse_version(self.current):
                return self._done.emit(("uptodate", manual))
            if not manual and latest == parse_version(self.app.settings["update_skip"]):
                return self._done.emit(("uptodate", manual))    # Windows blocked this one before
            assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
            if ASSET not in assets or SIG_ASSET not in assets:
                return self._done.emit(("error", "The new release has no signed installer yet"))
            with self._get(assets[SIG_ASSET], binary=True) as r:
                sig = json.loads(r.read(4096).decode("utf-8"))
            if not verify(sig):
                return self._done.emit(("error", "The update's signature is not valid; it was not installed"))
            sv = parse_version(sig["version"])
            if not sv or sv <= parse_version(self.current) or sv != latest:
                return self._done.emit(("error", "The update's version doesn't match; it was not installed"))
            folder = os.path.join(tempfile.gettempdir(), "notch-update")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"Notch-Setup-{sig['version']}.exe")
            h = hashlib.sha256()
            with self._get(assets[ASSET], binary=True) as r, open(path, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                if total > 300 * 1024 * 1024:
                    raise ValueError("update too large")
                got, last = 0, -1
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    pct = int(got * 100 / total) if total else 0
                    if pct != last:
                        last = pct
                        self.progress.emit(pct)
            if h.hexdigest() != sig["sha256"].lower():
                os.remove(path)
                return self._done.emit(("error", "The download didn't match its signature; it was not installed"))
            self._done.emit(("ready", (sig["version"], path, sig["sha256"].lower())))
        except Exception as e:  # noqa: BLE001 (network errors, GitHub limits, …)
            self._done.emit(("error", f"Couldn't check for updates ({type(e).__name__})" if manual else None))

    def _finish(self, res):
        kind, data = res
        self.busy = False
        if kind in ("uptodate", "ready"):
            self.app.settings.set("update_last_check", time.time())
        if kind == "uptodate":
            self.status.emit(f"Up to date (version {self.current})")
        elif kind == "ready":
            self.ready = data
            self.status.emit(f"Version {data[0]} is ready to install")
            self.available.emit(data[0])
        elif data:
            self.status.emit(data)

    def take_failure_note(self):
        """Setup leaves a note when Windows wouldn't let the new version run. Returns it once."""
        from .config import DATA_DIR
        path = os.path.join(DATA_DIR, "update_failed.json")
        try:
            with open(path, encoding="utf-8") as f:
                note = json.load(f)
            os.remove(path)
        except (OSError, ValueError):
            return None
        self.app.settings.set("update_skip", str(note.get("version", "")))
        return note

    # ------------------------------------------------------------ installing
    def install(self):
        """Run the verified installer silently; it closes this Notch and starts the new one."""
        if not self.ready:
            return False
        version, path, sha = self.ready
        # check the file once more right before running it: nothing may have changed it on disk
        try:
            with open(path, "rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
        except OSError:
            return False
        if digest != sha:
            self.ready = None
            self.status.emit("The downloaded update changed on disk; it was not installed")
            return False
        import subprocess
        subprocess.Popen([path, "--silent"], creationflags=0x00000008 | 0x08000000)   # detached, no window
        return True
