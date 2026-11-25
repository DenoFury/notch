"""The open island card: Home, Music, Notifications and Clipboard tabs."""
import datetime as dt
import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget)

from .theme import G, font, icon_font, rgba, safe_tip


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def ago(t):
    d = dt.datetime.now() - t
    s = int(d.total_seconds())
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return t.strftime("%d %b")


class Ctx:
    """Scale + colours shared by the card's widgets."""

    def __init__(self, island):
        st = island.st
        self.S = island.S
        fill, fg, dim, line = st.surface()
        self.bg = QColor(*fill)
        self.fg, self.dim, self.line = QColor(fg), QColor(dim), QColor(line)
        self.accent = QColor(st.accent())

    def css(self, c):
        return c.name(QColor.HexArgb) if c.alpha() < 255 else c.name()


# ---------------------------------------------------------------- small widgets
class IconButton(QPushButton):
    def __init__(self, ctx, glyph, size=30, glyph_px=14, tip="", accent=False, filled=False):
        super().__init__(glyph)
        S = ctx.S
        self.setFont(icon_font(S(glyph_px)))
        self.setFixedSize(int(S(size)), int(S(size)))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        col = ctx.accent if accent else ctx.fg
        base = rgba(ctx.fg, 28) if filled else QColor(0, 0, 0, 0)
        self.setStyleSheet(
            f"QPushButton{{color:{col.name()};background:{ctx.css(base)};border:none;"
            f"border-radius:{int(S(size) / 2)}px;}}"
            f"QPushButton:hover{{background:{ctx.css(rgba(ctx.fg, 40))};}}"
            f"QPushButton:checked{{background:{ctx.css(rgba(ctx.fg, 36))};color:{ctx.accent.name()};}}")


class Tile(QFrame):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("tile")
        self.setStyleSheet(f"QFrame#tile{{background:{ctx.css(rgba(ctx.fg, 14))};"
                           f"border-radius:{int(ctx.S(16))}px;}}")


def label(ctx, text="", px=12, color=None, weight=QFont.Normal, display=False):
    lb = QLabel()
    # plain text always: notification titles, file names and song titles come from outside and
    # must never be read as HTML (Qt would otherwise render text that looks like markup)
    lb.setTextFormat(Qt.PlainText)
    lb.setText(text)
    lb.setFont(font(ctx.S(px), weight, display))
    lb.setStyleSheet(f"color:{(color or ctx.fg).name()};background:transparent;")
    return lb


class ElidedLabel(QLabel):
    def setText(self, t):
        self._full = t
        super().setText(t)
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        full = getattr(self, "_full", "")
        el = QFontMetrics(self.font()).elidedText(full, Qt.ElideRight, max(10, self.width()))
        if el != super().text():
            super().setText(el)

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        s.setWidth(10)
        return s


def elabel(ctx, px=12, color=None, weight=QFont.Normal):
    lb = ElidedLabel()
    lb.setTextFormat(Qt.PlainText)          # outside text is never HTML
    lb.setFont(font(ctx.S(px), weight))
    lb.setStyleSheet(f"color:{(color or ctx.fg).name()};background:transparent;")
    lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return lb


class Ring(QWidget):
    def __init__(self, ctx, size):
        super().__init__()
        self.ctx, self.frac, self.text = ctx, 0.0, ""
        self.setFixedSize(int(size), int(size))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.ctx.S(5)
        r = QRectF(w, w, self.width() - 2 * w, self.height() - 2 * w)
        track = QPen(rgba(self.ctx.fg, 40), w)
        p.setPen(track)
        p.drawEllipse(r)
        if self.frac > 0:
            arc = QPen(self.ctx.accent, w)
            arc.setCapStyle(Qt.RoundCap)
            p.setPen(arc)
            p.drawArc(r, 90 * 16, int(-360 * 16 * self.frac))
        p.setPen(self.ctx.fg)
        p.setFont(font(self.ctx.S(15), QFont.DemiBold))
        p.drawText(self.rect(), Qt.AlignCenter, self.text)


