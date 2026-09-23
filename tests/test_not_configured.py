"""Tests for catalogue admission and the installer repair."""

from datetime import timedelta
from unittest.mock import MagicMock

from ampio_mqtt import (
    AccessTier,
    AmpioClient,
    AmpioNotConfigured,
    NotConfigured,
    ObjectAdded,
    ObjectRemoved,
    format_mac,
)
from ampio_mqtt.testing import AmpioStore, apply_reply, build_store
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ampio.const import (
    DOMAIN,
    NOT_CONFIGURED_ISSUE,
    STALE_RECORDS_ISSUE,
)
from custom_components.ampio.stale import find_stale_records
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_RESTORED, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.util import dt as dt_util

from . import setup_integration
from .conftest import (
    HIDDEN_LEAFLESS_ROW,
    LEAFLESS_ALARM_ROW,
    emit,
    entity_id_of,
    make_object,
    module_unique_id,
    set_access_tier,
    unique_id,
    with_buzzer,
)

# A module mac no seeded object carries, so the objects put on it here are
# the only ones that resolve to its device.
PUMP_MAC = 53257
PUMP_IDENTIFIER = (DOMAIN, f"module_mac:{format_mac(PUMP_MAC)}")
PUMP_OBJECT = make_object(
    300, "przekaznik", 0, leaf_id="0_d009_257_2_1", funkcja=30, name="Pompa", state="0"
)


def _refuse(client: MagicMock, object_id: int) -> None:
    """Take a row out of the catalogue the way the admission door does.

    The door serves every other row with the connection up, so the
    connect raises and setup runs on what was served.
    """
    refused = client.objects.pop(object_id)
    client.connect.side_effect = AmpioNotConfigured(
        objects=((refused.id, refused.name),)
    )


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def _settle(hass: HomeAssistant) -> None:
    """Let the reconcile cooldown elapse and the batch finish.

    The batch runs as an entry background task, which the default
    ``async_block_till_done`` does not wait for.
    """
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
def refused_catalogue() -> AmpioStore:
    """Read the leafless alarm and hidden ghost through the library door."""
    store = build_store(AmpioClient)
    apply_reply(
        store,
        "params_devices",
        {
            "List": [
                {"id": 168, "params": 0, "czas": 0, "url": ""},
                {"id": 99, "params": 16, "czas": 0, "url": ""},
            ]
        },
    )
    apply_reply(
        store, "data_devices", {"List": [LEAFLESS_ALARM_ROW, HIDDEN_LEAFLESS_ROW]}
    )
    return store


