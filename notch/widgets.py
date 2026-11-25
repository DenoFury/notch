"""Widgets for the island's Widgets tab and for the desktop.

Every widget is a tile that paints itself. Its timer runs only while it is on screen, and
anything from the internet is cached and fetched off the UI thread, so a hidden widget costs nothing.
"""
import ast
import calendar as _cal
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import json
import math
import operator
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu, QPlainTextEdit, QVBoxLayout, QWidget

from .config import DATA_DIR
from .theme import font, icon_font, rgba

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NotchWidget/1.0"}


# ---------------------------------------------------------------- shared helpers
_cache = {}            # url -> (time, data)
_inflight = set()


def fetch_json(url, ttl, done, headers=None):
    """Cached GET of JSON (or text when the reply isn't JSON); done(data|None) on the UI thread."""
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        done(hit[1])
        return
    if url in _inflight:
        return
    _inflight.add(url)
    from .tools import _Bridge
    bridge = _Bridge.get()

    def work():
        data = None
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except ValueError:
                data = raw
            _cache[url] = (time.time(), data)
        except Exception:
            data = hit[1] if hit else None
        _inflight.discard(url)
        bridge.call.emit(done, data)
    threading.Thread(target=work, daemon=True).start()


def load_json(name, default):
    try:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(name, data):
    try:
        tmp = os.path.join(DATA_DIR, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, os.path.join(DATA_DIR, name))
    except OSError:
        pass


def fmt_num(v, dec=2):
    if v is None:
        return "–"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.{dec}f}"


# ---------------------------------------------------------------- base tile
class Tile(QWidget):
    key, title, glyph = "", "", ""
    span = 1                 # columns in the island's grid
    interval = 0             # ms between refreshes while visible (0 = none)
    rows = 1

    def __init__(self, ctx, app, desktop=False):
        super().__init__()
        self.ctx, self.app, self.desktop = ctx, app, desktop
        S = ctx.S
        self.setMinimumHeight(int(S(100 * self.rows + 8 * (self.rows - 1))))
        self.setFixedHeight(int(S(100 * self.rows + 8 * (self.rows - 1))))
        self._t = QTimer(self, timeout=self.refresh)
        if self.interval:
            self._t.setInterval(self.interval)

    # lifecycle: only alive while shown
    def showEvent(self, e):
        self.refresh()
        if self.interval:
            self._t.start()

    def hideEvent(self, e):
        self._t.stop()

    def refresh(self):
        self.update()

    def S(self, v):
        return self.ctx.S(v)

    # drawing
    def paintEvent(self, _):
        S, ctx = self.S, self.ctx
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ctx.bg.red(), ctx.bg.green(), ctx.bg.blue(), 215) if self.desktop else rgba(ctx.fg, 14))
        p.drawRoundedRect(r, S(16), S(16))
        if self.desktop:
            p.setPen(QPen(rgba(ctx.line, 200), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), S(16), S(16))
        p.setPen(ctx.dim)
        p.setFont(icon_font(S(11)))
        p.drawText(QRectF(S(12), S(8), S(16), S(16)), Qt.AlignCenter, self.glyph)
        p.setFont(font(S(10.5), QFont.DemiBold))
        p.drawText(QRectF(S(31), S(8), r.width() - S(40), S(16)), Qt.AlignVCenter | Qt.AlignLeft, self.title)
        self.draw(p, QRectF(S(12), S(28), r.width() - S(24), r.height() - S(36)))

    def draw(self, p, r):
        pass

    def text(self, p, rect, s, px=12, color=None, weight=QFont.Normal, align=Qt.AlignLeft | Qt.AlignVCenter):
        p.setFont(font(self.S(px), weight))
        p.setPen(color or self.ctx.fg)
        p.drawText(rect, align, QFontMetrics(p.font()).elidedText(s, Qt.ElideRight, int(rect.width())))

    def bar(self, p, rect, frac, color=None):
        h = rect.height()
        p.setPen(Qt.NoPen)
        p.setBrush(rgba(self.ctx.fg, 35))
        p.drawRoundedRect(rect, h / 2, h / 2)
        if frac > 0:
            p.setBrush(color or self.ctx.accent)
            p.drawRoundedRect(QRectF(rect.left(), rect.top(), max(h, rect.width() * min(1, frac)), h), h / 2, h / 2)

    # right-click: desktop / remove / settings for that widget
    def contextMenuEvent(self, e):
        m = QMenu(self)
        self.extra_menu(m)
        host = self.app.widget_host
        if self.desktop:
            m.addAction("Unlock to move" if host.locked() else "Lock position", host.toggle_lock)
            m.addAction("Remove from desktop", lambda: host.unpin(self.key))
        else:
            m.addAction("Put on the desktop", lambda: host.pin(self.key))
            m.addAction("Remove widget", lambda: host.remove(self.key))
        m.popup(e.globalPos())

    def extra_menu(self, m):
        pass

    def ask(self, title, label_, key, default=""):
        cur = self.app.settings[key]
        if isinstance(cur, list):
            cur = ", ".join(cur)
        txt, ok = QInputDialog.getText(None, title, label_, QLineEdit.Normal, str(cur or default))
        return txt.strip() if ok else None


# ---------------------------------------------------------------- 1 world clock
class WorldClock(Tile):
    key, title, glyph, interval = "world", "World clock", "", 15_000

    def draw(self, p, r):
        from zoneinfo import ZoneInfo
        clocks = self.app.settings["world_clocks"] or []
        now = dt.datetime.now().astimezone()
        rh = r.height() / max(1, min(3, len(clocks)))
        for n, entry in enumerate(clocks[:3]):
            city, _, zone = entry.partition("|")
            try:
                t = dt.datetime.now(ZoneInfo(zone.strip() or city.strip()))
            except Exception:
                self.text(p, QRectF(r.left(), r.top() + n * rh, r.width(), rh), f"{city}: unknown zone", 11, self.ctx.dim)
                continue
            diff = (t.utcoffset() - now.utcoffset()).total_seconds() / 3600
            fmt = "%H:%M" if self.app.settings["clock_24h"] else "%I:%M %p"
            row = QRectF(r.left(), r.top() + n * rh, r.width(), rh)
            self.text(p, row, city.strip(), 12, weight=QFont.DemiBold)
            day = "" if t.date() == now.date() else (" tmrw" if t.date() > now.date() else " yday")
            self.text(p, row, f"{diff:+.0f}h{day}", 10, self.ctx.dim, align=Qt.AlignCenter)
            shown = t.strftime(fmt) if self.app.settings["clock_24h"] else t.strftime(fmt).lstrip("0")
            self.text(p, row, shown, 13, weight=QFont.DemiBold, align=Qt.AlignRight | Qt.AlignVCenter)

    def extra_menu(self, m):
        m.addAction("Cities…", self._edit)

    def _edit(self):
        t = self.ask("World clock", "Up to 3 entries 'City|Area/Zone', comma separated\n"
                                    "e.g. New York|America/New_York, Tokyo|Asia/Tokyo", "world_clocks")
        if t is not None:
            self.app.settings.set("world_clocks", [x.strip() for x in t.split(",") if x.strip()][:3])
            self.update()


