"""Send to phone: a tiny web page on your own Wi-Fi, opened by scanning a QR code.

- Runs only while needed: starts when the Phone tab is shown, stops 10 minutes after the last use.
- The address has a random token, so other devices on the network can't use it without the code.
- Phone -> PC: files (streamed to disk, any size) land in Downloads\\From phone and on the shelf;
  text goes to the clipboard.  PC -> phone: files on the shelf can be downloaded.
"""
import html
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, QTimer, Signal

IDLE_STOP = 10 * 60
MAX_UPLOAD = 8 * 1024 ** 3          # 8 GB per file
KEEP_FREE = 2 * 1024 ** 3           # never fill the disk: leave 2 GB free
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def safe_filename(raw):
    """A plain file name: no folders, no control or invisible direction characters (which can
    disguise "photo<RLO>gpj.exe" as "photoexe.jpg"), no reserved Windows names."""
    import unicodedata
    name = os.path.basename((raw or "").replace("\\", "/"))
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C" and c not in '<>:"/\\|?*')
    name = name.strip().rstrip(". ")
    if not name or name.split(".")[0].upper() in RESERVED or set(name) <= {"."}:
        name = "file_" + (name.strip(".") or "upload")
    return name[:180]


def mark_from_internet(path):
    """Tag a received file the way browsers tag downloads, so SmartScreen and Office treat it
    with care if it's ever opened."""
    try:
        with open(path + ":Zone.Identifier", "w") as f:
            f.write("[ZoneTransfer]\nZoneId=3\n")
    except OSError:
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9))          # no packet is sent; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Notch</title><style>
:root{color-scheme:dark}body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0b0b0d;color:#f2f2f5;
margin:0;padding:18px;max-width:560px;margin:auto}h1{font-size:22px;margin:6px 0 16px}
.card{background:#1a1a1e;border-radius:16px;padding:14px;margin-bottom:14px}
.t{color:#8e8e95;font-size:13px;margin:0 0 10px}button,label.b{display:inline-block;background:#30d158;color:#000;
border:0;border-radius:12px;padding:12px 16px;font-size:16px;font-weight:600}textarea{width:100%;box-sizing:border-box;
background:#26262b;color:#fff;border:0;border-radius:12px;padding:10px;font-size:16px;min-height:80px}
a.f{display:block;color:#f2f2f5;text-decoration:none;padding:10px 0;border-bottom:1px solid #2a2a2e}
a.f span{color:#8e8e95;font-size:13px;float:right}#st{color:#30d158;margin-top:8px;min-height:1em}
.c{background:#26262b;border-radius:10px;padding:8px 10px;margin:6px 0;font-size:14px;white-space:pre-wrap;word-break:break-word}
</style></head><body><h1>Notch</h1>
<div class="card"><p class="t">Send photos and files to the PC</p>
<label class="b">Choose files<input id="fi" type="file" multiple hidden></label><div id="st"></div></div>
<div class="card"><p class="t">Send text (goes to the PC's clipboard)</p>
<textarea id="tx" placeholder="Type or paste"></textarea><p><button id="sb">Send text</button></p></div>
<div class="card"><p class="t">Files on the PC's shelf</p>%FILES%</div>
%CLIP%
<script>
const st=document.getElementById('st');
document.getElementById('fi').onchange=async e=>{for(const f of e.target.files){st.textContent='Sending '+f.name+'…';
 const r=await fetch('upload?name='+encodeURIComponent(f.name),{method:'POST',body:f});
 st.textContent=r.ok?'Sent '+f.name:'Failed: '+f.name}};
document.getElementById('sb').onclick=async()=>{const t=document.getElementById('tx').value;if(!t)return;
 const r=await fetch('text',{method:'POST',body:t});st.textContent=r.ok?'Text sent':'Failed';
 if(r.ok)document.getElementById('tx').value=''};
</script></body></html>"""


class PhoneLink(QObject):
    received_file = Signal(str)      # saved path
    received_text = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.token = secrets.token_urlsafe(9)
        self.server = None
        self.url = ""
        self._last = 0.0
        self._idle = QTimer(self, interval=30_000, timeout=self._check_idle)

    # -- lifecycle
    def start(self):
        self._last = time.monotonic()
        if self.server:
            return self.url
        link = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ok(self, body, ctype="text/html; charset=utf-8", code=200):
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _route(self):
                parts = urllib.parse.urlsplit(self.path)
                segs = parts.path.strip("/").split("/", 1)
                if not segs or not secrets.compare_digest(segs[0], link.token):
                    self._ok("Not found", "text/plain", 404)
                    return None, None
                link._last = time.monotonic()
                return (segs[1] if len(segs) > 1 else ""), urllib.parse.parse_qs(parts.query)

            def do_GET(self):
                route, q = self._route()
                if route is None:
                    return
                if route == "":
                    return self._ok(link._page())
                if route.startswith("file/"):
                    try:
                        path = link._shelf_files()[int(route[5:])]
                    except (ValueError, IndexError):
                        return self._ok("Not found", "text/plain", 404)
                    size = os.path.getsize(path)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    name = urllib.parse.quote(os.path.basename(path))
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{name}")
                    self.end_headers()
                    with open(path, "rb") as f:
                        while True:
                            chunk = f.read(1 << 16)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    return
                self._ok("Not found", "text/plain", 404)

            def do_POST(self):
                route, q = self._route()
                if route is None:
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n <= 0:
                    return self._ok("empty", "text/plain", 411)
                if route == "text":
                    text = self.rfile.read(min(n, 200_000)).decode("utf-8", "replace")
                    link.received_text.emit(text)
                    return self._ok("ok", "text/plain")
                if route == "upload":
                    name = safe_filename((q.get("name") or ["file"])[0])
                    folder = os.path.join(link._downloads(), "From phone")
                    os.makedirs(folder, exist_ok=True)
                    import shutil
                    if n > MAX_UPLOAD or shutil.disk_usage(folder).free - n < KEEP_FREE:
                        return self._ok("too big", "text/plain", 413)
                    path = os.path.join(folder, name)
                    base, ext = os.path.splitext(path)
                    k = 1
                    while os.path.exists(path):
                        path = f"{base} ({k}){ext}"
                        k += 1
                    left = n
                    try:
                        with open(path, "xb") as f:          # never overwrite anything
                            while left > 0:
                                chunk = self.rfile.read(min(left, 1 << 16))
                                if not chunk:
                                    break
                                f.write(chunk)
                                left -= len(chunk)
                    except OSError:
                        left = left or 1
                    if not left:
                        mark_from_internet(path)
                    if left:
                        if os.path.exists(path):
                            os.remove(path)
                        return self._ok("incomplete", "text/plain", 400)
                    link.received_file.emit(path)
                    return self._ok("ok", "text/plain")
                self._ok("Not found", "text/plain", 404)

        ip = lan_ip()
        self.server = ThreadingHTTPServer((ip, 0), Handler)
        self.server.daemon_threads = True
        port = self.server.server_address[1]
        self.url = f"http://{ip}:{port}/{self.token}/"
        threading.Thread(target=self.server.serve_forever, daemon=True, name="phone-link").start()
        self._idle.start()
        self.running_changed.emit(True)
        return self.url

    def touch(self):
        self._last = time.monotonic()

    def _check_idle(self):
        if time.monotonic() - self._last > IDLE_STOP:
            self.stop()

    def stop(self):
        if self.server:
            srv, self.server = self.server, None
            threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
            self._idle.stop()
            self.url = ""
            self.running_changed.emit(False)

    # -- content
    def _downloads(self):
        from .indicators import downloads_folder
        return downloads_folder()

    def _shelf_files(self):
        return [i["path"] for i in self.app.shelf.items if os.path.isfile(i["path"])]

    def _page(self):
        from .indicators import human
        files = self._shelf_files()
        rows = "".join(f'<a class="f" href="file/{n}">{html.escape(os.path.basename(p))}'
                       f'<span>{human(os.path.getsize(p))}</span></a>' for n, p in enumerate(files[:40]))
        clip = ""
        if self.app.settings["phone_clipboard"]:
            texts = [i["text"] for i in self.app.clipboard.items[:5]]
            clip = ('<div class="card"><p class="t">Recently copied on the PC</p>'
                    + "".join(f'<div class="c">{html.escape(t[:600])}</div>' for t in texts) + "</div>")
        return PAGE.replace("%FILES%", rows or '<p class="t">Empty. Drop files on the island to share them.</p>') \
                   .replace("%CLIP%", clip)


def qr_matrix(text):
    """Rows of booleans for a QR code of `text` (dark = True)."""
    import segno
    q = segno.make(text, error="m", micro=False)
    return [[bool(v) for v in row] for row in q.matrix]
