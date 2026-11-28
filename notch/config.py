"""Settings storage, themes and the start-with-Windows switch."""
import json
import os
import sys
import winreg

from PySide6.QtCore import QObject, Signal

APP_NAME = "NotchWidget"
DATA_DIR = os.environ.get("NOTCH_DATA") or os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Notch")
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

DEFAULTS = {
    # appearance
    "shape": "island",          # island | notch
    "surface": "black",         # see SURFACES
    "accent": "green",          # see ACCENTS
    "scale": 1.0,
    "top_margin": 8,
    "open_delay_ms": 120,
    "show_title_in_pill": True,
    "hide_in_fullscreen": True,
    "hide_from_capture": False,  # keep the island and bar out of screenshots / recordings / screen sharing
    "position": "center",       # center | left | right (along the top edge)
    "island_screens": "main",   # main | all | mouse
    "hud_style": "bar",         # bar | thin | segments | dots
    "pet": "none",              # none | cat | ginger | black | white
    # panels
    "card_tabs": ["home", "music", "notif", "files", "clip", "controls", "launch", "phone", "web", "widgets"],
    "launcher_slots": [None, None, None, None, None, None],
    "phone_clipboard": False,   # let the phone page show recent clipboard text
    "web_pins": [],
    "record_mic": False,
    "quiet": False,             # no notification popups
    # updates
    "auto_update": "ask",       # ask | auto | off
    "update_last_check": 0,
    "update_skip": "",          # a version Windows wouldn't let run here: don't retry it every day
    # widgets (phase 4)
    "widgets": ["system", "world", "progress", "prices"],
    "desktop_widgets": [],
    "desktop_positions": {},
    "desktop_locked": False,
    "world_clocks": ["New York|America/New_York", "London|Europe/London", "Tokyo|Asia/Tokyo"],
    "countdown_title": "", "countdown_date": "",
    "currency_base": "EUR", "currencies": ["USD", "GBP", "TRY"],
    "tickers": ["BTC-USD", "ETH-USD", "AAPL", "EURUSD=X"],
    "price_alerts": [],
    "uptime_urls": [],
    "ping_host": "1.1.1.1",
    "ics_url": "",
    "photos_dir": "",
    "day_clock": False, "day_clock_size": 96, "day_clock_color": "#ffffff",
    "wallpaper": False, "wallpaper_video": "", "wallpaper_screens": "main",
    "wallpaper_pause_covered": True, "wallpaper_pause_battery": True,
    # huds
    "hud_volume": True,
    "hud_brightness": True,
    "hud_battery": True,
    "hud_locks": True,
    "notifications": True,
    "hud_privacy": True,         # mic / camera in use
    "hud_audio_device": True,    # headphones / speakers switched
    "hud_downloads": True,
    "notification_seconds": 5,
    # music
    "lyrics": True,
    # clipboard
    "clipboard": True,
    "clipboard_keep": 50,
    # widgets
    "city": "",
    "fahrenheit": False,
    "focus_min": 25,
    "break_min": 5,
    "clock_24h": True,
    # taskbar
    "taskbar": False,
    "taskbar_hide_windows": True,
    "taskbar_all_screens": True,
    "taskbar_own_apps": True,
    "taskbar_dock": True,
    "taskbar_dock_size": 1.4,
    "taskbar_previews": True,
    "taskbar_scale": 1.0,
    # what maximised windows do: "autohide" = use the whole screen, the bar slides away and comes back
    # at the bottom edge; "float" = use the whole screen, the bar stays on top; "reserve" = keep a strip free
    "taskbar_space": "autohide",
    "taskbar_pins": None,       # seeded from Windows' own pinned apps on first use
}

SURFACES = {
    #            fill (r,g,b,a)           text        dim         line
    "black":    ((0, 0, 0, 255),          "#ffffff", "#9a9aa0", "#262629"),
    "graphite": ((38, 38, 42, 255),       "#ffffff", "#a5a5ab", "#3a3a3f"),
    "glass":    ((18, 18, 22, 200),       "#ffffff", "#b5b5bb", "#3b3b42"),
    "white":    ((250, 250, 252, 255),    "#111114", "#6b6b72", "#e3e3e8"),
    "midnight": ((14, 20, 40, 245),       "#ffffff", "#9aa5c4", "#26304d"),
    "ocean":    ((8, 34, 48, 245),        "#ffffff", "#8fb6c4", "#1d4a5c"),
    "forest":   ((14, 34, 24, 245),       "#ffffff", "#93b8a0", "#244a33"),
    "plum":     ((40, 16, 38, 245),       "#ffffff", "#c09ab8", "#4e2748"),
    "sand":     ((242, 234, 220, 255),    "#2a241b", "#7d725f", "#ddd2bd"),
    "frost":    ((236, 238, 244, 215),    "#111114", "#62636b", "#cfd2da"),
}

ACCENTS = {
    "green": "#30d158", "blue": "#0a84ff", "purple": "#bf5af2", "pink": "#ff375f",
    "orange": "#ff9f0a", "teal": "#40c8e0", "white": "#ffffff",
    "red": "#ff453a", "yellow": "#ffd60a", "indigo": "#5e5ce6", "mint": "#63e6be",
    "album": None,      # taken from the playing song's cover
}


class Settings(QObject):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.data = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def set(self, k, v):
        if self.data.get(k) == v:
            return
        self.data[k] = v
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass
        self.changed.emit(k)

    # theme helpers
    def surface(self):
        return SURFACES.get(self["surface"], SURFACES["black"])

    album_color = None      # set by the app from the cover art

    def accent(self):
        if self["accent"] == "album":
            return self.album_color or ACCENTS["green"]
        return ACCENTS.get(self["accent"]) or ACCENTS["green"]


# ---------------------------------------------------------------- startup
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup",
                           "Notch.lnk")


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pythonw():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pyw if os.path.exists(pyw) else sys.executable


def startup_command():
    return f'"{_pythonw()}" "{os.path.join(_root(), "run.py")}"'


def _remove_run_value():
    """Older versions used the Run registry key, which Windows was skipping."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except OSError:
        pass


def startup_enabled():
    return os.path.exists(STARTUP_LNK)


def set_startup(on):
    """A shortcut in the user's Startup folder (listed under Settings > Apps > Startup)."""
    _remove_run_value()
    if not on:
        try:
            os.remove(STARTUP_LNK)
        except OSError:
            pass
        return
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(STARTUP_LNK)
    # go through the same launcher as "Start Notch.vbs": starting pythonw straight from a
    # shortcut left Windows showing a busy cursor over Notch's windows
    if getattr(sys, "frozen", False):          # packaged Notch.exe
        sc.TargetPath = sys.executable
        sc.Arguments = ""
        sc.WorkingDirectory = os.path.dirname(sys.executable)
    else:
        sc.TargetPath = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
        sc.Arguments = f'"{os.path.join(_root(), "Start Notch.vbs")}"'
        sc.WorkingDirectory = _root()
    sc.Description = "Notch"
    sc.WindowStyle = 1
    sc.Save()