class SeekBar(QWidget):
    seek = Signal(float)

    def __init__(self, ctx):
        super().__init__()
        self.ctx, self.frac = ctx, 0.0
        self.setFixedHeight(int(ctx.S(14)))
        self.setCursor(Qt.PointingHandCursor)
        self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.ctx.S(5)
        y = (self.height() - h) / 2
        frac = self._drag if self._drag is not None else self.frac
        p.setPen(Qt.NoPen)
        p.setBrush(rgba(self.ctx.fg, 45))
        p.drawRoundedRect(QRectF(0, y, self.width(), h), h / 2, h / 2)
        if frac > 0:
            p.setBrush(self.ctx.accent)
            p.drawRoundedRect(QRectF(0, y, max(h, self.width() * frac), h), h / 2, h / 2)
        if self.underMouse() or self._drag is not None:
            k = self.ctx.S(12)
            p.setBrush(self.ctx.fg)
            p.drawEllipse(QRectF(self.width() * frac - k / 2, (self.height() - k) / 2, k, k))

    def _f(self, e):
        return min(1.0, max(0.0, e.position().x() / max(1, self.width())))

    def mousePressEvent(self, e):
        self._drag = self._f(e)
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._drag = self._f(e)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self.frac = self._f(e)
            self._drag = None
            self.seek.emit(self.frac)
            self.update()

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


def slider(ctx):
    s = QSlider(Qt.Horizontal)
    s.setRange(0, 100)
    S = ctx.S
    s.setStyleSheet(
        f"QSlider{{background:transparent;}}"
        f"QSlider::groove:horizontal{{height:{int(S(5))}px;background:{ctx.css(rgba(ctx.fg, 45))};"
        f"border-radius:{int(S(2.5))}px;}}"
        f"QSlider::sub-page:horizontal{{background:{ctx.fg.name()};border-radius:{int(S(2.5))}px;}}"
        f"QSlider::handle:horizontal{{background:{ctx.fg.name()};width:{int(S(11))}px;"
        f"margin:-{int(S(3))}px 0;border-radius:{int(S(5.5))}px;}}")
    s.setCursor(Qt.PointingHandCursor)
    return s


def line_edit(ctx, placeholder):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFont(font(ctx.S(12)))
    S = ctx.S
    e.setStyleSheet(
        f"QLineEdit{{background:{ctx.css(rgba(ctx.fg, 20))};color:{ctx.fg.name()};border:none;"
        f"border-radius:{int(S(10))}px;padding:{int(S(5))}px {int(S(10))}px;"
        f"selection-background-color:{ctx.accent.name()};}}")
    return e


def scroll_area(ctx):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    S = ctx.S
    sa.setStyleSheet(
        f"QScrollArea{{background:transparent;}} QScrollArea > QWidget > QWidget{{background:transparent;}}"
        f"QScrollBar:vertical{{background:transparent;width:{int(S(6))}px;margin:0;}}"
        f"QScrollBar::handle:vertical{{background:{ctx.css(rgba(ctx.fg, 50))};border-radius:{int(S(3))}px;"
        f"min-height:{int(S(20))}px;}}"
        f"QScrollBar::add-line,QScrollBar::sub-line{{height:0;}}"
        f"QScrollBar::add-page,QScrollBar::sub-page{{background:transparent;}}")
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, int(S(6)), 0)
    lay.setSpacing(int(S(6)))
    sa.setWidget(inner)
    return sa, lay


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            it.widget().deleteLater()


class ClickRow(QFrame):
    clicked = Signal()

    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("row")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"QFrame#row{{background:{ctx.css(rgba(ctx.fg, 14))};border-radius:{int(ctx.S(12))}px;}}"
                           f"QFrame#row:hover{{background:{ctx.css(rgba(ctx.fg, 26))};}}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


