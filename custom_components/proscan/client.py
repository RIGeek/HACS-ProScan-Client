"""Client for the ProScan web server.

ProScan's web page talks to the server over a WebSocket at the server root
(``ws(s)://host:port/``). Messages are comma-separated text; the first field
is the message type. The ones this client uses:

    D,<base64 PNG>   full frame of the scanner's LCD display
    B,<checksum>     "show the cached D frame with this checksum"
    R                server restarted
    K,...            keep-alive (ignored)

The checksum is the sum of the character codes of the base64 string.

Handshake (from ws_main.js): send ``M,<random id>,panel;`` to say the client
has a virtual scanner panel, then ``A,D`` to request the current display.

Logging in (only needed when a Monitor password is set) is an HTTP POST to the
server root with the form data carried in an ``x-custom-header`` header; the
server associates the access level with the client.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import logging
import random
import re
import time
from urllib.parse import quote, urlsplit

import aiohttp

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .const import FIRST_FRAME_TIMEOUT, FRAME_CACHE_SIZE, MAX_FAILURES

_LOGGER = logging.getLogger(__name__)

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)
_WS_MAIN_RE = re.compile(r"ws_main\.js/([^\"'?]+)")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class ProScanError(Exception):
    """Base error talking to ProScan."""


class ProScanConnectionError(ProScanError):
    """The server could not be reached or is not a ProScan web server."""


class ProScanAuthError(ProScanError):
    """The server rejected the password."""


@dataclass(slots=True)
class ProScanInfo:
    """What the server's home page tells us about it."""

    server_version: str | None
    scanner_type: str | None
    page_title: str | None


def normalize_url(url: str) -> str:
    """Return the server URL as scheme://host[:port] with no trailing slash."""
    url = url.strip()
    if "://" not in url:
        url = f"http://{url}"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"Not an http(s) URL: {url}")
    return f"{parts.scheme}://{parts.netloc}".lower()


