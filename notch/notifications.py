"""Mirrors new Windows toast notifications by reading the local notification store (read-only)."""
import datetime as dt
import os
import sqlite3
import subprocess
import threading
import xml.etree.ElementTree as ET

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

DB = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")
QUERY = ("select n.Id, h.PrimaryId, n.ArrivalTime, n.Payload from Notification n "
         "join NotificationHandler h on h.RecordId = n.HandlerId "
         "where n.Type = 'toast' and n.Id > ? order by n.Id")


def _filetime(ft):
    try:
        utc = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=ft / 10)
        return utc.astimezone().replace(tzinfo=None)
    except Exception:
        return dt.datetime.now()


class Notifications(QObject):
    arrived = Signal(dict)

    def __init__(self):
        super().__init__()
        self.history = []          # newest first
        self._names = {}
        self._names_loading = False
        self._last = None
        self._poll()
        # wake only when Windows writes to its notification store
        self._debounce = QTimer(self, singleShot=True, interval=200, timeout=self._poll)
        self._watcher = QFileSystemWatcher(self)
        self._watch_files()
        self._watcher.fileChanged.connect(self._on_file)
        self._watcher.directoryChanged.connect(self._on_file)
        QTimer(self, interval=30_000, timeout=self._poll).start()   # safety net

    def _watch_files(self):
        paths = [p for p in (DB, DB + "-wal") if os.path.exists(p) and p not in self._watcher.files()]
        if paths:
            self._watcher.addPaths(paths)
        d = os.path.dirname(DB)
        if d not in self._watcher.directories():
            self._watcher.addPath(d)

    def _on_file(self, _path):
        self._watch_files()   # the WAL file is recreated now and then
        self._debounce.start()

    def _load_names(self):
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            folder = win32com.client.Dispatch("Shell.Application").NameSpace("shell:AppsFolder")
            self._names = {it.Path: it.Name for it in folder.Items()}
        except Exception:
            pass

    def app_name(self, aumid):
        if aumid in self._names:
            return self._names[aumid]
        if not self._names_loading:   # load the Start-menu name list only once it's needed
            self._names_loading = True
            threading.Thread(target=self._load_names, daemon=True).start()
        base = aumid.split("!")[0].split("_")[0]
        return base.split(".")[-1] if "." in base else base

    def _poll(self):
        if not os.path.exists(DB):
            return
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=0.5)
            try:
                if self._last is None:
                    row = con.execute("select max(Id) from Notification").fetchone()
                    self._last = row[0] or 0
                    # seed the history with the most recent few
                    rows = con.execute(QUERY, (max(0, self._last - 400),)).fetchall()[-15:]
                    for r in rows:
                        n = self._parse(r)
                        if n:
                            self.history.insert(0, n)
                    return
                rows = con.execute(QUERY, (self._last,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return
        for r in rows:
            self._last = max(self._last, r[0])
            n = self._parse(r)
            if n:
                self.history.insert(0, n)
                del self.history[30:]
                self.arrived.emit(n)

    def _parse(self, row):
        nid, aumid, arrival, payload = row
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", "replace")
            root = ET.fromstring(payload)
            texts = [t.text.strip() for t in root.iter("text") if t.text and t.text.strip()]
        except ET.ParseError:
            return None
        if not texts:
            return None
        return {
            "id": nid,
            "aumid": aumid,
            "title": texts[0],
            "body": " ".join(texts[1:3]),
            "time": _filetime(arrival),
        }

    @staticmethod
    def open_app(n):
        try:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{n['aumid']}"])
        except OSError:
            pass
