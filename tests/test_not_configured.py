"""Tests for catalogue admission and the installer repair."""

from datetime import timedelta
import json
from pathlib import Path
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
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.ampio.const import (
    DOMAIN,
    NOT_CONFIGURED_ISSUE,
    STALE_RECORDS_ISSUE,
    UNRECOGNIZED_OBJECTS_ISSUE,
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
    EMPTY_SWEEP,
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

MANIFEST = Path(__file__).parent.parent / "custom_components/ampio/manifest.json"

# A module mac no seeded object carries, so the objects put on it here are
# the only ones that resolve to its device.
PUMP_MAC = 53257
PUMP_IDENTIFIER = (DOMAIN, f"module_mac:{format_mac(PUMP_MAC)}")
PUMP_OBJECT = make_object(
    300, "przekaznik", 0, leaf_id="0_d009_257_2_1", funkcja=30, name="Pompa", state="0"
)

# A relay on an Ampio LoRa device. Its leaf starts with the LoRa group, a
# shape the library does not read, so the door refuses the row alone.
UNRECOGNIZED_ROW = {
    **LEAFLESS_ALARM_ROW,
    "id": 170,
    "typ_komponentu": "przekaznik",
    "funkcja": 30,
    "leafId": "1_1a2b_257_2_0",
    "opis_menu": "Przekaznik ogrod",
}


def _refuse(client: MagicMock, object_id: int, *, unrecognized: bool = False) -> None:
    """Take a row out of the catalogue the way the admission door does.

    The door serves every other row with the connection up, so the
    connect raises and setup runs on what was served. The row is refused
    as leafless, or as unrecognized when asked.
    """
    obj = client.objects.pop(object_id)
    refused = ((obj.id, obj.name),)
    client.connect.side_effect = (
        AmpioNotConfigured(unrecognized=refused)
        if unrecognized
        else AmpioNotConfigured(leafless=refused)
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


@pytest.fixture
def unrecognized_catalogue() -> AmpioStore:
    """Read a row with a leaf outside the CAN group through the library door."""
    store = build_store(AmpioClient)
    apply_reply(
        store,
        "params_devices",
        {"List": [{"id": UNRECOGNIZED_ROW["id"], "params": 0, "czas": 0, "url": ""}]},
    )
    apply_reply(store, "data_devices", {"List": [UNRECOGNIZED_ROW]})
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
    refused = refused_catalogue.leafless
    assert refused == ((168, "Alarm strefa garaz"),)
    assert refused_catalogue.objects == {}
    mock_client.connect.side_effect = AmpioNotConfigured(leafless=refused)

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


@pytest.mark.parametrize("tier", [AccessTier.ADMIN, AccessTier.RESTRICTED])
async def test_unrecognized_leaf_raises_the_report_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    unrecognized_catalogue: AmpioStore,
    tier: AccessTier,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A leaf the library cannot read raises the report repair, not the Designer one.

    The repair names the refused id, offers no fix flow, and links the
    issue tracker the manifest declares. Neither the repair nor the log
    repeats the row's Designer name.
    """
    set_access_tier(mock_client, tier)
    refused = unrecognized_catalogue.unrecognized
    assert refused == ((170, "Przekaznik ogrod"),)
    assert unrecognized_catalogue.objects == {}
    mock_client.connect.side_effect = AmpioNotConfigured(unrecognized=refused)

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id_of(hass, "sensor", unique_id(36))) is not None
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None
    issue = issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE)
    assert issue is not None
    assert issue.translation_key == "unrecognized_objects"
    assert issue.is_fixable is False
    assert (
        issue.learn_more_url
        == json.loads(MANIFEST.read_text(encoding="utf-8"))["issue_tracker"]
    )
    assert issue.translation_placeholders == {"object_count": "1", "objects": "- 170"}
    assert any(
        record.levelname == "WARNING" and "[170]" in record.getMessage()
        for record in caplog.records
    )
    assert "Przekaznik ogrod" not in caplog.text


async def test_the_door_event_raises_and_clears_the_report_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An unrecognized row that appears while the entry runs raises the repair, and the recovery clears it."""
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE) is None

    emit(mock_client, NotConfigured(unrecognized=((170, "Przekaznik ogrod"),)))
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE)

    emit(mock_client, NotConfigured())
    await hass.async_block_till_done()
    assert mock_config_entry.runtime_data.not_configured is None
    assert issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE) is None


