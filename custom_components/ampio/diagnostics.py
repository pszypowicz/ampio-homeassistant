"""Diagnostics platform for the Ampio integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .data import AmpioConfigEntry

TO_REDACT_ENTRY = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}
# The snapshot carries no credentials by the library's contract, but the
# server self-report inside it names the M-SERV's LAN address and serial,
# masked for the same reason the entry's host is. The raw ``info`` entry
# under ``last_payloads`` embeds the same facts plus the account's street
# address, GPS coordinates, cloud endpoint, and public key inside one
# string, where key-based redaction cannot reach - so that payload is
# masked wholesale; the parsed ``server_info`` keeps the debugging value.
TO_REDACT_SNAPSHOT = {"local_ip", "device_id", "info"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmpioConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT_ENTRY),
        "snapshot": async_redact_data(
            entry.runtime_data.client.diagnostics_snapshot(), TO_REDACT_SNAPSHOT
        ),
    }
