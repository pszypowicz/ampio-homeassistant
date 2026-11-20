"""Tests for the Ampio event platform."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

from ampio_mqtt import AccessTier, AvailabilityChanged, BusEventRaised
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import BUS_EVENT_KEY, DOMAIN
from custom_components.ampio.stale import find_stale_records
from homeassistant.components.event import ATTR_EVENT_TYPE
from homeassistant.const import (
    CONF_USERNAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from . import setup_integration
from .conftest import emit, entity_id_of, set_access_tier


def BUS_EVENT_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id of the hub's bus event entity."""
    return entity_id_of(hass, "event", BUS_EVENT_KEY)


@pytest.fixture(autouse=True)
def event_only() -> Generator[None]:
    """Limit setup to the event platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.EVENT]):
        yield


async def _downgrade(
    hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry
) -> None:
    """Point the entry at a standard account and reload it."""
    set_access_tier(client, AccessTier.RESTRICTED)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_USERNAME: "user"}
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_client")
async def test_all_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every entity's registry entry and state."""
    await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("mock_client")
async def test_bus_event_sits_on_the_hub(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The bus is the install's, so its entity attaches to the M-SERV device."""
    await setup_integration(hass, mock_config_entry)

    record = entity_registry.async_get(BUS_EVENT_ENTITY_ID(hass))
    assert record is not None
    assert record.entity_category is None
    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, "hub"), mock_config_entry.entry_id
    )
    assert hub is not None
    assert record.device_id == hub.id


async def test_no_bus_event_on_a_standard_account(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Received events ride the raw tree, which a standard login is not served."""
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_USERNAME: "user"}
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id("event", DOMAIN, BUS_EVENT_KEY) is None
    assert not hass.states.async_entity_ids("event")


async def test_bus_event_fires_with_number_and_source(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Each event fires the entity with its number and the mac that raised it."""
    await setup_integration(hass, mock_config_entry)
    entity_id = BUS_EVENT_ENTITY_ID(hass)
    assert hass.states.get(entity_id).state == STATE_UNKNOWN

    emit(mock_client, BusEventRaised(event_number=4, mac=1))
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert state.attributes[ATTR_EVENT_TYPE] == "raised"
    assert state.attributes["event_number"] == 4
    assert state.attributes["source"] == "0x1"


async def test_bus_event_fires_again_for_the_same_number(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A repeated number is a new event, so automations see every press."""
    await setup_integration(hass, mock_config_entry)
    entity_id = BUS_EVENT_ENTITY_ID(hass)
    changes: list[str] = []
    async_track_state_change_event(
        hass, entity_id, lambda event: changes.append(event.data["new_state"].state)
    )

    emit(mock_client, BusEventRaised(event_number=4, mac=0xCB8F))
    await hass.async_block_till_done()
    freezer.tick(1)
    emit(mock_client, BusEventRaised(event_number=4, mac=0xCB8F))
    await hass.async_block_till_done()

    assert len(changes) == 2
    assert changes[0] != changes[1]
    assert hass.states.get(entity_id).attributes["source"] == "0xCB8F"


async def test_bus_event_reports_the_discovery_refresh_event(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Event 254 from the M-SERV is reported like any other, never filtered."""
    await setup_integration(hass, mock_config_entry)

    emit(mock_client, BusEventRaised(event_number=254, mac=1))
    await hass.async_block_till_done()

    assert hass.states.get(BUS_EVENT_ENTITY_ID(hass)).attributes["event_number"] == 254


async def test_bus_event_follows_the_connection(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entity is unavailable while the broker is down, and back after."""
    await setup_integration(hass, mock_config_entry)
    entity_id = BUS_EVENT_ENTITY_ID(hass)

    mock_client.available = False
    emit(mock_client, AvailabilityChanged(available=False))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    mock_client.available = True
    emit(mock_client, AvailabilityChanged(available=True))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNKNOWN


async def test_bus_event_record_is_never_stale_on_the_administrator_login(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The bus answers to the entry, as the hub does, so the report keeps it."""
    await setup_integration(hass, mock_config_entry)

    stale = find_stale_records(hass, mock_config_entry)

    assert BUS_EVENT_KEY not in {record.unique_id for record in stale.entities}
    assert BUS_EVENT_KEY not in {record.unique_id for record in stale.withheld}


async def test_bus_event_is_withheld_after_a_downgrade(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A downgrade keeps the record and names it as withheld, not as stale."""
    await setup_integration(hass, mock_config_entry)
    entity_id = BUS_EVENT_ENTITY_ID(hass)

    await _downgrade(hass, mock_client, mock_config_entry)

    assert entity_registry.async_get(entity_id) is not None
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert mock_config_entry.runtime_data.withheld_unique_ids() == {BUS_EVENT_KEY}
    stale = find_stale_records(hass, mock_config_entry)
    assert [record.unique_id for record in stale.withheld] == [BUS_EVENT_KEY]
    assert not stale.entities
