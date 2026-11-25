"""Clipboard history, saved encrypted to your Windows account (DPAPI)."""
import json
import os
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from .config import DATA_DIR

FILE = os.path.join(DATA_DIR, "clipboard.bin")
SKIP_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")


def _protect(data: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(data, "notch-clipboard", None, None, None, 0)


def _unprotect(blob: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]


class ClipboardHistory(QObject):
    changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.items = []         # dicts: text, time, pinned
        self._ignore = None
        self._load()
        self._cb = QGuiApplication.clipboard()
        self._cb.dataChanged.connect(self._on_change)
        self._save_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._save)

    def _on_change(self):
        if not self.settings["clipboard"]:
            return
        md = self._cb.mimeData()
        if md is None or not md.hasText():
            return
        if any(s in f for f in md.formats() for s in SKIP_FORMATS):
            return  # password managers mark their copies like this
        text = md.text()
        if not text.strip() or text == self._ignore:
            self._ignore = None
            return
        text = text[:10000]
        pinned = False
        for it in list(self.items):
            if it["text"] == text:
                pinned = it["pinned"]
                self.items.remove(it)
        self.items.insert(0, {"text": text, "time": time.time(), "pinned": pinned})
        self._trim()
        self.changed.emit()
        self._save_timer.start()

    def _trim(self):
        keep = int(self.settings["clipboard_keep"])
        unpinned = [i for i in self.items if not i["pinned"]]
        for extra in unpinned[keep:]:
            self.items.remove(extra)

    def copy(self, item):
        self._ignore = item["text"]
        self._cb.setText(item["text"])

    def toggle_pin(self, item):
        item["pinned"] = not item["pinned"]
        self.changed.emit()
        self._save_timer.start()

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)
            self.changed.emit()
            self._save_timer.start()

    def clear(self):
        self.items = [i for i in self.items if i["pinned"]]
        self.changed.emit()
        self._save()

    def _load(self):
        try:
            with open(FILE, "rb") as f:
                self.items = json.loads(_unprotect(f.read()).decode("utf-8"))
        except Exception:
            self.items = []

    def _save(self):
        try:
            blob = _protect(json.dumps(self.items).encode("utf-8"))
            with open(FILE, "wb") as f:
                f.write(blob)
        except Exception:
            pass