# ---------------------------------------------------------------- 2 system
class _GPU:
    """GPU 3D-engine load from Windows' performance counters (opened only while shown)."""
    def __init__(self):
        self.q = None

    def read(self):
        try:
            import win32pdh
            if self.q is None:
                self.q = win32pdh.OpenQuery()
                self.c = win32pdh.AddEnglishCounter(self.q, r"\GPU Engine(*engtype_3D)\Utilization Percentage")
                win32pdh.CollectQueryData(self.q)
                return None
            win32pdh.CollectQueryData(self.q)
            vals = win32pdh.GetFormattedCounterArray(self.c, win32pdh.PDH_FMT_DOUBLE)
            if isinstance(vals, tuple):          # older pywin32 returned (type, values)
                vals = vals[-1]
            return min(100.0, sum(vals.values()))
        except Exception:
            return None

    def close(self):
        if self.q is not None:
            try:
                import win32pdh
                win32pdh.CloseQuery(self.q)
            except Exception:
                pass
            self.q = None


class System(Tile):
    key, title, glyph, interval, span = "system", "System", "", 2000, 2

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.gpu = _GPU()
        self.vals = {}
        self._net = None

    def showEvent(self, e):
        self.app.system.want_cpu(True)
        super().showEvent(e)

    def hideEvent(self, e):
        self.app.system.want_cpu(False)
        self.gpu.close()
        super().hideEvent(e)

    def refresh(self):
        import psutil
        v = self.vals
        v["CPU"] = self.app.system.cpu
        v["RAM"] = psutil.virtual_memory().percent
        g = self.gpu.read()
        if g is not None:
            v["GPU"] = g
        try:
            v["Disk"] = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").percent
        except OSError:
            pass
        n = psutil.net_io_counters()
        now = time.monotonic()
        if self._net:
            t0, r0, s0 = self._net
            dtm = max(0.1, now - t0)
            v["down"], v["up"] = (n.bytes_recv - r0) / dtm, (n.bytes_sent - s0) / dtm
        self._net = (now, n.bytes_recv, n.bytes_sent)
        self.update()

    def draw(self, p, r):
        from .indicators import human
        keys = [k for k in ("CPU", "GPU", "RAM", "Disk") if k in self.vals]
        colw = r.width() * 0.62
        rh = r.height() / 4
        for n, k in enumerate(keys):
            y = r.top() + n * rh
            lw = self.S(36)
            self.text(p, QRectF(r.left(), y, lw, rh), k, 11, self.ctx.dim)
            self.bar(p, QRectF(r.left() + lw, y + rh / 2 - self.S(3), colw - lw - self.S(40), self.S(6)), self.vals[k] / 100)
            self.text(p, QRectF(r.left() + colw - self.S(36), y, self.S(36), rh), f"{self.vals[k]:.0f}%", 11,
                      align=Qt.AlignRight | Qt.AlignVCenter)
        x = r.left() + colw + self.S(14)
        w = r.right() - x
        self.text(p, QRectF(x, r.top(), w, r.height() / 2), "↓ " + (human(self.vals["down"]) + "/s" if "down" in self.vals
                                                                    else "…"), 13, weight=QFont.DemiBold)
        self.text(p, QRectF(x, r.center().y(), w, r.height() / 2), "↑ " + (human(self.vals["up"]) + "/s" if "up" in self.vals
                                                                           else "…"), 13, self.ctx.dim)


# ---------------------------------------------------------------- 3 progress
class Progress(Tile):
    key, title, glyph, interval = "progress", "Progress", "", 60_000

    def draw(self, p, r):
        now = dt.datetime.now()
        day = (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400
        week = (now.weekday() + day) / 7
        mdays = _cal.monthrange(now.year, now.month)[1]
        month = (now.day - 1 + day) / mdays
        ydays = 366 if _cal.isleap(now.year) else 365
        year = (now.timetuple().tm_yday - 1 + day) / ydays
        rh = r.height() / 4
        for n, (name, f) in enumerate((("Day", day), ("Week", week), ("Month", month), ("Year", year))):
            y = r.top() + n * rh
            self.text(p, QRectF(r.left(), y, self.S(44), rh), name, 11, self.ctx.dim)
            self.bar(p, QRectF(r.left() + self.S(46), y + rh / 2 - self.S(3), r.width() - self.S(84), self.S(6)), f)
            self.text(p, QRectF(r.right() - self.S(34), y, self.S(34), rh), f"{f * 100:.0f}%", 11,
                      align=Qt.AlignRight | Qt.AlignVCenter)


# ---------------------------------------------------------------- 4 sun and moon
def moon_phase(d=None):
    """0..1 (0 new, .5 full) from a known new moon; good to a few hours."""
    d = d or dt.datetime.utcnow()
    ref = dt.datetime(2000, 1, 6, 18, 14)
    return ((d - ref).total_seconds() / 86400 % 29.530588853) / 29.530588853


def moon_name(ph):
    names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon",
             "Waning gibbous", "Last quarter", "Waning crescent"]
    return names[int((ph * 8) + 0.5) % 8]


