"""Config flow for ProScan Client."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import build_server
from .client import ProScanAuthError, ProScanError, normalize_url
from .const import (
    CONF_IDLE_TIMEOUT,
    CONF_MONITOR_PASSWORD,
    CONF_STREAM_NAME,
    CONF_STREAM_PASSWORD,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_STREAM_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _user_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Required(
                CONF_STREAM_NAME,
                default=defaults.get(CONF_STREAM_NAME, DEFAULT_STREAM_NAME),
            ): str,
            vol.Optional(CONF_STREAM_PASSWORD): _PASSWORD,
            vol.Optional(CONF_MONITOR_PASSWORD): _PASSWORD,
            vol.Required(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)
            ): bool,
        }
    )


async def _validate(hass, data: dict[str, Any]) -> str:
    """Check the server and password. Return a default title."""
    server = build_server(hass, data)
    info = await server.async_get_info()
    await server.async_login()
    host = urlsplit(server.base_url).hostname or server.base_url
    if info.scanner_type:
        return f"{info.scanner_type} ({host})"
    return f"ProScan ({host})"


class ProScanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server URL and stream details."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if v not in ("", None)}
            try:
                data[CONF_URL] = normalize_url(data[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(data[CONF_URL])
                self._abort_if_unique_id_configured()
                try:
                    title = await _validate(self.hass, data)
                except ProScanAuthError:
                    errors[CONF_MONITOR_PASSWORD] = "invalid_auth"
                except ProScanError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error validating ProScan server")
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(title=title, data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options flow."""
        return ProScanOptionsFlow()


class ProScanOptionsFlow(OptionsFlow):
    """Tune stream name, passwords and the display idle timeout."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {k: v for k, v in user_input.items() if v not in ("", None)}
            # Keep a password that was set before unless the field was filled in.
            for key in (CONF_STREAM_PASSWORD, CONF_MONITOR_PASSWORD):
                if key not in options and current.get(key):
                    options[key] = current[key]
            try:
                await _validate(self.hass, {**current, **options})
            except ProScanAuthError:
                errors[CONF_MONITOR_PASSWORD] = "invalid_auth"
            except ProScanError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STREAM_NAME,
                    default=current.get(CONF_STREAM_NAME, DEFAULT_STREAM_NAME),
                ): str,
                vol.Optional(CONF_STREAM_PASSWORD): _PASSWORD,
                vol.Optional(CONF_MONITOR_PASSWORD): _PASSWORD,
                vol.Required(
                    CONF_IDLE_TIMEOUT,
                    default=current.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=3600,
                        step=5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