class ProScanServer:
    """HTTP side of a ProScan web server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        stream_name: str,
        stream_password: str | None = None,
        monitor_password: str | None = None,
    ) -> None:
        """Initialize."""
        self._session = session
        self.base_url = normalize_url(url)
        self.stream_name = stream_name.strip().strip("/")
        self._stream_password = stream_password or None
        self._monitor_password = monitor_password or None

    @property
    def session(self) -> aiohttp.ClientSession:
        """The aiohttp session."""
        return self._session

    @property
    def ws_url(self) -> str:
        """WebSocket URL (same host and port, ws/wss scheme)."""
        return self.base_url.replace("http", "ws", 1) + "/"

    @property
    def stream_url(self) -> str:
        """MP3 stream URL, with the stream password appended if one is set."""
        url = f"{self.base_url}/{quote(self.stream_name)}"
        if self._stream_password:
            # ProScan uses "+" for spaces in the stream password.
            url += "+" + quote(self._stream_password.replace(" ", "+"), safe="+")
        return url

    @property
    def has_monitor_password(self) -> bool:
        """Whether a Monitor password is configured."""
        return self._monitor_password is not None

    async def async_get_info(self) -> ProScanInfo:
        """Fetch the home page and confirm this is a ProScan web server."""
        try:
            async with self._session.get(
                self.base_url + "/", timeout=_HTTP_TIMEOUT
            ) as resp:
                server = resp.headers.get("Server", "")
                text = await resp.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ProScanConnectionError(str(err)) from err

        if "proscan" not in server.lower() and "proscan" not in text.lower():
            raise ProScanConnectionError("Server did not identify as ProScan")

        version = server.split(" ", 1)[1] if server.lower().startswith("proscan ") else None
        scanner_type = None
        if match := _WS_MAIN_RE.search(text):
            fields = match.group(1).split(",")
            if len(fields) > 2 and fields[2]:
                scanner_type = fields[2]
        title = None
        if match := _TITLE_RE.search(text):
            title = match.group(1).strip() or None
        return ProScanInfo(version, scanner_type, title)

    async def async_login(self) -> None:
        """Log in with the Monitor password, if one is configured."""
        if not self._monitor_password:
            return
        header = f"password={self._monitor_password}&login=Log In"
        try:
            async with self._session.post(
                self.base_url + "/",
                headers={"x-custom-header": header},
                timeout=_HTTP_TIMEOUT,
            ) as resp:
                text = await resp.text(errors="replace")
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ProScanConnectionError(str(err)) from err
        if status != 200:
            raise ProScanConnectionError(f"Login returned HTTP {status}")
        if "incorrect password" in text.lower():
            raise ProScanAuthError("Incorrect password")


class ProScanDisplay:
    """On-demand WebSocket client that keeps the latest LCD frame in memory.

    The socket is opened when someone asks for a frame and closed again once
    nobody has asked for ``idle_timeout`` seconds, so the server only spends
    bandwidth on the display while a dashboard is actually showing it.
    Frames are never written to Home Assistant state.
    """

    def __init__(
        self, hass: HomeAssistant, server: ProScanServer, idle_timeout: float
    ) -> None:
        """Initialize."""
        self._hass = hass
        self._server = server
        self._idle_timeout = idle_timeout
        self._task: asyncio.Task[None] | None = None
        self._last_touch = 0.0
        self._frame: bytes | None = None
        self._frame_seq = 0
        self._frame_event = asyncio.Event()
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._connected = False
        self._failures = 0
        self._listeners: list[CALLBACK_TYPE] = []
        self._stopping = False

    @property
    def connected(self) -> bool:
        """Whether the WebSocket is open."""
        return self._connected

    @property
    def available(self) -> bool:
        """False after several connection attempts in a row have failed."""
        return self._failures < MAX_FAILURES

    @property
    def frame_seq(self) -> int:
        """Increments every time a new frame is shown."""
        return self._frame_seq

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> Callable[[], None]:
        """Call ``update`` when connected/available changes."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def touch(self) -> None:
        """Mark the display as being watched and make sure the socket runs."""
        self._last_touch = time.monotonic()
        if self._stopping:
            return
        if self._task is None or self._task.done():
            # Anything cached from a previous session is stale.
            self._frame = None
            self._cache.clear()
            self._task = self._hass.async_create_background_task(
                self._run(), name=f"proscan display {self._server.base_url}"
            )

    def _idle(self) -> bool:
        return time.monotonic() - self._last_touch > self._idle_timeout

    async def async_get_frame(
        self, timeout: float = FIRST_FRAME_TIMEOUT
    ) -> bytes | None:
        """Return the current frame as PNG bytes, waiting briefly for the first one."""
        self.touch()
        if self._frame is None:
            event = self._frame_event
            try:
                async with asyncio.timeout(timeout):
                    await event.wait()
            except TimeoutError:
                pass
        return self._frame

    async def async_stop(self) -> None:
        """Close the socket and stop."""
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _set_frame(self, png: bytes) -> None:
        self._frame = png
        self._frame_seq += 1
        event, self._frame_event = self._frame_event, asyncio.Event()
        event.set()

    def _set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            self._notify()

    def _record_result(self, ok: bool) -> None:
        was_available = self.available
        self._failures = 0 if ok else self._failures + 1
        if was_available != self.available:
            self._notify()

    async def _run(self) -> None:
        backoff = 1.0
        try:
            while not self._idle():
                try:
                    await self._server.async_login()
                    await self._session_loop()
                    backoff = 1.0
                except ProScanAuthError as err:
                    _LOGGER.error("ProScan rejected the Monitor password: %s", err)
                    self._record_result(False)
                except (
                    ProScanConnectionError,
                    aiohttp.ClientError,
                    TimeoutError,
                    OSError,
                ) as err:
                    _LOGGER.debug("ProScan display connection failed: %s", err)
                    self._record_result(False)
                finally:
                    self._set_connected(False)
                if self._idle():
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        finally:
            self._set_connected(False)

    async def _session_loop(self) -> None:
        async with self._server.session.ws_connect(
            self._server.ws_url,
            heartbeat=30,
            max_msg_size=0,
            timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=10),
        ) as ws:
            instance = random.randint(1, 99000)
            await ws.send_str(f"M,{instance},panel;")
            await ws.send_str("A,D")
            self._set_connected(True)
            self._record_result(True)
            _LOGGER.debug("ProScan display connected to %s", self._server.ws_url)

            while True:
                if self._idle():
                    _LOGGER.debug("ProScan display idle, closing socket")
                    return
                try:
                    msg = await ws.receive(timeout=5)
                except TimeoutError:
                    continue
                if msg.type is aiohttp.WSMsgType.TEXT:
                    reply = self._handle_message(msg.data)
                    if reply == "restart":
                        return
                    if reply:
                        await ws.send_str(reply)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    _LOGGER.debug("ProScan display socket closed: %s", msg)
                    return

    def _handle_message(self, data: str) -> str | None:
        """Handle one message; return a reply to send, "restart", or None."""
        kind, _, payload = data.partition(",")
        if kind == "D":
            try:
                png = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError):
                _LOGGER.debug("Ignoring undecodable display frame")
                return None
            checksum = sum(payload.encode("ascii"))
            self._cache[checksum] = png
            self._cache.move_to_end(checksum)
            while len(self._cache) > FRAME_CACHE_SIZE:
                self._cache.popitem(last=False)
            self._set_frame(png)
        elif kind == "B":
            try:
                checksum = int(payload.strip())
            except ValueError:
                return None
            png = self._cache.get(checksum)
            if png is None:
                return "A,D"  # ask the server to resend the full frame
            self._cache.move_to_end(checksum)
            if png is not self._frame:
                self._set_frame(png)
        elif kind == "R":
            # Server restarted; reconnect (and log in again if needed).
            return "restart"
        return None
