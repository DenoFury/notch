"""Settings window. Every change applies immediately."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .config import ACCENTS, SURFACES, set_startup, startup_enabled
from .theme import font

CSS = """
QWidget#settings { background:#141416; }
QLabel { color:#f2f2f5; background:transparent; }
QLabel[dim="true"] { color:#8e8e95; }
QLabel[section="true"] { color:#8e8e95; letter-spacing:1px; }
QFrame#group { background:#1d1d20; border-radius:12px; }
QComboBox, QSpinBox, QLineEdit { background:#2a2a2e; color:#f2f2f5; border:1px solid #34343a;
    border-radius:8px; padding:5px 8px; min-width:140px; }
QComboBox QAbstractItemView { background:#2a2a2e; color:#f2f2f5; selection-background-color:#3a3a40; }
QCheckBox::indicator { width:38px; height:20px; border-radius:10px; background:#3a3a40; }
QCheckBox::indicator:checked { background:#30d158; }
QSlider::groove:horizontal { height:4px; background:#3a3a40; border-radius:2px; }
QSlider::sub-page:horizontal { background:#f2f2f5; border-radius:2px; }
QSlider::handle:horizontal { background:#f2f2f5; width:14px; margin:-5px 0; border-radius:7px; }
QScrollArea { border:none; background:transparent; }
QScrollBar:vertical { background:transparent; width:8px; }
QScrollBar::handle:vertical { background:#3a3a40; border-radius:4px; min-height:30px; }
QScrollBar::add-line, QScrollBar::sub-line { height:0; }
"""


class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.Window)
        self.app = app
        self.st = app.settings
        self.setObjectName("settings")
        self.setWindowTitle("Notch settings")
        self.setStyleSheet(CSS)
        self.resize(560, 680)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        outer.addWidget(sa)
        body = QWidget()
        body.setObjectName("settings")
        sa.setWidget(body)
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(24, 20, 24, 24)
        self.lay.setSpacing(8)

        t = QLabel("Settings")
        t.setFont(font(26, QFont.DemiBold, display=True))
        self.lay.addWidget(t)

        g = self.group("GENERAL")
        self.switch(g, "Start with Windows", None, startup_enabled(), set_startup)
        self.switch(g, "Hide in fullscreen apps and games", "hide_in_fullscreen")
        self.spin(g, "Open delay (ms)", "open_delay_ms", 0, 1000, 20,
                  "How long the pointer rests on the island before it opens.")
        self.combo(g, "Island on", "island_screens", {"Main screen": "main", "Every screen": "all",
                                                       "The screen you're working on": "follow"})
        self.combo(g, "Position", "position", {"Top centre": "center", "Top left": "left", "Top right": "right"})
        self.switch(g, "Hide from screenshots and screen sharing", "hide_from_capture",
                    desc="The island and taskbar stay on your screen but don't show up in screenshots, "
                         "recordings, Teams/Discord screen sharing or OBS.")

        g = self.group("TASKBAR")
        self.switch(g, "Notch taskbar", "taskbar",
                    desc="A taskbar along the bottom of the main screen: Start, your pinned and open apps, "
                         "the song playing, the timer, quick settings and the clock. Right-click an app to pin it.")
        self.switch(g, "Hide Windows' taskbar", "taskbar_hide_windows",
                    desc="Windows' own taskbar comes back as it was when you turn this off or Notch closes. "
                         "The ^ button on the notch taskbar shows it briefly for its tray icons.")
        self.switch(g, "On every screen", "taskbar_all_screens",
                    desc="A taskbar along the bottom of each screen. The ^ tray button stays on the main one.")
        self.switch(g, "Each screen's own apps", "taskbar_own_apps",
                    desc="Each taskbar shows the apps open on its own screen, with your pins on all of them. "
                         "Off, every taskbar shows every app.")
        self.switch(g, "Window previews", "taskbar_previews",
                    desc="Rest the pointer on an app to see its windows live. Click one to bring it forward.")
        self.switch(g, "Dock effect", "taskbar_dock", desc="Icons grow as the pointer passes over them.")
        self.slide(g, "Dock size", "taskbar_dock_size", 100, 180, lambda v: v / 100, lambda v: int(v * 100), "%")

        g = self.group("APPEARANCE")
        self.combo(g, "Shape", "shape", {"Island (floats)": "island", "Notch (hangs from the edge)": "notch"})
        self.combo(g, "Surface", "surface", {k.capitalize(): k for k in SURFACES})
        self.combo(g, "Accent", "accent", {("Album cover" if k == "album" else k.capitalize()): k for k in ACCENTS})
        self.combo(g, "Popup bar style", "hud_style", {"Bar": "bar", "Thin": "thin", "Segments": "segments",
                                                       "Dots": "dots"})
        self.combo(g, "Pet", "pet", {"None": "none", "Grey cat": "cat", "Ginger cat": "ginger",
                                     "Black cat": "black", "White cat": "white"})
        self.slide(g, "Size", "scale", 80, 140, lambda v: v / 100, lambda v: int(v * 100), "%")
        self.spin(g, "Distance from top (px)", "top_margin", 0, 40, 1)
        self.switch(g, "Song title in the closed island", "show_title_in_pill")

        g = self.group("POPUPS")
        self.switch(g, "Volume", "hud_volume")
        self.switch(g, "Brightness", "hud_brightness",
                    desc=None if app.brightness.available else "This screen doesn't report brightness.")
        self.switch(g, "Battery (plugged in, unplugged, low)", "hud_battery")
        self.switch(g, "Caps Lock and Num Lock", "hud_locks")
        self.switch(g, "Microphone and camera in use", "hud_privacy",
                    desc="A popup when an app starts using them, and an orange (mic) or green (camera) dot "
                         "beside the island while it does.")
        self.switch(g, "Headphones and speakers", "hud_audio_device",
                    desc="Shows where sound goes when you connect or switch an audio device.")
        self.switch(g, "Downloads", "hud_downloads",
                    desc="Progress in the island while your browser downloads, then Open / Show when it's done.")
        self.switch(g, "Notifications", "notifications",
                    desc="Shows new Windows notifications on the island.")
        self.spin(g, "Notification time (s)", "notification_seconds", 2, 20, 1)

        g = self.group("ISLAND TABS")
        names = {"music": "Music", "notif": "Notifications", "files": "Files shelf",
                 "clip": "Clipboard and snippets", "controls": "Controls (Wi-Fi, Bluetooth, night light…)",
                 "launch": "Launcher (screenshot, record, app slots)", "phone": "Send to phone",
                 "web": "Web (mini browser)", "widgets": "Widgets"}
        for key, title in names.items():
            self.switch(g, title, None, key in (self.st["card_tabs"] or []),
                        lambda on, k=key: self._tab(k, on))

        g = self.group("DESKTOP")
        self.switch(g, "Day clock", "day_clock", desc="Big time and date on your desktop. Drag it where you like.")
        self.spin(g, "Day clock size", "day_clock_size", 40, 260, 4)
        self.combo(g, "Day clock colour", "day_clock_color", {"White": "#ffffff", "Black": "#111114",
                                                              "Accent green": "#30d158", "Soft yellow": "#ffe08a",
                                                              "Sky": "#8fd3ff", "Pink": "#ff9ecb"})
        self.switch(g, "Lock desktop widgets", "desktop_locked",
                    desc="To put a widget on the desktop: open the island's Widgets tab, right-click a widget, "
                         "Put on the desktop. Unlocked, drag them to move.")

        g = self.group("LIVE WALLPAPER")
        self.switch(g, "Video wallpaper", "wallpaper",
                    desc="Plays a video behind your desktop icons, muted and looping. It pauses while a window "
                         "covers that screen, and unloads after two covered minutes to free memory.")
        from PySide6.QtWidgets import QPushButton
        b = QPushButton("Choose video…")
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton{background:#2a2a2e;color:#f2f2f5;border:1px solid #34343a;border-radius:8px;"
                        "padding:5px 12px;}")
        vid = self.st["wallpaper_video"]
        self._vid_label = "Video: " + (__import__("os").path.basename(vid) if vid else "none chosen")
        b.clicked.connect(self._pick_video)
        self.row(g, self._vid_label, b)
        self.combo(g, "Wallpaper on", "wallpaper_screens", {"Main screen": "main", "Every screen": "all"})
        self.switch(g, "Pause on battery", "wallpaper_pause_battery")
        self.switch(g, "Pause when a window covers the screen", "wallpaper_pause_covered")

        g = self.group("UPDATES")
        self.combo(g, "Updates", "auto_update", {"Ask me first": "ask", "Install automatically": "auto",
                                                 "Off": "off"})
        from PySide6.QtWidgets import QPushButton as _Btn
        chk = _Btn("Check now")
        chk.setCursor(Qt.PointingHandCursor)
        chk.setStyleSheet("QPushButton{background:#2a2a2e;color:#f2f2f5;border:1px solid #34343a;border-radius:8px;"
                          "padding:5px 12px;}")
        up = app.updater
        self._upd_status = QLabel(f"Version {app.version}")
        self._upd_status.setProperty("dim", True)
        self._upd_status.setFont(font(11))
        up.status.connect(self._upd_status.setText)
        chk.clicked.connect(lambda: up.check(manual=True))
        self.row(g, f"Notch {app.version}", chk,
                 desc="Updates come from the project's GitHub releases and are only installed if they carry "
                      "the developer's signature.")
        g.addWidget(self._upd_status)
        if up.ready:
            b = _Btn(f"Install version {up.ready[0]}")
            b.setStyleSheet(chk.styleSheet())
            b.clicked.connect(app.install_update)
            self.row(g, "An update is ready", b)

        g = self.group("MUSIC")
        self.switch(g, "Synced lyrics", "lyrics",
                    desc="Looks up the playing song's title, artist and length on lrclib.net, once per song. "
                         "Off, nothing about what you play leaves your PC.")

        g = self.group("CLIPBOARD")
        self.switch(g, "Clipboard history", "clipboard",
                    desc="Kept encrypted for your Windows account. Password managers' copies are skipped.")
        self.spin(g, "Entries to keep", "clipboard_keep", 10, 500, 10, "Pinned entries don't count.")

        g = self.group("WIDGETS")
        self.text(g, "Weather city", "city", "e.g. Madrid",
                  "Only the city name and its coordinates go to open-meteo.com.")
        self.switch(g, "Fahrenheit", "fahrenheit")
        self.switch(g, "24-hour clock", "clock_24h")
        self.spin(g, "Focus minutes", "focus_min", 1, 120, 1)
        self.spin(g, "Break minutes", "break_min", 1, 60, 1)

        self.lay.addStretch()

    def _pick_video(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Video wallpaper", "",
                                              "Videos (*.mp4 *.mov *.mkv *.webm *.wmv *.avi)")
        if path:
            self.st.set("wallpaper_video", path)
            self.st.set("wallpaper", True)

    def _tab(self, key, on):
        from .card import Card
        tabs = [t for t in (self.st["card_tabs"] or []) if t != key]
        if on:
            tabs.append(key)
        order = [t[0] for t in Card.TABS]
        self.st.set("card_tabs", sorted(set(tabs), key=order.index))

    # ---------------------------------------------------------- builders
    def group(self, title):
        lb = QLabel(title)
        lb.setProperty("section", True)
        lb.setFont(font(11, QFont.DemiBold))
        self.lay.addSpacing(10)
        self.lay.addWidget(lb)
        fr = QFrame()
        fr.setObjectName("group")
        v = QVBoxLayout(fr)
        v.setContentsMargins(16, 6, 16, 6)
        v.setSpacing(0)
        self.lay.addWidget(fr)
        return v

    def row(self, g, title, widget, desc=None):
        h = QHBoxLayout()
        h.setContentsMargins(0, 8, 0, 8)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setFont(font(13))
        col.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setProperty("dim", True)
            d.setWordWrap(True)
            d.setFont(font(11))
            col.addWidget(d)
        h.addLayout(col, 1)
        h.addSpacing(12)
        h.addWidget(widget, 0, Qt.AlignVCenter)
        g.addLayout(h)

    def switch(self, g, title, key, value=None, on_change=None, desc=None):
        cb = QCheckBox()
        cb.setCursor(Qt.PointingHandCursor)
        cb.setChecked(bool(self.st[key] if key else value))
        cb.toggled.connect(on_change if on_change else (lambda v, k=key: self.st.set(k, v)))
        self.row(g, title, cb, desc)

    def combo(self, g, title, key, options):
        c = QComboBox()
        for label, val in options.items():
            c.addItem(label, val)
        idx = c.findData(self.st[key])
        c.setCurrentIndex(max(0, idx))
        c.currentIndexChanged.connect(lambda i, k=key, c=c: self.st.set(k, c.itemData(i)))
        self.row(g, title, c)

    def spin(self, g, title, key, lo, hi, step, desc=None):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setValue(int(self.st[key]))
        s.valueChanged.connect(lambda v, k=key: self.st.set(k, v))
        self.row(g, title, s, desc)

    def slide(self, g, title, key, lo, hi, to_val, from_val, unit):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setFixedWidth(150)
        s.setValue(from_val(self.st[key]))
        lb = QLabel(f"{s.value()}{unit}")
        lb.setFixedWidth(44)
        s.valueChanged.connect(lambda v: lb.setText(f"{v}{unit}"))
        s.sliderReleased.connect(lambda: self.st.set(key, to_val(s.value())))
        s.valueChanged.connect(lambda v: None if s.isSliderDown() else self.st.set(key, to_val(v)))
        h.addWidget(s)
        h.addWidget(lb)
        self.row(g, title, w)

    def text(self, g, title, key, placeholder, desc=None):
        e = QLineEdit(self.st[key])
        e.setPlaceholderText(placeholder)
        e.editingFinished.connect(lambda k=key, e=e: self.st.set(k, e.text().strip()))
        self.row(g, title, e, desc)
