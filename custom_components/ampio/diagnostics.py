"""Diagnostics platform for the Ampio integration."""

from dataclasses import asdict
from typing import Any

from ampio_mqtt import ModuleFunction, OutputKind, PanelLightSignal, PanelSettings

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .data import AmpioConfigEntry, AmpioData, eligible_objects

TO_REDACT_ENTRY = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}
# The snapshot carries no credentials by the library's contract, but the
# server self-report inside it names the M-SERV's LAN address and serial,
# masked for the same reason the entry's host is. The raw ``info`` entry
# under ``last_payloads`` embeds the same facts plus the account's street
# address, GPS coordinates, cloud endpoint, and public key inside one
# string, where key-based redaction cannot reach - so that payload is
# masked wholesale; the parsed ``server_info`` keeps the debugging value.
TO_REDACT_SNAPSHOT = {"local_ip", "device_id", "info"}


def _capability_name(function_id: int) -> str:
    """The ``ModuleFunction`` name for a capability id, or the id itself as a string."""
    try:
        return ModuleFunction(function_id).name
    except ValueError:
        return str(function_id)


def _light_signal_name(value: int) -> str:
    """The ``PanelLightSignal`` name for a light-signal value, or the value as a string."""
    try:
        return PanelLightSignal(value).name
    except ValueError:
        return str(value)


def _panel_settings(settings: PanelSettings) -> dict[str, Any]:
    """A panel's stored settings, with ``light_signal`` named the way a capability id is."""
    data = asdict(settings)
    data["light_signal"] = [
        _light_signal_name(value) for value in settings.light_signal
    ]
    return data


def _designer_config(data: AmpioData) -> dict[str, Any]:
    """The Designer configuration the entities read: modules and cover travel.

    No redaction here. Colors, flags, timings, function ids, channel counts,
    and a module or object ``id`` fill this section, and none of them is
    masked: an ``id`` here is the same local Designer row number the
    ``snapshot`` block above already carries unredacted in its own
    ``modules`` list, not a global identifier or an address.

    ``client.modules`` raises on a standard account, because the M-SERV
    serves the module catalogue to the reserved admin login alone.
    ``AmpioData.module_row()`` is the codebase-wide route for a
    module-catalogue read, but it answers for one row, and this section
    wants the whole catalogue, so it reads through ``is_admin`` directly
    instead.
    """
    modules: list[dict[str, Any]] = []
    if data.is_admin:
        modules = [
            {
                "id": module.id,
                "capabilities": {
                    _capability_name(function_id): count
                    for function_id, count in module.capabilities.items()
                },
                "panel_settings": (
                    _panel_settings(module.panel_settings)
                    if module.panel_settings is not None
                    else None
                ),
            }
            for module in sorted(
                data.client.modules.values(), key=lambda module: module.id
            )
        ]
    # Every eligible cover is listed, with None for one the sweep has not
    # proven, so a cover on an unproven board is not confused with an
    # object that is not a cover at all. This needs no tier gate of its
    # own: an object's kind is tier-shared, so both accounts list the same
    # covers, and ``cover_parameters`` is filled by the administrator sweep
    # alone, so a standard account simply reads None on every one of them.
    covers = [
        {
            "id": obj.id,
            "cover_parameters": (
                asdict(obj.cover_parameters)
                if obj.cover_parameters is not None
                else None
            ),
        }
        for obj in eligible_objects(data.client)
        if isinstance(obj.kind, OutputKind) and obj.kind.cover
    ]
    return {"modules": modules, "covers": covers}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmpioConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT_ENTRY),
        "snapshot": async_redact_data(
            entry.runtime_data.client.diagnostics_snapshot(), TO_REDACT_SNAPSHOT
        ),
        "designer_config": _designer_config(entry.runtime_data),
    }
