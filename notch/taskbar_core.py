"""Open windows, pinned apps and app actions for the taskbar.

Updates are event-driven: the shell hook tells us when windows open, close,
change title or become active, so nothing is polled.
"""
import ctypes
import ctypes.wintypes as wt
import glob
import os
import subprocess

from PySide6.QtCore import QFileInfo, QObject, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFileIconProvider, QWidget

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetForegroundWindow.restype = wt.HWND
user32.SendMessageTimeoutW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM, ctypes.c_uint,
                                       ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
user32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsWindow.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.RegisterShellHookWindow.argtypes = [wt.HWND]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
GW_OWNER = 4
WM_GETICON, WM_CLOSE, WM_SYSCOMMAND = 0x7F, 0x0010, 0x0112
SC_MINIMIZE = 0xF020
GCLP_HICON, GCLP_HICONSM = -14, -34
DWMWA_CLOAKED = 14
HSHELL = {1: "created", 2: "destroyed", 4: "activated", 6: "redraw", 0x8004: "activated", 0x8006: "flash"}

PINNED_DIR = os.path.expandvars(r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar")
SKIP_EXES = {"textinputhost.exe", "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe"}

EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WinEventProc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


def window_title(h):
    n = user32.GetWindowTextLengthW(h)
    if not n:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def window_class(h):
    b = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(h, b, 128)
    return b.value


def window_pid(h):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


def exe_of_pid(pid):
    hp = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(hp)


def is_cloaked(h):
    v = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(wt.HWND(h), DWMWA_CLOAKED, ctypes.byref(v), 4)
    return bool(v.value)


def is_app_window(h):
    if not user32.IsWindowVisible(h) or user32.GetWindow(h, GW_OWNER):
        return False
    ex = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    if (ex & WS_EX_TOOLWINDOW or ex & WS_EX_NOACTIVATE) and not ex & WS_EX_APPWINDOW:
        return False
    if is_cloaked(h) or not user32.GetWindowTextLengthW(h):
        return False
    return True


def real_exe(h):
    """The app behind a window (looks inside UWP frame windows)."""
    exe = exe_of_pid(window_pid(h))
    if exe.lower().endswith("applicationframehost.exe"):
        found = []

        def cb(c, _):
            if window_class(c) == "Windows.UI.Core.CoreWindow":
                found.append(c)
                return False
            return True
        user32.EnumChildWindows(h, EnumProc(cb), 0)
        if found:
            exe = exe_of_pid(window_pid(found[0])) or exe
    return exe


_names = {}


def app_name(path):
    if path in _names:
        return _names[path]
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\VarFileInfo\\Translation")
        lang, cp = info[0]
        desc = win32api.GetFileVersionInfo(path, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription")
        if desc and desc.strip():
            name = desc.strip()
    except Exception:
        pass
    _names[path] = name
    return name


_icons = {}
_provider = None
_factory_iface = None


def _image_factory():
    """IShellItemImageFactory: the shell's own large icons (what Explorer shows at Extra large)."""
    global _factory_iface
    if _factory_iface is None:
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

        class IShellItemImageFactory(IUnknown):
            _iid_ = GUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")
            _methods_ = [COMMETHOD([], HRESULT, "GetImage", (["in"], wt.SIZE, "size"),
                                   (["in"], ctypes.c_int, "flags"), (["out"], ctypes.POINTER(wt.HBITMAP), "phbm"))]
        _factory_iface = IShellItemImageFactory
    return _factory_iface


def shell_icon(path, px=256, thumbnail=False):
    """Sharp icon for a file/exe/shortcut at up to `px` pixels, or a null pixmap.
    thumbnail=True gives a picture's / video's preview instead of its file-type icon."""
    try:
        import comtypes
        iface = _image_factory()
        shell32 = ctypes.windll.shell32
        shell32.SHCreateItemFromParsingName.argtypes = [wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                                        ctypes.POINTER(ctypes.c_void_p)]
        ptr = ctypes.c_void_p()
        iid = iface._iid_
        if shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(iid), ctypes.byref(ptr)) != 0:
            return QPixmap()
        fac = ctypes.cast(ptr, ctypes.POINTER(iface))
        hbm = fac.GetImage(wt.SIZE(px, px), 0x0 if thumbnail else 0x4)   # RESIZETOFIT | ICONONLY
        del fac                                            # comtypes releases it (exactly once)
        hbm = (hbm or 0) & 0xFFFFFFFF          # GDI handles are 32-bit; some come back sign-extended
        img = _hbitmap_to_image(hbm)
        ctypes.windll.gdi32.DeleteObject(wt.HANDLE(hbm))
        if img.isNull():
            return QPixmap()
        # small-only icons come back as a little icon in a big empty square: trim it
        rect = _opaque_rect(img)
        if rect is not None and (rect.width() < img.width() * 0.6):
            img = img.copy(rect)
        return QPixmap.fromImage(img)
    except Exception:
        return QPixmap()


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _DIBSECTION(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG), ("bmWidthBytes", wt.LONG),
                ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD), ("bmBits", ctypes.c_void_p),
                ("dsBmih", _BITMAPINFOHEADER), ("dsBitfields", wt.DWORD * 3), ("dshSection", wt.HANDLE),
                ("dsOffset", wt.DWORD)]


