"""Diagnostics test helpers.

Vendored from Home Assistant core's ``tests/components/diagnostics`` because
``pytest-homeassistant-custom-component`` does not re-export it. Keep in sync
with core; the only change from upstream is the ``tests.typing`` import, which
becomes ``pytest_homeassistant_custom_component.typing`` in this layout.
"""

from http import HTTPStatus
from typing import cast

from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.diagnostics import DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.setup import async_setup_component
from homeassistant.util.json import JsonObjectType


async def _get_diagnostics_for_config_entry(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: ConfigEntry,
) -> JsonObjectType:
    """Return the diagnostics config entry for the specified domain."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    client = await hass_client()
    response = await client.get(
        f"/api/diagnostics/config_entry/{config_entry.entry_id}"
    )
    assert response.status == HTTPStatus.OK
    return cast(JsonObjectType, await response.json())


async def get_diagnostics_for_config_entry(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: ConfigEntry,
) -> JsonObjectType:
    """Return the diagnostics config entry for the specified domain."""
    data = await _get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    return cast(JsonObjectType, data["data"])


async def _get_diagnostics_for_device(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: ConfigEntry,
    device: DeviceEntry,
) -> JsonObjectType:
    """Return the diagnostics for the specified device."""
    assert await async_setup_component(hass, DOMAIN, {})

    client = await hass_client()
    response = await client.get(
        f"/api/diagnostics/config_entry/{config_entry.entry_id}/device/{device.id}"
    )
    assert response.status == HTTPStatus.OK
    return cast(JsonObjectType, await response.json())


async def get_diagnostics_for_device(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: ConfigEntry,
    device: DeviceEntry,
) -> JsonObjectType:
    """Return the diagnostics for the specified device."""
    data = await _get_diagnostics_for_device(hass, hass_client, config_entry, device)
    return cast(JsonObjectType, data["data"])
