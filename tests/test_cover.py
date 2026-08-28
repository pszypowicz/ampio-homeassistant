"""Tests for the Ampio cover platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import ObjectUpdated
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_COVER,
    SERVICE_CLOSE_COVER_TILT,
    SERVICE_OPEN_COVER,
    SERVICE_OPEN_COVER_TILT,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
    SERVICE_STOP_COVER,
    SERVICE_STOP_COVER_TILT,
    STATE_CLOSED,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import MSENS_SLUG, emit

PLAIN_ENTITY_ID = f"cover.{MSENS_SLUG}_roleta_sypialnia"
POSITION_ENTITY_ID = f"cover.{MSENS_SLUG}_roleta_kuchnia"
TILT_ENTITY_ID = f"cover.{MSENS_SLUG}_zaluzja_goscinny"


@pytest.fixture(autouse=True)
def cover_only() -> Generator[None]:
    """Limit setup to the cover platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.COVER]):
        yield


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


async def test_travel_services_map_to_verbs(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Open, close, and stop map to the plain travel verbs."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: POSITION_ENTITY_ID},
        blocking=True,
    )
    mock_client.open.assert_awaited_once_with(82)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_CLOSE_COVER,
        {ATTR_ENTITY_ID: POSITION_ENTITY_ID},
        blocking=True,
    )
    mock_client.close.assert_awaited_once_with(82)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_STOP_COVER,
        {ATTR_ENTITY_ID: POSITION_ENTITY_ID},
        blocking=True,
    )
    mock_client.stop.assert_awaited_once_with(82)


async def test_set_position_maps_to_percent(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The position service passes the percent through unchanged."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: POSITION_ENTITY_ID, ATTR_POSITION: 60},
        blocking=True,
    )
    mock_client.set_roller_pos.assert_awaited_once_with(82, 60)


async def test_tilt_services_map_to_lamella(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Tilt set, open, and close drive the lamella axis."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_TILT_POSITION,
        {ATTR_ENTITY_ID: TILT_ENTITY_ID, ATTR_TILT_POSITION: 25},
        blocking=True,
    )
    mock_client.set_roller_lamella.assert_awaited_once_with(83, 25)
    mock_client.set_roller_lamella.reset_mock()

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER_TILT,
        {ATTR_ENTITY_ID: TILT_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_roller_lamella.assert_awaited_once_with(83, 100)
    mock_client.set_roller_lamella.reset_mock()

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_CLOSE_COVER_TILT,
        {ATTR_ENTITY_ID: TILT_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_roller_lamella.assert_awaited_once_with(83, 0)


async def test_stop_tilt_maps_to_stop(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Stopping slat rotation uses the stop verb, which halts either axis."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_STOP_COVER_TILT,
        {ATTR_ENTITY_ID: TILT_ENTITY_ID},
        blocking=True,
    )
    mock_client.stop.assert_awaited_once_with(83)


async def test_push_echo_updates_position(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed travel echo updates the reported position."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[82], state="80")
    mock_client.objects[82] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    state = hass.states.get(POSITION_ENTITY_ID)
    assert state.attributes[ATTR_CURRENT_POSITION] == 80


async def test_zero_position_reads_closed(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Position zero means fully closed."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[82], state="0")
    mock_client.objects[82] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(POSITION_ENTITY_ID).state == STATE_CLOSED


async def test_plain_cover_has_no_position(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A cover without a position axis reports neither position nor closed."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(PLAIN_ENTITY_ID)
    assert ATTR_CURRENT_POSITION not in state.attributes
    assert state.state == "unknown"
