"""Tests for the Ampio sensor platform."""

from datetime import UTC, datetime
import json

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from homeassistant.const import CONF_MAC, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC

from .conftest import DEFAULT_DETAILS, DEFAULT_DEVICES, USER_INPUT, FakeMqttBroker


def _prefix(entry: MockConfigEntry) -> str:
    return entry.unique_id or entry.entry_id


def _last_seen_entity_id(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry, module_id: int
) -> str | None:
    return entity_registry.async_get_entity_id(
        Platform.SENSOR,
        "ampio",
        f"{_prefix(entry)}_module_{module_id}_last_seen",
    )


def _entity_id(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry, object_id: int
) -> str | None:
    return entity_registry.async_get_entity_id(
        Platform.SENSOR, "ampio", f"{_prefix(entry)}_obj_{object_id}"
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_all_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every entity's registry entry and state."""
    await _setup(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_devices(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every device registry entry the integration creates."""
    await _setup(hass, mock_config_entry)
    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert devices
    for device in devices:
        assert device == snapshot(name=f"device-{device.name}")


async def test_module_with_mixed_rooms_has_no_suggested_area(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiomqtt: FakeMqttBroker,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A module whose objects span multiple rooms is left without an area hint."""
    mock_aiomqtt.groups = [
        {"id": 1, "opis_menu": "Salon"},
        {"id": 2, "opis_menu": "Kuchnia"},
    ]
    mock_aiomqtt.group_devices = [
        {"id_grupy": 1, "id_obiektu": 36},
        {"id_grupy": 2, "id_obiektu": 37},
    ]
    await _setup(hass, mock_config_entry)

    device = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{_prefix(mock_config_entry)}:17")}
    )
    assert device is not None
    assert device.area_id is None


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_device_serial_number_uses_global_can_mac(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """serial_number is the global CAN id in hex, falling back to the local mac."""
    await _setup(hass, mock_config_entry)

    # M-SERV: local mac is 1, global is 47846 -> the global id must win.
    mserv = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{_prefix(mock_config_entry)}:1")}
    )
    assert mserv is not None
    assert mserv.serial_number == "0xBAE6"

    # m-sens salon has no global id in the fixture -> local mac 52111 is used.
    sens = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{_prefix(mock_config_entry)}:17")}
    )
    assert sens is not None
    assert sens.serial_number == "0xCB8F"


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_mserv_network_mac_connection(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A DHCP-known Ethernet MAC is wired into the M-SERV DeviceInfo connections."""
    ethernet_mac = "b8:27:eb:b2:83:df"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ampio (ampio.test)",
        data={**USER_INPUT, CONF_MAC: ethernet_mac},
        unique_id="47846",
    )
    await _setup(hass, entry)

    mserv = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{_prefix(entry)}:1")}
    )
    assert mserv is not None
    assert (CONNECTION_NETWORK_MAC, ethernet_mac) in mserv.connections


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_only_real_sensors_created(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Phantom (no value, no name) objects do not produce sensor entities."""
    await _setup(hass, mock_config_entry)

    for oid in (36, 37, 43):
        assert _entity_id(entity_registry, mock_config_entry, oid) is not None
    assert _entity_id(entity_registry, mock_config_entry, 99) is None


async def test_push_update_changes_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiomqtt: FakeMqttBroker,
    entity_registry: er.EntityRegistry,
) -> None:
    """A pushed object update is reflected in the entity state."""
    await _setup(hass, mock_config_entry)
    entity_id = _entity_id(entity_registry, mock_config_entry, 36)
    assert entity_id is not None

    mock_aiomqtt.push_state(36, "25.5")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "25.5"


async def test_unknown_kind_is_skipped(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiomqtt: FakeMqttBroker,
    entity_registry: er.EntityRegistry,
) -> None:
    """Objects classified into a kind without a static description are skipped."""
    mock_aiomqtt.details.append(
        {
            "id": 400,
            "id_urzadzenia": 17,
            "opis_menu": "Status",
            "stan_json": json.dumps({"state": "armed"}),
        }
    )
    await _setup(hass, mock_config_entry)

    assert _entity_id(entity_registry, mock_config_entry, 400) is None


async def test_fallback_device_for_object_without_module(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiomqtt: FakeMqttBroker,
    device_registry: dr.DeviceRegistry,
) -> None:
    """An object with no module id is attached to a generic 'Ampio' hub device."""
    mock_aiomqtt.details.append(
        {
            "id": 300,
            "typ_komponentu": "temp",
            "interpretacja": 1,
            "opis_menu": "Orphan",
            "stan_json": json.dumps({"state": "1"}),
        }
    )
    await _setup(hass, mock_config_entry)

    device = device_registry.async_get_device(
        identifiers={("ampio", f"{_prefix(mock_config_entry)}:hub")}
    )
    assert device is not None
    assert device.name == "Ampio"


@pytest.mark.usefixtures("mock_aiomqtt")
async def test_module_last_seen_seeded_value(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The Last seen state reflects the module's seeded last_seen timestamp."""
    await _setup(hass, mock_config_entry)

    entity_id = _last_seen_entity_id(entity_registry, mock_config_entry, 17)
    assert entity_id is not None
    expected = datetime.fromtimestamp(1779565263.0, tz=UTC).isoformat()
    assert hass.states.get(entity_id).state == expected

    other_id = _last_seen_entity_id(entity_registry, mock_config_entry, 3)
    assert other_id is not None
    assert hass.states.get(other_id).state == "unknown"


async def test_module_last_seen_updates_on_state_push(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiomqtt: FakeMqttBroker,
    entity_registry: er.EntityRegistry,
) -> None:
    """A state push with an ``on`` timestamp advances the module's Last seen."""
    await _setup(hass, mock_config_entry)
    entity_id = _last_seen_entity_id(entity_registry, mock_config_entry, 17)
    assert entity_id is not None

    new_ts = 1779565999.0
    mock_aiomqtt.push_state(36, "24.4", on_ms=int(new_ts * 1000))
    await hass.async_block_till_done()

    expected = datetime.fromtimestamp(new_ts, tz=UTC).isoformat()
    assert hass.states.get(entity_id).state == expected


async def test_dynamic_discovery_adds_entities_post_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiomqtt: FakeMqttBroker,
    entity_registry: er.EntityRegistry,
) -> None:
    """A module and object that appear after setup are discovered and added."""
    await _setup(hass, mock_config_entry)

    mock_aiomqtt.push_devices(
        [
            *DEFAULT_DEVICES,
            {
                "id": 42,
                "mac": 999,
                "typ_urzadzenia": 44,
                "nazwa_urzadzenia": "m-extra",
            },
        ]
    )
    mock_aiomqtt.push_details(
        [
            *DEFAULT_DETAILS,
            {
                "id": 500,
                "id_urzadzenia": 42,
                "typ_komponentu": "temp",
                "interpretacja": 1,
                "opis_menu": "Late temp",
                "stan_json": json.dumps({"state": "20.5"}),
            },
        ]
    )
    await hass.async_block_till_done()

    assert _entity_id(entity_registry, mock_config_entry, 500) is not None
    assert _last_seen_entity_id(entity_registry, mock_config_entry, 42) is not None


async def test_fallback_device_name_without_module_metadata(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiomqtt: FakeMqttBroker,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A module with no name entry falls back to 'Ampio module <id>'."""
    mock_aiomqtt.devices = [
        # Keep the M-SERV so module 17 is not auto-promoted to M-SERV.
        d
        for d in mock_aiomqtt.devices
        if d["id"] != 17
    ]
    mock_aiomqtt.devices.append({"id": 17})
    # Module 17 has no metadata, so the temperature/humidity objects can't
    # populate sw_version etc. - their objects still exist in details.
    await _setup(hass, mock_config_entry)

    device = device_registry.async_get_device(
        identifiers={("ampio", f"{_prefix(mock_config_entry)}:17")}
    )
    assert device is not None
    assert device.name == "Ampio module 17"
    assert device.sw_version is None