class SunMoon(Tile):
    key, title, glyph, interval = "sun", "Sun and moon", "", 600_000

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sun = None

    def refresh(self):
        c = self.app.weather._coords
        if c:
            fetch_json(f"https://api.open-meteo.com/v1/forecast?latitude={c[0]}&longitude={c[1]}"
                       f"&daily=sunrise,sunset&timezone=auto&forecast_days=1", 3 * 3600, self._got)
        self.update()

    def _got(self, d):
        try:
            self.sun = (d["daily"]["sunrise"][0][-5:], d["daily"]["sunset"][0][-5:])
        except (TypeError, KeyError, IndexError):
            self.sun = None
        self.update()

    def draw(self, p, r):
        half = r.height() / 2
        if self.sun:
            self.text(p, QRectF(r.left(), r.top(), r.width() * 0.5, half), "↑ " + self.sun[0], 14, weight=QFont.DemiBold)
            self.text(p, QRectF(r.left(), r.top() + half, r.width() * 0.5, half), "↓ " + self.sun[1], 14, self.ctx.dim)
        else:
            self.text(p, QRectF(r.left(), r.top(), r.width() * 0.55, r.height()),
                      "Add your city in ⚙" if not self.app.settings["city"] else "…", 11, self.ctx.dim)
        ph = moon_phase()
        d = min(half * 1.25, r.width() * 0.25)
        c = QPointF(r.right() - d / 2 - self.S(4), r.top() + d / 2 + self.S(2))
        self._moon(p, c, d / 2, ph)
        self.text(p, QRectF(r.left() + r.width() * 0.45, r.bottom() - self.S(16), r.width() * 0.55, self.S(16)),
                  moon_name(ph), 10, self.ctx.dim, align=Qt.AlignRight | Qt.AlignVCenter)

    def _moon(self, p, c, rad, ph):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(60, 60, 68))
        p.drawEllipse(c, rad, rad)
        lit = QPainterPath()
        # lit part: half disc on one side plus an ellipse whose width follows the phase
        k = math.cos(ph * 2 * math.pi)            # 1 new … -1 full
        side = 1 if ph < 0.5 else -1               # waxing: lit on the right
        half = QPainterPath()
        half.moveTo(c.x(), c.y() - rad)
        half.arcTo(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), 90, -180 * side)
        half.closeSubpath()
        ell = QPainterPath()
        ell.addEllipse(c, abs(k) * rad, rad)
        lit = half.subtracted(ell) if k > 0 else half.united(ell)
        p.setBrush(QColor(240, 236, 220))
        p.drawPath(lit)


# ---------------------------------------------------------------- 5 air quality
class Air(Tile):
    key, title, glyph, interval = "air", "Air quality", "", 1_800_000

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.d = None

    def refresh(self):
        c = self.app.weather._coords
        if c:
            fetch_json(f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={c[0]}&longitude={c[1]}"
                       f"&current=european_aqi,pm2_5,pm10,uv_index", 1800, self._got)
        self.update()

    def _got(self, d):
        self.d = d.get("current") if isinstance(d, dict) else None
        self.update()

    def draw(self, p, r):
        if not self.d:
            self.text(p, r, "Add your city in ⚙" if not self.app.settings["city"] else "…", 11, self.ctx.dim)
            return
        aqi = self.d.get("european_aqi") or 0
        label, col = next(((lb, c) for lim, lb, c in ((20, "Good", "#30d158"), (40, "Fair", "#a3d930"),
                                                     (60, "Moderate", "#ffd60a"), (80, "Poor", "#ff9f0a"),
                                                     (100, "Very poor", "#ff453a"), (1e9, "Extremely poor", "#bf5af2"))
                           if aqi <= lim))
        self.text(p, QRectF(r.left(), r.top(), r.width(), r.height() * 0.55), f"{aqi:.0f}", 24, weight=QFont.DemiBold)
        self.text(p, QRectF(r.left() + self.S(48), r.top(), r.width(), r.height() * 0.55), label, 12, QColor(col),
                  QFont.DemiBold)
        self.text(p, QRectF(r.left(), r.top() + r.height() * 0.6, r.width(), r.height() * 0.4),
                  f"PM2.5 {self.d.get('pm2_5', 0):.0f} · PM10 {self.d.get('pm10', 0):.0f} · UV {self.d.get('uv_index', 0):.0f}",
                  10, self.ctx.dim)


# ---------------------------------------------------------------- 6 to-do
class Todo(Tile):
    key, title, glyph, span, rows = "todo", "To-do", "", 2, 2
    FILE = "todo.json"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.items = load_json(self.FILE, [])
        self.inp = QLineEdit(self)
        self.inp.setPlaceholderText("Add a task and press Enter")
        self.inp.setFont(font(self.S(12)))
        self.inp.setStyleSheet(f"QLineEdit{{background:{self.ctx.css(rgba(self.ctx.fg, 20))};color:{self.ctx.fg.name()};"
                               f"border:none;border-radius:{int(self.S(9))}px;padding:{int(self.S(4))}px {int(self.S(9))}px;}}")
        self.inp.returnPressed.connect(self._add)
        self._rows = []
        self.setMouseTracking(True)

    def resizeEvent(self, e):
        self.inp.setGeometry(int(self.S(12)), int(self.height() - self.S(36)), int(self.width() - self.S(24)), int(self.S(28)))

    def _add(self):
        t = self.inp.text().strip()
        if t:
            self.items.append({"t": t, "done": False})
            save_json(self.FILE, self.items)
            self.inp.clear()
            self.update()

    def draw(self, p, r):
        r = QRectF(r.left(), r.top(), r.width(), r.height() - self.S(36))
        self._rows = []
        lh = self.S(22)
        open_first = sorted(range(len(self.items)), key=lambda i: self.items[i]["done"])
        if not self.items:
            self.text(p, QRectF(r.left(), r.top(), r.width(), lh), "Nothing to do", 11, self.ctx.dim)
        for row, i in enumerate(open_first):
            y = r.top() + row * lh
            if y + lh > r.bottom() + 1:
                break
            it = self.items[i]
            box = QRectF(r.left(), y + lh / 2 - self.S(7), self.S(14), self.S(14))
            p.setPen(QPen(self.ctx.accent if it["done"] else self.ctx.dim, 1.4))
            p.setBrush(self.ctx.accent if it["done"] else Qt.NoBrush)
            p.drawRoundedRect(box, self.S(4), self.S(4))
            if it["done"]:
                p.setPen(QPen(QColor("#000"), 1.6))
                p.drawLine(QPointF(box.left() + 3, box.center().y()), QPointF(box.center().x() - 1, box.bottom() - 3))
                p.drawLine(QPointF(box.center().x() - 1, box.bottom() - 3), QPointF(box.right() - 3, box.top() + 3))
            self.text(p, QRectF(r.left() + self.S(22), y, r.width() - self.S(46), lh), it["t"], 12,
                      self.ctx.dim if it["done"] else self.ctx.fg)
            x = QRectF(r.right() - self.S(18), y, self.S(18), lh)
            p.setPen(rgba(self.ctx.dim, 160))
            p.setFont(icon_font(self.S(9)))
            p.drawText(x, Qt.AlignCenter, "")
            self._rows.append((QRectF(r.left(), y, r.width() - self.S(22), lh), x, i))

    def mousePressEvent(self, e):
        pos = e.position()
        for row, x, i in self._rows:
            if x.contains(pos):
                del self.items[i]
            elif row.contains(pos):
                self.items[i]["done"] = not self.items[i]["done"]
            else:
                continue
            save_json(self.FILE, self.items)
            self.update()
            return

    def extra_menu(self, m):
        m.addAction("Clear finished", self._clear_done)

    def _clear_done(self):
        self.items = [i for i in self.items if not i["done"]]
        save_json(self.FILE, self.items)
        self.update()


