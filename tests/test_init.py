"""Tests for the Ampio integration setup and teardown."""

import logging
from unittest.mock import MagicMock

from ampio_mqtt import (
    AmpioAuthError,
    AmpioConnectionError,
    AmpioTimeoutError,
    AuthFailed,
    AvailabilityChanged,
    ConnectionDied,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ampio import async_remove_config_entry_device
from custom_components.ampio.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from . import setup_integration
from .conftest import MSENS_FALLBACK_NAME, MSENS_IDENTIFIER, MSERV_MAC, USER_INPUT, emit


async def test_setup_and_unload(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads, and unloading stops the client."""
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.stop.assert_awaited_once()


async def test_shutdown_stops_client(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Home Assistant stopping closes the connection.

    Entries are not unloaded at shutdown, so the stop event is the only place
    the client is reached, and a connection left open there is torn down by
    task cancellation and reported as an outage.
    """
    await setup_integration(hass, mock_config_entry)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    mock_client.stop.assert_awaited_once()


@pytest.mark.parametrize(
    ("start_result", "expected_state"),
    [
        pytest.param(
            AmpioConnectionError("refused"),
            ConfigEntryState.SETUP_RETRY,
            id="connection-error",
        ),
        pytest.param(
            AmpioAuthError("denied"), ConfigEntryState.SETUP_ERROR, id="auth-error"
        ),
        # A discovery cycle that does not complete in time is retryable.
        pytest.param(False, ConfigEntryState.SETUP_RETRY, id="incomplete-discovery"),
    ],
)
async def test_setup_failure_stops_client(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    start_result: Exception | bool,
    expected_state: ConfigEntryState,
) -> None:
    """A failed start maps to the right entry state and stops the client."""
    mock_client.start.side_effect = [start_result]

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is expected_state
    mock_client.stop.assert_awaited_once()


@pytest.mark.usefixtures("mock_client")
async def test_setup_fails_on_server_identity_mismatch(hass: HomeAssistant) -> None:
    """A host now answering as a different M-SERV lands the entry in SETUP_ERROR.

    Proceeding would re-key every unique_id and device identifier under the
    new server's prefix, orphaning the existing registry entries.
    """
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="99999")

    await setup_integration(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "unexpected_device"


@pytest.mark.usefixtures("mock_client")
async def test_hub_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The hub device carries the server identity; module devices link to it."""
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, MSERV_MAC), mock_config_entry.entry_id
    )
    assert hub is not None

    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert module.via_device_id == hub.id


async def test_restricted_account_groups_by_module_mac(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Without the module catalogue, grouping still keys on the leaf-derived mac.

    A standard (non-administrator) account is served the object catalogue but
    no module list, so the module device carries a fallback name and no
    metadata while the entity-to-device mapping matches the admin tier.
    """
    mock_client.modules = {}
    mock_client.mserv = None

    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, MSERV_MAC), mock_config_entry.entry_id
    )
    assert hub is not None
    assert hub.name == "M-SERV"

    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert module.name == MSENS_FALLBACK_NAME
    assert module.model is None
    assert module.via_device_id == hub.id

    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    assert len(entities) == 22
    # Scenes and the server-owned flag live directly on the hub device
    # regardless of account tier. Sensor and input entities live directly on
    # their module device; every output/thermostat entity sits on its own
    # device, bound to the module through via_device_id.
    scene_entities = [entity for entity in entities if entity.domain == "scene"]
    assert len(scene_entities) == 1
    assert all(entity.device_id == hub.id for entity in scene_entities)

    hub_flag_entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{MSERV_MAC}_leaf_0_1_flaga_0_9"
    )
    assert hub_flag_entity_id is not None
    hub_flag_entity = entity_registry.async_get(hub_flag_entity_id)
    assert hub_flag_entity is not None
    assert hub_flag_entity.device_id == hub.id

    excluded_ids = {entity.entity_id for entity in scene_entities} | {
        hub_flag_entity_id
    }
    module_entities = [
        entity for entity in entities if entity.entity_id not in excluded_ids
    ]
    direct_domains = {"sensor", "binary_sensor"}
    for entity in module_entities:
        if entity.domain in direct_domains:
            assert entity.device_id == module.id
            continue
        device = device_registry.async_get(entity.device_id)
        assert device is not None
        assert device.via_device_id == module.id


