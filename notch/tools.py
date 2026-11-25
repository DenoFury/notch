"""Back ends for the card's panels: file shelf, snippets, screenshots, screen recording and
quick controls (Wi-Fi, Bluetooth, night light, mic mute, brightness).

Everything heavy (Qt Multimedia, WinRT radios, WMI) is imported only when first used.
"""
import asyncio
import datetime as dt
import json
import os
import threading
import time
import winreg

from PySide6.QtCore import QObject, QTimer, Signal

from .config import DATA_DIR


def _known_folder(guid, fallback):
    try:
        from .tray_apps import _known_folder as kf
        return kf(guid) or fallback
    except Exception:
        return fallback


def pictures_dir():
    return os.path.join(_known_folder("33E28130-4E1E-4676-835A-98395C3BC3BB",
                                      os.path.expanduser("~\\Pictures")), "Screenshots")


def videos_dir():
    return _known_folder("18989B1D-99B5-455B-841C-AB7C74E4DDFC", os.path.expanduser("~\\Videos"))


def stamp():
    return dt.datetime.now().strftime("%Y-%m-%d %H.%M.%S")


# ---------------------------------------------------------------- file shelf
class Shelf(QObject):
    """Files parked on the island. Only their paths are kept (nothing is copied)."""
    changed = Signal()
    FILE = os.path.join(DATA_DIR, "shelf.json")
    LIMIT = 60

    def __init__(self):
        super().__init__()
        try:
            with open(self.FILE, encoding="utf-8") as f:
                self.items = [i for i in json.load(f) if os.path.exists(i["path"])]
        except (OSError, ValueError, KeyError, TypeError):
            self.items = []

    def _save(self):
        try:
            with open(self.FILE, "w", encoding="utf-8") as f:
                json.dump(self.items, f)
        except OSError:
            pass

    def add(self, paths):
        now = time.time()
        known = {i["path"].lower() for i in self.items}
        added = 0
        for p in paths:
            if p and os.path.exists(p) and p.lower() not in known:
                self.items.insert(0, {"path": p, "time": now})
                known.add(p.lower())
                added += 1
        if added:
            del self.items[self.LIMIT:]
            self._save()
            self.changed.emit()
        return added

    def remove(self, path):
        self.items = [i for i in self.items if i["path"] != path]
        self._save()
        self.changed.emit()

    def clear(self):
        self.items = []
        self._save()
        self.changed.emit()

    def prune(self):
        alive = [i for i in self.items if os.path.exists(i["path"])]
        if len(alive) != len(self.items):
            self.items = alive
            self._save()
            self.changed.emit()


# ---------------------------------------------------------------- snippets
class Snippets(QObject):
    """Saved texts to paste again, kept encrypted for your Windows account like the clipboard."""
    changed = Signal()
    FILE = os.path.join(DATA_DIR, "snippets.bin")

    def __init__(self):
        super().__init__()
        from .clipboard import _unprotect
        try:
            with open(self.FILE, "rb") as f:
                self.items = json.loads(_unprotect(f.read()).decode("utf-8"))
        except Exception:
            self.items = []

    def _save(self):
        from .clipboard import _protect
        try:
            with open(self.FILE, "wb") as f:
                f.write(_protect(json.dumps(self.items).encode("utf-8")))
        except Exception:
            pass

    def add(self, text):
        text = text.strip()
        if text and all(i["text"] != text for i in self.items):
            self.items.insert(0, {"text": text, "time": time.time()})
            self._save()
            self.changed.emit()
            return True
        return False

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)
            self._save()
            self.changed.emit()


# ---------------------------------------------------------------- screenshots
def screenshot(screen=None, all_screens=False):
    """Save a PNG of one screen (or all of them side by side); returns (path, QImage)."""
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    screens = QGuiApplication.screens() if all_screens else [screen or QGuiApplication.primaryScreen()]
    shots = [(s.geometry(), s.grabWindow(0).toImage()) for s in screens]
    if len(shots) == 1:
        img = shots[0][1]
    else:
        left = min(g.x() for g, _ in shots)
        top = min(g.y() for g, _ in shots)
        w = max(g.x() + i.width() for g, i in shots) - left
        h = max(g.y() + i.height() for g, i in shots) - top
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(0)
        p = QPainter(img)
        for g, i in shots:
            p.drawImage(g.x() - left, g.y() - top, i)
        p.end()
    folder = pictures_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"Screenshot {stamp()}.png")
    img.save(path, "PNG")
    QGuiApplication.clipboard().setImage(img)
    return path, img


