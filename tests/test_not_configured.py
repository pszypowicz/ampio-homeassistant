"""Tests for catalogue admission and the installer repair."""

from datetime import timedelta
from unittest.mock import MagicMock

from ampio_mqtt import AccessTier, AmpioNotConfigured, ModuleUpdated
from ampio_mqtt._protocol import ENDPOINT_BY_NAME
from ampio_mqtt._store import AmpioStore
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
from custom_components.ampio.stale import async_check_not_configured, find_stale_records
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.util import dt as dt_util

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

    The door serves every other row with the connection up, so both the
    connect and the later door reads raise while the row stands.
    """
    refused = client.objects.pop(object_id)
    error = AmpioNotConfigured(objects=((refused.id, refused.name),))
    client.connect.side_effect = error
    client.wait_for_initial_discovery.side_effect = error


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def refused_catalogue() -> AmpioStore:
    """Read the leafless alarm and hidden ghost through the library door."""
    store = AmpioStore()
    store.apply_endpoint(
        ENDPOINT_BY_NAME["params_devices"],
        {
            "List": [
                {"id": 168, "params": 0, "czas": 0, "url": ""},
                {"id": 99, "params": 16, "czas": 0, "url": ""},
            ]
        },
    )
    store.apply_endpoint(
        ENDPOINT_BY_NAME["data_devices"],
        {"List": [LEAFLESS_ALARM_ROW, HIDDEN_LEAFLESS_ROW]},
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
    error = refused_catalogue.admission_failure()
    assert isinstance(error, AmpioNotConfigured)
    assert error.objects == ((168, "Alarm strefa garaz"),)
    assert refused_catalogue.objects == {}
    mock_client.connect.side_effect = error
    mock_client.wait_for_initial_discovery.side_effect = error

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

    mock_client.wait_for_initial_discovery.side_effect = None
    await async_check_not_configured(hass, mock_config_entry)

    assert mock_config_entry.runtime_data.not_configured is None
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None


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
    store = AmpioStore()
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
    store.apply_endpoint(
        ENDPOINT_BY_NAME["params_devices"],
        {
            "List": [
                {"id": row["id"], "params": 0, "czas": 0, "url": ""} for row in rows
            ]
        },
    )
    store.apply_endpoint(ENDPOINT_BY_NAME["data_devices"], {"List": rows})
    assert set(store.objects) == {36}
    assert store.admission_failure() is None
    mock_client.objects = dict(store.objects)

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(pinned_id("sensor", 36)) is not None


async def test_not_configured_repair_clears_without_an_object_event(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    refused_catalogue: AmpioStore,
) -> None:
    """A batch that queues no object clears the installer repair.

    A row the door refused is in no catalogue the integration reads, so
    its deletion carries no object event and the batch that notices has
    nothing to reconcile. The door is re-read ahead of the empty-batch
    return, and the room map is not, so the count of room fetches says
    the batch stayed empty.
    """
    error = refused_catalogue.admission_failure()
    assert isinstance(error, AmpioNotConfigured)
    mock_client.connect.side_effect = error
    mock_client.wait_for_initial_discovery.side_effect = error

    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is not None
    fetched_rooms = mock_client.fetch_rooms.await_count

    # The installer restored the leaf in Ampio Designer and saved.
    mock_client.wait_for_initial_discovery.side_effect = None
    emit(mock_client, ModuleUpdated(module=mock_client.modules[17]))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_config_entry.runtime_data.not_configured is None
    assert issue_registry.async_get_issue(DOMAIN, NOT_CONFIGURED_ISSUE) is None
    assert mock_client.fetch_rooms.await_count == fetched_rooms


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