@pytest.mark.parametrize("tier", [AccessTier.ADMIN, AccessTier.RESTRICTED])
async def test_leafless_alarm_raises_repair_without_an_entity(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    refused_catalogue: AmpioStore,
    tier: AccessTier,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty leaf leaves other entities loaded and reports only the refused id."""
    set_access_tier(mock_client, tier)
    refused = refused_catalogue.not_configured
    assert refused == ((168, "Alarm strefa garaz"),)
    assert refused_catalogue.objects == {}
    mock_client.connect.side_effect = AmpioNotConfigured(objects=refused)

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id_of(hass, "sensor", unique_id(36))) is not None
    issue = issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE)
    assert issue is not None
    assert issue.translation_key == "not_configured_objects"
    assert issue.translation_placeholders == {
        "object_count": "1",
        "objects": "- 168",
        "module_count": "0",
        "modules": "",
    }
    assert "Alarm strefa garaz" not in caplog.text


# sfId 8 is a placeholder with unknown wire provenance. System rows are
# discarded by type before their leaf is parsed.
@pytest.mark.parametrize("system_type", ["detekcja", "symulacja"])
@pytest.mark.parametrize("leaf_id", ["", "0_cb8f_8_0_2"])
async def test_system_rows_do_not_enter_the_integration_catalogue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    system_type: str,
    leaf_id: str,
) -> None:
    """The library drops system rows before the integration builds entities."""
    store = build_store(AmpioClient)
    rows = [
        {
            **LEAFLESS_ALARM_ROW,
            "id": 62,
            "typ_komponentu": system_type,
            "leafId": leaf_id,
        },
        {
            **LEAFLESS_ALARM_ROW,
            "id": 36,
            "typ_komponentu": "temp",
            "leafId": "0_cb8f_76_0_1",
        },
    ]
    apply_reply(
        store,
        "params_devices",
        {
            "List": [
                {"id": row["id"], "params": 0, "czas": 0, "url": ""} for row in rows
            ]
        },
    )
    apply_reply(store, "data_devices", {"List": rows})
    assert set(store.objects) == {36}
    assert store.not_configured == ()
    mock_client.objects = dict(store.objects)

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(entity_id_of(hass, "sensor", unique_id(36))) is not None


async def test_the_door_event_clears_the_repair_without_an_object_event(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    refused_catalogue: AmpioStore,
) -> None:
    """The door's own event takes the installer repair down.

    A row the door refused is in no catalogue the integration reads, so
    its deletion carries no object event and nothing else announces the
    recovery. No time is advanced here, so the debounced batch has not
    run and the event alone is what cleared the repair.
    """
    mock_client.connect.side_effect = AmpioNotConfigured(
        objects=refused_catalogue.not_configured
    )

    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is not None

    # The installer restored the leaf in Ampio Designer and saved.
    emit(mock_client, NotConfigured())
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.not_configured is None
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None


async def test_the_door_event_raises_the_repair_for_both_faults(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fault that appears while the entry runs raises the repair from the event.

    Both sides ride every event, so one event can name a leafless object
    row and a shared override mac at once, and the text answers for both.
    The event carries the object row's Designer name, and neither the
    issue nor the log may repeat it.
    """
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None

    emit(
        mock_client,
        NotConfigured(
            objects=((168, "Alarm strefa garaz"),), collisions=((52111, (17, 18)),)
        ),
    )
    await hass.async_block_till_done()

    issue = issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE)
    assert issue is not None
    assert issue.translation_key == "not_configured_both"
    assert issue.translation_placeholders == {
        "object_count": "1",
        "objects": "- 168",
        "module_count": "1",
        "modules": "- 0xCB8F: 17, 18",
    }
    assert "Alarm strefa garaz" not in caplog.text


async def test_the_door_event_raises_the_repair_for_an_object_fault_alone(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An objects-only event raises the object text, with the module side empty.

    The event carries the object row's Designer name, and neither the
    issue nor the log may repeat it.
    """
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None

    emit(mock_client, NotConfigured(objects=((168, "Alarm strefa garaz"),)))
    await hass.async_block_till_done()

    issue = issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE)
    assert issue is not None
    assert issue.translation_key == "not_configured_objects"
    assert issue.translation_placeholders == {
        "object_count": "1",
        "objects": "- 168",
        "module_count": "0",
        "modules": "",
    }
    assert "Alarm strefa garaz" not in caplog.text


async def test_the_door_event_raises_the_repair_for_a_module_fault_alone(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A collisions-only event raises the module text, with the object side empty."""
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None

    emit(mock_client, NotConfigured(collisions=((52111, (17, 18)),)))
    await hass.async_block_till_done()

    issue = issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE)
    assert issue is not None
    assert issue.translation_key == "not_configured_modules"
    assert issue.translation_placeholders == {
        "object_count": "0",
        "objects": "",
        "module_count": "1",
        "modules": "- 0xCB8F: 17, 18",
    }


