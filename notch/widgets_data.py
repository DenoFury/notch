"""Data behind the widgets: weather, focus timer and reminders."""
import datetime as dt
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

from .config import DATA_DIR

UA = {"User-Agent": "NotchWidget/1.0 (desktop notch app)"}

# WMO weather codes -> (label, Segoe Fluent glyph)
WMO = {
    0: ("Clear", ""), 1: ("Mostly clear", ""), 2: ("Partly cloudy", ""),
    3: ("Cloudy", ""), 45: ("Fog", ""), 48: ("Fog", ""),
    51: ("Drizzle", ""), 53: ("Drizzle", ""), 55: ("Drizzle", ""),
    61: ("Rain", ""), 63: ("Rain", ""), 65: ("Heavy rain", ""),
    66: ("Freezing rain", ""), 67: ("Freezing rain", ""),
    71: ("Snow", ""), 73: ("Snow", ""), 75: ("Heavy snow", ""), 77: ("Snow", ""),
    80: ("Showers", ""), 81: ("Showers", ""), 82: ("Heavy showers", ""),
    85: ("Snow showers", ""), 86: ("Snow showers", ""),
    95: ("Thunderstorm", ""), 96: ("Thunderstorm", ""), 99: ("Thunderstorm", ""),
}


def _get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- weather
class Weather(QObject):
    changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.place = ""
        self.temp = self.high = self.low = None
        self.label, self.glyph = "", ""
        self.error = ""
        self._coords = None
        self._coords_for = None
        QTimer(self, interval=15 * 60 * 1000, timeout=self.refresh).start()
        settings.changed.connect(lambda k: k in ("city", "fahrenheit") and self.refresh())
        self.refresh()

    def refresh(self):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        city = self.settings["city"].strip()
        if not city:
            self.error = "Add city in ⚙"
            self.changed.emit()
            return
        try:
            if self._coords_for != city:
                r = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name="
                              + urllib.parse.quote(city))
                hit = (r.get("results") or [None])[0]
                if not hit:
                    self.error = f"City not found: {city}"
                    self.changed.emit()
                    return
                self._coords = (hit["latitude"], hit["longitude"])
                self._coords_for = city
                self.place = hit["name"]
            lat, lon = self._coords
            unit = "&temperature_unit=fahrenheit" if self.settings["fahrenheit"] else ""
            d = _get_json(
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min"
                f"&timezone=auto&forecast_days=1{unit}")
            self.temp = round(d["current"]["temperature_2m"])
            self.high = round(d["daily"]["temperature_2m_max"][0])
            self.low = round(d["daily"]["temperature_2m_min"][0])
            self.label, self.glyph = WMO.get(d["current"]["weather_code"], ("", ""))
            self.error = ""
        except Exception:
            self.error = "Weather unavailable"
        self.changed.emit()


# ---------------------------------------------------------------- focus timer
class FocusTimer(QObject):
    tick = Signal()
    finished = Signal(str)      # "focus" or "break" that just ended

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.phase = "focus"
        self.running = False
        self.remaining = settings["focus_min"] * 60
        self._end = 0.0
        self._t = QTimer(self, interval=250, timeout=self._step)

    @property
    def total(self):
        return (self.settings["focus_min"] if self.phase == "focus" else self.settings["break_min"]) * 60

    def toggle(self):
        if self.running:
            self.remaining = max(0, self._end - time.monotonic())
            self.running = False
            self._t.stop()
        else:
            self._end = time.monotonic() + self.remaining
            self.running = True
            self._t.start()
        self.tick.emit()

    def reset(self):
        self.running = False
        self._t.stop()
        self.phase = "focus"
        self.remaining = self.total
        self.tick.emit()

    def _step(self):
        before = int(round(self.remaining))
        self.remaining = max(0, self._end - time.monotonic())
        if self.remaining > 0 and int(round(self.remaining)) == before:
            return  # same second on screen: nothing to redraw
        if self.remaining <= 0:
            done = self.phase
            self.phase = "break" if done == "focus" else "focus"
            self.running = False
            self._t.stop()
            self.remaining = self.total
            self.finished.emit(done)
        self.tick.emit()

    def text(self):
        s = int(round(self.remaining))
        return f"{s // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- reminders
REM_FILE = os.path.join(DATA_DIR, "reminders.json")


def parse_reminder(text, now=None):
    """'20m milk', '1h30m call', '19:30 call mum', 'tomorrow 9:00 stand-up', 'milk' (+1h)."""
    now = now or dt.datetime.now()
    s = text.strip()
    when = None
    tomorrow = False
    m = re.match(r"^(tomorrow|tmr)\s+", s, re.I)
    if m:
        tomorrow = True
        s = s[m.end():]
    m = re.match(r"^(?:in\s+)?(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\b\s*", s, re.I)
    if m and (m.group(1) or m.group(2)) and not tomorrow:
        when = now + dt.timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0))
        s = s[m.end():]
    else:
        m = re.match(r"^(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b\s*", s, re.I)
        if m and (m.group(2) or m.group(3)):
            h, mi = int(m.group(1)), int(m.group(2) or 0)
            ap = (m.group(3) or "").lower()
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            if h < 24 and mi < 60:
                when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                if tomorrow or when <= now:
                    when += dt.timedelta(days=1)
                s = s[m.end():]
    if when is None:
        when = (now + dt.timedelta(days=1) if tomorrow else now) + dt.timedelta(hours=1)
    return when, (s.strip() or "Reminder")


class Reminders(QObject):
    changed = Signal()
    due = Signal(dict)

    def __init__(self):
        super().__init__()
        self.items = []
        try:
            with open(REM_FILE, encoding="utf-8") as f:
                self.items = json.load(f)
        except (OSError, ValueError):
            pass
        QTimer(self, interval=1000, timeout=self._check).start()

    def add(self, text):
        when, what = parse_reminder(text)
        self.items.append({"when": when.timestamp(), "what": what})
        self.items.sort(key=lambda r: r["when"])
        self._save()
        return when, what

    def remove(self, r):
        if r in self.items:
            self.items.remove(r)
            self._save()

    def snooze(self, r, minutes=10):
        r = dict(r, when=time.time() + minutes * 60)
        self.items.append(r)
        self.items.sort(key=lambda x: x["when"])
        self._save()

    def next(self):
        return self.items[0] if self.items else None

    def _check(self):
        now = time.time()
        fired = [r for r in self.items if r["when"] <= now]
        for r in fired:
            self.items.remove(r)
        if fired:
            self._save()
        for r in fired:
            self.due.emit(r)

    def _save(self):
        try:
            with open(REM_FILE, "w", encoding="utf-8") as f:
                json.dump(self.items, f)
        except OSError:
            pass
        self.changed.emit()