# ---------------------------------------------------------------- pages
class HomePage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(8)))
        row = QHBoxLayout()
        row.setSpacing(int(S(8)))
        lay.addLayout(row, 1)

        # clock
        t = Tile(ctx)
        v = QVBoxLayout(t)
        v.setContentsMargins(int(S(14)), int(S(12)), int(S(10)), int(S(12)))
        self.clock = label(ctx, "", 34, weight=QFont.DemiBold, display=True)
        self.date = label(ctx, "", 12, ctx.dim)
        self.day = label(ctx, "", 12, ctx.accent, QFont.DemiBold)
        v.addWidget(self.day)
        v.addWidget(self.clock)
        v.addWidget(self.date)
        v.addStretch()
        t.setFixedWidth(int(S(124)))
        row.addWidget(t)

        # weather
        t = Tile(ctx)
        t.setCursor(Qt.PointingHandCursor)
        t.mousePressEvent = lambda e: app.weather.refresh()
        v = QVBoxLayout(t)
        v.setContentsMargins(int(S(12)), int(S(12)), int(S(10)), int(S(12)))
        v.setSpacing(int(S(2)))
        self.w_glyph = QLabel()
        self.w_glyph.setFont(icon_font(S(20)))
        self.w_glyph.setStyleSheet(f"color:{ctx.accent.name()};background:transparent;")
        self.w_temp = label(ctx, "", 26, weight=QFont.DemiBold, display=True)
        self.w_label = elabel(ctx, 12)
        self.w_hl = label(ctx, "", 11, ctx.dim)
        self.w_place = elabel(ctx, 11, ctx.dim)
        for w in (self.w_glyph, self.w_temp, self.w_label, self.w_hl, self.w_place):
            v.addWidget(w)
        v.addStretch()
        t.setFixedWidth(int(S(108)))
        row.addWidget(t)

        # focus timer
        t = Tile(ctx)
        v = QVBoxLayout(t)
        v.setContentsMargins(int(S(8)), int(S(10)), int(S(8)), int(S(8)))
        v.setSpacing(int(S(4)))
        self.ring = Ring(ctx, S(68))
        self.phase = label(ctx, "", 11, ctx.dim)
        self.phase.setAlignment(Qt.AlignCenter)
        btns = QHBoxLayout()
        self.t_play = IconButton(ctx, G["play"], 28, 12, "Start / pause", filled=True)
        self.t_reset = IconButton(ctx, G["refresh"], 28, 12, "Reset", filled=True)
        self.t_play.clicked.connect(app.timer.toggle)
        self.t_reset.clicked.connect(app.timer.reset)
        btns.addStretch()
        btns.addWidget(self.t_play)
        btns.addWidget(self.t_reset)
        btns.addStretch()
        v.addWidget(self.ring, 0, Qt.AlignHCenter)
        v.addWidget(self.phase)
        v.addLayout(btns)
        t.setFixedWidth(int(S(108)))
        row.addWidget(t)

        # reminders
        t = Tile(ctx)
        v = QVBoxLayout(t)
        v.setContentsMargins(int(S(12)), int(S(10)), int(S(10)), int(S(10)))
        v.setSpacing(int(S(4)))
        hdr = QHBoxLayout()
        ic = QLabel(G["alarm"])
        ic.setFont(icon_font(S(12)))
        ic.setStyleSheet(f"color:{ctx.accent.name()};background:transparent;")
        hdr.addWidget(ic)
        title = elabel(ctx, 12, weight=QFont.DemiBold)
        title.setText("Reminders")
        hdr.addWidget(title, 1)
        v.addLayout(hdr)
        self.rem_box = QVBoxLayout()
        self.rem_box.setSpacing(int(S(2)))
        v.addLayout(self.rem_box)
        v.addStretch()
        self.rem_in = line_edit(ctx, "20m tea · 19:30 call")
        self.rem_in.returnPressed.connect(self._add_reminder)
        self.rem_in.setMinimumWidth(10)
        v.addWidget(self.rem_in)
        row.addWidget(t, 1)

        self.stats = label(ctx, "", 11, ctx.dim)
        lay.addWidget(self.stats)

        app.weather.changed.connect(self.refresh_weather)
        app.timer.tick.connect(self.refresh_timer)
        app.reminders.changed.connect(self.refresh_reminders)
        self._t = QTimer(self, interval=1000, timeout=self.refresh_clock)

    def showEvent(self, e):
        if not e.spontaneous():
            self.app.system.want_cpu(True)
        self.refresh()
        self._t.start()

    def hideEvent(self, e):
        if not e.spontaneous():
            self.app.system.want_cpu(False)
        self._t.stop()

    def refresh(self):
        self.refresh_clock()
        self.refresh_weather()
        self.refresh_timer()
        self.refresh_reminders()

    def refresh_clock(self):
        now = dt.datetime.now()
        h24 = self.app.settings["clock_24h"]
        self.clock.setText(now.strftime("%H:%M") if h24 else now.strftime("%I:%M").lstrip("0"))
        self.day.setText(now.strftime("%A"))
        self.date.setText(now.strftime("%d %B"))
        sw = self.app.system
        parts = [f"CPU {sw.cpu:.0f}%", f"RAM {sw.ram():.0f}%"]
        b = sw.battery()
        if b:
            parts.append(f"Battery {b.percent:.0f}%" + (" · charging" if b.power_plugged else ""))
        self.stats.setText("    ".join(parts))

    def refresh_weather(self):
        w = self.app.weather
        if w.error:
            self.w_glyph.setText(G["sun"])
            self.w_temp.setText("--°")
            self.w_label.setText(w.error)
            self.w_hl.setText("")
            self.w_place.setText("")
        else:
            self.w_glyph.setText(w.glyph)
            self.w_temp.setText(f"{w.temp}°" if w.temp is not None else "…")
            self.w_label.setText(w.label)
            self.w_hl.setText(f"H {w.high}°  L {w.low}°" if w.high is not None else "")
            self.w_place.setText(w.place)

    def refresh_timer(self):
        t = self.app.timer
        self.ring.text = t.text()
        self.ring.frac = 1 - t.remaining / max(1, t.total)
        self.ring.update()
        self.t_play.setText(G["pause"] if t.running else G["play"])
        self.phase.setText(("Focus" if t.phase == "focus" else "Break") + (" · running" if t.running else ""))

    def refresh_reminders(self):
        clear_layout(self.rem_box)
        items = self.app.reminders.items[:3]
        if not items:
            empty = elabel(self.ctx, 11, self.ctx.dim)
            empty.setText("Nothing coming up")
            self.rem_box.addWidget(empty)
        for r in items:
            row = QHBoxLayout()
            when = dt.datetime.fromtimestamp(r["when"])
            fmt = "%H:%M" if when.date() == dt.date.today() else "%a %H:%M"
            tl = label(self.ctx, when.strftime(fmt), 11, self.ctx.accent, QFont.DemiBold)
            what = elabel(self.ctx, 11)
            what.setText(r["what"])
            done = IconButton(self.ctx, G["check"], 20, 10, "Done")
            done.clicked.connect(lambda _=False, r=r: self.app.reminders.remove(r))
            row.addWidget(tl)
            row.addWidget(what, 1)
            row.addWidget(done)
            w = QWidget()
            w.setStyleSheet("background:transparent;")
            w.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.rem_box.addWidget(w)

    def _add_reminder(self):
        text = self.rem_in.text().strip()
        if not text:
            return
        when, what = self.app.reminders.add(text)
        self.rem_in.clear()