# ---------------------------------------------------------------- 7 note
class Note(Tile):
    key, title, glyph, span, rows = "note", "Note", "", 2, 2

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.ed = QPlainTextEdit(self)
        self.ed.setFont(font(self.S(12)))
        self.ed.setPlaceholderText("Write anything. It's saved as you type.")
        self.ed.setStyleSheet(f"QPlainTextEdit{{background:transparent;color:{self.ctx.fg.name()};border:none;}}")
        try:
            with open(os.path.join(DATA_DIR, "note.txt"), encoding="utf-8") as f:
                self.ed.setPlainText(f.read())
        except OSError:
            pass
        self._save = QTimer(self, singleShot=True, interval=800, timeout=self._write)
        self.ed.textChanged.connect(self._save.start)

    def resizeEvent(self, e):
        self.ed.setGeometry(int(self.S(8)), int(self.S(26)), int(self.width() - self.S(16)), int(self.height() - self.S(32)))

    def _write(self):
        try:
            with open(os.path.join(DATA_DIR, "note.txt"), "w", encoding="utf-8") as f:
                f.write(self.ed.toPlainText())
        except OSError:
            pass


# ---------------------------------------------------------------- 8 countdown
class Countdown(Tile):
    key, title, glyph, interval = "countdown", "Countdown", "", 30_000

    def draw(self, p, r):
        st = self.app.settings
        name, when = st["countdown_title"], st["countdown_date"]
        try:
            target = dt.datetime.fromisoformat(when)
        except (TypeError, ValueError):
            self.text(p, r, "Right-click › Set date…", 11, self.ctx.dim)
            return
        left = target - dt.datetime.now()
        secs = left.total_seconds()
        if secs <= 0:
            big, small = "It's here!", target.strftime("%d %b %Y")
        elif secs >= 86400:
            big, small = f"{int(secs // 86400)} days", f"{int(secs % 86400 // 3600)} h  ·  {target:%d %b %Y}"
        else:
            big, small = f"{int(secs // 3600)} h {int(secs % 3600 // 60)} min", target.strftime("%H:%M")
        self.text(p, QRectF(r.left(), r.top(), r.width(), r.height() * 0.3), name or "Countdown", 11, self.ctx.dim)
        self.text(p, QRectF(r.left(), r.top() + r.height() * 0.28, r.width(), r.height() * 0.45), big, 20,
                  weight=QFont.DemiBold)
        self.text(p, QRectF(r.left(), r.top() + r.height() * 0.74, r.width(), r.height() * 0.26), small, 10, self.ctx.dim)

    def extra_menu(self, m):
        m.addAction("Set date…", self._edit)

    def _edit(self):
        t = self.ask("Countdown", "What are you counting down to?", "countdown_title", "Holiday")
        if t is None:
            return
        d = self.ask("Countdown", "Date (and time), e.g. 2026-12-24 or 2026-12-24 18:00", "countdown_date",
                     dt.date.today().isoformat())
        if d is None:
            return
        try:
            dt.datetime.fromisoformat(d)
        except ValueError:
            return
        self.app.settings.set("countdown_title", t)
        self.app.settings.set("countdown_date", d)
        self.update()


# ---------------------------------------------------------------- 9 calculator
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10,
          "ln": math.log, "abs": abs, "round": round, "pi": math.pi, "e": math.e}


def safe_eval(expr):
    """Arithmetic only: numbers, + - * / % ** //, parentheses and a few maths functions."""
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", ".")
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow):
                # keep results small enough to compute instantly ((9**999)**999 would freeze Notch)
                bits = abs(b) * max(1, abs(int(a)).bit_length() if isinstance(a, int) else 64)
                if abs(b) > 1000 or bits > 20000:
                    raise ValueError("too big")
            r = _OPS[type(n.op)](a, b)
            if isinstance(r, int) and r.bit_length() > 20000:
                raise ValueError("too big")
            return r
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _FUNCS and not callable(_FUNCS[n.id]):
            return _FUNCS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and callable(_FUNCS.get(n.func.id)):
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("not allowed")
    if len(expr) > 200:
        raise ValueError("too long")
    return ev(ast.parse(expr, mode="eval"))


