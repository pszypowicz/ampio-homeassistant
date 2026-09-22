"""Tests for catalogue admission and the installer repair."""

from unittest.mock import MagicMock

from ampio_mqtt import AccessTier, AmpioClient, AmpioNotConfigured, NotConfigured
from ampio_mqtt.testing import AmpioStore, apply_reply, build_store
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ampio.const import (
    DOMAIN,
    NOT_CONFIGURED_ISSUE,
    STALE_RECORDS_ISSUE,
)
from custom_components.ampio.stale import find_stale_records
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from . import setup_integration
from .conftest import (
    HIDDEN_LEAFLESS_ROW,
    LEAFLESS_ALARM_ROW,
    emit,
    make_object,
    module_pinned_id,
    pinned_id,
    set_access_tier,
    unique_id,
)

# A module mac no seeded object carries, so the objects put on it here are
# the only ones that resolve to its device.
PUMP_MAC = 53257
PUMP_IDENTIFIER = (DOMAIN, f"module_mac:{PUMP_MAC}")
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
    assert hass.states.get(pinned_id("sensor", 36)) is not None
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

    assert hass.states.get(pinned_id("sensor", 36)) is not None


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
    on_the_module = {
        entity_id
        for entity_id in (
            module_pinned_id("button", PUMP_MAC, "_identify"),
            module_pinned_id("sensor", PUMP_MAC, "_voltage"),
            module_pinned_id("sensor", PUMP_MAC, "_temperature"),
        )
        if hass.states.get(entity_id) is not None
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