class LyricsView(QWidget):
    """Synced lyrics: lines before and after stay visible and dim, the line being sung is
    highlighted and kept in the middle; it glides to the next line. Click a line to jump there."""

    def __init__(self, ctx, media):
        super().__init__()
        self.ctx, self.media = ctx, media
        S = ctx.S
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(int(S(60)))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._font = font(S(13), QFont.DemiBold)
        self._gap = S(7)
        self._lines = []          # the lyrics these rows were built from
        self._rows = []           # [(top, height, text)]
        self._row_w = -1
        self._idx = -1
        self._y = 0.0             # content y shown at the middle of the view
        self.active = False       # lyrics on, something playing and lyrics found
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        self._anim = QVariantAnimation(self, duration=380, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._y = float(v)
        self.update()

    def _layout(self):
        lines = self.media.lyrics
        w = self.width()
        if lines is self._lines and w == self._row_w:
            return
        self._lines, self._row_w = lines, w
        fm = QFontMetrics(self._font)
        y, rows = 0.0, []
        for _, text in lines:
            text = text or "♪"
            h = fm.boundingRect(0, 0, max(10, w), 10000, int(Qt.TextWordWrap), text).height()
            rows.append((y, h, text))
            y += h + self._gap
        self._rows = rows
        self._y = self._target(self._idx)

    def _target(self, i):
        if not self._rows:
            return 0.0
        top, h, _ = self._rows[max(0, i)]
        return top + h / 2 - (self.height() * 0.18 if i < 0 else 0)   # before line 1: show it a bit lower

    def set_index(self, i):
        lines_changed = self.media.lyrics is not self._lines
        if i == self._idx and not lines_changed:
            return
        jump = lines_changed or abs(i - self._idx) > 2      # new song or a seek: no long glide
        self._idx = i
        self._layout()
        target = self._target(i)
        self._anim.stop()
        if jump or not self.isVisible():
            self._y = target
            self.update()
        else:
            self._anim.setStartValue(self._y)
            self._anim.setEndValue(target)
            self._anim.start()

    def resizeEvent(self, e):
        self._layout()
        self._y = self._target(self._idx)

    def _row_at(self, y):
        if not self.active:
            return None
        cy = y - self.height() / 2 + self._y
        for n, (top, h, _) in enumerate(self._rows):
            if top - self._gap / 2 <= cy <= top + h + self._gap / 2:
                return n
        return None

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self._row_at(e.position().y()) is not None else Qt.ArrowCursor)

    def mousePressEvent(self, e):
        n = self._row_at(e.position().y())
        if n is not None and e.button() == Qt.LeftButton:
            self.media.seek(max(0.0, self.media.lyrics[n][0]))
            self.set_index(n)

    def paintEvent(self, _):
        if not self.active:
            return
        self._layout()
        if not self._rows:
            return
        from PySide6.QtGui import QLinearGradient, QPixmap
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self._font)
        off = h / 2 - self._y
        accent, dim = self.ctx.accent, self.ctx.dim
        for n, (top, rh, text) in enumerate(self._rows):
            y = top + off
            if y + rh < 0:
                continue
            if y > h:
                break
            d = abs(n - self._idx) if self._idx >= 0 else n + 1
            p.setPen(accent if d == 0 else rgba(dim, (210, 150, 110)[min(d - 1, 2)]))
            p.drawText(QRectF(0, y, w, rh), int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop), text)
        # fade the top and bottom edges out
        g = QLinearGradient(0, 0, 0, h)
        f = min(0.3, self.ctx.S(22) / max(1, h))
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(f, QColor(0, 0, 0, 255))
        g.setColorAt(1 - f, QColor(0, 0, 0, 255))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.fillRect(QRectF(0, 0, w, h), g)
        p.end()
        QPainter(self).drawPixmap(0, 0, pm)


