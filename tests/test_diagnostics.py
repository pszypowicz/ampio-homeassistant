"""Tests for the Ampio diagnostics platform."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from homeassistant.const import CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import USER_INPUT
from .diagnostics import get_diagnostics_for_config_entry, get_diagnostics_for_device

REDACTED = "**REDACTED**"
_ETHERNET_MAC = "b8:27:eb:b2:83:df"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_config_entry_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the integration-wide diagnostics payload."""
    await _setup(hass, mock_config_entry)
    data = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)

    # Spot-check redactions so a regression has a clear failure message;
    # the rest of the payload shape is covered by the snapshot.
    assert data["entry"]["data"][CONF_PASSWORD] == REDACTED
    assert data["server_info"]["local_ip"] == REDACTED
    assert data["server_info"]["mac"] == 47846
    # JSON snapshot keys are strings; coordinator.room_map keys are int.
    assert data["room_map"]["36"] == "Salon"
    assert data == snapshot


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_device_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot per-device diagnostics for a known module."""
    await _setup(hass, mock_config_entry)
    device = device_registry.async_get_device(
        identifiers={("ampio", f"{mock_config_entry.unique_id}:17")}
    )
    assert device is not None
    data = await get_diagnostics_for_device(
        hass, hass_client, mock_config_entry, device
    )
    assert data == snapshot


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_config_entry_diagnostics_redacts_ethernet_mac(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """An entry that stores the DHCP-derived MAC has it redacted in diagnostics."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ampio (ampio.test)",
        data={**USER_INPUT, CONF_MAC: _ETHERNET_MAC},
        unique_id="47846",
    )
    await _setup(hass, entry)
    data = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert data["entry"]["data"][CONF_MAC] == REDACTED


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_device_diagnostics_for_unknown_device(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the null-shape diagnostics for a hub-fallback device."""
    await _setup(hass, mock_config_entry)
    hub = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={("ampio", f"{mock_config_entry.unique_id}:hub")},
    )
    data = await get_diagnostics_for_device(hass, hass_client, mock_config_entry, hub)
    assert data == snapshot
