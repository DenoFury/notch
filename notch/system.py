"""Volume, audio level, brightness, battery, lock keys and fullscreen detection.

Everything here is event-driven: Windows tells us when something changes, so the
app does no work while nothing happens.
"""
import ctypes
import ctypes.wintypes as wt
import threading

import psutil
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

user32 = ctypes.windll.user32
user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p, ctypes.POINTER(wt.UINT), wt.UINT]
user32.GetRawInputData.restype = wt.UINT
user32.SetWinEventHook.restype = wt.HANDLE
user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD]
user32.GetForegroundWindow.restype = wt.HWND
user32.MonitorFromWindow.restype = wt.HANDLE
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]

WM_INPUT = 0x00FF
WM_POWERBROADCAST = 0x0218
PBT_APMPOWERSTATUSCHANGE = 0x000A
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
VK_CAPITAL, VK_NUMLOCK = 0x14, 0x90


# ---------------------------------------------------------------- audio
class Audio(QObject):
    volume_changed = Signal(float, bool)   # level 0..1, muted
    _notified = Signal(float, bool)        # from the COM callback thread
    _device_changed = Signal()
    output_changed = Signal(str, str)      # device name, kind: bluetooth | headphones | speaker

    def __init__(self):
        super().__init__()
        self._dev_id = None
        self._ev = self._meter = None
        self._cb = None
        # Audio objects of a device that has just disappeared (a monitor going to sleep takes its
        # speakers with it) can crash when released. Superseded ones are parked here instead:
        # a few bytes per device change.
        self._keep = []
        self.level, self.muted = 0.0, False
        self._notified.connect(self._on_notify)
        # one re-acquire per burst of device changes (waking monitors send several)
        self._reacquire = QTimer(self, singleShot=True, interval=600, timeout=self._acquire)
        self._device_changed.connect(self._reacquire.start)
        self._acquire()
        self._watch_devices()

    def _acquire(self):
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.callbacks import AudioEndpointVolumeCallback
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IAudioMeterInformation

        if self._ev is not None and self._cb is not None:
            try:
                self._ev.UnregisterControlChangeNotify(self._cb)
            except Exception:
                pass
        if self._ev is not None or self._cb is not None:
            self._keep.append((self._ev, self._meter, self._cb))
        try:
            dev = AudioUtilities.GetDeviceEnumerator().GetDefaultAudioEndpoint(0, 1)
            self._keep.append(dev)
            self._describe(dev)
            # QueryInterface (not ctypes.cast): a cast makes a second owner of the same COM pointer,
            # which gets released twice when it's thrown away and crashes during garbage collection
            self._ev = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
            self._meter = dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None).QueryInterface(
                IAudioMeterInformation)
            self.level = round(self._ev.GetMasterVolumeLevelScalar(), 3)
            self.muted = bool(self._ev.GetMute())
        except Exception:
            self._ev = self._meter = None
            return

        owner = self

        class _Cb(AudioEndpointVolumeCallback):
            def on_notify(self, new_volume, new_mute, *_):
                owner._notified.emit(float(new_volume), bool(new_mute))

        self._cb = _Cb()
        try:
            self._ev.RegisterControlChangeNotify(self._cb)
        except Exception:
            self._cb = None

    def _describe(self, dev):
        """Tell the island when sound moves to another device (headphones connected and so on)."""
        try:
            dev_id = dev.GetId()
        except Exception:
            return
        first = self._dev_id is None
        if dev_id == self._dev_id:
            return
        self._dev_id = dev_id
        if first:
            return
        try:
            from pycaw.pycaw import AudioUtilities
            d = AudioUtilities.CreateDevice(dev)
            self._keep.append(d)
            props = {k.upper(): v for k, v in d.properties.items()}
            name = props.get("{B3F8FA53-0004-438E-9003-51A46E139BFC} 6") or d.FriendlyName or "Audio device"
            bus = str(props.get("{A45C254E-DF1C-4EFD-8020-67D146A850E0} 24") or "").upper()
            form = props.get("{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0")
            kind = "bluetooth" if bus.startswith("BTH") else "headphones" if form in (3, 5) else "speaker"
            self.output_changed.emit(str(name), kind)
        except Exception:
            pass

    def _watch_devices(self):
        try:
            from pycaw.callbacks import MMNotificationClient
            from pycaw.pycaw import AudioUtilities
            owner = self

            class _Dev(MMNotificationClient):
                def on_default_device_changed(self, flow, flow_id, role, role_id, device_id):
                    if flow_id == 0 and role_id == 0:  # render / console
                        owner._device_changed.emit()

            self._dev_cb = _Dev()
            self._enum = AudioUtilities.GetDeviceEnumerator()
            self._enum.RegisterEndpointNotificationCallback(self._dev_cb)
        except Exception:
            pass

    def _on_notify(self, lv, mu):
        lv = round(lv, 3)
        if (lv, mu) != (self.level, self.muted):
            self.level, self.muted = lv, mu
            self.volume_changed.emit(lv, mu)

    def peak(self):
        try:
            return float(self._meter.GetPeakValue()) if self._meter else 0.0
        except Exception:
            return 0.0

    def set_level(self, v):
        if self._ev:
            try:
                self._ev.SetMasterVolumeLevelScalar(max(0.0, min(1.0, v)), None)
                if v > 0 and self.muted:
                    self._ev.SetMute(0, None)
            except Exception:
                pass

    def toggle_mute(self):
        if self._ev:
            try:
                self._ev.SetMute(0 if self.muted else 1, None)
            except Exception:
                pass