async def test_module_device_survives_while_it_parents_a_refused_child(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The stale repair offers neither a refused row's child nor its module device.

    Removing a parent removes its children, so offering the device would
    delete the very records the installer repair keeps.
    """
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    module = device_registry.async_get_device_by_identifier(PUMP_IDENTIFIER, entry_id)
    assert module is not None

    _refuse(mock_client, PUMP_OBJECT.id)
    await _reload(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is not None
    held = device_registry.async_get_device_by_identifier(PUMP_IDENTIFIER, entry_id)
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(PUMP_OBJECT.id)), entry_id
    )
    assert held is not None
    assert held.id == module.id
    assert child is not None
    stale = find_stale_records(hass, mock_config_entry)
    assert {device.id for device in stale.devices}.isdisjoint({held.id, child.id})
    assert unique_id(PUMP_OBJECT.id) not in {
        entity.unique_id for entity in stale.entities
    }


async def test_a_refusal_during_setup_reaches_the_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A row the door refuses while setup runs is named, not offered for deletion.

    Setup subscribes to the door before it asks the server for anything
    else, so the event lands on a listener. A refused row really is out of
    the catalogue, so without the subscription the repair would stay away
    and the row's records would read as leftovers.
    """
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(PUMP_OBJECT.id)), entry_id
    )
    assert child is not None

    def _refuse_while_fetching() -> dict[int, str]:
        """Refuse the row in the middle of setup's room fetch.

        The row was admitted at connect, so the library evicts the object
        it can no longer admit and reports the door change beside it.
        """
        del mock_client.objects[PUMP_OBJECT.id]
        emit(mock_client, ObjectRemoved(object=PUMP_OBJECT))
        emit(
            mock_client,
            NotConfigured(objects=((PUMP_OBJECT.id, PUMP_OBJECT.name),)),
        )
        return {}

    mock_client.fetch_rooms.side_effect = _refuse_while_fetching
    await _reload(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    issue = issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE)
    assert issue is not None
    assert issue.translation_placeholders["objects"] == f"- {PUMP_OBJECT.id}"
    held = device_registry.async_get_device_by_identifier(PUMP_IDENTIFIER, entry_id)
    assert held is not None
    stale = find_stale_records(hass, mock_config_entry)
    assert {device.id for device in stale.devices}.isdisjoint({held.id, child.id})
    assert unique_id(PUMP_OBJECT.id) not in {
        entity.unique_id for entity in stale.entities
    }


async def test_a_refusal_takes_the_module_controls_down(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A refusal takes the module controls down, as a deletion does.

    The refused row leaves the catalogue, so no object names its mac and
    the report offers the records of the entities on its module device.
    The module row stays admitted, so those entities would otherwise keep
    working while the report offered them for deletion. Unlike a deletion,
    the row is expected back, so the records are what its entities return
    under and the device that parents its child is held.
    """
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    await setup_integration(hass, mock_config_entry)
    button_id = entity_id_of(hass, "button", module_unique_id(PUMP_MAC, "_identify"))
    assert hass.states.get(button_id) is not None

    # Ampio Designer loses the row's leaf while the entry runs. The row
    # was an object, so the library evicts it as it would a deleted one
    # and reports the door change beside it.
    del mock_client.objects[PUMP_OBJECT.id]
    emit(mock_client, ObjectRemoved(object=PUMP_OBJECT))
    emit(mock_client, NotConfigured(objects=((PUMP_OBJECT.id, PUMP_OBJECT.name),)))
    await _settle(hass)

    assert hass.states.get(button_id).attributes.get(ATTR_RESTORED) is True
    held = device_registry.async_get_device_by_identifier(
        PUMP_IDENTIFIER, mock_config_entry.entry_id
    )
    assert held is not None
    stale = find_stale_records(hass, mock_config_entry)
    # The device is held for the refused row's child, so its own records
    # are offered on their own, and nothing built stands behind them now.
    assert held.id not in {device.id for device in stale.devices}
    assert button_id in {record.entity_id for record in stale.entities}


async def test_deleting_a_refused_row_surfaces_its_records(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A row deleted while it stood refused stops being held out of the offer.

    The object left the catalogue when the door refused it, so its
    deletion carries no second removal event, and the door's own event is
    the only thing that announces it.
    """
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    _refuse(mock_client, PUMP_OBJECT.id)
    await _reload(hass, mock_config_entry)
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(PUMP_OBJECT.id)), entry_id
    )
    assert child is not None
    held = find_stale_records(hass, mock_config_entry)
    assert child.id not in {device.id for device in held.devices}

    # The installer deleted the row in Ampio Designer, so the door has
    # nothing left to refuse. No object event carries that, because the
    # row left the catalogue when it was refused.
    emit(mock_client, NotConfigured())
    await _settle(hass)

    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None
    stale = find_stale_records(hass, mock_config_entry)
    assert child.id in {device.id for device in stale.devices}
    issue = issue_registry.async_get_issue(DOMAIN, STALE_RECORDS_ISSUE)
    assert issue is not None
    assert f"- {PUMP_OBJECT.name}" in issue.translation_placeholders["names"]