async def test_each_refusal_repair_follows_its_own_side(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A leafless and an unrecognized row raise one repair each, and each names its own row.

    When the unrecognized side empties, its repair goes and the Designer
    repair stays.
    """
    await setup_integration(hass, mock_config_entry)

    emit(
        mock_client,
        NotConfigured(
            leafless=((168, "Alarm strefa garaz"),),
            unrecognized=((170, "Przekaznik ogrod"),),
        ),
    )
    await hass.async_block_till_done()

    designer = issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE)
    assert designer is not None
    assert designer.translation_key == "not_configured_objects"
    assert designer.translation_placeholders == {
        "object_count": "1",
        "objects": "- 168",
        "module_count": "0",
        "modules": "",
    }
    report = issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE)
    assert report is not None
    assert report.translation_placeholders == {"object_count": "1", "objects": "- 170"}

    emit(mock_client, NotConfigured(leafless=((168, "Alarm strefa garaz"),)))
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is not None
    assert issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE) is None


async def test_removing_the_entry_clears_both_refusal_repairs(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Neither refusal repair outlives the entry it was raised for."""
    mock_client.connect.side_effect = AmpioNotConfigured(
        leafless=((168, "Alarm strefa garaz"),),
        unrecognized=((170, "Przekaznik ogrod"),),
    )
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is not None
    assert issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE)

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None
    assert issue_registry.async_get_issue(DOMAIN, UNRECOGNIZED_OBJECTS_ISSUE) is None


async def test_an_unrecognized_row_keeps_its_records_out_of_the_stale_offer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The stale repair offers neither an unrecognized row's child nor its entity.

    The server still serves the row, so its records are what the entities
    come back under once the library reads its leaf.
    """
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id

    _refuse(mock_client, PUMP_OBJECT.id, unrecognized=True)
    await _reload(hass, mock_config_entry)

    held = device_registry.async_get_device_by_identifier(PUMP_IDENTIFIER, entry_id)
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(PUMP_OBJECT.id)), entry_id
    )
    assert held is not None
    assert child is not None
    stale = find_stale_records(hass, mock_config_entry)
    assert {device.id for device in stale.devices}.isdisjoint({held.id, child.id})
    assert unique_id(PUMP_OBJECT.id) not in {
        entity.unique_id for entity in stale.entities
    }


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
    assert store.leafless == ()
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
        leafless=refused_catalogue.leafless
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
            leafless=((168, "Alarm strefa garaz"),), collisions=((52111, (17, 18)),)
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

    emit(mock_client, NotConfigured(leafless=((168, "Alarm strefa garaz"),)))
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
            NotConfigured(leafless=((PUMP_OBJECT.id, PUMP_OBJECT.name),)),
        )
        return {}

    mock_client.fetch_rooms.side_effect = _refuse_while_fetching
    await _reload(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    issue = issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE)
    assert issue is not None
    assert issue.translation_placeholders == {
        "object_count": "1",
        "objects": f"- {PUMP_OBJECT.id}",
        "module_count": "0",
        "modules": "",
    }
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
    emit(mock_client, NotConfigured(leafless=((PUMP_OBJECT.id, PUMP_OBJECT.name),)))
    await _settle(hass)

    button = hass.states.get(button_id)
    assert button is not None
    assert button.attributes.get(ATTR_RESTORED) is True
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
    placeholders = issue.translation_placeholders
    assert placeholders is not None
    assert f"- {PUMP_OBJECT.name}" in placeholders["names"]


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
    restored = hass.states.get(switch_id)
    assert restored is not None
    assert restored.attributes.get(ATTR_RESTORED) is True

    # The installer checks the box again, so the door admits the row.
    mock_client.objects[PUMP_OBJECT.id] = PUMP_OBJECT
    caplog.clear()
    emit(mock_client, ObjectAdded(object=PUMP_OBJECT))
    emit(mock_client, NotConfigured())
    await _settle(hass)

    state = hass.states.get(switch_id)
    assert state is not None
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
    placeholders = issue.translation_placeholders
    assert placeholders is not None
    for entity_id in on_the_module:
        assert f"- {entity_id}" in placeholders["names"]


@pytest.mark.parametrize("answered", [True, False])
async def test_collision_keeps_the_colliding_macs_module_entities_out_of_the_stale_offer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    answered: bool,
) -> None:
    """A collision on a still-live mac keeps that mac's module records out of both stale lists.

    The door evicts the colliding rows with their capability maps, and the
    record sweep at the next connect fills the map again for every mac the
    server's module records answer, the colliding one included. So the
    buzzer is built again when the sweep answers the mac, and stays
    unbuilt when the module is silent through it. Either way an object
    still resolves to the mac, so the record is one the catalogue accounts
    for and the report has no cause to name for it.
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

    # The door evicts the colliding row, and its capability map with it.
    del mock_client.modules[17]
    answered_map = mock_client.capabilities.pop(52111)

    async def _sweep(*_args: object, **_kwargs: object) -> object:
        """Refill the map only for a module that answers the sweep."""
        if answered:
            mock_client.capabilities[52111] = answered_map
        return EMPTY_SWEEP

    mock_client.resolve_records.side_effect = _sweep
    mock_client.connect.side_effect = AmpioNotConfigured(
        collisions=((52111, (17, 18)),)
    )
    await _reload(hass, mock_config_entry)

    assert mock_config_entry.runtime_data.module_row_for(52111) is None
    assert (entity_id in _claimed()) is answered
    stale = find_stale_records(hass, mock_config_entry)
    offered = {entity.unique_id for entity in stale.entities} | {
        entity.unique_id for entity in stale.withheld
    }
    assert buzzer_key not in offered
