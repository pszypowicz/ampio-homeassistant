"""Diagnostics platform for the Ampio integration."""

from dataclasses import asdict
from typing import Any

from ampio_mqtt import (
    ModuleFunction,
    OutputKind,
    PanelLightSignal,
    PanelSettings,
    format_mac,
)

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .data import AmpioConfigEntry, AmpioData

TO_REDACT_ENTRY = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}
# The snapshot carries no credentials by the library's contract, but the
# server self-report inside it names the M-SERV's LAN address and serial,
# masked for the same reason the entry's host is. The raw ``info`` payload
# under ``last_payloads`` is one JSON string, and key-based redaction
# cannot reach inside a string, so a field the library's safelist does not
# cover would ride through whole. The parsed ``server_info`` carries the
# debugging value, so the string is masked here.
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

    The two lists answer to different account tiers, because their sources
    do. The module catalogue and the description-record sweep reach the
    reserved administrator login alone, so ``modules`` is empty on a
    standard account and every cover there reports None. Which objects are
    covers comes from the object catalogue both tiers receive, so the
    ``covers`` list itself reads the same on either one, and it is what
    answers why a roller became a switch.

    No redaction here. Colors, flags, timings, function ids, channel
    counts, an override mac, and a module or object ``id`` fill this
    section, and none of them is masked. A module's ``id`` here is the
    same local Designer row number the ``snapshot`` block already
    carries unredacted in its own ``modules`` list, and the mac beside
    it is the same bus address that list carries. A cover's ``id`` is a
    Designer object id instead, carrying no name of its own, so leaving
    it unmasked reveals nothing docs/debugging.md promises to hide.
    Neither the record's ``desc`` nor its ``location`` is read: those
    are the installer's own module name and mounting note, which
    docs/debugging.md promises the download leaves behind.

    ``AmpioData.module_row_for()`` is the codebase-wide route for a
    module-catalogue read, but it answers for one mac, and this section
    wants the whole catalogue, so it reads the narrowed admin client.

    Both lists are sorted by id, so two downloads from one install differ
    only where the install did.
    """
    admin = data.admin
    modules: list[dict[str, Any]] = []
    if admin is not None:
        # The sweep datasets key on the module's override mac, which is
        # also what the module device's registry identifier carries, so the
        # entry names the mac it was gathered under.
        for module in sorted(admin.modules.values(), key=lambda module: module.id):
            settings = admin.panel_settings.get(module.mac)
            modules.append(
                {
                    "id": module.id,
                    "mac": format_mac(module.mac),
                    "capabilities": {
                        _capability_name(function_id): count
                        for function_id, count in admin.capabilities.get(
                            module.mac, {}
                        ).items()
                    },
                    "panel_settings": (
                        None if settings is None else _panel_settings(settings)
                    ),
                }
            )
    # Every cover the catalogue admits is listed, with None for one no
    # sweep has proven, so a cover on an unproven board is not confused
    # with an object that is not a cover at all.
    covers: list[dict[str, Any]] = []
    for obj in sorted(data.client.objects.values(), key=lambda obj: obj.id):
        if not isinstance(obj.kind, OutputKind) or not obj.kind.cover:
            continue
        parameters = None if admin is None else admin.cover_parameters.get(obj.id)
        covers.append(
            {
                "id": obj.id,
                "cover_parameters": (
                    None if parameters is None else asdict(parameters)
                ),
            }
        )
    return {"modules": modules, "covers": covers}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmpioConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    snapshot = entry.runtime_data.client.diagnostics_snapshot()
    # The admission door reports each row it refused as ``(id, name)``, and
    # that name is the installer's own Designer name. docs/debugging.md
    # tells a user the download carries no object, room, or module name, so
    # the id survives and the name is masked. Home Assistant's key-based
    # redaction cannot reach a value inside a list, so the pair is rewritten
    # before the snapshot is handed to it.
    snapshot["not_configured"] = [
        [object_id, REDACTED] for object_id, _name in snapshot["not_configured"]
    ]
    # Two connection entries key on an MQTT topic, and an account topic
    # carries the username in the middle of the key, where a key-based
    # redactor cannot reach it. ampio-mqtt masks that segment itself, so
    # nothing here rewrites a key.
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT_ENTRY),
        "snapshot": async_redact_data(snapshot, TO_REDACT_SNAPSHOT),
        "designer_config": _designer_config(entry.runtime_data),
    }