class MusicPage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        m = app.media
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(16)))
        self.art = QLabel()
        self.art.setFixedSize(int(S(150)), int(S(150)))
        self.art.setStyleSheet("background:transparent;")
        lay.addWidget(self.art, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(int(S(3)))
        lay.addLayout(col, 1)
        self.title = elabel(ctx, 16, weight=QFont.DemiBold)
        self.sub = elabel(ctx, 12, ctx.dim)
        self.lyrics = LyricsView(ctx, m)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        col.addSpacing(int(S(4)))
        col.addWidget(self.lyrics, 1)
        col.addSpacing(int(S(4)))
        self.seek = SeekBar(ctx)
        self.seek.seek.connect(lambda f: m.seek(f * m.duration) if m.duration else None)
        col.addWidget(self.seek)
        times = QHBoxLayout()
        self.t_pos = label(ctx, "", 10, ctx.dim)
        self.t_len = label(ctx, "", 10, ctx.dim)
        times.addWidget(self.t_pos)
        times.addStretch()
        times.addWidget(self.t_len)
        col.addLayout(times)

        ctl = QHBoxLayout()
        ctl.setSpacing(int(S(6)))
        self.b_prev = IconButton(ctx, G["prev"], 36, 15, "Previous")
        self.b_play = IconButton(ctx, G["play"], 42, 18, "Play / pause", filled=True)
        self.b_next = IconButton(ctx, G["next"], 36, 15, "Next")
        self.b_prev.clicked.connect(m.prev)
        self.b_play.clicked.connect(m.toggle)
        self.b_next.clicked.connect(m.next)
        for b in (self.b_prev, self.b_play, self.b_next):
            ctl.addWidget(b)
        ctl.addSpacing(int(S(14)))
        self.b_mute = IconButton(ctx, G["vol3"], 28, 13, "Mute")
        self.b_mute.clicked.connect(app.audio.toggle_mute)
        ctl.addWidget(self.b_mute)
        self.vol = slider(ctx)
        self.vol.valueChanged.connect(lambda v: self.vol.isSliderDown() and app.audio.set_level(v / 100))
        self.vol.sliderMoved.connect(lambda v: app.audio.set_level(v / 100))
        ctl.addWidget(self.vol, 1)
        col.addLayout(ctl)

        self.empty = label(ctx, "Nothing playing", 14, ctx.dim)
        m.changed.connect(self.refresh)
        m.art_changed.connect(self.refresh_art)
        m.lyrics_changed.connect(self.tick)
        app.audio.volume_changed.connect(self.refresh_volume)
        self._t = QTimer(self, interval=250, timeout=self.tick)

    def showEvent(self, e):
        self.refresh()
        self.refresh_art()
        self.refresh_volume()
        self._t.start()

    def hideEvent(self, e):
        self._t.stop()

    def refresh_volume(self, *_):
        a = self.app.audio
        if not self.vol.isSliderDown():
            self.vol.blockSignals(True)
            self.vol.setValue(int(round(a.level * 100)))
            self.vol.blockSignals(False)
        from .theme import vol_glyph
        self.b_mute.setText(vol_glyph(a.level, a.muted))

    def refresh(self):
        m = self.app.media
        if not m.available:
            self.title.setText("Nothing playing")
            self.sub.setText("Play something in Spotify, a browser or any media app")
        else:
            self.title.setText(m.title)
            self.sub.setText("  ·  ".join(x for x in (m.artist, m.app) if x))
        self.b_play.setText(G["pause"] if m.playing else G["play"])
        self.tick()

    def refresh_art(self):
        S = self.ctx.S
        size = int(S(150))
        from PySide6.QtGui import QPixmap
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), S(16), S(16))
        p.setClipPath(path)
        img = self.app.media.art
        if img.isNull() or not self.app.media.available:
            p.fillRect(QRectF(0, 0, size, size), rgba(self.ctx.fg, 22))
            p.setPen(self.ctx.dim)
            p.setFont(icon_font(S(40)))
            p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, G["music"])
        else:
            # crop to square
            s = min(img.width(), img.height())
            src = QRectF((img.width() - s) / 2, (img.height() - s) / 2, s, s)
            p.drawImage(QRectF(0, 0, size, size), img, src)
        p.end()
        self.art.setPixmap(pm)

    def tick(self):
        m = self.app.media
        if m.duration:
            pos = m.position
            self.seek.frac = pos / m.duration
            self.t_pos.setText(fmt_time(pos))
            self.t_len.setText("-" + fmt_time(m.duration - pos))
        else:
            self.seek.frac = 0
            self.t_pos.setText("")
            self.t_len.setText("")
        self.seek.update()
        show = bool(self.app.settings["lyrics"] and m.available and m.lyrics)
        if show != self.lyrics.active:
            self.lyrics.active = show
            self.lyrics.update()
        if show:
            self.lyrics.set_index(m.lyric_index())


