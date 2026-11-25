"""Wires everything together: data sources -> island HUDs, tray icon, settings."""
import ctypes
import gc
import sys
import winsound

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import theme
from .clipboard import ClipboardHistory
from .config import Settings
from .island import IslandGroup
from .media import Media
from .notifications import Notifications
from .system import Audio, Brightness, SystemWatch
from .theme import G, vol_glyph
from .indicators import DownloadWatch, PrivacyWatch
from .widgets_data import FocusTimer, Reminders, Weather

RED, AMBER = "#ff453a", "#ff9f0a"
GL_RECORD = ""


def chime():
    winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)


class NotchApp:
    def __init__(self, qapp):
        self.qapp = qapp
        theme.init_fonts()
        self.settings = Settings()
        self.media = Media(self.settings)
        self.audio = Audio()
        self.brightness = Brightness()
        self.system = SystemWatch()
        self.notifications = Notifications()
        self.clipboard = ClipboardHistory(self.settings)
        self.weather = Weather(self.settings)
        self.timer = FocusTimer(self.settings)
        self.reminders = Reminders()
        self.privacy = PrivacyWatch()
        self.downloads = DownloadWatch()
        from .phone import PhoneLink
        from .tools import Recorder, Shelf, Snippets
        self.shelf = Shelf()
        self.snippets = Snippets()
        self.recorder = Recorder()
        self.phone = PhoneLink(self)
        self._web = None
        self.island = IslandGroup(self)
        self._settings_win = None
        self._chimes = QTimer(interval=15000, timeout=self._chime_tick)
        self._chimes_left = 0

        st = self.settings
        self.audio.volume_changed.connect(self.on_volume)
        self.brightness.changed.connect(self.on_brightness)
        self.system.battery_event.connect(self.on_battery)
        self.system.lock_changed.connect(self.on_lock)
        self.system.fullscreen_changed.connect(self.island.set_fullscreen)
        self.system.active_screen_changed.connect(self.island.follow)
        self.notifications.arrived.connect(self.on_notification)
        self.privacy.changed.connect(self.on_privacy)
        self.audio.output_changed.connect(self.on_output)
        self.downloads.started.connect(self.on_download_started)
        self.downloads.finished.connect(self.on_download_finished)
        self.downloads.progress.connect(self._downloads_progress)
        self.phone.received_file.connect(self.on_phone_file)
        self.phone.received_text.connect(self.on_phone_text)
        self.recorder.state_changed.connect(self.on_recording)
        self.recorder.saved.connect(lambda p: self._saved_hud("Recording saved", p, GL_RECORD))
        self.recorder.failed.connect(lambda msg: self.island.show_hud(
            {"kind": "message", "glyph": GL_RECORD, "color": RED, "sub": "Recording failed", "title": msg,
             "w": 380, "h": 60}, 4))
        self._rec_tick = QTimer(interval=1000, timeout=self.island.update)
        self.reminders.due.connect(self.on_reminder)
        self.timer.finished.connect(self.on_timer_done)
        self.timer.tick.connect(self._timer_tick)
        self.media.changed.connect(self.island.retarget)
        self.media.art_changed.connect(self.island.update)
        self.media.art_changed.connect(self._update_album_color)
        st.changed.connect(self._on_setting)

        self._was_running = False
        self._mic_seen, self._cam_seen = list(self.privacy.mic), list(self.privacy.cam)
        self._trim_timer = QTimer(singleShot=True, interval=60_000, timeout=trim_memory)

        self.taskbars = []
        self.tracker = None
        self.hotkeys = None
        self.taskbar_hider = TaskbarHider(self)
        qapp.screenAdded.connect(self._screens_changed)
        qapp.screenRemoved.connect(self._screens_changed)
        qapp.primaryScreenChanged.connect(self._screens_changed)
        self.qapp.aboutToQuit.connect(self._shutdown)
        # Windows is signing out / shutting down: put its taskbar back before we get killed
        self.qapp.commitDataRequest.connect(self._session_ending)
        self._apply_taskbar()

        from .desktop import PriceAlerts, UptimeWatch, WidgetHost
        from .widgets import Usage
        self.usage = Usage(self)
        self.uptime = UptimeWatch(self)
        self.price_alerts = PriceAlerts(self)
        self.widget_host = WidgetHost(self)
        from .installer import VERSION
        from .updater import Updater
        self.version = VERSION
        self.updater = Updater(self, VERSION)
        self.updater.available.connect(self.on_update_ready)
        note = self.updater.take_failure_note()
        if note:
            QTimer.singleShot(3000, lambda: self.island.show_hud(
                {"kind": "message", "glyph": "", "color": AMBER, "sub": "Update not installed",
                 "title": f"Windows blocked Notch {note.get('version', '')}: {note.get('reason', '')}",
                 "body": f"You're still on {VERSION}, which keeps working.", "w": 460, "h": 76}, 10))

        self._make_tray()
        self._update_album_color()      # the cover may have loaded before we were listening
        self.island.show()
        self._demo()
        # once start-up is over, hand back the memory only start-up needed
        QTimer.singleShot(8000, lambda: (gc.collect(), gc.freeze(), trim_memory()))

    def card_closed(self):
        """Release memory the open card used once it has stayed closed for a minute."""
        self._trim_timer.start()

    def _demo(self):
        """Test hooks: --open <tab> keeps the card open; --hud <kind> shows a sample popup;
        --debug-timers prints which animation timers are running."""
        args = self.qapp.arguments()
        if "--debug-timers" in args:
            def report():
                tb = self.taskbar
                print("island anim", self.island.main._anim.isActive(), "viz", self.island.main._viz.isActive(),
                      "| taskbar anim", tb._anim.isActive() if tb else None,
                      "| media playing", self.media.playing, flush=True)
            self._dbg = QTimer(interval=3000, timeout=report)
            self._dbg.start()
        if "--open" in args:
            tab = args[args.index("--open") + 1]
            delay = int(args[args.index("--open-delay") + 1]) if "--open-delay" in args else 600
            self.island.main.pinned_open = True

            def go():
                isl = self.island.main
                isl.pinned_open = True
                isl._ensure_card()
                isl.set_expanded(True)
                isl.card.show_tab(tab)      # after opening: opening jumps to Music while a song plays
            QTimer.singleShot(delay, go)
        if "--web" in args:
            QTimer.singleShot(1500, lambda: self.open_web(args[args.index("--web") + 1]))
        if "--web-close" in args:
            QTimer.singleShot(1500 + int(args[args.index("--web-close") + 1]) * 1000,
                              lambda: self._web and self._web.close_panel())
        if "--settings" in args:
            QTimer.singleShot(1200, self.open_settings)
        if "--shot" in args:
            QTimer.singleShot(1500, lambda: self.take_screenshot(False))
        if "--record" in args:
            secs = int(args[args.index("--record") + 1])
            QTimer.singleShot(1500, self.start_recording)
            QTimer.singleShot(1500 + 500 + secs * 1000, self.recorder.stop)
        if "--hud" in args:
            kind = args[args.index("--hud") + 1]
            samples = {
                "volume": lambda: self.on_volume(0.62, False),
                "battery": lambda: self.on_battery("low", 18, False),
                "notification": lambda: self.on_notification(
                    {"app": "WhatsApp", "title": "Alex", "body": "See you at 8 then?", "aumid": "",
                     "time": __import__("datetime").datetime.now()}),
                "reminder": lambda: self.on_reminder({"what": "Call the dentist", "when": 0}),
                "caps": lambda: self.on_lock("Caps Lock", True),
                "mic": lambda: (setattr(self.privacy, "mic", ["Discord"]), self.on_privacy(["Discord"], [])),
                "camera": lambda: (setattr(self.privacy, "cam", ["Camera"]), self.on_privacy([], ["Camera"])),
                "device": lambda: self.on_output("WH-1000XM5", "bluetooth"),
                "download": lambda: self.on_download_finished("holiday-photos.zip",
                                                              self.downloads.folder),
            }
            QTimer.singleShot(600, samples[kind])
            self._stop_chimes_soon = QTimer.singleShot(700, self._stop_chimes)

    # ------------------------------------------------------------ HUD sources
    def on_volume(self, level, muted):
        if not self.settings["hud_volume"]:
            return
        pct = round(level * 100)
        self.island.show_hud({"kind": "level", "glyph": vol_glyph(level, muted),
                              "value": 0 if muted else level, "text": "Off" if muted else f"{pct}%"}, 1.6)

    def on_brightness(self, level):
        if self.settings["hud_brightness"]:
            self.island.show_hud({"kind": "level", "glyph": G["sun"], "value": level / 100,
                                  "text": f"{level}%"}, 1.6)

    def on_battery(self, kind, pct, plugged):
        if not self.settings["hud_battery"]:
            return
        title, color = {
            "plugged": ("Charging", "#30d158"),
            "unplugged": ("On battery", None),
            "low": ("Battery low", AMBER),
            "critical": ("Battery very low", RED),
            "full": ("Fully charged", "#30d158"),
        }[kind]
        self.island.show_hud({"kind": "battery", "title": title, "value": pct / 100,
                              "text": f"{pct}%", "color": color, "w": 300,
                              "urgent": kind == "critical"}, 3.0)

    def on_lock(self, name, on):
        if self.settings["hud_locks"]:
            self.island.show_hud({"kind": "message", "glyph": G["keyboard"],
                                  "title": f"{name} {'on' if on else 'off'}", "w": 230, "h": 46}, 1.3)

    # ------------------------------------------------------------ panels (phase 3)
    def _saved_hud(self, sub, path, glyph):
        import os

        def open_it():
            self.island.clear_hud()
            from .panels import open_path
            open_path(path)

        def show_it():
            self.island.clear_hud()
            from .panels import show_in_folder
            show_in_folder(path)
        self.shelf.add([path])
        self.island.show_hud({"kind": "message", "glyph": glyph, "color": "#30d158", "sub": sub,
                              "title": os.path.basename(path), "w": 440, "h": 64,
                              "buttons": [("Open", open_it), ("Show", show_it)]}, 5)

    def take_screenshot(self, all_screens=False):
        """Close the card first so it isn't in the picture."""
        self.island.set_expanded(False)
        self.island.clear_hud()
        scr = self.system.active_screen() or self.island.main.scr

        def shoot():
            from .tools import screenshot
            try:
                path, _img = screenshot(scr, all_screens)
            except Exception as e:
                print("screenshot failed:", e, flush=True)
                return
            self._saved_hud("Screenshot saved · copied", path, "")
        QTimer.singleShot(450, shoot)

    def snip(self):
        self.island.set_expanded(False)
        import subprocess
        QTimer.singleShot(300, lambda: subprocess.Popen(["explorer.exe", "ms-screenclip:"]))

    def start_recording(self):
        self.island.set_expanded(False)
        scr = self.system.active_screen() or self.island.main.scr
        QTimer.singleShot(450, lambda: self.recorder.start(scr, bool(self.settings["record_mic"])))

    def on_recording(self, on):
        if on:
            self._rec_tick.start()
            self.island.show_hud({"kind": "message", "glyph": GL_RECORD, "color": RED, "sub": "Recording",
                                  "title": "Click here to stop", "w": 300, "h": 56,
                                  "on_click": self.recorder.stop}, 2.5)
        else:
            self._rec_tick.stop()
        self.island.retarget()

    def on_phone_file(self, path):
        import os
        self.shelf.add([path])
        self.island.show_hud({"kind": "message", "glyph": "", "sub": "From your phone",
                              "title": os.path.basename(path), "w": 380, "h": 60,
                              # show it in its folder rather than running it: received files can be anything
                              "on_click": lambda: __import__("notch.panels", fromlist=["x"]).show_in_folder(path)},
                             3)

    def on_phone_text(self, text):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
        self.island.show_hud({"kind": "message", "glyph": "", "sub": "Text from your phone · copied",
                              "title": " ".join(text.split())[:80], "w": 400, "h": 60}, 3)

    def open_web(self, url):
        from .webpanel import WebPanel
        isl = self.island.main
        self.island.set_expanded(False)
        if self._web is None:
            self._web = WebPanel(self)
            from .theme import hide_from_capture
            hide_from_capture(self._web, self.settings["hide_from_capture"])
        self._web.place_under(isl)
        self._web.load(url)
        self._web.show()
        self._web.raise_()
        self._web.activateWindow()

    def web_panel_released(self):
        self._web = None
        self._trim_timer.start(5000)

    # ------------------------------------------------------------ updates
    def on_update_ready(self, version):
        self._update_action.setText(f"Update to {version}")
        self._update_action.setVisible(True)
        if self.settings["auto_update"] == "auto":
            self.island.show_hud({"kind": "message", "glyph": "", "sub": "Updating Notch",
                                  "title": f"Installing version {version}…", "w": 340, "h": 60}, 4)
            QTimer.singleShot(3500, self.install_update)
            return
        self.island.show_hud({"kind": "message", "glyph": "", "sub": "Update ready",
                              "title": f"Notch {version} is ready", "w": 420, "h": 64,
                              "buttons": [("Update", self.install_update), ("Later", self.island.clear_hud)]}, 20)

    def install_update(self):
        self.island.clear_hud()
        if self.updater.install():
            # the installer closes this Notch and starts the new one; leave tidily first
            QTimer.singleShot(400, self.qapp.quit)

    def on_notification(self, n):
        if not self.settings["notifications"] or self.settings["quiet"]:
            return
        app_name = n.get("app") or self.notifications.app_name(n["aumid"])
        self.island.show_hud({"kind": "message", "glyph": G["bell"], "sub": app_name,
                              "title": n["title"], "body": n["body"], "w": 430, "h": 76,
                              "on_click": lambda: self.notifications.open_app(n)},
                             self.settings["notification_seconds"])

    def on_privacy(self, mic, cam):
        new_mic = [a for a in mic if a not in self._mic_seen]
        new_cam = [a for a in cam if a not in self._cam_seen]
        self._mic_seen, self._cam_seen = list(mic), list(cam)
        self.island.retarget()           # the little dots next to the island
        if not self.settings["hud_privacy"]:
            return
        if new_cam:
            self.island.show_hud({"kind": "message", "glyph": G["camera"], "color": "#30d158",
                                  "sub": "Camera in use", "title": ", ".join(new_cam), "w": 330, "h": 60}, 2.5)
        elif new_mic:
            self.island.show_hud({"kind": "message", "glyph": G["mic"], "color": AMBER,
                                  "sub": "Microphone in use", "title": ", ".join(new_mic), "w": 330, "h": 60}, 2.5)

    def on_output(self, name, kind):
        if self.settings["hud_audio_device"]:
            glyph = {"bluetooth": G["bluetooth"], "headphones": G["headphones"]}.get(kind, G["speaker"])
            self.island.show_hud({"kind": "message", "glyph": glyph, "sub": "Sound now plays on",
                                  "title": name, "w": 360, "h": 60}, 2.5)

    def on_download_started(self, name):
        if self.settings["hud_downloads"]:
            self.island.show_hud({"kind": "message", "glyph": G["download"], "sub": "Downloading",
                                  "title": name, "w": 340, "h": 60}, 2.0)
        self.island.retarget()

    def on_download_finished(self, name, path):
        self.island.retarget()
        if not self.settings["hud_downloads"]:
            return
        import os
        import subprocess

        def open_it():
            self.island.clear_hud()
            try:
                os.startfile(path)
            except OSError:
                pass

        def show_it():
            self.island.clear_hud()
            subprocess.Popen(["explorer.exe", "/select,", path])

        self.island.show_hud({"kind": "message", "glyph": G["download"], "color": "#30d158", "sub": "Downloaded",
                              "title": name, "w": 440, "h": 64,
                              "buttons": [("Open", open_it), ("Show", show_it)]}, 6)

    def _downloads_progress(self):
        if self.settings["hud_downloads"]:
            self.island.update()

    def on_reminder(self, r):
        def done():
            self._stop_chimes()
            self.island.clear_hud()

        def snooze():
            self.reminders.snooze(r, 10)
            done()

        self.island.set_expanded(False)
        self.island.show_hud({"kind": "message", "glyph": G["alarm"], "sub": "Reminder",
                              "title": r["what"], "w": 440, "h": 64, "urgent": True,
                              "buttons": [("Snooze 10m", snooze), ("Done", done)]}, 120)
        self._chimes_left = 4
        chime()
        self._chimes.start()

    def on_timer_done(self, phase):
        st = self.settings
        if phase == "focus":
            title, sub, btn = f"Take a {st['break_min']} min break", "Focus session done", "Start break"
        else:
            title, sub, btn = "Break's over", "Ready to focus again?", "Start focus"

        def start():
            self.island.clear_hud()
            self.timer.toggle()

        self.island.set_expanded(False)
        self.island.show_hud({"kind": "message", "glyph": G["timer"], "sub": sub, "title": title,
                              "w": 420, "h": 64, "urgent": True,
                              "buttons": [(btn, start), ("Later", self.island.clear_hud)]}, 30)
        chime()

    def _timer_tick(self):
        if self.island.expanded or self.island.hud:
            return
        if self.timer.running != self._was_running:
            self._was_running = self.timer.running
            self.island.retarget()     # pill changes size
        else:
            self.island.update()       # just the seconds

    def _chime_tick(self):
        self._chimes_left -= 1
        if self._chimes_left <= 0 or not self.island.hud:
            self._stop_chimes()
        else:
            chime()

    def _stop_chimes(self):
        self._chimes.stop()
        self._chimes_left = 0

    def _update_album_color(self):
        st = self.settings
        c = theme.cover_color(self.media.art) if self.media.available else None
        if c != st.album_color:
            st.album_color = c
            if st["accent"] == "album":
                self.island.accent_changed()
                for tb in self.taskbars:
                    tb.update()

    def _on_setting(self, key):
        if key == "island_screens":
            self.island.rebuild()
        if key == "accent" and self.settings["accent"] == "album":
            self.settings.album_color = theme.cover_color(self.media.art) if self.media.available else None
            self.island.accent_changed()      # the island rebuilt its card before the colour was known
        if key in ("focus_min", "break_min") and not self.timer.running:
            self.timer.reset()
        if key in ("taskbar", "taskbar_hide_windows", "taskbar_all_screens"):
            self._apply_taskbar()
        elif key == "taskbar_own_apps":
            for tb in self.taskbars:
                tb.rebuild()

    # ------------------------------------------------------------ taskbar
    @property
    def taskbar(self):
        """The main screen's bar (or None)."""
        return next((t for t in self.taskbars if t.main), None)

    def _apply_taskbar(self):
        on = bool(self.settings["taskbar"])
        self._close_taskbars()
        if on:
            from .taskbar import Taskbar
            from .taskbar_core import WindowTracker
            if self.tracker is None:
                self.tracker = WindowTracker(self.settings)
            primary = QGuiApplication.primaryScreen()
            screens = QGuiApplication.screens() if self.settings["taskbar_all_screens"] else [primary]
            # create every bar first so each knows how many screens share the apps
            self.taskbars = [Taskbar(self, s, self.tracker, main=(s is primary)) for s in screens]
            for tb in self.taskbars:
                tb.rebuild(snap=True)
                tb.show()
        self.taskbar_hider.apply()
        # Win+1…9 follow the notch bar while it replaces Windows' taskbar
        keys = on and bool(self.settings["taskbar_hide_windows"])
        if keys and self.hotkeys is None:
            from .hotkeys import WinNumberKeys
            self.hotkeys = WinNumberKeys()
            self.hotkeys.pressed.connect(self._win_number)
        elif not keys and self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys.deleteLater()
            self.hotkeys = None
        for tb in self.taskbars:
            # Windows gives the hidden taskbar's space back a moment later: claim it again
            QTimer.singleShot(600, tb._appbar_pos)

    def _win_number(self, n):
        tb = self.taskbar
        if tb is not None:
            tb.open_index(n)

    def _close_taskbars(self):
        for tb in self.taskbars:
            if tb.preview:
                tb.preview.hide_now()
            tb.unregister_appbar()
            tb.close()
            tb.deleteLater()
        self.taskbars = []

    def _screens_changed(self, *_):
        QTimer.singleShot(800, self.island.rebuild)   # a screen came or went: re-place the islands
        if self.settings["taskbar"]:
            QTimer.singleShot(800, self._apply_taskbar)   # let Windows settle the new layout first

    def _session_ending(self, *_):
        print("session ending: restoring Windows' taskbar", flush=True)
        self._shutdown()
        # if the shutdown gets cancelled we are still running: hide it again a bit later
        QTimer.singleShot(30_000, self.taskbar_hider.apply)

    def _shutdown(self):
        if getattr(self, "widget_host", None):
            self.widget_host.shutdown()
        for tb in self.taskbars:
            tb.unregister_appbar()
        self.taskbar_hider.restore()

    # ------------------------------------------------------------ tray / settings
    def _make_tray(self):
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor("#ffffff"))
        p.setBrush(QColor("#000000"))
        p.drawRoundedRect(QRectF(4, 20, 56, 24), 12, 12)
        p.setBrush(QColor("#30d158"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(42, 28, 8, 8))
        p.end()
        self.tray = QSystemTrayIcon(QIcon(pm))
        self.tray.setToolTip("Notch")
        menu = QMenu()
        a = QAction("Settings", menu, triggered=self.open_settings)
        menu.addAction(a)
        b = QAction("Open island", menu, triggered=lambda: self.island.set_expanded(True))
        menu.addAction(b)
        self._update_action = QAction("Update", menu, triggered=self.install_update)
        self._update_action.setVisible(False)
        menu.addAction(self._update_action)
        menu.addSeparator()
        menu.addAction(QAction("Quit", menu, triggered=self.qapp.quit))
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: reason == QSystemTrayIcon.Trigger and self.open_settings())
        self.tray.show()

    def open_settings(self):
        from .settings_ui import SettingsWindow
        self.island.set_expanded(False)
        if self._settings_win is None:
            w = SettingsWindow(self)
            w.setWindowIcon(self.tray.icon())
            w.setAttribute(Qt.WA_DeleteOnClose)   # freed when closed
            w.destroyed.connect(self._settings_gone)
            self._settings_win = w
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()


    def _settings_gone(self, *_):
        self._settings_win = None
        self._trim_timer.start(5000)