class Calc(Tile):
    key, title, glyph = "calc", "Calculator", ""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.result = ""
        self.inp = QLineEdit(self)
        self.inp.setPlaceholderText("12*(3+4)  ·  15% of 80 → 0.15*80")
        self.inp.setFont(font(self.S(12)))
        self.inp.setStyleSheet(f"QLineEdit{{background:{self.ctx.css(rgba(self.ctx.fg, 20))};color:{self.ctx.fg.name()};"
                               f"border:none;border-radius:{int(self.S(9))}px;padding:{int(self.S(4))}px {int(self.S(9))}px;}}")
        self.inp.textChanged.connect(self._calc)
        self.inp.returnPressed.connect(self._copy)

    def resizeEvent(self, e):
        self.inp.setGeometry(int(self.S(12)), int(self.S(28)), int(self.width() - self.S(24)), int(self.S(28)))

    def _calc(self, t):
        try:
            v = safe_eval(t) if t.strip() else None
            self.result = "" if v is None else (f"{v:,.10g}" if isinstance(v, float) else f"{v:,}")
        except Exception:
            self.result = "…"
        self.update()

    def _copy(self):
        if self.result and self.result != "…":
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(self.result.replace(",", ""))
            self.inp.setText(self.result.replace(",", ""))

    def draw(self, p, r):
        self.text(p, QRectF(r.left(), r.top() + self.S(32), r.width(), r.height() - self.S(32)),
                  ("= " + self.result) if self.result else "Enter copies the result", 18 if self.result else 10,
                  self.ctx.fg if self.result else self.ctx.dim, QFont.DemiBold, Qt.AlignRight | Qt.AlignVCenter)


# ---------------------------------------------------------------- 10 currency (ECB rates)
class Currency(Tile):
    key, title, glyph, interval = "currency", "Exchange rates", "", 3_600_000

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rates, self.date = {}, ""

    def refresh(self):
        st = self.app.settings
        base = (st["currency_base"] or "EUR").upper()
        syms = ",".join(s.upper() for s in (st["currencies"] or []) if s.upper() != base)
        if syms:
            fetch_json(f"https://api.frankfurter.app/latest?from={base}&to={syms}", 3600, self._got)
        self.update()

    def _got(self, d):
        if isinstance(d, dict):
            self.rates, self.date = d.get("rates", {}), d.get("date", "")
        self.update()

    def draw(self, p, r):
        base = (self.app.settings["currency_base"] or "EUR").upper()
        items = list(self.rates.items())[:3]
        rh = r.height() / 4
        self.text(p, QRectF(r.left(), r.top(), r.width(), rh), f"1 {base} =", 10, self.ctx.dim)
        for n, (k, v) in enumerate(items):
            row = QRectF(r.left(), r.top() + (n + 1) * rh, r.width(), rh)
            self.text(p, row, k, 12, weight=QFont.DemiBold)
            self.text(p, row, fmt_num(v, 4 if v < 10 else 2), 12, align=Qt.AlignRight | Qt.AlignVCenter)
        if not items:
            self.text(p, QRectF(r.left(), r.top() + rh, r.width(), rh), "…", 11, self.ctx.dim)

    def extra_menu(self, m):
        m.addAction("Currencies…", self._edit)

    def _edit(self):
        b = self.ask("Exchange rates", "Base currency (e.g. EUR)", "currency_base", "EUR")
        if b is None:
            return
        t = self.ask("Exchange rates", "Up to 3 currencies, comma separated (e.g. USD, GBP, TRY)", "currencies")
        if t is None:
            return
        self.app.settings.set("currency_base", b.upper()[:3])
        self.app.settings.set("currencies", [x.strip().upper()[:3] for x in t.split(",") if x.strip()][:3])
        self.refresh()


