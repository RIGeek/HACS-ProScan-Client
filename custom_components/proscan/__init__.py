"""ProScan Client: live scanner display and audio stream from a ProScan web server."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ProScanDisplay, ProScanError, ProScanInfo, ProScanServer
from .const import (
    CONF_IDLE_TIMEOUT,
    CONF_MONITOR_PASSWORD,
    CONF_STREAM_NAME,
    CONF_STREAM_PASSWORD,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_STREAM_NAME,
)

PLATFORMS: list[Platform] = [Platform.CAMERA]


@dataclass(slots=True)
class ProScanData:
    """Runtime data for a config entry."""

    server: ProScanServer
    display: ProScanDisplay
    info: ProScanInfo


type ProScanConfigEntry = ConfigEntry[ProScanData]


def build_server(hass: HomeAssistant, settings: dict) -> ProScanServer:
    """Create a ProScanServer from entry data/options."""
    session = async_get_clientsession(
        hass, verify_ssl=settings.get(CONF_VERIFY_SSL, True)
    )
    return ProScanServer(
        session,
        settings[CONF_URL],
        settings.get(CONF_STREAM_NAME) or DEFAULT_STREAM_NAME,
        settings.get(CONF_STREAM_PASSWORD),
        settings.get(CONF_MONITOR_PASSWORD),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ProScanConfigEntry) -> bool:
    """Set up ProScan from a config entry."""
    settings = {**entry.data, **entry.options}
    server = build_server(hass, settings)
    try:
        info = await server.async_get_info()
    except ProScanError as err:
        raise ConfigEntryNotReady(
            f"Cannot reach ProScan at {server.base_url}: {err}"
        ) from err

    display = ProScanDisplay(
        hass, server, settings.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)
    )
    entry.runtime_data = ProScanData(server, display, info)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ProScanConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.display.async_stop()
    return unloaded


async def _async_update_listener(
    hass: HomeAssistant, entry: ProScanConfigEntry
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