class NotifPage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(6)))
        hdr = QHBoxLayout()
        hdr.addWidget(label(ctx, "Notifications", 14, weight=QFont.DemiBold))
        hdr.addStretch()
        clr = IconButton(ctx, G["clear"], 26, 12, "Clear all")
        clr.clicked.connect(self._clear)
        hdr.addWidget(clr)
        lay.addLayout(hdr)
        self.sa, self.list = scroll_area(ctx)
        lay.addWidget(self.sa, 1)
        app.notifications.arrived.connect(self._on_arrived)

    def _on_arrived(self, _n):
        if self.isVisible():
            self.refresh()

    def showEvent(self, e):
        self.refresh()

    def _clear(self):
        self.app.notifications.history.clear()
        self.refresh()

    def refresh(self):
        clear_layout(self.list)
        ctx = self.ctx
        S = ctx.S
        hist = self.app.notifications.history
        if not hist:
            self.list.addWidget(label(ctx, "No notifications yet", 12, ctx.dim))
        for n in hist[:30]:
            row = ClickRow(ctx)
            v = QVBoxLayout(row)
            v.setContentsMargins(int(S(12)), int(S(8)), int(S(12)), int(S(8)))
            v.setSpacing(int(S(1)))
            top = QHBoxLayout()
            app_l = label(ctx, self.app.notifications.app_name(n["aumid"]), 11, ctx.accent, QFont.DemiBold)
            top.addWidget(app_l)
            top.addStretch()
            top.addWidget(label(ctx, ago(n["time"]), 10, ctx.dim))
            v.addLayout(top)
            t = elabel(ctx, 12, weight=QFont.DemiBold)
            t.setText(n["title"])
            v.addWidget(t)
            if n["body"]:
                b = elabel(ctx, 11, ctx.dim)
                b.setText(n["body"])
                v.addWidget(b)
            row.clicked.connect(lambda n=n: self.app.notifications.open_app(n))
            self.list.addWidget(row)
        self.list.addStretch()


