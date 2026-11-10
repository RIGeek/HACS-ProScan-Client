"""Media source exposing each ProScan server's audio stream."""

from __future__ import annotations

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import ProScanConfigEntry
from .const import DOMAIN

STREAM_MIME = "audio/mpeg"


async def async_get_media_source(hass: HomeAssistant) -> ProScanMediaSource:
    """Set up the ProScan media source."""
    return ProScanMediaSource(hass)


class ProScanMediaSource(MediaSource):
    """Lists the scanner streams in the media browser."""

    name = "ProScan"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _entries(self) -> list[ProScanConfigEntry]:
        return [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]

    def _entry(self, entry_id: str | None) -> ProScanConfigEntry:
        for entry in self._entries():
            if entry.entry_id == entry_id:
                return entry
        raise Unresolvable(f"Unknown ProScan stream: {entry_id}")

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Return the stream URL."""
        entry = self._entry(item.identifier)
        return PlayMedia(entry.runtime_data.server.stream_url, STREAM_MIME)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the configured streams."""
        if item.identifier:
            return self._stream_item(self._entry(item.identifier))
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.CHANNELS,
            title=self.name,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.CHANNEL,
            children=[self._stream_item(entry) for entry in self._entries()],
        )

    def _stream_item(self, entry: ProScanConfigEntry) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=entry.entry_id,
            media_class=MediaClass.CHANNEL,
            media_content_type=STREAM_MIME,
            title=entry.title,
            can_play=True,
            can_expand=False,
        )