async def test_a_row_refused_at_setup_comes_back_under_its_module(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A row refused at setup, alone on its module, comes back once Designer is fixed.

    The entity is live again, no misparent warning is logged, and the
    stale report offers neither the object's child device nor its module
    device.
    """
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    switch_id = entity_id_of(hass, "switch", unique_id(PUMP_OBJECT.id))
    _refuse(mock_client, PUMP_OBJECT.id)
    await _reload(hass, mock_config_entry)
    assert hass.states.get(switch_id).attributes.get(ATTR_RESTORED) is True

    # The installer checks the box again, so the door admits the row.
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    caplog.clear()
    emit(mock_client, ObjectAdded(object=PUMP_OBJECT))
    emit(mock_client, NotConfigured())
    await _settle(hass)

    state = hass.states.get(switch_id)
    assert state.state != STATE_UNAVAILABLE
    assert state.attributes.get(ATTR_RESTORED) is None
    assert "hangs under a different module" not in caplog.text
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None
    module = device_registry.async_get_device_by_identifier(PUMP_IDENTIFIER, entry_id)
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(PUMP_OBJECT.id)), entry_id
    )
    assert module is not None
    assert child is not None
    assert child.parent_device_id == module.id
    stale = find_stale_records(hass, mock_config_entry)
    assert {device.id for device in stale.devices}.isdisjoint({module.id, child.id})


async def test_a_dead_entity_on_a_held_module_is_still_offered(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The held device's own entities are listed on their own.

    A module device the stale repair offers covers the entities that sit
    on it, and none of those is listed again. This device is held
    instead, so each of its unclaimed entities is offered by entity id.
    """
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    module = device_registry.async_get_device_by_identifier(PUMP_IDENTIFIER, entry_id)
    assert module is not None
    registry = er.async_get(hass)
    on_the_module = {
        entity_id
        for domain, suffix in (
            ("button", "_identify"),
            ("sensor", "_voltage"),
            ("sensor", "_temperature"),
        )
        if (
            entity_id := registry.async_get_entity_id(
                domain, DOMAIN, module_unique_id(PUMP_MAC, suffix)
            )
        )
        is not None
    }
    assert on_the_module

    _refuse(mock_client, PUMP_OBJECT.id)
    await _reload(hass, mock_config_entry)

    stale = find_stale_records(hass, mock_config_entry)
    assert on_the_module <= {entity.entity_id for entity in stale.entities}
    issue = issue_registry.async_get_issue(DOMAIN, STALE_RECORDS_ISSUE)
    assert issue is not None
    for entity_id in on_the_module:
        assert f"- {entity_id}" in issue.translation_placeholders["names"]


async def test_collision_keeps_the_colliding_macs_module_entities_out_of_the_stale_offer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A collision on a still-live mac keeps that mac's module records out of both stale lists.

    A colliding mac empties the capability map, so a capability-gated
    module entity such as the buzzer stops being built on the next
    connect. An object still resolves to the mac, so the record is a
    module record the catalogue accounts for and there is no cause to
    name for it. A module prefix that drifted from the stem the entity
    builds its key with would read the record as a shape nothing mints
    and offer it, which is what this guards.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)
    buzzer_key = module_unique_id(52111, "_buzzer")
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("siren", DOMAIN, buzzer_key)
    assert entity_id is not None

    def _claimed() -> set[str]:
        return {
            claimed_id
            for platform in entity_platform.async_get_platforms(hass, DOMAIN)
            for claimed_id in platform.entities
        }

    assert entity_id in _claimed()

    mock_client.capabilities[52111] = {}
    mock_client.connect.side_effect = AmpioNotConfigured(
        collisions=((52111, (17, 18)),)
    )
    await _reload(hass, mock_config_entry)

    # The registry record survives the reload, but the capability gate
    # keeps the buzzer platform from rebuilding it, so no loaded platform
    # claims it, which is what makes it a stale-list candidate at all.
    assert entity_id not in _claimed()
    stale = find_stale_records(hass, mock_config_entry)
    offered = {entity.unique_id for entity in stale.entities} | {
        entity.unique_id for entity in stale.withheld
    }
    assert buzzer_key not in offered