# ---------------------------------------------------------------- 11 prices (stocks / crypto / forex)
def quote(symbol, done):
    """Yahoo Finance chart API: done({"price", "prev", "points"[]}) or None."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range=1d&interval=15m")

    def got(d):
        try:
            res = d["chart"]["result"][0]
            meta = res["meta"]
            pts = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
            done({"price": meta["regularMarketPrice"], "prev": meta.get("chartPreviousClose") or meta.get("previousClose"),
                  "points": pts, "cur": meta.get("currency", "")})
        except Exception:
            done(None)
    fetch_json(url, 110, got)


class Prices(Tile):
    key, title, glyph, interval, span = "prices", "Prices", "", 120_000, 2

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.q = {}

    def refresh(self):
        for s in (self.app.settings["tickers"] or [])[:4]:
            quote(s, lambda d, s=s: self._got(s, d))
        self.update()

    def _got(self, s, d):
        if d:
            self.q[s] = d
        self.update()

    def draw(self, p, r):
        syms = (self.app.settings["tickers"] or [])[:4]
        if not syms:
            self.text(p, r, "Right-click › Symbols…", 11, self.ctx.dim)
            return
        rh = r.height() / max(3, len(syms))
        for n, s in enumerate(syms):
            row = QRectF(r.left(), r.top() + n * rh, r.width(), rh)
            d = self.q.get(s)
            self.text(p, QRectF(row.left(), row.top(), self.S(90), rh), s.replace("-USD", "").replace("=X", ""), 12,
                      weight=QFont.DemiBold)
            if not d:
                self.text(p, row, "…", 11, self.ctx.dim, align=Qt.AlignRight | Qt.AlignVCenter)
                continue
            chg = (d["price"] / d["prev"] - 1) * 100 if d.get("prev") else 0
            col = QColor("#30d158") if chg >= 0 else QColor("#ff453a")
            self._spark(p, QRectF(row.left() + self.S(96), row.top() + rh * 0.2, row.width() * 0.32, rh * 0.6),
                        d["points"], col)
            self.text(p, QRectF(row.right() - self.S(170), row.top(), self.S(110), rh), fmt_num(d["price"]), 12,
                      align=Qt.AlignRight | Qt.AlignVCenter)
            self.text(p, QRectF(row.right() - self.S(56), row.top(), self.S(56), rh), f"{chg:+.2f}%", 11, col,
                      align=Qt.AlignRight | Qt.AlignVCenter)

    def _spark(self, p, r, pts, col):
        if len(pts) < 2:
            return
        lo, hi = min(pts), max(pts)
        span = (hi - lo) or 1
        path = QPainterPath()
        for i, v in enumerate(pts):
            pt = QPointF(r.left() + r.width() * i / (len(pts) - 1), r.bottom() - (v - lo) / span * r.height())
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(col, self.S(1.4)))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def extra_menu(self, m):
        m.addAction("Symbols…", self._edit)
        m.addAction("Price alerts…", self._alerts)

    def _edit(self):
        t = self.ask("Prices", "Up to 4 symbols (Yahoo Finance), comma separated\n"
                               "Crypto: BTC-USD, ETH-USD  ·  Stocks: AAPL, MSFT  ·  Forex: EURUSD=X", "tickers")
        if t is not None:
            self.app.settings.set("tickers", [x.strip().upper() for x in t.split(",") if x.strip()][:4])
            self.refresh()

    def _alerts(self):
        t = self.ask("Price alerts", "Alerts, comma separated, e.g. BTC-USD>70000, AAPL<180\n"
                                     "Checked every 5 minutes; each alert fires once.", "price_alerts")
        if t is not None:
            self.app.settings.set("price_alerts", [x.strip().upper() for x in t.split(",") if x.strip()])


# ---------------------------------------------------------------- 12 website uptime
class Uptime(Tile):
    key, title, glyph, interval = "uptime", "Websites", "", 300_000

    def refresh(self):
        self.app.uptime.check()
        self.update()

    def showEvent(self, e):
        self.app.uptime.changed.connect(self.update)
        super().showEvent(e)

    def draw(self, p, r):
        urls = (self.app.settings["uptime_urls"] or [])[:3]
        if not urls:
            self.text(p, r, "Right-click › Websites…", 11, self.ctx.dim)
            return
        rh = r.height() / 3
        for n, u in enumerate(urls):
            row = QRectF(r.left(), r.top() + n * rh, r.width(), rh)
            st = self.app.uptime.state.get(u)
            col = QColor("#8e8e95") if st is None else QColor("#30d158") if st[0] else QColor("#ff453a")
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(row.left() + self.S(5), row.center().y()), self.S(4), self.S(4))
            self.text(p, QRectF(row.left() + self.S(16), row.top(), row.width() - self.S(70), rh),
                      urllib.parse.urlsplit(u).netloc or u, 12)
            self.text(p, row, "…" if st is None else (f"{st[1]:.0f} ms" if st[0] else "down"), 11, self.ctx.dim,
                      align=Qt.AlignRight | Qt.AlignVCenter)

    def extra_menu(self, m):
        m.addAction("Websites…", self._edit)

    def _edit(self):
        t = self.ask("Websites", "Up to 3 addresses, comma separated (e.g. https://example.com)", "uptime_urls")
        if t is not None:
            urls = [(x if "://" in x else "https://" + x) for x in (y.strip() for y in t.split(",")) if x]
            self.app.settings.set("uptime_urls", urls[:3])
            self.app.uptime.check(force=True)


# ---------------------------------------------------------------- 13 ping
class _ICMP:
    def __init__(self):
        ip = ctypes.windll.iphlpapi
        self.ip = ip
        ip.IcmpCreateFile.restype = wt.HANDLE
        ip.IcmpSendEcho.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.WORD, ctypes.c_void_p,
                                    ctypes.c_void_p, wt.DWORD, wt.DWORD]
        ip.IcmpCloseHandle.argtypes = [wt.HANDLE]

    def ping(self, host, n=5, timeout=1000):
        import socket
        try:
            addr = socket.gethostbyname(host)
        except OSError:
            return None
        a = int.from_bytes(socket.inet_aton(addr), "little")
        h = self.ip.IcmpCreateFile()
        times = []
        try:
            buf = ctypes.create_string_buffer(64)
            reply = ctypes.create_string_buffer(256)
            for _ in range(n):
                if self.ip.IcmpSendEcho(h, a, buf, 32, None, reply, 256, timeout):
                    status = int.from_bytes(reply.raw[4:8], "little")
                    if status == 0:
                        times.append(int.from_bytes(reply.raw[8:12], "little"))
                time.sleep(0.15)
        finally:
            self.ip.IcmpCloseHandle(h)
        loss = (n - len(times)) / n * 100
        if not times:
            return {"avg": None, "jitter": None, "loss": loss}
        jit = sum(abs(times[i] - times[i - 1]) for i in range(1, len(times))) / max(1, len(times) - 1)
        return {"avg": sum(times) / len(times), "jitter": jit, "loss": loss, "addr": addr}


class Ping(Tile):
    key, title, glyph, interval = "ping", "Ping", "", 30_000

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.res = None
        self._busy = False

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        host = self.app.settings["ping_host"] or "1.1.1.1"
        from .tools import _Bridge
        bridge = _Bridge.get()

        def work():
            r = _ICMP().ping(host)
            bridge.call.emit(self._got, r)
        threading.Thread(target=work, daemon=True).start()

    def _got(self, r):
        self._busy = False
        self.res = r if r is not None else {"avg": None, "jitter": None, "loss": 100}
        self.update()

    def draw(self, p, r):
        host = self.app.settings["ping_host"] or "1.1.1.1"
        if not self.res:
            self.text(p, r, f"Pinging {host}…", 11, self.ctx.dim)
            return
        avg = self.res["avg"]
        col = self.ctx.fg if avg is None or avg < 80 else QColor("#ff9f0a") if avg < 200 else QColor("#ff453a")
        self.text(p, QRectF(r.left(), r.top(), r.width(), r.height() * 0.55),
                  "No reply" if avg is None else f"{avg:.0f} ms", 22, col, QFont.DemiBold)
        sub = (f"jitter {self.res['jitter']:.0f} ms · loss {self.res['loss']:.0f}%" if avg is not None
               else f"loss {self.res['loss']:.0f}%")
        self.text(p, QRectF(r.left(), r.top() + r.height() * 0.58, r.width(), r.height() * 0.21), sub, 10, self.ctx.dim)
        self.text(p, QRectF(r.left(), r.top() + r.height() * 0.79, r.width(), r.height() * 0.21), host, 10, self.ctx.dim)

    def extra_menu(self, m):
        m.addAction("Host…", self._edit)

    def _edit(self):
        t = self.ask("Ping", "Host to ping (e.g. 1.1.1.1 or google.com)", "ping_host", "1.1.1.1")
        if t:
            self.app.settings.set("ping_host", t)
            self.res = None
            self.refresh()


# ---------------------------------------------------------------- 14 batteries
DEVPKEY_BT_BATTERY = ("{104EA319-6EE2-4701-BD47-8DDBF425BBE5}", 2)


def bluetooth_batteries():
    """[(name, percent)] for paired Bluetooth devices that report a battery level."""
    import uuid
    sa = ctypes.windll.setupapi

    class DEVPROPKEY(ctypes.Structure):
        _fields_ = [("fmtid", ctypes.c_byte * 16), ("pid", wt.ULONG)]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", ctypes.c_byte * 16), ("DevInst", wt.DWORD),
                    ("Reserved", ctypes.c_void_p)]
    sa.SetupDiGetClassDevsW.restype = wt.HANDLE
    sa.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, wt.HWND, wt.DWORD]
    sa.SetupDiEnumDeviceInfo.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    sa.SetupDiGetDevicePropertyW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(DEVPROPKEY),
                                             ctypes.POINTER(wt.ULONG), ctypes.c_void_p, wt.DWORD,
                                             ctypes.POINTER(wt.DWORD), wt.DWORD]
    sa.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]

    def key(guid, pid):
        k = DEVPROPKEY()
        ctypes.memmove(k.fmtid, uuid.UUID(guid).bytes_le, 16)
        k.pid = pid
        return k
    k_batt = key(*DEVPKEY_BT_BATTERY)
    k_name = key("{a45c254e-df1c-4efd-8020-67d146a850e0}", 14)       # DEVPKEY_Device_FriendlyName
    h = sa.SetupDiGetClassDevsW(None, None, None, 0x2 | 0x4)          # DIGCF_PRESENT | DIGCF_ALLCLASSES
    out = []
    try:
        i = 0
        info = SP_DEVINFO_DATA()
        info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        while sa.SetupDiEnumDeviceInfo(h, i, ctypes.byref(info)):
            i += 1
            typ = wt.ULONG()
            val = ctypes.c_ubyte()
            if sa.SetupDiGetDevicePropertyW(h, ctypes.byref(info), ctypes.byref(k_batt), ctypes.byref(typ),
                                            ctypes.byref(val), 1, None, 0):
                buf = ctypes.create_unicode_buffer(256)
                name = ""
                if sa.SetupDiGetDevicePropertyW(h, ctypes.byref(info), ctypes.byref(k_name), ctypes.byref(typ),
                                                buf, ctypes.sizeof(buf), None, 0):
                    name = buf.value
                if name and not any(n == name for n, _ in out):
                    out.append((name, int(val.value)))
    finally:
        sa.SetupDiDestroyDeviceInfoList(h)
    return out


class Batteries(Tile):
    key, title, glyph, interval = "batteries", "Batteries", "", 300_000

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.devs = None

    def refresh(self):
        from .tools import _Bridge
        bridge = _Bridge.get()

        def work():
            try:
                d = bluetooth_batteries()
            except Exception:
                d = []
            bridge.call.emit(self._got, d)
        threading.Thread(target=work, daemon=True).start()
        self.update()

    def _got(self, d):
        self.devs = d
        self.update()

    def draw(self, p, r):
        rows = []
        b = self.app.system.battery()
        if b:
            rows.append(("This PC" + (" ⚡" if b.power_plugged else ""), int(b.percent)))
        rows += (self.devs or [])
        if not rows:
            self.text(p, r, "No batteries found" if self.devs is not None else "…", 11, self.ctx.dim)
            return
        rh = r.height() / 3
        for n, (name, pct) in enumerate(rows[:3]):
            row = QRectF(r.left(), r.top() + n * rh, r.width(), rh)
            self.text(p, QRectF(row.left(), row.top(), row.width() * 0.5, rh), name, 11)
            col = QColor("#ff453a") if pct <= 20 else None
            self.bar(p, QRectF(row.left() + row.width() * 0.52, row.center().y() - self.S(3), row.width() * 0.3,
                               self.S(6)), pct / 100, col)
            self.text(p, row, f"{pct}%", 11, align=Qt.AlignRight | Qt.AlignVCenter)


# ---------------------------------------------------------------- 15 screen time + play time
class ScreenTime(Tile):
    key, title, glyph, interval = "screentime", "Screen time", "", 60_000

    def draw(self, p, r):
        st = self.app.usage.today()

        def hm(s):
            return f"{int(s // 3600)} h {int(s % 3600 // 60)} min" if s >= 3600 else f"{int(s // 60)} min"
        self.text(p, QRectF(r.left(), r.top(), r.width(), r.height() * 0.45), hm(st["active"]), 20,
                  weight=QFont.DemiBold)
        self.text(p, QRectF(r.left(), r.top() + r.height() * 0.48, r.width(), r.height() * 0.26),
                  "at the computer today", 10, self.ctx.dim)
        self.text(p, QRectF(r.left(), r.top() + r.height() * 0.74, r.width(), r.height() * 0.26),
                  f"Play time (fullscreen): {hm(st['play'])}", 10, self.ctx.dim)


class Usage:
    """Counts active minutes (input in the last 2 minutes) and fullscreen-app minutes, per day.
    One wake-up a minute, and only when the Screen time widget is in use."""
    FILE = "usage.json"

    def __init__(self, app):
        self.app = app
        self.data = load_json(self.FILE, {})
        self._t = QTimer(interval=60_000, timeout=self._tick)
        self.set_enabled(False)

    def set_enabled(self, on):
        if on and not self._t.isActive():
            self._t.start()
        elif not on:
            self._t.stop()

    def today(self):
        return self.data.get(dt.date.today().isoformat(), {"active": 0, "play": 0})

    def _tick(self):
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
        idle = (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000
        key = dt.date.today().isoformat()
        d = self.data.setdefault(key, {"active": 0, "play": 0})
        if idle < 120 or self.app.media.playing:
            d["active"] += 60
        if self.app.system.fullscreen:
            d["play"] += 60
        for k in sorted(self.data)[:-30]:       # keep a month
            del self.data[k]
        save_json(self.FILE, self.data)


# ---------------------------------------------------------------- 16 calendar (.ics)
def parse_ics(text, days=2):
    """Today's and tomorrow's events from an .ics file: [(start datetime, all_day, title)].
    Handles single events and simple daily/weekly repeats."""
    from zoneinfo import ZoneInfo
    text = re.sub(r"\r?\n[ \t]", "", text)
    events = []
    local = dt.datetime.now().astimezone().tzinfo
    start_day = dt.date.today()
    end_day = start_day + dt.timedelta(days=days)
    for block in text.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        props = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            name, _, params = k.partition(";")
            props[name.upper()] = (params, v.strip())
        if "DTSTART" not in props:
            continue
        params, v = props["DTSTART"]
        title = props.get("SUMMARY", ("", "(no title)"))[1].replace("\\,", ",").replace("\\n", " ")
        try:
            if len(v) == 8:
                start, all_day = dt.datetime.strptime(v, "%Y%m%d"), True
            else:
                all_day = False
                start = dt.datetime.strptime(v.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
                if v.endswith("Z"):
                    start = start.replace(tzinfo=dt.timezone.utc).astimezone(local).replace(tzinfo=None)
                elif "TZID=" in params:
                    tz = params.split("TZID=")[1].split(";")[0]
                    try:
                        start = start.replace(tzinfo=ZoneInfo(tz)).astimezone(local).replace(tzinfo=None)
                    except Exception:
                        pass
        except ValueError:
            continue
        rule = props.get("RRULE", ("", ""))[1]
        occ = [start]
        if "FREQ=DAILY" in rule or "FREQ=WEEKLY" in rule:
            step = dt.timedelta(days=1 if "FREQ=DAILY" in rule else 7)
            until = re.search(r"UNTIL=(\d{8})", rule)
            last = dt.datetime.strptime(until.group(1), "%Y%m%d").date() if until else end_day
            t = start
            if t.date() < start_day:
                t += step * max(0, (start_day - t.date()).days // step.days)
            occ = []
            while t.date() < end_day and t.date() <= last:
                occ.append(t)
                t += step
        for s in occ:
            if start_day <= s.date() < end_day:
                events.append((s, all_day, title))
    events.sort(key=lambda e: (e[0].date(), not e[1], e[0]))
    return events


class Today(Tile):
    key, title, glyph, interval, span = "today", "Today", "", 600_000, 2

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.events = None

    def refresh(self):
        url = (self.app.settings["ics_url"] or "").strip()
        if url:
            fetch_json(url.replace("webcal://", "https://"), 1800, self._got)
        self.update()

    def _got(self, d):
        self.events = parse_ics(d) if isinstance(d, str) else []
        self.update()

    def draw(self, p, r):
        now = dt.datetime.now()
        self.text(p, QRectF(r.left(), r.top(), r.width() * 0.3, self.S(24)), now.strftime("%a %d"), 15,
                  weight=QFont.DemiBold)
        if not (self.app.settings["ics_url"] or "").strip():
            self.text(p, QRectF(r.left() + r.width() * 0.3, r.top(), r.width() * 0.7, r.height()),
                      "Right-click › Calendar link… (an .ics address from Google/Outlook)", 10, self.ctx.dim)
            return
        if self.events is None:
            self.text(p, QRectF(r.left() + r.width() * 0.3, r.top(), r.width() * 0.7, self.S(20)), "…", 11, self.ctx.dim)
            return
        up = [e for e in self.events if e[1] or e[0] >= now - dt.timedelta(minutes=30)][:3]
        if not up:
            self.text(p, QRectF(r.left() + r.width() * 0.3, r.top(), r.width() * 0.7, self.S(20)), "Nothing planned", 11,
                      self.ctx.dim)
        rh = r.height() / 3
        for n, (s, all_day, title) in enumerate(up):
            row = QRectF(r.left() + r.width() * 0.3, r.top() + n * rh, r.width() * 0.7, rh)
            when = "all day" if all_day else s.strftime("%H:%M")
            if s.date() != now.date():
                when = "tmrw " + when
            self.text(p, QRectF(row.left(), row.top(), self.S(78), rh), when, 11, self.ctx.accent, QFont.DemiBold)
            self.text(p, QRectF(row.left() + self.S(80), row.top(), row.width() - self.S(80), rh), title, 12)

    def extra_menu(self, m):
        m.addAction("Calendar link…", self._edit)

    def _edit(self):
        t = self.ask("Calendar", "Secret .ics address of your calendar\n(Google: Settings › your calendar › "
                                 "'Secret address in iCal format')", "ics_url")
        if t is not None:
            self.app.settings.set("ics_url", t)
            self.events = None
            self.refresh()


# ---------------------------------------------------------------- 17 photos
class Photos(Tile):
    key, title, glyph, interval, span, rows = "photos", "Photos", "", 12_000, 2, 2

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.files, self.i, self.img = [], -1, None

    def refresh(self):
        folder = self.app.settings["photos_dir"]
        if folder and os.path.isdir(folder):
            if not self.files:
                exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
                for root, _, fs in os.walk(folder):
                    self.files += [os.path.join(root, f) for f in fs if f.lower().endswith(exts)]
                    if len(self.files) > 2000:
                        break
                import random
                random.shuffle(self.files)
            if self.files:
                from PySide6.QtGui import QImageReader
                self.i = (self.i + 1) % len(self.files)
                rd = QImageReader(self.files[self.i])
                rd.setAutoTransform(True)
                sz = rd.size()
                w, h = int(self.width() * self.devicePixelRatioF()), int(self.height() * self.devicePixelRatioF())
                if sz.isValid() and sz.width() > 0:
                    k = max(w / sz.width(), h / sz.height())
                    rd.setScaledSize(sz * min(1.0, k))          # decode only as big as needed
                self.img = rd.read()
        self.update()

    def paintEvent(self, e):
        if self.img is None or self.img.isNull():
            return super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, self.S(16), self.S(16))
        p.setClipPath(path)
        iw, ih = self.img.width(), self.img.height()
        k = max(r.width() / iw, r.height() / ih)
        src = QRectF((iw - r.width() / k) / 2, (ih - r.height() / k) / 2, r.width() / k, r.height() / k)
        p.drawImage(r, self.img, src)

    def draw(self, p, r):
        self.text(p, r, "Right-click › Folder…", 11, self.ctx.dim)

    def extra_menu(self, m):
        m.addAction("Folder…", self._edit)
        m.addAction("Next photo", self.refresh)

    def _edit(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(None, "Photos folder", self.app.settings["photos_dir"] or "")
        if d:
            self.app.settings.set("photos_dir", d)
            self.files, self.i = [], -1
            self.refresh()


# ---------------------------------------------------------------- registry
WIDGETS = {c.key: c for c in (System, WorldClock, Progress, SunMoon, Air, Todo, Note, Countdown, Calc, Currency,
                              Prices, Uptime, Ping, Batteries, ScreenTime, Today, Photos)}
