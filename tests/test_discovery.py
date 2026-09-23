"""Tests for the runtime discovery of the Ampio integration."""

from dataclasses import replace
from datetime import timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from ampio_mqtt import (
    AccessTier,
    AmpioAdminClient,
    AmpioConnectionError,
    AmpioObject,
    AmpioTimeoutError,
    ModuleFunction,
    ObjectAdded,
    ObjectRemoved,
    ObjectUpdated,
    format_mac,
    parse_module_address,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio import async_remove_config_entry_device
from custom_components.ampio.const import DOMAIN, MODULE_KEY_STEM
from custom_components.ampio.data import AmpioData, module_identifier
from custom_components.ampio.stale import find_stale_records
from homeassistant.const import ATTR_RESTORED, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform, async_get_platforms
from homeassistant.util import dt as dt_util

from . import setup_integration
from .conftest import (
    DEFAULT_ROOMS,
    EMPTY_SWEEP,
    HUB_IDENTIFIER,
    MSENS_IDENTIFIER,
    emit,
    entity_id_of,
    make_object,
    module_unique_id,
    set_access_tier,
    unique_id,
    with_buzzer,
)

NEW_INPUT_ID = 200
ISSUE_ID = "stale_records"


def NEW_INPUT_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for object 200, composed from the registry."""
    return entity_id_of(hass, "binary_sensor", unique_id(NEW_INPUT_ID))


def WEJ_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for object 146, composed from the registry."""
    return entity_id_of(hass, "binary_sensor", unique_id(146))


def RELAY_SWITCH_ID(hass: HomeAssistant) -> str:
    """Entity id for object 74's switch entity, composed from the registry."""
    return entity_id_of(hass, "switch", unique_id(74))


def RELAY_LIGHT_ID(hass: HomeAssistant) -> str:
    """Entity id for object 74's light entity, composed from the registry."""
    return entity_id_of(hass, "light", unique_id(74))


def RELAY_PULSE_ID(hass: HomeAssistant) -> str:
    """Entity id for object 74's pulse diagnostic, composed from the registry."""
    return entity_id_of(hass, "sensor", unique_id(74, "_pulse"))


def _new_input(**overrides: Any) -> AmpioObject:
    """A wired input added in Designer after setup, on the default module."""
    fields: dict[str, Any] = {
        "leaf_id": "0_cb8f_257_1_12",
        "funkcja": 12,
        "name": "Przycisk taras",
        "state": "0",
    }
    fields.update(overrides)
    return make_object(NEW_INPUT_ID, "wej", 7, **fields)


async def _settle(hass: HomeAssistant) -> None:
    """Let the reconcile cooldown elapse and the batch finish.

    The batch runs as an entry background task, which the default
    ``async_block_till_done`` does not wait for.
    """
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _add(hass: HomeAssistant, client: MagicMock, obj: AmpioObject) -> None:
    client.objects[obj.id] = obj
    emit(client, ObjectAdded(object=obj))
    await _settle(hass)


async def _update(hass: HomeAssistant, client: MagicMock, obj: AmpioObject) -> None:
    client.objects[obj.id] = obj
    emit(client, ObjectUpdated(object=obj))
    await _settle(hass)


async def _remove(hass: HomeAssistant, client: MagicMock, oid: int) -> AmpioObject:
    obj: AmpioObject = client.objects.pop(oid)
    emit(client, ObjectRemoved(object=obj))
    await _settle(hass)
    return obj


def _child(
    device_registry: dr.DeviceRegistry, entry: MockConfigEntry, oid: int
) -> dr.ChildDeviceEntry | None:
    return device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(oid)), entry.entry_id
    )


async def test_new_object_gets_its_entity_device_and_area(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """An object added in Designer appears under its module, in its app room."""
    await setup_integration(hass, mock_config_entry)
    assert (
        entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, unique_id(NEW_INPUT_ID)
        )
        is None
    )
    mock_client.fetch_rooms.return_value = {**DEFAULT_ROOMS, NEW_INPUT_ID: "Taras"}

    await _add(hass, mock_client, _new_input())

    assert entity_registry.async_get(NEW_INPUT_ENTITY_ID(hass)) == snapshot
    assert hass.states.get(NEW_INPUT_ENTITY_ID(hass)) == snapshot
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert child is not None
    assert module is not None
    assert child.parent_device_id == module.id
    assert child.name == "Przycisk taras"
    taras = area_registry.async_get_area_by_name("Taras")
    assert taras is not None
    assert child.area_id == taras.id
    assert mock_client.fetch_rooms.await_count == 2


