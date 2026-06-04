"""Diagnostics support for the Ampio integration."""

from collections import Counter
from dataclasses import asdict
from typing import Any

from ampio_mqtt import AmpioModule, AmpioObject

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import AmpioConfigEntry

# Credentials, the connection host, and the DHCP-derived Ethernet MAC are
# stripped from any payload shared in a bug report. Inside server_info the
# local LAN IP is also redacted; the CAN MAC (mac, mac_global), device_id,
# and firmware versions are left intact because a maintainer needs them to
# diagnose topology issues and they are not network-routable on their own.
ENTRY_REDACT = {CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_USERNAME}
SERVER_INFO_REDACT = {"local_ip"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmpioConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the integration's config entry."""
    client = entry.runtime_data.client
    server_info = (
        async_redact_data(asdict(client.server_info), SERVER_INFO_REDACT)
        if client.server_info is not None
        else None
    )
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), ENTRY_REDACT),
            "has_unique_id": entry.unique_id is not None,
            "version": entry.version,
        },
        "available": client.available,
        "mserv_module_id": client.mserv_id,
        "server_info": server_info,
        "modules": [_module_summary(m) for m in client.modules.values()],
        "objects": _objects_overview(client.objects),
        "room_map": dict(entry.runtime_data.room_map),
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: AmpioConfigEntry, device: dr.DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a single Ampio module device."""
    client = entry.runtime_data.client
    module_id = _module_id_from_device(device)
    module = client.modules.get(module_id) if module_id is not None else None
    return {
        "module": _module_summary(module) if module else None,
        "objects": [
            _object_summary(o)
            for o in client.objects.values()
            if o.device_id == module_id
        ],
    }


def _module_summary(module: AmpioModule) -> dict[str, Any]:
    """Public-safe snapshot of a module."""
    return {
        "id": module.id,
        "name": module.name,
        "type": module.type,
        "model": module.model,
        "capabilities": sorted(c.value for c in module.capabilities),
        "sw_version": module.sw_version,
        "hw_version": module.hw_version,
        "mac": module.mac,
        "mac_global": module.mac_global,
        "last_seen": module.last_seen,
    }


def _object_summary(obj: AmpioObject) -> dict[str, Any]:
    """Metadata for one DB object. Deliberately omits live value/state."""
    return {
        "id": obj.id,
        "device_id": obj.device_id,
        "typ_komponentu": obj.typ_komponentu,
        "interpretacja": obj.interpretacja,
        "kind": obj.kind.key if obj.kind else None,
        "has_name": bool(obj.name),
        "has_value": obj.value is not None,
    }


def _objects_overview(objects: dict[int, AmpioObject]) -> dict[str, Any]:
    """Aggregate counts so a reviewer can see what the install looks like."""
    by_typ = Counter(o.typ_komponentu for o in objects.values())
    classified = sum(1 for o in objects.values() if o.is_sensor)
    return {
        "total": len(objects),
        "classified_sensors": classified,
        "by_typ_komponentu": dict(by_typ),
    }


def _module_id_from_device(device: dr.DeviceEntry) -> int | None:
    """Extract the module id from a device entry's identifier tuple."""
    for domain, ident in device.identifiers:
        if domain == DOMAIN and ":" in ident:
            tail = ident.rsplit(":", 1)[1]
            if tail.isdigit():
                return int(tail)
    return None