def _hbitmap_to_image(hbm):
    """The shell's 32-bit bitmap as a QImage *with* its transparency (QImage.fromHBITMAP drops the
    alpha channel, which put a black square behind every icon on a light background)."""
    from PySide6.QtCore import Qt
    gdi32 = ctypes.windll.gdi32
    gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
    ds = _DIBSECTION()
    n = gdi32.GetObjectW(wt.HANDLE(hbm), ctypes.sizeof(ds), ctypes.byref(ds))
    if n >= ctypes.sizeof(ds) and ds.bmBits and ds.bmBitsPixel == 32:
        w, h, stride = ds.bmWidth, abs(ds.bmHeight), ds.bmWidthBytes
        data = ctypes.string_at(ds.bmBits, stride * h)
        img = QImage(data, w, h, stride, QImage.Format_ARGB32_Premultiplied).copy()
        if not any(data[i] for i in range(3, len(data), 4 * 97)):     # no alpha at all: treat as opaque
            img = img.convertToFormat(QImage.Format_RGB32)
        if ds.dsBmih.biHeight > 0:                                    # stored bottom-up
            img = img.flipped(Qt.Vertical) if hasattr(img, "flipped") else img.mirrored(False, True)
        return img
    img = QImage.fromHBITMAP(hbm)                                     # anything else: Qt's conversion
    if not img.isNull():
        img = img.flipped(Qt.Vertical) if hasattr(img, "flipped") else img.mirrored(False, True)
    return img