async def test_new_module_mac_gets_a_device_on_a_restricted_account(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A new module MAC gets its device before its child on a standard account."""
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)

    await _add(hass, mock_client, _new_input(leaf_id="0_d009_257_1_1"))

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module_mac:0xD009"), mock_config_entry.entry_id
    )
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert hub is not None
    assert module is not None
    assert child is not None
    assert module.name == "Ampio module 0xD009"
    assert module.via_device_id == hub.id
    assert child.parent_device_id == module.id
    assert hass.states.get(NEW_INPUT_ENTITY_ID(hass)).state == STATE_OFF


async def test_one_batch_per_burst_of_events(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The many events of one catalogue reply fold into one batch and one room fetch."""
    await setup_integration(hass, mock_config_entry)

    for offset in range(20):
        obj = make_object(
            300 + offset,
            "wej",
            7,
            leaf_id=f"0_cb8f_257_1_{20 + offset}",
            funkcja=20 + offset,
            state="0",
        )
        mock_client.objects[obj.id] = obj
        emit(mock_client, ObjectAdded(object=obj))
    await _settle(hass)

    assert mock_client.fetch_rooms.await_count == 2
    for offset in range(20):
        state = hass.states.get(
            entity_id_of(hass, "binary_sensor", unique_id(300 + offset))
        )
        assert state is not None
        assert state.state == STATE_OFF


async def test_state_push_schedules_no_batch(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An update that changes no catalogue field reaches the entity and nothing else."""
    await setup_integration(hass, mock_config_entry)

    await _update(hass, mock_client, replace(mock_client.objects[146], state="1"))

    assert hass.states.get(WEJ_ENTITY_ID(hass)).state == STATE_ON
    mock_client.fetch_rooms.assert_awaited_once()


async def test_removed_object_loses_its_entity_and_keeps_its_record(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A row that drops out of the catalogue leaves a restored record, and a re-add restores the id."""
    await setup_integration(hass, mock_config_entry)
    # Captured once, before the removal: the assertions below must find
    # this exact string again, not whatever the registry happens to
    # compose next, or a silent rename would pass unnoticed.
    entity_id = WEJ_ENTITY_ID(hass)

    obj = await _remove(hass, mock_client, 146)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert state.attributes[ATTR_RESTORED] is True
    assert entity_registry.async_get(entity_id) is not None

    await _add(hass, mock_client, obj)

    assert hass.states.get(entity_id).state == STATE_OFF


async def test_deleted_relay_loses_its_entity_until_readmitted(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A relay's removal and readmission restore its entity on an admin account."""
    await setup_integration(hass, mock_config_entry)

    relay = await _remove(hass, mock_client, 74)
    state = hass.states.get(RELAY_SWITCH_ID(hass))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert state.attributes[ATTR_RESTORED] is True

    await _add(hass, mock_client, relay)
    assert hass.states.get(RELAY_SWITCH_ID(hass)).state == STATE_ON


async def test_retagged_relay_moves_from_switch_to_light(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Lighting tag set in Designer swaps the platform without a reload."""
    await setup_integration(hass, mock_config_entry)
    assert (
        er.async_get(hass).async_get_entity_id("light", DOMAIN, unique_id(74)) is None
    )

    await _update(
        hass, mock_client, replace(mock_client.objects[74], matter_device_type=0x0100)
    )

    assert hass.states.get(RELAY_SWITCH_ID(hass)).state == STATE_UNAVAILABLE
    assert hass.states.get(RELAY_LIGHT_ID(hass)).state == STATE_ON


async def test_pulse_time_adds_and_removes_the_diagnostic(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The pulse sensor follows the Designer time on the object."""
    await setup_integration(hass, mock_config_entry)
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, unique_id(74, "_pulse")
        )
        is None
    )
    relay = mock_client.objects[74]

    await _update(hass, mock_client, replace(relay, czas=300))
    assert hass.states.get(RELAY_PULSE_ID(hass)).state == "3.0"

    await _update(hass, mock_client, relay)
    assert hass.states.get(RELAY_PULSE_ID(hass)).state == STATE_UNAVAILABLE


async def test_moved_object_keeps_its_entities_and_is_deletable(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A move in Designer keeps the entities under the old module, warns once, and permits the delete."""
    await setup_integration(hass, mock_config_entry)
    child = _child(device_registry, mock_config_entry, 74)
    assert child is not None
    old_parent = child.parent_device_id
    entity_id = RELAY_SWITCH_ID(hass)

    moved = replace(
        mock_client.objects[74],
        address=parse_module_address("0_be82_257_2_1"),
        leaf_key="leaf_0_be82_257_2_1",
    )
    await _update(hass, mock_client, moved)
    # A second batch for the same object, while its child stays put, and
    # it builds a new entity, which registers under the child as well.
    await _update(hass, mock_client, replace(moved, czas=300))

    assert hass.states.get(entity_id).state == STATE_ON
    assert hass.states.get(RELAY_PULSE_ID(hass)).state == "3.0"
    for built in (entity_id, RELAY_PULSE_ID(hass)):
        record = entity_registry.async_get(built)
        assert record is not None
        assert record.device_id == child.id
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "Ampio object 74 hangs under a different module" in record.getMessage()
    ]
    assert len(warnings) == 1
    stuck = _child(device_registry, mock_config_entry, 74)
    assert stuck is not None
    assert stuck.id == child.id
    assert stuck.parent_device_id == old_parent
    assert await async_remove_config_entry_device(hass, mock_config_entry, stuck)


async def test_room_fetch_failure_degrades_the_batch(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed room fetch costs the area seed and nothing else."""
    await setup_integration(hass, mock_config_entry)
    mock_client.fetch_rooms.side_effect = AmpioConnectionError("down")

    await _add(hass, mock_client, _new_input())

    assert hass.states.get(NEW_INPUT_ENTITY_ID(hass)).state == STATE_OFF
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert child is not None
    assert child.area_id is None
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "room map" in record.getMessage()
    ]
    assert len(warnings) == 1


async def test_unload_drops_the_subscription(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """After unload no listener is left for a late catalogue event."""
    await setup_integration(hass, mock_config_entry)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_client.live_subscriptions == []


async def test_removed_object_is_listed_by_the_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The repair lists a removed object within one batch, and a re-add clears it."""
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None

    obj = await _remove(hass, mock_client, 146)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "stale_records_not_served"
    assert issue.translation_placeholders == {"names": "- Przycisk kino"}

    await _add(hass, mock_client, obj)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_deleted_object_is_listed_by_the_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A removed admin catalogue object is listed as deleted by the repair."""
    await setup_integration(hass, mock_config_entry)

    await _remove(hass, mock_client, 74)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "stale_records_deleted"
    assert issue.translation_placeholders == {"names": "- Object 74"}


async def test_moved_object_is_listed_by_the_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A moved object's stuck child is what the repair offers to delete."""
    await setup_integration(hass, mock_config_entry)

    await _update(
        hass,
        mock_client,
        replace(
            mock_client.objects[74],
            address=parse_module_address("0_be82_257_2_1"),
            leaf_key="leaf_0_be82_257_2_1",
        ),
    )

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_placeholders == {"names": "- Object 74"}


async def test_deleting_a_moved_child_brings_it_back_under_the_new_module(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """The delete the hook permits is the move, and the next batch completes it."""
    await setup_integration(hass, mock_config_entry)
    # Captured once, before the move: the child device survives the delete
    # and rebuild under its new parent, and the entity id must follow it,
    # not whatever the registry happens to compose next.
    entity_id = RELAY_SWITCH_ID(hass)
    child = _child(device_registry, mock_config_entry, 74)
    assert child is not None
    piwnica = area_registry.async_get_or_create("Piwnica")
    device_registry.async_update_child_device(
        child.id, name_by_user="Przekaznik piwnica", area_id=piwnica.id
    )

    await _update(
        hass,
        mock_client,
        replace(
            mock_client.objects[74],
            address=parse_module_address("0_be82_257_2_1"),
            leaf_key="leaf_0_be82_257_2_1",
        ),
    )
    # The entity keeps working until the delete.
    assert hass.states.get(entity_id).state == STATE_ON
    stuck = _child(device_registry, mock_config_entry, 74)
    assert stuck is not None
    assert await async_remove_config_entry_device(hass, mock_config_entry, stuck)
    device_registry.async_remove_device(stuck.id)
    await _settle(hass)

    moved = _child(device_registry, mock_config_entry, 74)
    new_module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module_mac:0xBE82"), mock_config_entry.entry_id
    )
    assert moved is not None
    assert new_module is not None
    assert moved.id == child.id
    assert moved.parent_device_id == new_module.id
    assert moved.name_by_user == "Przekaznik piwnica"
    assert moved.area_id == piwnica.id
    assert hass.states.get(entity_id).state == STATE_ON


async def test_a_child_deleted_during_a_batch_comes_back_under_the_new_module(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An entity built before its stuck child is deleted registers under the new module."""
    await setup_integration(hass, mock_config_entry)
    child = _child(device_registry, mock_config_entry, 74)
    assert child is not None
    entity_id = RELAY_SWITCH_ID(hass)
    sensor_platform = next(
        platform
        for platform in async_get_platforms(hass, DOMAIN)
        if platform.domain == "sensor"
    )
    add = sensor_platform.async_add_entities

    async def delete_then_add(entities: list[Entity]) -> None:
        # The user deletes the stuck child after the batch built the new
        # pulse sensor and before the batch adds it.
        if any(entity.unique_id == unique_id(74, "_pulse") for entity in entities):
            stuck = _child(device_registry, mock_config_entry, 74)
            assert stuck is not None
            assert await async_remove_config_entry_device(
                hass, mock_config_entry, stuck
            )
            device_registry.async_remove_device(stuck.id)
        await add(entities)

    # The move and a new pulse time land in one push, so the batch builds
    # the pulse sensor while the child still hangs under the old module.
    moved = replace(
        mock_client.objects[74],
        address=parse_module_address("0_be82_257_2_1"),
        leaf_key="leaf_0_be82_257_2_1",
        czas=300,
    )
    with patch.object(sensor_platform, "async_add_entities", delete_then_add):
        await _update(hass, mock_client, moved)
    await _settle(hass)

    rebuilt = _child(device_registry, mock_config_entry, 74)
    new_module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module_mac:0xBE82"), mock_config_entry.entry_id
    )
    assert rebuilt is not None
    assert new_module is not None
    assert rebuilt.id == child.id
    assert rebuilt.parent_device_id == new_module.id
    for built in (entity_id, RELAY_PULSE_ID(hass)):
        record = entity_registry.async_get(built)
        assert record is not None
        assert record.device_id == child.id
    assert hass.states.get(entity_id).state == STATE_ON


async def test_the_misparent_warning_returns_with_a_second_move(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An object misparented again after its device was rebuilt is warned about again."""
    await setup_integration(hass, mock_config_entry)
    original = mock_client.objects[74]
    moved = replace(
        original,
        address=parse_module_address("0_be82_257_2_1"),
        leaf_key="leaf_0_be82_257_2_1",
    )

    def warnings() -> int:
        return sum(
            1
            for record in caplog.records
            if record.levelno == logging.WARNING
            and "Ampio object 74 hangs under a different module" in record.getMessage()
        )

    caplog.clear()
    await _update(hass, mock_client, moved)
    assert warnings() == 1

    stuck = _child(device_registry, mock_config_entry, 74)
    assert stuck is not None
    assert await async_remove_config_entry_device(hass, mock_config_entry, stuck)
    device_registry.async_remove_device(stuck.id)
    await _settle(hass)
    rebuilt = _child(device_registry, mock_config_entry, 74)
    assert rebuilt is not None
    assert rebuilt.parent_device_id != stuck.parent_device_id
    assert warnings() == 1

    await _update(hass, mock_client, original)
    assert warnings() == 2


async def test_deleting_the_old_module_brings_a_stuck_child_back_too(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A module delete takes its children with it, so the hook queues them first.

    Home Assistant's device registry removes a parent's children as part of
    removing the parent, without asking the removal hook about each child.
    A moved object's stuck child is one of those children, so deleting the
    old module has to queue that object the same way deleting the child
    itself does, or its entities never return.
    """
    await setup_integration(hass, mock_config_entry)
    # Captured once, before the move: the same identical-defect shape as
    # the sibling test above, so the final assertion below catches a
    # silent rename instead of finding whatever id the registry composes
    # next.
    entity_id = RELAY_SWITCH_ID(hass)
    child = _child(device_registry, mock_config_entry, 74)
    assert child is not None

    await _update(
        hass,
        mock_client,
        replace(
            mock_client.objects[74],
            address=parse_module_address("0_be82_257_2_1"),
            leaf_key="leaf_0_be82_257_2_1",
        ),
    )
    assert hass.states.get(entity_id).state == STATE_ON
    stuck = _child(device_registry, mock_config_entry, 74)
    assert stuck is not None
    assert stuck.id == child.id

    old_module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert old_module is not None
    # Every other object on the old module leaves too, so it reads as
    # stale and its own device page permits the delete.
    for object_id in [
        obj.id for obj in mock_client.objects.values() if obj.address.mac == 52111
    ]:
        del mock_client.objects[object_id]

    assert await async_remove_config_entry_device(hass, mock_config_entry, old_module)
    device_registry.async_remove_device(old_module.id)
    await _settle(hass)

    moved = _child(device_registry, mock_config_entry, 74)
    new_module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module_mac:0xBE82"), mock_config_entry.entry_id
    )
    assert moved is not None
    assert new_module is not None
    assert moved.id == child.id
    assert moved.parent_device_id == new_module.id
    assert hass.states.get(entity_id).state == STATE_ON


async def test_module_factory_builds_now_and_for_a_new_mac(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Each module factory receives the admin client and MAC at setup and discovery."""
    await setup_integration(hass, mock_config_entry)
    data: AmpioData = mock_config_entry.runtime_data
    platform = MagicMock(spec=EntityPlatform)
    platform.async_add_entities = AsyncMock()
    # The batch reads a module platform's own table to see what is built
    # on each mac, as it does for the object platforms. This stub keeps
    # nothing, so every entity a factory offers reads as missing.
    platform.entities = {}
    built: list[int] = []
    second_built: list[int] = []

    def factory(_data: AmpioData, admin: AmpioAdminClient, mac: int) -> list[Entity]:
        assert admin is mock_client
        built.append(mac)
        return [MagicMock(spec=Entity)]

    def second_factory(
        _data: AmpioData, admin: AmpioAdminClient, mac: int
    ) -> list[Entity]:
        assert admin is mock_client
        second_built.append(mac)
        return [MagicMock(spec=Entity)]

    async_add_entities = MagicMock()
    with patch(
        "custom_components.ampio.data.async_get_current_platform",
        return_value=platform,
    ):
        data.async_add_admin_module_platform(factory, async_add_entities)
        data.async_add_admin_module_platform(second_factory, async_add_entities)

    assert built == [52111]
    assert second_built == [52111]
    assert async_add_entities.call_count == 2
    for call in async_add_entities.call_args_list:
        assert len(call.args[0]) == 1
    assert data.ensure_module_device(mock_client.objects[36]) is None

    await _add(hass, mock_client, _new_input(leaf_id="0_d009_257_1_1"))

    # The batch asks every mac the tree holds what it expects, and hands
    # the platform what that mac has not got built. Both factories share
    # this platform, so one call carries what the two of them expect for
    # the two macs.
    assert built == [52111, 52111, 53257]
    assert second_built == [52111, 52111, 53257]
    assert platform.async_add_entities.await_count == 1
    assert len(platform.async_add_entities.call_args.args[0]) == 4


async def test_module_factory_is_never_called_on_a_restricted_account(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Setup, discovery, and withheld-id lookup never run an admin factory off-tier."""
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)
    data: AmpioData = mock_config_entry.runtime_data
    platform = MagicMock(spec=EntityPlatform)
    platform.async_add_entities = AsyncMock()
    platform.entities = {}
    factory = MagicMock(return_value=[MagicMock(spec=Entity)])

    async_add_entities = MagicMock()
    with patch(
        "custom_components.ampio.data.async_get_current_platform",
        return_value=platform,
    ):
        data.async_add_admin_module_platform(factory, async_add_entities)

    factory.assert_not_called()
    async_add_entities.assert_called_once_with([])

    await _add(hass, mock_client, _new_input(leaf_id="0_d009_257_1_1"))

    assert data.withheld_unique_ids() == set()
    factory.assert_not_called()
    platform.async_add_entities.assert_not_awaited()


async def test_deleted_module_device_comes_back_with_its_object(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A returning object restores its deleted module device, child, and Identify button."""
    await setup_integration(hass, mock_config_entry)
    new_input = _new_input(leaf_id="0_d009_257_1_1")
    await _add(hass, mock_client, new_input)
    module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module_mac:0xD009"), mock_config_entry.entry_id
    )
    assert module is not None
    # Cached once, while the entity exists: a remove and re-add restores
    # the same string from the registry's deleted-entity record, and the
    # cached string still resolves an absence correctly in between.
    button_id = entity_id_of(hass, "button", module_unique_id(53257, "_identify"))
    assert hass.states.get(button_id) is not None

    await _remove(hass, mock_client, NEW_INPUT_ID)
    assert await async_remove_config_entry_device(hass, mock_config_entry, module)
    device_registry.async_remove_device(module.id)
    await hass.async_block_till_done()
    assert hass.states.get(button_id) is None

    await _add(hass, mock_client, new_input)

    rebuilt = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module_mac:0xD009"), mock_config_entry.entry_id
    )
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert rebuilt is not None
    assert rebuilt.id == module.id
    assert child is not None
    assert child.parent_device_id == rebuilt.id
    assert hass.states.get(NEW_INPUT_ENTITY_ID(hass)).state == STATE_OFF
    assert hass.states.get(button_id) is not None
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_a_module_that_loses_its_last_object_loses_its_controls(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A mac no object names keeps no working control on its module device.

    The module row stays admitted, so the command would still reach the
    panel, while the mac has left the object tree and the report offers
    the device. A batch that left the entities up would leave a control
    that works on a device the next submit deletes.
    """
    await setup_integration(hass, mock_config_entry)
    await _add(hass, mock_client, _new_input(leaf_id="0_d009_257_1_1"))
    button_id = entity_id_of(hass, "button", module_unique_id(53257, "_identify"))
    assert hass.states.get(button_id) is not None

    await _remove(hass, mock_client, NEW_INPUT_ID)

    assert hass.states.get(button_id).attributes.get(ATTR_RESTORED) is True
    # The record stays for the repair to offer with the device it sits on.
    assert entity_registry.async_get(button_id) is not None
    module = device_registry.async_get_device_by_identifier(
        module_identifier(53257), mock_config_entry.entry_id
    )
    assert module is not None
    stale = find_stale_records(hass, mock_config_entry)
    assert module.id in {device.id for device in stale.devices}
    assert button_id not in {record.entity_id for record in stale.entities}


async def test_a_batch_leaves_a_live_macs_controls_alone(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A capability the module did not report is no reason to take a control down.

    The buzzer is built from the capability map, which a module that
    stayed silent through the description sweep leaves unknown. Its mac is
    still named by objects, so the batch adds what that mac is missing and
    takes nothing away from it.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)
    buzzer_id = entity_id_of(hass, "siren", module_unique_id(52111, "_buzzer"))
    assert hass.states.get(buzzer_id) is not None

    mock_client.capabilities[52111] = {}
    await _add(hass, mock_client, _new_input())

    assert hass.states.get(buzzer_id).attributes.get(ATTR_RESTORED) is None


NEW_MAC = 53257
SECOND_NEW_MAC = 53258


def _report_buzzer(client: MagicMock, *macs: int) -> None:
    """Make the next sweep report a buzzer on each mac, as a new panel would."""

    def _resolve() -> Any:
        for mac in macs:
            client.capabilities[mac] = {ModuleFunction.BUZZER: 4}
        return EMPTY_SWEEP

    client.resolve_records.side_effect = _resolve


async def test_a_new_module_gets_its_gated_controls_in_the_same_batch(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module added while the entry runs gets its buzzer from a fresh sweep."""
    await setup_integration(hass, mock_config_entry)
    _report_buzzer(mock_client, NEW_MAC)

    await _add(hass, mock_client, _new_input(leaf_id="0_d009_257_1_1"))

    assert mock_client.resolve_records.await_count == 2
    buzzer_id = entity_id_of(hass, "siren", module_unique_id(NEW_MAC, "_buzzer"))
    assert hass.states.get(buzzer_id) is not None


async def test_two_new_modules_in_one_batch_sweep_once(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A batch that adds two module devices runs one sweep for both."""
    await setup_integration(hass, mock_config_entry)
    _report_buzzer(mock_client, NEW_MAC, SECOND_NEW_MAC)
    first = _new_input(leaf_id="0_d009_257_1_1")
    second = make_object(
        NEW_INPUT_ID + 1,
        "wej",
        7,
        leaf_id="0_d00a_257_1_1",
        funkcja=12,
        name="Przycisk garaz",
        state="0",
    )

    for obj in (first, second):
        mock_client.objects[obj.id] = obj
        emit(mock_client, ObjectAdded(object=obj))
    await _settle(hass)

    assert mock_client.resolve_records.await_count == 2
    for mac in (NEW_MAC, SECOND_NEW_MAC):
        buzzer_id = entity_id_of(hass, "siren", module_unique_id(mac, "_buzzer"))
        assert hass.states.get(buzzer_id) is not None


async def test_a_batch_without_a_new_module_does_not_sweep(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An object on a module the tree already holds costs no sweep."""
    await setup_integration(hass, mock_config_entry)

    await _add(hass, mock_client, _new_input())

    assert hass.states.get(NEW_INPUT_ENTITY_ID(hass)) is not None
    mock_client.resolve_records.assert_awaited_once_with()


async def test_a_failed_sweep_leaves_the_rest_of_the_batch_built(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A sweep that times out keeps the object entities and the Identify button, and warns."""
    await setup_integration(hass, mock_config_entry)
    mock_client.resolve_records.side_effect = AmpioTimeoutError("no reply")

    await _add(hass, mock_client, _new_input(leaf_id="0_d009_257_1_1"))

    assert mock_client.resolve_records.await_count == 2
    assert hass.states.get(NEW_INPUT_ENTITY_ID(hass)).state == STATE_OFF
    button_id = entity_id_of(hass, "button", module_unique_id(NEW_MAC, "_identify"))
    assert hass.states.get(button_id) is not None
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "sweep" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert format_mac(NEW_MAC) in warnings[0]
    assert "reload" in warnings[0]
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_a_new_module_on_a_standard_account_does_not_sweep(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A standard account is not served the sweep, so a new module runs none.

    The restricted client has no ``resolve_records`` at all, so a batch
    that reached for it would fail rather than finish.
    """
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)
    data: AmpioData = mock_config_entry.runtime_data
    report = MagicMock()
    data.async_mark_ready(report)

    await _add(hass, mock_client, _new_input(leaf_id="0_d009_257_1_1"))

    assert not hasattr(mock_client, "resolve_records")
    assert NEW_MAC in data.module_device_ids
    report.assert_called_once_with()
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_a_module_that_gets_its_object_back_needs_no_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The last object off a module and back again brings everything straight back.

    The mac keeps its place in the tree while its device record stands,
    so the object resolves to the device its child still hangs under. A
    tree that dropped the mac would resolve the object to the hub, and
    the repair would offer a child that sits where it belongs.
    """
    await setup_integration(hass, mock_config_entry)
    new_input = _new_input(leaf_id="0_d009_257_1_1")
    await _add(hass, mock_client, new_input)
    entity_id = NEW_INPUT_ENTITY_ID(hass)
    button_id = entity_id_of(hass, "button", module_unique_id(53257, "_identify"))
    module = device_registry.async_get_device_by_identifier(
        module_identifier(53257), mock_config_entry.entry_id
    )
    assert module is not None

    await _remove(hass, mock_client, NEW_INPUT_ID)
    await _add(hass, mock_client, new_input)

    data: AmpioData = mock_config_entry.runtime_data
    assert data.parent_for(new_input) == module.id
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert child is not None
    assert child.parent_device_id == module.id
    assert hass.states.get(entity_id).state == STATE_OFF
    assert hass.states.get(button_id).attributes.get(ATTR_RESTORED) is None
    stale = find_stale_records(hass, mock_config_entry)
    assert module.id not in {device.id for device in stale.devices}
    assert child.id not in {device.id for device in stale.devices}
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


@pytest.mark.parametrize(
    "away_leaf", [None, "0_cb8f_257_1_12"], ids=["removed", "moved"]
)
async def test_a_module_that_gets_its_object_back_after_a_reload_needs_no_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
    caplog: pytest.LogCaptureFixture,
    away_leaf: str | None,
) -> None:
    """The last object off a module, a reload, and the object back brings it all back.

    The reload builds the tree without the module while the registry keeps
    its device record. The returning object resolves to that record, its
    child stays under it, no misparent warning is logged, and the stale
    report offers neither device (#133).
    """
    await setup_integration(hass, mock_config_entry)
    new_input = _new_input(leaf_id="0_d009_257_1_1")
    await _add(hass, mock_client, new_input)
    entity_id = NEW_INPUT_ENTITY_ID(hass)
    module = device_registry.async_get_device_by_identifier(
        module_identifier(53257), mock_config_entry.entry_id
    )
    assert module is not None
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert child is not None
    assert child.parent_device_id == module.id

    if away_leaf is None:
        await _remove(hass, mock_client, NEW_INPUT_ID)
    else:
        await _update(
            hass,
            mock_client,
            replace(
                new_input,
                address=parse_module_address(away_leaf),
                leaf_key=f"leaf_{away_leaf}",
            ),
        )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    data: AmpioData = mock_config_entry.runtime_data
    assert 53257 not in data.module_device_ids
    assert (
        device_registry.async_get_device_by_identifier(
            module_identifier(53257), mock_config_entry.entry_id
        )
        is not None
    )

    caplog.clear()
    if away_leaf is None:
        await _add(hass, mock_client, new_input)
    else:
        await _update(hass, mock_client, new_input)

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF
    assert state.attributes.get(ATTR_RESTORED) is None
    assert "hangs under a different module" not in caplog.text
    data = mock_config_entry.runtime_data
    assert data.parent_for(new_input) == module.id
    returned = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert returned is not None
    assert returned.id == child.id
    assert returned.parent_device_id == module.id
    stale = find_stale_records(hass, mock_config_entry)
    assert {device.id for device in stale.devices}.isdisjoint({module.id, child.id})
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_module_row_reads_none_on_a_standard_account(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A module lookup returns None when the client has no admin catalogue."""
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)

    data = mock_config_entry.runtime_data
    assert data.module_row_for(52111) is None
    assert not hasattr(mock_client, "modules")


async def test_module_row_reads_none_for_a_row_the_catalogue_lost(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A module lookup returns None for a MAC absent from the admin catalogue."""
    await setup_integration(hass, mock_config_entry)

    data = mock_config_entry.runtime_data
    assert data.module_row_for(999) is None


async def test_home_assistant_composes_an_entity_id_from_the_names(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A named object in an app room takes an id built from both names."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # The child device carries the object's name and the app room seeds the
    # area, so those two compose the id. The primary entity adds no name of
    # its own, so no third part joins them.
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("light", DOMAIN, unique_id(71))
    assert entity_id == "light.taras_taras_led"
    assert entity_id != f"light.ampio_{unique_id(71)}"


async def test_an_unnamed_object_takes_its_device_translation(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An object with no Designer name composes from the object translation."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # No name and no app room, so the child device falls to the ``object``
    # device translation and the id starts from that rather than from Home
    # Assistant's ``<platform>_<unique id>`` fallback.
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id(43))
    assert entity_id is not None
    assert entity_id.startswith("sensor.object_43")
    assert entity_id != f"sensor.ampio_{unique_id(43)}"


def test_one_function_writes_every_module_mac() -> None:
    """format_mac's text is exactly what module_identifier embeds, with no decimal leftover."""
    mac = 52111
    text = format_mac(mac)
    assert text == "0xCB8F"
    assert module_identifier(mac) == (DOMAIN, f"{MODULE_KEY_STEM}:{text}")
    assert str(mac) not in module_identifier(mac)[1]


async def test_a_module_entity_keys_on_the_hex_mac(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A module entity's unique id and its device identifier carry the same mac text."""
    with_buzzer(mock_client)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    text = format_mac(52111)
    key = f"{MODULE_KEY_STEM}_{text}_buzzer"
    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id("siren", DOMAIN, key)
    assert entity_id is not None
    entity = entity_registry.async_get(entity_id)
    assert entity is not None
    assert entity.device_id is not None
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device is not None
    assert (DOMAIN, f"{MODULE_KEY_STEM}:{text}") in device.identifiers
