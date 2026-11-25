"""Now-playing info from Windows' media sessions, plus synced lyrics."""
import asyncio
import datetime as dt
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    HAVE_WINRT = True
except Exception:
    HAVE_WINRT = False

USER_AGENT = "NotchWidget/1.0 (desktop notch app)"
LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


def _pretty_app(aumid):
    name = aumid.split("!")[-1].split("\\")[-1]
    name = re.sub(r"\.exe$", "", name, flags=re.I)
    known = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
             "app": aumid.split("_")[0].split(".")[-1]}
    return known.get(name.lower(), name)


class Media(QObject):
    changed = Signal()          # metadata / play state
    art_changed = Signal()
    lyrics_changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.title = self.artist = self.album = self.app = ""
        self.playing = False
        self.available = False
        self.duration = 0.0
        self._pos = 0.0
        self._pos_at = time.monotonic()
        self.art = QImage()
        self._small = None
        self.lyrics = []           # [(seconds, line)]
        self._art_key = None
        self._lyrics_key = None
        self._mgr = None
        self.loop = asyncio.new_event_loop()
        if HAVE_WINRT:
            threading.Thread(target=self._run, daemon=True, name="media").start()

    # ---------------------------------------------------------- public
    @property
    def position(self):
        p = self._pos + (time.monotonic() - self._pos_at if self.playing else 0)
        return min(p, self.duration) if self.duration else p

    def art_small(self, px):
        """The cover scaled once for the pill, instead of on every frame."""
        if self._small is None or self._small[0] != px:
            img = self.art
            s = min(img.width(), img.height())
            sq = img.copy((img.width() - s) // 2, (img.height() - s) // 2, s, s)
            self._small = (px, sq.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        return self._small[1]

    def lyric_index(self):
        """Index of the line being sung now, -1 before the first line."""
        if not self.lyrics:
            return -1
        p = self.position + 0.3
        i = -1
        for n, (t, _) in enumerate(self.lyrics):
            if t > p:
                break
            i = n
        return i

    def current_lyric(self):
        i = self.lyric_index()
        return self.lyrics[i][1] if i >= 0 else ""

    def toggle(self):
        self.playing = not self.playing
        self._pos_at = time.monotonic()
        self.changed.emit()
        self._do("try_toggle_play_pause_async")

    def next(self):
        self._do("try_skip_next_async")

    def prev(self):
        self._do("try_skip_previous_async")

    def seek(self, seconds):
        self._pos, self._pos_at = seconds, time.monotonic()
        self._do("try_change_playback_position_async", int(seconds * 10_000_000))

    # ---------------------------------------------------------- worker
    def _run(self):
        asyncio.set_event_loop(self.loop)
        self._wake = asyncio.Event()
        self._sess_id = None
        self._sess = None
        self._tokens = []
        self._mgr_tokens = None
        self.loop.create_task(self._watch())
        self.loop.run_forever()

    def _on_event(self, *_):
        """Called by Windows on its own threads whenever media state changes."""
        self.loop.call_soon_threadsafe(self._wake.set)

    def _subscribe(self, s):
        sid = s.source_app_user_model_id if s else None
        if sid == self._sess_id and s is not None and self._sess is not None:
            return
        if self._sess is not None:
            for name, tok in self._tokens:
                try:
                    getattr(self._sess, "remove_" + name)(tok)
                except Exception:
                    pass
        self._tokens = []
        self._sess, self._sess_id = s, sid
        if s is not None:
            for name in ("media_properties_changed", "playback_info_changed", "timeline_properties_changed"):
                try:
                    self._tokens.append((name, getattr(s, "add_" + name)(self._on_event)))
                except Exception:
                    pass

    async def _watch(self):
        """Refresh only when Windows reports a change, plus a slow safety refresh."""
        while True:
            try:
                if self._mgr is None:
                    self._mgr = await MediaManager.request_async()
                    self._mgr.add_current_session_changed(self._on_event)
                    self._mgr.add_sessions_changed(self._on_event)
                self._subscribe(self._mgr.get_current_session())
                await self._poll()
            except Exception:
                self._mgr = None
                self._sess = self._sess_id = None
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=10 if self.playing else 60)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.08)  # merge bursts of events into one refresh

    def _do(self, name, *args):
        async def go():
            try:
                s = await self._session()
                if s:
                    await getattr(s, name)(*args)
            except Exception:
                pass
            self._wake.set()
        if HAVE_WINRT:
            asyncio.run_coroutine_threadsafe(go(), self.loop)

    async def _session(self):
        if self._mgr is None:
            self._mgr = await MediaManager.request_async()
        return self._mgr.get_current_session()

    async def _poll(self):
        s = await self._session()
        if s is None:
            if self.available:
                self.available = self.playing = False
                self.changed.emit()
            return
        p = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        playing = info.playback_status == PlaybackStatus.PLAYING
        title, artist, album = p.title or "", p.artist or "", p.album_title or ""
        app = _pretty_app(s.source_app_user_model_id or "")

        # timeline
        try:
            tl = s.get_timeline_properties()
            dur = tl.end_time.total_seconds()
            pos = tl.position.total_seconds()
            if playing and tl.last_updated_time and tl.last_updated_time.year > 1700:
                now = dt.datetime.now(dt.timezone.utc)
                pos += max(0.0, (now - tl.last_updated_time).total_seconds())
            if dur > 0:
                # only trust the reported position if it disagrees noticeably
                if abs(pos - self.position) > 1.5 or not playing or title != self.title:
                    self._pos, self._pos_at = pos, time.monotonic()
                self.duration = dur
            else:
                self.duration = 0.0
        except Exception:
            self.duration = 0.0

        meta = (title, artist, album, app, playing, bool(title))
        if meta != (self.title, self.artist, self.album, self.app, self.playing, self.available):
            if not playing and self.playing:
                self._pos, self._pos_at = self.position, time.monotonic()
            self.title, self.artist, self.album, self.app = title, artist, album, app
            self.playing, self.available = playing, bool(title)
            self.changed.emit()

        key = (title, artist)
        if key != self._art_key:
            self._art_key = key
            await self._load_art(p)
        if key != self._lyrics_key and title:
            self._lyrics_key = key
            self.lyrics = []
            self.lyrics_changed.emit()
            if self.settings["lyrics"]:
                threading.Thread(target=self._fetch_lyrics,
                                 args=(title, artist, album, self.duration, key),
                                 daemon=True).start()

    async def _load_art(self, props):
        img = QImage()
        try:
            if props.thumbnail:
                stream = await props.thumbnail.open_read_async()
                size = int(stream.size)
                buf = Buffer(size)
                await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
                reader = DataReader.from_buffer(buf)
                data = bytearray(buf.length)
                reader.read_bytes(data)
                img.loadFromData(bytes(data))
        except Exception:
            img = QImage()
        self.art = img
        self._small = None
        self.art_changed.emit()

    # ---------------------------------------------------------- lyrics
    def _fetch_lyrics(self, title, artist, album, duration, key):
        def get(url):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode("utf-8"))

        synced = None
        q = {"track_name": title, "artist_name": artist}
        try:
            params = dict(q, album_name=album)
            if duration:
                params["duration"] = int(round(duration))
            synced = get("https://lrclib.net/api/get?" + urllib.parse.urlencode(params)).get("syncedLyrics")
        except Exception:
            pass
        if not synced:
            try:
                for hit in get("https://lrclib.net/api/search?" + urllib.parse.urlencode(q))[:5]:
                    if hit.get("syncedLyrics"):
                        synced = hit["syncedLyrics"]
                        break
            except Exception:
                pass
        lines = []
        for raw in (synced or "").splitlines():
            m = LRC_LINE.match(raw.strip())
            if m:
                lines.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
        if key == self._lyrics_key:
            self.lyrics = lines
            try:
                self.lyrics_changed.emit()
            except RuntimeError:
                pass   # app is shutting down
