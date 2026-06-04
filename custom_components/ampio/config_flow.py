"""Config flow for the Ampio integration."""

from collections.abc import Mapping
import logging
from typing import Any, Self

from ampio_mqtt import AmpioAuthError, AmpioClient, AmpioConnectionError, discover
import voluptuous as vol

from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Default the host field to the well-known M-SERV mDNS hostname when nothing
# better is known. The library's `discover()` probes this address as the
# first strategy, so it doubles as a sensible fallback default.
_DEFAULT_HOST = "ampio.local"

# Trim the library's default 2s timeout so the config-flow form is not held
# open for noticeably long when no broker is reachable.
_DISCOVER_TIMEOUT = 1.5

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AmpioConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ampio."""

    def __init__(self) -> None:
        """Initialise the flow state."""
        # Set by `async_step_dhcp` and consumed by the credential step so a
        # discovery-triggered flow lands on the user form with the host
        # pre-populated.
        self._discovered_host: str | None = None
        # Ethernet MAC from DHCP discovery; stored on the entry so a later
        # renewal can update the host silently without a credential prompt.
        self._discovered_mac: str | None = None
        # Cached result of the library's LAN probe so a credential retry does
        # not re-pay the multicast wait on every form re-render.
        self._cached_default_host: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await AmpioClient.test_connection(
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except AmpioAuthError:
                errors["base"] = "invalid_auth"
            except AmpioConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if info.mac is None:
                    # The connection succeeded but the broker did not report
                    # its identity (typically a restricted account). Without a
                    # stable id we cannot detect duplicate setups; refuse the
                    # flow rather than fall back to the host IP.
                    errors["base"] = "no_server_info"
                else:
                    await self.async_set_unique_id(str(info.mac))
                    updates: dict[str, Any] = {CONF_HOST: user_input[CONF_HOST]}
                    if self._discovered_mac is not None:
                        # Backfill the Ethernet MAC onto an existing entry so
                        # future DHCP renewals can find it without prompting.
                        updates[CONF_MAC] = self._discovered_mac
                    self._abort_if_unique_id_configured(updates=updates)
                    data = user_input
                    if self._discovered_mac is not None:
                        data = {**user_input, CONF_MAC: self._discovered_mac}
                    return self.async_create_entry(
                        title=f"Ampio ({user_input[CONF_HOST]})", data=data
                    )

        default_host = self._discovered_host or await self._discover_default_host()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, {CONF_HOST: default_host}
            ),
            errors=errors,
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Handle DHCP-triggered discovery of an Ampio M-SERV.

        A renewal that matches a previously-configured entry (by its DHCP
        Ethernet MAC) just refreshes the stored host and reloads, without
        showing a credential card. New M-SERVs forward to the user step with
        the host pre-filled. A second concurrent DHCP discovery for the same
        device is deduplicated by ``is_matching`` on ``_discovered_mac``; the
        user step is what calls ``async_set_unique_id`` once, with the M-SERV
        CAN MAC, before the entry is created.
        """
        formatted_mac = format_mac(discovery_info.macaddress)
        self._discovered_mac = formatted_mac

        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_MAC) != formatted_mac:
                continue
            if entry.data.get(CONF_HOST) != discovery_info.ip:
                self.hass.config_entries.async_update_entry(
                    entry, data=entry.data | {CONF_HOST: discovery_info.ip}
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")

        if self.hass.config_entries.flow.async_has_matching_flow(self):
            return self.async_abort(reason="already_in_progress")

        self._discovered_host = discovery_info.ip
        self.context["title_placeholders"] = {
            "name": discovery_info.hostname or discovery_info.ip
        }
        return await self.async_step_user()

    def is_matching(self, other_flow: Self) -> bool:
        """Return True if another in-progress flow has the same Ethernet MAC."""
        return (
            other_flow._discovered_mac is not None
            and other_flow._discovered_mac == self._discovered_mac
        )

    async def _discover_default_host(self) -> str:
        """Best-effort: return the first reachable Ampio host, else the default."""
        if self._cached_default_host is not None:
            return self._cached_default_host
        aiozc = await zeroconf.async_get_async_instance(self.hass)
        try:
            results = await discover(timeout=_DISCOVER_TIMEOUT, zeroconf=aiozc)
        except OSError, TimeoutError:
            _LOGGER.debug("Ampio LAN discovery failed", exc_info=True)
            self._cached_default_host = _DEFAULT_HOST
            return self._cached_default_host
        self._cached_default_host = results[0].host if results else _DEFAULT_HOST
        return self._cached_default_host

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Trigger reauth when stored credentials no longer work."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user for new credentials and update the existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await AmpioClient.test_connection(
                    entry.data[CONF_HOST],
                    entry.data[CONF_PORT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except AmpioAuthError:
                errors["base"] = "invalid_auth"
            except AmpioConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if info.mac is None:
                    errors["base"] = "no_server_info"
                else:
                    await self.async_set_unique_id(str(info.mac))
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry, data_updates=user_input
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_REAUTH_DATA_SCHEMA,
                {CONF_USERNAME: entry.data[CONF_USERNAME]},
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change host/port (or any field) on an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await AmpioClient.test_connection(
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except AmpioAuthError:
                errors["base"] = "invalid_auth"
            except AmpioConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if info.mac is None:
                    errors["base"] = "no_server_info"
                else:
                    await self.async_set_unique_id(str(info.mac))
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry, data_updates=user_input
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                {
                    CONF_HOST: entry.data[CONF_HOST],
                    CONF_PORT: entry.data[CONF_PORT],
                    CONF_USERNAME: entry.data[CONF_USERNAME],
                },
            ),
            errors=errors,
        )