# ---------------------------------------------------------------- brightness
class Brightness(QObject):
    changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.level = None
        self.available = False
        threading.Thread(target=self._watch, daemon=True, name="brightness").start()

    def _watch(self):
        """Reads the level once, then blocks on WMI's brightness event (no polling)."""
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            w = win32com.client.GetObject(r"winmgmts:\\.\root\WMI")
            for m in w.ExecQuery("SELECT CurrentBrightness FROM WmiMonitorBrightness"):
                self.level = int(m.CurrentBrightness)
                self.available = True
            if not self.available:
                return
            ev = w.ExecNotificationQuery("SELECT * FROM WmiMonitorBrightnessEvent")
            while True:
                try:
                    e = ev.NextEvent()   # waits forever, wakes only on a change
                except Exception:
                    continue
                lv = int(e.Brightness)
                if lv != self.level:
                    self.level = lv
                    self.changed.emit(lv)
        except Exception:
            pass

    def set(self, value):
        def go():
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                w = win32com.client.GetObject(r"winmgmts:\\.\root\WMI")
                for m in w.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods"):
                    m.WmiSetBrightness(0, int(value))
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()


# ---------------------------------------------------------------- raw input / power messages
class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wt.USHORT), ("Flags", wt.USHORT), ("Reserved", wt.USHORT),
                ("VKey", wt.USHORT), ("Message", wt.UINT), ("ExtraInformation", wt.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]