def _opaque_rect(img):
    """Bounding box of the visible pixels (checked on a coarse grid; cheap)."""
    from PySide6.QtCore import QRect
    w, h = img.width(), img.height()
    step = max(1, w // 64)
    xs, ys = [], []
    for y in range(0, h, step):
        for x in range(0, w, step):
            if img.pixelColor(x, y).alpha() > 8:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    pad = step * 2
    x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
    x1, y1 = min(w, max(xs) + pad + 1), min(h, max(ys) + pad + 1)
    side = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    return QRect(max(0, cx - side // 2), max(0, cy - side // 2), side, side)


def package_app_id(pid):
    """App ID of a Store (packaged) app's process, or None."""
    h = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        kernel32.GetApplicationUserModelId.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p]
        n = ctypes.c_uint32(256)
        buf = ctypes.create_unicode_buffer(256)
        return buf.value if kernel32.GetApplicationUserModelId(h, ctypes.byref(n), buf) == 0 else None
    finally:
        kernel32.CloseHandle(h)


def app_icon(path, hwnd=None, pid=None):
    """High-resolution icon for an exe/shortcut, falling back to the window's own icon."""
    key = path.lower() if path else f"hwnd:{hwnd}"
    if key in _icons:
        return _icons[key]
    global _provider
    pm = QPixmap()
    if path and "\\windowsapps\\" in path.lower():
        # Store apps: their exe only has a generic icon; the app's tile icon lives with its app ID
        if pid is None and hwnd:
            pid = window_pid(hwnd)
        aumid = package_app_id(pid) if pid else None
        if aumid:
            pm = shell_icon("shell:AppsFolder\\" + aumid)
    if pm.isNull() and path and os.path.exists(path):
        pm = shell_icon(path)
        if pm.isNull():
            if _provider is None:
                _provider = QFileIconProvider()
            pm = _provider.icon(QFileInfo(path)).pixmap(256, 256)
    if (pm.isNull() or pm.width() < 32) and hwnd:
        res = ctypes.c_size_t()
        user32.SendMessageTimeoutW(hwnd, WM_GETICON, 1, 0, 2, 50, ctypes.byref(res))
        hicon = res.value or user32.GetClassLongPtrW(hwnd, GCLP_HICON)
        if hicon:
            img = QImage.fromHICON(hicon)
            if not img.isNull():
                pm = QPixmap.fromImage(img)
    if not pm.isNull() and pm.width() > 128:
        # 128 px is plenty for the biggest dock size; keeps the cache small
        from PySide6.QtCore import Qt
        pm = pm.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _icons[key] = pm
    return pm


_sized = {}


def draw_icon(p, rect, pm, dpr):
    """Draw `pm` into `rect` sharply: scaled once with a high-quality filter to the exact
    number of screen pixels, on whole pixels (cached per size)."""
    if pm.isNull():
        return
    from PySide6.QtCore import QPointF, Qt
    px = max(1, round(rect.width() * dpr))
    key = (pm.cacheKey(), px)
    s = _sized.get(key)
    if s is None:
        if len(_sized) > 600:
            _sized.clear()
        s = pm.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        s.setDevicePixelRatio(dpr)
        _sized[key] = s
    w = s.width() / dpr
    h = s.height() / dpr
    x = round((rect.center().x() - w / 2) * dpr) / dpr
    y = round((rect.center().y() - h / 2) * dpr) / dpr
    p.drawPixmap(QPointF(x, y), s)


def resolve_shortcut(lnk):
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(lnk)
        return sc.TargetPath or ""
    except Exception:
        return ""


_targets = {}


def resolve_target_cached(lnk):
    if lnk not in _targets:
        _targets[lnk] = resolve_shortcut(lnk)
    return _targets[lnk]


class _ShellWindow(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self._msg = user32.RegisterWindowMessageW("SHELLHOOK")
        self._created = user32.RegisterWindowMessageW("TaskbarCreated")
        user32.RegisterShellHookWindow(int(self.winId()))

    def nativeEvent(self, event_type, message):
        msg = wt.MSG.from_address(int(message))
        if msg.message == self._msg:
            kind = HSHELL.get(msg.wParam & 0xFFFF if msg.wParam < 0x8000 else msg.wParam)
            if kind:
                self.owner._shell_event(kind, msg.lParam)
        elif msg.message == self._created:
            self.owner.explorer_restarted.emit()
        return False, 0


class AppGroup:
    __slots__ = ("key", "path", "name", "pinned", "windows", "hwnd_hint", "exe")

    def __init__(self, key, path, name, pinned=False):
        self.key, self.path, self.name, self.pinned = key, path, name, pinned
        self.windows = []        # [(hwnd, title)]
        self.hwnd_hint = None
        self.exe = None          # the real program when `path` is a shortcut

    def icon(self):
        src = self.exe or self.path
        if src.lower().endswith(".lnk"):
            target = resolve_target_cached(src)
            src = target if target.lower().endswith(".exe") and os.path.exists(target) else src
        return app_icon(src, self.windows[0][0] if self.windows else self.hwnd_hint)


class WindowTracker(QObject):
    changed = Signal()
    active_changed = Signal()
    flashed = Signal(int)
    explorer_restarted = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.groups = []
        self.active = None
        self._order = []          # keys of running-but-unpinned apps in first-seen order
        self._exe_cache = {}
        if self.settings["taskbar_pins"] is None:
            self.settings.set("taskbar_pins", self._seed_pins())
        self._debounce = QTimer(self, singleShot=True, interval=60, timeout=self.refresh)
        self._win = _ShellWindow(self)
        # windows dragged (or snapped) to another screen: refresh when a move ends
        self._hook_proc = WinEventProc(lambda *a: self._debounce.start(150))
        user32.SetWinEventHook.restype = wt.HANDLE
        user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, ctypes.c_void_p,
                                           wt.DWORD, wt.DWORD, wt.DWORD]
        self._hook = user32.SetWinEventHook(0x000B, 0x000B, None,   # EVENT_SYSTEM_MOVESIZEEND
                                            ctypes.cast(self._hook_proc, ctypes.c_void_p), 0, 0, 0)
        self.refresh()

    # ---------------------------------------------------------- pins
    def _seed_pins(self):
        """Start with the apps already pinned to Windows' own taskbar."""
        pins = []
        for lnk in sorted(glob.glob(os.path.join(PINNED_DIR, "*.lnk"))):
            target = resolve_shortcut(lnk)
            pins.append({"path": target if target.lower().endswith(".exe") else lnk,
                         "lnk": lnk, "name": os.path.splitext(os.path.basename(lnk))[0]})
        return pins

    def pins(self):
        return self.settings["taskbar_pins"] or []

    def is_pinned(self, path):
        p = (path or "").lower()
        return any(x["path"].lower() == p for x in self.pins())

    def pin(self, group):
        if not self.is_pinned(group.path):
            self.settings.set("taskbar_pins", self.pins() + [{"path": group.path, "name": group.name}])
            self.refresh()

    def unpin(self, group):
        p = group.path.lower()
        self.settings.set("taskbar_pins", [x for x in self.pins() if x["path"].lower() != p])
        self.refresh()

    def move_pin(self, group, index):
        pins = self.pins()
        p = group.path.lower()
        item = next((x for x in pins if x["path"].lower() == p), None)
        if item:
            pins.remove(item)
            pins.insert(max(0, min(index, len(pins))), item)
            self.settings.set("taskbar_pins", pins)
            self.refresh()

    # ---------------------------------------------------------- tracking
    def _shell_event(self, kind, hwnd):
        if kind == "activated":
            self.active = hwnd or None
            self.active_changed.emit()
        elif kind == "flash":
            self.flashed.emit(int(hwnd or 0))
        if kind in ("created", "destroyed", "redraw", "activated"):
            self._debounce.start()

    def refresh(self):
        wins = []

        def cb(h, _):
            if is_app_window(h):
                wins.append(h)
            return True
        user32.EnumWindows(EnumProc(cb), 0)

        own_pid = os.getpid()
        by_key = {}
        for h in wins:
            if window_pid(h) == own_pid:
                continue
            exe = self._exe_cache.get(h)
            if exe is None:
                exe = real_exe(h)
                self._exe_cache[h] = exe
            if not exe or os.path.basename(exe).lower() in SKIP_EXES:
                continue
            key = exe.lower()
            g = by_key.get(key)
            if g is None:
                g = by_key[key] = AppGroup(key, exe, app_name(exe))
            g.windows.append((h, window_title(h)))
        alive = set(wins)
        self._exe_cache = {h: e for h, e in self._exe_cache.items() if h in alive}

        groups = []
        for pin in self.pins():
            key = pin["path"].lower()
            g = by_key.pop(key, None)
            if g is None:
                # shortcuts often point at a launcher (Discord -> Update.exe): match by name too
                pname = (pin.get("name") or "").lower()
                names = {pname, os.path.splitext(os.path.basename(key))[0]}
                for k, cand in list(by_key.items()):
                    stem = os.path.splitext(os.path.basename(k))[0]
                    if (cand.name.lower() in names or stem in names
                            or (len(stem) >= 4 and stem in pname.split())):   # "explorer" in "file explorer"
                        g = by_key.pop(k)
                        g.exe = g.path                            # icon from the real app
                        g.path = pin.get("lnk") or pin["path"]   # launch it the way the shortcut does
                        break
            if g is None:
                launch = pin.get("lnk") if not os.path.exists(pin["path"]) and pin.get("lnk") else pin["path"]
                g = AppGroup(key, launch, pin.get("name") or app_name(pin["path"]))
            g.pinned = True
            groups.append(g)
        for key in list(by_key):
            if key not in self._order:
                self._order.append(key)
        self._order = [k for k in self._order if k in by_key]
        groups += [by_key[k] for k in self._order]
        self.groups = groups
        fg = user32.GetForegroundWindow()
        self.active = fg or self.active
        self.changed.emit()

    def group_of(self, hwnd):
        for g in self.groups:
            if any(h == hwnd for h, _ in g.windows):
                return g
        return None

    # ---------------------------------------------------------- actions
    @staticmethod
    def activate(hwnd):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        user32.SwitchToThisWindow(hwnd, True)

    @staticmethod
    def minimize(hwnd):
        user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)

    @staticmethod
    def close(hwnd):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    @staticmethod
    def launch(path):
        try:
            os.startfile(path)
        except OSError:
            try:
                subprocess.Popen([path])
            except OSError:
                pass

    def click(self, group):
        """Taskbar click: start it, bring it forward, or minimise it if it is in front."""
        if not group.windows:
            self.launch(group.path)
            return
        hwnds = [h for h, _ in group.windows]
        fg = user32.GetForegroundWindow()
        if len(hwnds) == 1:
            h = hwnds[0]
            if h == fg and not user32.IsIconic(h):
                self.minimize(h)
            else:
                self.activate(h)
            return
        # several windows: cycle to the next one
        if fg in hwnds:
            nxt = hwnds[(hwnds.index(fg) + 1) % len(hwnds)]
        else:
            nxt = hwnds[0]
        self.activate(nxt)