async def test_tier_switch_keeps_device_grouping(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An entry keeps its devices across account-tier switches in both directions.

    Metadata enriches on an upgrade to admin; a downgrade back to restricted
    degrades the whole device coherently instead of mixing the fallback name
    with stale admin-era metadata.
    """
    admin_modules = mock_client.modules
    admin_mserv = mock_client.mserv
    mock_client.modules = {}
    mock_client.mserv = None

    await setup_integration(hass, mock_config_entry)
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    entity_devices = {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    }

    mock_client.modules = admin_modules
    mock_client.mserv = admin_mserv
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    enriched = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert enriched is not None
    assert enriched.id == module.id
    assert enriched.name == "m-sens salon"
    assert enriched.model == admin_modules[17].model
    assert {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    } == entity_devices

    mock_client.modules = {}
    mock_client.mserv = None
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    downgraded = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert downgraded is not None
    assert downgraded.id == module.id
    assert downgraded.name == MSENS_FALLBACK_NAME
    assert downgraded.model is None


async def test_runtime_auth_failure_reloads_into_auth_error(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A credential rejection after startup surfaces as an entry auth error.

    The library's reconnect loop stops for good on an unauthorized reconnect;
    the integration schedules a reload, whose setup then raises
    ConfigEntryAuthFailed and lands the entry in SETUP_ERROR.
    """
    await setup_integration(hass, mock_config_entry)
    mock_client.start.side_effect = AmpioAuthError("credentials changed")

    emit(mock_client, AuthFailed(reason="not authorized"))
    await hass.async_block_till_done()

    assert "reloading" in caplog.text
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.error_reason_translation_key == "invalid_auth"


async def test_connection_died_reloads_and_recovers(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A terminal connection-loop crash re-runs setup and recovers."""
    await setup_integration(hass, mock_config_entry)

    emit(mock_client, ConnectionDied(reason="internal error"))
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_client.start.await_count == 2


async def test_availability_transitions_log_once_per_edge(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One warning on loss, one info on restore, nothing on first connect."""
    await setup_integration(hass, mock_config_entry)

    with caplog.at_level(logging.INFO, logger="custom_components.ampio"):
        emit(mock_client, AvailabilityChanged(available=True))
        emit(mock_client, AvailabilityChanged(available=False))
        emit(mock_client, AvailabilityChanged(available=True))

    assert caplog.text.count("Connection to the Ampio server lost") == 1
    assert caplog.text.count("Connection to the Ampio server restored") == 1


async def test_module_devices_preregistered(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Module parent devices exist after setup, with catalogue metadata."""
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, MSERV_MAC), mock_config_entry.entry_id
    )
    module_device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert module_device is not None
    assert hub.area_id is None
    assert module_device.area_id is None
    assert module_device.name == "m-sens salon"
    assert module_device.via_device_id == hub.id
    assert mock_config_entry.runtime_data.module_device_ids[52111] == module_device.id


async def test_room_fetch_failure_degrades(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed room fetch logs one warning and setup still succeeds."""
    mock_client.fetch_rooms.side_effect = AmpioTimeoutError("no reply")
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    room_map_warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "room map" in record.getMessage()
    ]
    assert len(room_map_warnings) == 1
    assert mock_config_entry.runtime_data.rooms == {}


async def test_output_objects_get_own_devices(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """Module-owned output objects get own devices with names, rooms, entities."""
    await setup_integration(hass, mock_config_entry)

    module_device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module_device is not None

    # Named object 71 (Taras LED, room Taras).
    obj_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{MSERV_MAC}:obj:leaf_0_cb8f_led_0_1"), mock_config_entry.entry_id
    )
    assert obj_device is not None
    assert obj_device.via_device_id == module_device.id
    assert obj_device.name == "Taras LED"
    area = area_registry.async_get_area_by_name("Taras")
    assert area is not None
    assert obj_device.area_id == area.id
    entity_id = entity_registry.async_get_entity_id(
        "light", DOMAIN, f"{MSERV_MAC}_leaf_0_cb8f_led_0_1"
    )
    assert entity_id is not None
    entity_entry = entity_registry.async_get(entity_id)
    assert entity_entry is not None
    assert entity_entry.device_id == obj_device.id

    # Unnamed object 74 (a relay): fallback device name, translated entity name.
    unnamed = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{MSERV_MAC}:obj:leaf_0_cb8f_rel_0_4"), mock_config_entry.entry_id
    )
    assert unnamed is not None
    assert unnamed.name == "Ampio object leaf_0_cb8f_rel_0_4"
    assert unnamed.area_id is None

    # Sensor object 36 (Temperatura) is a property of its module, not a
    # deployed device of its own, so it gets no device of its own.
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{MSERV_MAC}:obj:leaf_0_cb8f_temp_0_1"),
            mock_config_entry.entry_id,
        )
        is None
    )
    sensor_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{MSERV_MAC}_leaf_0_cb8f_temp_0_1"
    )
    assert sensor_entity_id is not None
    sensor_entity = entity_registry.async_get(sensor_entity_id)
    assert sensor_entity is not None
    assert sensor_entity.device_id == module_device.id