class TaskbarHider:
    """Hides Windows' taskbar while the notch taskbar is on, and always brings it back."""

    def __init__(self, app):
        from . import winbar
        self.app = app
        self.wb = winbar
        self._guard = None
        self._check = QTimer(interval=2000, timeout=winbar.ensure_hidden)
        self._peek = QTimer(interval=500, timeout=self._peek_tick)
        self._peek_away = 0
        # crash recovery: hidden by a previous run that never restored it
        if winbar.is_hidden_by_us() and not self.wanted():
            winbar.restore()

    def wanted(self):
        st = self.app.settings
        return bool(st["taskbar"] and st["taskbar_hide_windows"])

    def apply(self):
        if self.wanted():
            if self.wb.hide():
                self._start_guard()
                self._check.start()
        else:
            self._check.stop()
            if self.wb.is_hidden_by_us():
                self.wb.restore()

    def restore(self):
        self._check.stop()
        self._peek.stop()
        if self.wb.is_hidden_by_us():
            self.wb.restore()

    def _start_guard(self):
        if self._guard is not None and self._guard.poll() is None:
            return
        import os
        import subprocess
        if getattr(sys, "frozen", False):      # packaged Notch.exe
            cmd = [sys.executable, "--guard", str(os.getpid())]
        else:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            guard = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taskbar_guard.py")
            cmd = [pyw if os.path.exists(pyw) else sys.executable, guard, str(os.getpid())]
        try:
            self._guard = subprocess.Popen(cmd,
                                           creationflags=0x08000000)   # CREATE_NO_WINDOW
        except OSError:
            self._guard = None

    def peek(self):
        """Show Windows' taskbar for its tray icons until you're done with it."""
        if not self.wb.is_hidden_by_us():
            return
        self._check.stop()
        self.wb.show_temporarily()
        self._peek_away = 0
        self._peek.start()

    def _peek_tick(self):
        trays = set(self.wb.tray_windows())
        fg = ctypes.windll.user32.GetForegroundWindow()
        self._peek_away = 0 if fg in trays else self._peek_away + 1
        if self._peek_away >= 3:
            self._peek.stop()
            if self.wanted():
                self.wb.hide()
                self._check.start()


def trim_memory():
    """Let Windows page out memory we aren't using right now (it comes back on demand)."""
    import ctypes.wintypes as wt
    gc.collect()
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.SetProcessWorkingSetSize.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
    k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)


def main():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = k32.CreateMutexW(None, False, "Local\\NotchWidget.SingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: already running
        return 0
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("Notch")
    app = NotchApp(qapp)  # noqa: F841 (kept alive by the event loop)
    rc = qapp.exec()
    k32.CloseHandle(mutex)
    return rc
