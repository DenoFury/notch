"""Shared drawing helpers: fonts, glyphs, colours."""
import functools

from PySide6.QtGui import QColor, QFont, QFontDatabase

ICON_FAMILY = "Segoe Fluent Icons"
TEXT_FAMILY = "Segoe UI Variable Text"
DISPLAY_FAMILY = "Segoe UI Variable Display"

G = {
    "play": "", "pause": "", "prev": "", "next": "",
    "vol0": "", "vol1": "", "vol2": "", "vol3": "", "mute": "",
    "sun": "", "bell": "", "home": "", "music": "",
    "clip": "", "gear": "", "pin": "", "unpin": "",
    "delete": "", "timer": "", "alarm": "", "refresh": "",
    "keyboard": "", "check": "", "copy": "", "clear": "",
    "plug": "", "battery": "",
}
G.update({
    "mic": "", "camera": "", "bluetooth": "", "headphones": "",
    "speaker": "", "download": "", "folder": "",
})


def init_fonts():
    global ICON_FAMILY
    fams = set(QFontDatabase.families())
    if ICON_FAMILY not in fams:
        ICON_FAMILY = "Segoe MDL2 Assets"
    global TEXT_FAMILY, DISPLAY_FAMILY
    if TEXT_FAMILY not in fams:
        TEXT_FAMILY = DISPLAY_FAMILY = "Segoe UI"


@functools.lru_cache(maxsize=128)
def _font(px, weight, display):
    f = QFont(DISPLAY_FAMILY if display else TEXT_FAMILY)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def font(px, weight=QFont.Normal, display=False):
    """Fonts are cached: painting asks for the same few sizes every frame."""
    return _font(max(1, int(round(px))), weight, display)


@functools.lru_cache(maxsize=32)
def _icon_font(px):
    f = QFont(ICON_FAMILY)
    f.setPixelSize(px)
    return f


def icon_font(px):
    return _icon_font(max(1, int(round(px))))


def rgba(c, a):
    q = QColor(c)
    q.setAlpha(int(a))
    return q


def vol_glyph(level, muted):
    if muted or level <= 0.001:
        return G["mute"]
    return G["vol1"] if level < 0.34 else G["vol2"] if level < 0.67 else G["vol3"]


def cover_color(img):
    """A lively colour from album art (for the "Album" accent), or None."""
    if img is None or img.isNull():
        return None
    from PySide6.QtCore import Qt
    small = img.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    best, score = None, 0.0
    buckets = {}
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            h, sat, val, _ = c.getHsvF()
            if h < 0 or sat < 0.25 or val < 0.3:
                continue
            k = int(h * 18)                       # 20-degree hue buckets
            b = buckets.setdefault(k, [0.0, 0.0, 0.0, 0])
            w = sat * val
            b[0] += h * w
            b[1] += sat * w
            b[2] += w
            b[3] += 1
    for k, (hs, ss, ws, n) in buckets.items():
        if ws > score:
            score, best = ws, (hs / ws, ss / ws)
    if best is None:
        return None
    c = QColor.fromHsvF(best[0], max(0.55, min(1.0, best[1])), 0.95)
    return c.name()


def hide_from_capture(widget, on):
    """Keep a window out of screenshots, recordings and screen sharing (Windows 10 2004+)."""
    import ctypes
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11 if on else 0)
    except Exception:
        pass


def safe_tip(text):
    """Tooltip text from outside (clipboard, file names, app names) shown literally: Qt renders
    tooltip text that looks like HTML, so escape it and say it's rich text explicitly."""
    import html
    return "<qt>" + html.escape(str(text)).replace("\n", "<br>") + "</qt>"