class ClipPage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(6)))
        hdr = QHBoxLayout()
        self.mode = "history"
        self.b_hist = IconButton(ctx, G["clip"], 26, 12, "Clipboard history")
        self.b_snip = IconButton(ctx, "", 26, 12, "Snippets: saved texts")
        for b, m in ((self.b_hist, "history"), (self.b_snip, "snippets")):
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, m=m: self._set_mode(m))
            hdr.addWidget(b)
        self.b_hist.setChecked(True)
        self.search = line_edit(ctx, "Search clipboard")
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self._enter)
        hdr.addWidget(self.search, 1)
        self.clr = IconButton(ctx, G["clear"], 26, 12, "Clear (keeps pinned)")
        self.clr.clicked.connect(app.clipboard.clear)
        hdr.addWidget(self.clr)
        lay.addLayout(hdr)
        self.sa, self.list = scroll_area(ctx)
        lay.addWidget(self.sa, 1)
        app.clipboard.changed.connect(self._on_changed)
        app.snippets.changed.connect(self._on_changed)

    def _set_mode(self, m):
        self.mode = m
        self.b_hist.setChecked(m == "history")
        self.b_snip.setChecked(m == "snippets")
        self.clr.setVisible(m == "history")
        self.search.clear()
        self.search.setPlaceholderText("Search clipboard" if m == "history"
                                       else "Type a snippet and press Enter to save it")
        self.refresh()

    def _enter(self):
        if self.mode == "snippets" and self.app.snippets.add(self.search.text()):
            self.search.clear()

    def _refresh_snippets(self):
        ctx, S = self.ctx, self.ctx.S
        q = self.search.text().lower()
        items = [i for i in self.app.snippets.items if q in i["text"].lower()]
        if not items:
            self.list.addWidget(label(ctx, "No snippets yet. Type one above, or star an entry in the history.",
                                      12, ctx.dim))
        for it in items[:80]:
            row = ClickRow(ctx)
            h = QHBoxLayout(row)
            h.setContentsMargins(int(S(12)), int(S(6)), int(S(6)), int(S(6)))
            t = elabel(ctx, 12)
            t.setText(" ".join(it["text"].split()))
            t.setToolTip(safe_tip(it["text"][:500]))
            h.addWidget(t, 1)
            de = IconButton(ctx, G["delete"], 24, 11, "Delete snippet")
            de.clicked.connect(lambda _=False, it=it: self.app.snippets.remove(it))
            h.addWidget(de)
            row.clicked.connect(lambda it=it: self._copy(it))
            self.list.addWidget(row)
        self.list.addStretch()

    def _on_changed(self):
        if self.isVisible():
            self.refresh()

    def showEvent(self, e):
        self.refresh()

    def refresh(self):
        clear_layout(self.list)
        if self.mode == "snippets":
            return self._refresh_snippets()
        ctx, S = self.ctx, self.ctx.S
        cb = self.app.clipboard
        if not self.app.settings["clipboard"]:
            self.list.addWidget(label(ctx, "Clipboard history is off (Settings)", 12, ctx.dim))
            self.list.addStretch()
            return
        q = self.search.text().lower()
        items = [i for i in cb.items if q in i["text"].lower()]
        items.sort(key=lambda i: not i["pinned"])
        if not items:
            self.list.addWidget(label(ctx, "Copy some text and it shows up here", 12, ctx.dim))
        for it in items[:60]:
            row = ClickRow(ctx)
            h = QHBoxLayout(row)
            h.setContentsMargins(int(S(12)), int(S(6)), int(S(6)), int(S(6)))
            text = " ".join(it["text"].split())
            t = elabel(ctx, 12)
            t.setText(text)
            t.setToolTip(safe_tip(it["text"][:500]))
            h.addWidget(t, 1)
            h.addWidget(label(ctx, ago(dt.datetime.fromtimestamp(it["time"])), 10, ctx.dim))
            pin = IconButton(ctx, G["unpin"] if it["pinned"] else G["pin"], 24, 11,
                             "Unpin" if it["pinned"] else "Pin", accent=it["pinned"])
            pin.clicked.connect(lambda _=False, it=it: cb.toggle_pin(it))
            de = IconButton(ctx, G["delete"], 24, 11, "Remove")
            de.clicked.connect(lambda _=False, it=it: cb.remove(it))
            star = IconButton(ctx, "", 24, 11, "Save as snippet")
            star.clicked.connect(lambda _=False, it=it: self.app.snippets.add(it["text"]))
            h.addWidget(star)
            h.addWidget(pin)
            h.addWidget(de)
            row.clicked.connect(lambda it=it: self._copy(it))
            self.list.addWidget(row)
        self.list.addStretch()

    def _copy(self, it):
        self.app.clipboard.copy(it if "pinned" in it else {"text": it["text"]})
        self.app.island.set_expanded(False)
        self.app.island.show_hud({"kind": "message", "glyph": G["copy"], "title": "Copied",
                                  "w": 200, "h": 44}, 1.2)