async def test_server_owned_objects_partition(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Server-owned inputs attach to the hub directly; thermostats keep own devices."""
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, MSERV_MAC), mock_config_entry.entry_id
    )
    assert hub is not None

    # Object 121 (server-owned flag, InputKind): entity on the hub, no device.
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{MSERV_MAC}:obj:leaf_0_1_flaga_0_9"),
            mock_config_entry.entry_id,
        )
        is None
    )
    flag_entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{MSERV_MAC}_leaf_0_1_flaga_0_9"
    )
    assert flag_entity_id is not None
    flag_entity = entity_registry.async_get(flag_entity_id)
    assert flag_entity is not None
    assert flag_entity.device_id == hub.id

    # Object 91 (module-owned thermostat, ThermostatKind): keeps its own device.
    module_device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module_device is not None
    thermostat_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{MSERV_MAC}:obj:leaf_0_cb8f_reg_0_1"), mock_config_entry.entry_id
    )
    assert thermostat_device is not None
    assert thermostat_device.via_device_id == module_device.id
    climate_entity_id = entity_registry.async_get_entity_id(
        "climate", DOMAIN, f"{MSERV_MAC}_leaf_0_cb8f_reg_0_1"
    )
    assert climate_entity_id is not None
    climate_entity = entity_registry.async_get(climate_entity_id)
    assert climate_entity is not None
    assert climate_entity.device_id == thermostat_device.id


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Only devices with no live backing object may be deleted."""
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, MSERV_MAC), mock_config_entry.entry_id
    )
    module_device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    obj_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{MSERV_MAC}:obj:leaf_0_cb8f_led_0_1"), mock_config_entry.entry_id
    )
    assert hub is not None
    assert module_device is not None
    assert obj_device is not None

    assert not await async_remove_config_entry_device(hass, mock_config_entry, hub)
    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, module_device
    )
    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, obj_device
    )

    # A stale sensor object device (pre-partition leftover) may be deleted
    # because sensor objects no longer get devices of their own.
    stale = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{MSERV_MAC}:obj:leaf_0_cb8f_temp_0_1")},
        name="stale sensor device",
        via_device_id=module_device.id,
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, stale)

    # Drop the LED object from the catalogue: its device goes stale.
    del mock_client.objects[71]
    assert await async_remove_config_entry_device(hass, mock_config_entry, obj_device)