# ---------------------------------------------------------------- screen recording
class Recorder(QObject):
    """Records a screen to MP4 with Qt Multimedia (hardware H.264 when available)."""
    state_changed = Signal(bool)
    saved = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.recording = False
        self.started_at = 0.0
        self._s = None

    def start(self, screen=None, mic=False):
        if self.recording:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtMultimedia import (QAudioInput, QMediaCaptureSession, QMediaFormat, QMediaRecorder,
                                          QScreenCapture)
        cap = QScreenCapture()
        cap.setScreen(screen or QGuiApplication.primaryScreen())
        sess = QMediaCaptureSession()
        sess.setScreenCapture(cap)
        audio = None
        if mic:
            audio = QAudioInput()
            sess.setAudioInput(audio)
        rec = QMediaRecorder()
        sess.setRecorder(rec)
        fmt = QMediaFormat()
        fmt.setFileFormat(QMediaFormat.MPEG4)
        fmt.setVideoCodec(QMediaFormat.VideoCodec.H264)
        if mic:
            fmt.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        rec.setMediaFormat(fmt)
        rec.setQuality(QMediaRecorder.HighQuality)
        folder = videos_dir()
        os.makedirs(folder, exist_ok=True)
        self._path = os.path.join(folder, f"Screen recording {stamp()}.mp4")
        rec.setOutputLocation(QUrl.fromLocalFile(self._path))
        rec.errorOccurred.connect(lambda _e, msg: self._fail(msg))
        rec.recorderStateChanged.connect(self._state)
        self._s = (cap, sess, rec, audio)
        cap.setActive(True)
        rec.record()

    def stop(self):
        if self._s:
            self._s[2].stop()

    def _state(self, st):
        from PySide6.QtMultimedia import QMediaRecorder
        on = st == QMediaRecorder.RecordingState
        if on and not self.recording:
            self.recording, self.started_at = True, time.monotonic()
            self.state_changed.emit(True)
        elif st == QMediaRecorder.StoppedState and self.recording:
            self.recording = False
            path = self._s[2].actualLocation().toLocalFile() or self._path
            self._s[0].setActive(False)
            QTimer.singleShot(0, self._release)
            self.state_changed.emit(False)
            if os.path.exists(path):
                self.saved.emit(path)

    def _release(self):
        self._s = None             # frees the encoder and capture (tens of MB)

    def _fail(self, msg):
        self.recording = False
        self._s = None
        self.state_changed.emit(False)
        self.failed.emit(msg or "Recording failed")

    def elapsed(self):
        s = int(time.monotonic() - self.started_at) if self.recording else 0
        return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- quick controls
def _run_async(coro_fn, done=None):
    """Run a WinRT coroutine on a short-lived thread; `done(result)` back on the UI thread."""
    holder = _Bridge.get()

    def worker():
        try:
            res = asyncio.run(coro_fn())
        except Exception:
            res = None
        if done is not None:
            holder.call.emit(done, res)
    threading.Thread(target=worker, daemon=True).start()


class _Bridge(QObject):
    call = Signal(object, object)
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
            cls._inst.call.connect(lambda fn, res: fn(res))
        return cls._inst


def radios_state(done):
    """done({"wifi": bool|None, "bluetooth": bool|None})"""
    async def go():
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState
        await Radio.request_access_async()
        out = {"wifi": None, "bluetooth": None}
        for r in await Radio.get_radios_async():
            k = {RadioKind.WI_FI: "wifi", RadioKind.BLUETOOTH: "bluetooth"}.get(r.kind)
            if k:
                out[k] = r.state == RadioState.ON
        return out
    _run_async(go, done)


def set_radio(kind, on, done=None):
    async def go():
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState
        await Radio.request_access_async()
        want = {"wifi": RadioKind.WI_FI, "bluetooth": RadioKind.BLUETOOTH}[kind]
        for r in await Radio.get_radios_async():
            if r.kind == want:
                await r.set_state_async(RadioState.ON if on else RadioState.OFF)
        return True
    _run_async(go, done)


NIGHT_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
             r"\default$windows.data.bluelightreduction.bluelightreductionstate"
             r"\windows.data.bluelightreduction.bluelightreductionstate")


def night_light():
    """True/False, or None if this PC has no night light setting yet."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NIGHT_KEY) as k:
            d = winreg.QueryValueEx(k, "Data")[0]
        return len(d) > 24 and d[18] == 0x15
    except OSError:
        return None


def set_night_light(on):
    """Flip Windows' night light the way its own toggle stores it (state byte + 2-byte marker)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NIGHT_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            d = bytearray(winreg.QueryValueEx(k, "Data")[0])
            is_on = d[18] == 0x15
            if on == is_on:
                return True
            if on:
                d = d[:23] + bytearray(b"\x10\x00") + d[23:]
                d[18] = 0x15
            else:
                del d[23:25]
                d[18] = 0x13
            for i in range(10, 15):          # bump the change stamp so Windows applies it
                if d[i] != 0xFF:
                    d[i] += 1
                    break
            winreg.SetValueEx(k, "Data", 0, winreg.REG_BINARY, bytes(d))
        return True
    except OSError:
        return False


_mic = [None, None]          # (device id, endpoint) of the default microphone, reused
_old_mics = []


def _mic_endpoint():
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetDeviceEnumerator().GetDefaultAudioEndpoint(1, 0)   # capture, console
    dev_id = dev.GetId()
    if _mic[0] != dev_id:
        _old_mics.append(_mic[1])          # never release a mic that may have just been unplugged
        # QueryInterface keeps COM reference counts right (a ctypes.cast double-releases and crashes)
        _mic[0], _mic[1] = dev_id, dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(
            IAudioEndpointVolume)
    return _mic[1]


def mic_muted():
    try:
        return bool(_mic_endpoint().GetMute())
    except Exception:
        return None


def set_mic_muted(on):
    try:
        _mic_endpoint().SetMute(1 if on else 0, None)
        return True
    except Exception:
        return False


def set_brightness(level):
    """Laptop / built-in screens only (WMI); runs off the UI thread."""
    def go():
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            w = win32com.client.GetObject(r"winmgmts:\\.\root\WMI")
            for m in w.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods"):
                m.WmiSetBrightness(1, int(max(0, min(100, level))))
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()