# ---------------------------------------------------------------- card
class Card(QWidget):
    TABS = (("home", G["home"], "Home"), ("music", G["music"], "Music"),
            ("notif", G["bell"], "Notifications"), ("files", G["folder"], "Files"),
            ("clip", G["clip"], "Clipboard & snippets"), ("controls", "", "Controls"),
            ("launch", "", "Launcher"), ("phone", "", "Send to phone"), ("web", "", "Web"),
            ("widgets", "", "Widgets"))

    def __init__(self, island):
        super().__init__(island)
        self.island = island
        self.app = island.app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.current = "home"
        self._root = None
        # the content fills the card through a layout, so it always matches the card's size
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)

    def rebuild(self):
        if self._root is not None:
            self._outer.removeWidget(self._root)
            self._root.deleteLater()
        ctx = Ctx(self.island)
        S = ctx.S
        root = QWidget(self)
        root.setStyleSheet("background:transparent;")
        self._root = root
        lay = QVBoxLayout(root)
        lay.setContentsMargins(int(S(18)), int(S(12)), int(S(18)), int(S(16)))
        lay.setSpacing(int(S(8)))

        bar = QHBoxLayout()
        bar.setSpacing(int(S(4)))
        self.tab_btns = {}
        wanted = self.app.settings["card_tabs"] or [t[0] for t in self.TABS]
        self.tabs = [t for t in self.TABS if t[0] in wanted or t[0] == "home"]
        for key, glyph, tip in self.tabs:
            b = IconButton(ctx, glyph, 30, 13, tip)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tab_btns[key] = b
            bar.addWidget(b)
        bar.addStretch()
        self.badge = label(ctx, "", 11, ctx.dim)
        bar.addWidget(self.badge)
        bar.addSpacing(int(S(6)))
        gear = IconButton(ctx, G["gear"], 30, 13, "Settings")
        gear.clicked.connect(self.app.open_settings)
        bar.addWidget(gear)
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background:transparent;")
        from . import panels
        makers = {"home": HomePage, "music": MusicPage, "notif": NotifPage, "clip": ClipPage,
                  "files": panels.ShelfPage, "controls": panels.ControlsPage, "launch": panels.LauncherPage,
                  "phone": panels.PhonePage, "web": panels.WebPage, "widgets": panels.WidgetsPage}
        self.pages = {k: makers[k](ctx, self.app) for k, _, _ in self.tabs}
        for k, _, _ in self.tabs:
            self.stack.addWidget(self.pages[k])
        lay.addWidget(self.stack, 1)
        self._outer.addWidget(root)
        self.show_tab(self.current)

    def show_tab(self, key):
        if key not in self.pages:
            key = "home"
        self.current = key
        for k, b in self.tab_btns.items():
            b.setChecked(k == key)
        self.stack.setCurrentWidget(self.pages[key])

    def refresh(self):
        """Called when the island opens: music first if something is playing."""
        m = self.app.media
        if m.available and m.playing:
            self.show_tab("music")
        elif self.current == "music" and not m.available:
            self.show_tab("home")
        w = self.app.weather
        if not w.error and w.temp is not None:
            self.badge.setText(f"{w.temp}°  {w.place}")
        else:
            self.badge.setText("")
