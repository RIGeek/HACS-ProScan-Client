"""Camera entity showing the scanner's live LCD display."""

from __future__ import annotations

from io import BytesIO
import logging

from aiohttp import web

from homeassistant.components.camera import Camera, async_get_still_stream
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProScanConfigEntry
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow ships with Home Assistant
    Image = None


def _png_to_jpeg(png: bytes) -> bytes:
    """Convert a display frame to JPEG for the live MJPEG view."""
    with Image.open(BytesIO(png)) as img:
        out = BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProScanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the display camera."""
    async_add_entities([ProScanDisplayCamera(entry)])


class ProScanDisplayCamera(Camera):
    """The scanner's LCD, as served by ProScan's virtual display."""

    _attr_has_entity_name = True
    _attr_translation_key = "display"
    _attr_frame_interval = 0.25

    def __init__(self, entry: ProScanConfigEntry) -> None:
        """Initialize."""
        super().__init__()
        data = entry.runtime_data
        self._display = data.display
        self.content_type = "image/png"
        self._attr_unique_id = f"{entry.entry_id}_display"
        model = (
            f"{data.info.scanner_type} via ProScan"
            if data.info.scanner_type
            else "ProScan web server"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ProScan",
            model=model,
            sw_version=data.info.server_version,
            configuration_url=data.server.base_url,
        )
        self._jpeg: bytes | None = None
        self._jpeg_seq = -1

    async def async_added_to_hass(self) -> None:
        """Track connection changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._display.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        """Unavailable after repeated connection failures."""
        return self._display.available

    @property
    def is_streaming(self) -> bool:
        """True while the display socket is open (someone is watching)."""
        return self._display.connected

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current display frame (PNG)."""
        return await self._display.async_get_frame()

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Live view: push each new display frame as it arrives."""
        if Image is None:
            return await super().handle_async_mjpeg_stream(request)
        return await async_get_still_stream(
            request, self._async_jpeg_frame, "image/jpeg", self.frame_interval
        )

    async def _async_jpeg_frame(self) -> bytes | None:
        png = await self._display.async_get_frame()
        if png is None:
            return None
        seq = self._display.frame_seq
        if seq != self._jpeg_seq:
            try:
                self._jpeg = await self.hass.async_add_executor_job(_png_to_jpeg, png)
            except OSError as err:
                _LOGGER.debug("Could not convert display frame: %s", err)
                return self._jpeg
            self._jpeg_seq = seq
        return self._jpeg