class _MessageWindow(QWidget):
    """Hidden window that receives keyboard raw input and power broadcasts."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self._buf = RAWINPUT()
        self._size = wt.UINT(ctypes.sizeof(RAWINPUT))
        self.winId()  # create the native window without showing it
        dev = RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, int(self.winId()))
        user32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev))

    def nativeEvent(self, event_type, message):
        msg = wt.MSG.from_address(int(message))
        if msg.message == WM_INPUT:
            size = wt.UINT(ctypes.sizeof(RAWINPUT))
            if user32.GetRawInputData(msg.lParam, RID_INPUT, ctypes.byref(self._buf), ctypes.byref(size),
                                      ctypes.sizeof(RAWINPUTHEADER)) > 0 and self._buf.header.dwType == 1:
                vk = self._buf.keyboard.VKey
                if vk in (VK_CAPITAL, VK_NUMLOCK):
                    self.owner._key(vk, not (self._buf.keyboard.Flags & 1))
        elif msg.message == WM_POWERBROADCAST and msg.wParam == PBT_APMPOWERSTATUSCHANGE:
            self.owner._battery()
        return False, 0


WinEventProc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


class SystemWatch(QObject):
    battery_event = Signal(str, int, bool)    # kind: plugged|unplugged|low|critical|full
    lock_changed = Signal(str, bool)          # "Caps Lock", on
    fullscreen_changed = Signal(bool)
    active_screen_changed = Signal()          # the focused window is now on another monitor
    foreground_changed = Signal()             # another window became active

    def __init__(self):
        super().__init__()
        b = psutil.sensors_battery()
        self._plugged = b.power_plugged if b else None
        self._low_warned = set()
        self._locks = {VK_CAPITAL: bool(user32.GetKeyState(VK_CAPITAL) & 1),
                       VK_NUMLOCK: bool(user32.GetKeyState(VK_NUMLOCK) & 1)}
        self._down = set()
        self.fullscreen = False
        self._fg_mon = None
        self.cpu = 0.0
        self._cpu_users = 0
        self._cpu_t = QTimer(self, interval=2000, timeout=self._cpu)
        self._win = _MessageWindow(self)
        # battery: Windows sends WM_POWERBROADCAST; a slow poll backs it up for % thresholds
        QTimer(self, interval=60_000, timeout=self._battery).start()
        # fullscreen: re-check when the foreground window changes, plus a slow backup poll
        self._fs_check = QTimer(self, singleShot=True, interval=250, timeout=self._fs_poll)
        self._hook_proc = WinEventProc(lambda *a: (self._fs_check.start(), self.foreground_changed.emit()))
        self._hook = user32.SetWinEventHook(0x0003, 0x0003, None,
                                            ctypes.cast(self._hook_proc, ctypes.c_void_p), 0, 0, 0)
        QTimer(self, interval=3000, timeout=self._fs_poll).start()

    # ---- cpu sampling only while something shows it
    def want_cpu(self, on):
        self._cpu_users += 1 if on else -1
        if self._cpu_users > 0 and not self._cpu_t.isActive():
            psutil.cpu_percent(None)
            QTimer.singleShot(500, self._cpu)   # first real reading quickly
            self._cpu_t.start()
        elif self._cpu_users <= 0:
            self._cpu_users = 0
            self._cpu_t.stop()

    def _cpu(self):
        self.cpu = psutil.cpu_percent(None)

    def battery(self):
        return psutil.sensors_battery()

    def ram(self):
        return psutil.virtual_memory().percent

    def _battery(self):
        b = psutil.sensors_battery()
        if not b:
            return
        pct = int(b.percent)
        if self._plugged is not None and b.power_plugged != self._plugged:
            self.battery_event.emit("plugged" if b.power_plugged else "unplugged", pct, b.power_plugged)
            self._low_warned.clear()
        self._plugged = b.power_plugged
        if not b.power_plugged:
            for limit, kind in ((10, "critical"), (20, "low")):
                if pct <= limit and limit not in self._low_warned:
                    self._low_warned.add(limit)
                    self.battery_event.emit(kind, pct, False)
                    break
        elif pct >= 100 and "full" not in self._low_warned:
            self._low_warned.add("full")
            self.battery_event.emit("full", pct, True)

    def _key(self, vk, down):
        if not down:
            self._down.discard(vk)
            return
        if vk in self._down:
            return  # auto-repeat while held
        self._down.add(vk)
        self._locks[vk] = not self._locks[vk]
        self.lock_changed.emit("Caps Lock" if vk == VK_CAPITAL else "Num Lock", self._locks[vk])

    def _fs_poll(self):
        fs = is_foreground_fullscreen()
        if fs != self.fullscreen:
            self.fullscreen = fs
            self.fullscreen_changed.emit(fs)
        mon = _monitor_origin(user32.GetForegroundWindow(), skip_shell=True)
        if mon is not None and mon != self._fg_mon:
            self._fg_mon = mon
            self.active_screen_changed.emit()

    def active_screen(self):
        """The QScreen of the focused window (None if that's the desktop or nothing)."""
        mon = _monitor_origin(user32.GetForegroundWindow(), skip_shell=True) or self._fg_mon
        if mon is None:
            return None
        from .island import screen_of_monitor
        return screen_of_monitor(*mon)

    def fullscreen_screen(self):
        mon = _monitor_origin(user32.GetForegroundWindow())
        if mon is None:
            return None
        from .island import screen_of_monitor
        return screen_of_monitor(*mon)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


_cls_buf = ctypes.create_unicode_buffer(64)
_SHELL_CLASSES = ("WorkerW", "Progman", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def _monitor_origin(hwnd, skip_shell=False):
    """(left, top) of the monitor a window is on, in pixels."""
    if not hwnd:
        return None
    if skip_shell:
        user32.GetClassNameW(hwnd, _cls_buf, 64)
        if _cls_buf.value in _SHELL_CLASSES or _cls_buf.value.startswith("Qt"):   # desktop, or Notch itself
            return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
        return None
    return mi.rcMonitor.left, mi.rcMonitor.top


def is_foreground_fullscreen():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    user32.GetClassNameW(hwnd, _cls_buf, 64)
    if _cls_buf.value in ("WorkerW", "Progman", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"):
        return False
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
