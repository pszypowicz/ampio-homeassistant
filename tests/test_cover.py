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
    CoverEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit, pinned_id

PLAIN_ENTITY_ID = pinned_id("cover", 81)
POSITION_ENTITY_ID = pinned_id("cover", 82)
TILT_ENTITY_ID = pinned_id("cover", 83)


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


@pytest.mark.parametrize(
    ("block", "expected_missing"),
    [
        pytest.param(0, CoverEntityFeature(0), id="no-lock"),
        pytest.param(1, CoverEntityFeature.CLOSE, id="closing-blocked"),
        pytest.param(2, CoverEntityFeature.OPEN, id="opening-blocked"),
        pytest.param(
            3,
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION,
            id="both-blocked",
        ),
    ],
)
async def test_a_lock_drops_the_feature_it_refuses(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    block: int,
    expected_missing: CoverEntityFeature,
) -> None:
    """Each lock bit removes the travel service the module would drop."""
    await setup_integration(hass, mock_config_entry)
    unlocked = hass.states.get(POSITION_ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]

    obj = replace(mock_client.objects[82], block=block)
    mock_client.objects[82] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    features = hass.states.get(POSITION_ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]
    assert features == unlocked & ~expected_missing
    assert CoverEntityFeature.STOP & features


async def test_a_travel_lock_leaves_the_slats_alone(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A cover locked both ways keeps every tilt service.

    The library documents the lock on the travel axis alone, and whether it
    also stops the slats cannot be tested without driving a real blocked
    cover. Keeping the tilt features preserves the behavior that exists.
    """
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[83], block=3)
    mock_client.objects[83] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    features = hass.states.get(TILT_ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]
    assert features == (
        CoverEntityFeature.STOP
        | CoverEntityFeature.SET_TILT_POSITION
        | CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
        | CoverEntityFeature.STOP_TILT
    )


@pytest.mark.parametrize(
    ("block", "target", "expected_key"),
    [
        pytest.param(2, 80, "cover_blocked_opening", id="opening-blocked-moves-up"),
        pytest.param(1, 10, "cover_blocked_closing", id="closing-blocked-moves-down"),
    ],
)
async def test_a_blocked_position_move_is_refused(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    block: int,
    target: int,
    expected_key: str,
) -> None:
    """A position move in the locked direction raises instead of going out.

    The feature stays, because the other direction still runs, so nothing
    but the move's own direction can decide this one. The translation key
    is the only thing that tells the two blocked directions apart, so it
    is what proves the module's message names the direction that is
    actually locked.
    """
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[82], block=block)
    mock_client.objects[82] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_SET_COVER_POSITION,
            {ATTR_ENTITY_ID: POSITION_ENTITY_ID, ATTR_POSITION: target},
            blocking=True,
        )
    assert excinfo.value.translation_key == expected_key
    mock_client.set_roller_pos.assert_not_awaited()


async def test_an_unknown_position_allows_the_move(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Without a reported position, nothing can decide a direction, so the move goes out.

    A position-capable cover grants ``SET_POSITION`` from its kind alone, so
    it can offer the service before a parsable state has ever arrived.
    """
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[82], state=None, block=2)
    mock_client.objects[82] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: POSITION_ENTITY_ID, ATTR_POSITION: 50},
        blocking=True,
    )
    mock_client.set_roller_pos.assert_awaited_once_with(82, 50)


@pytest.mark.parametrize(
    ("block", "target"),
    [
        pytest.param(2, 10, id="opening-blocked-moves-down"),
        pytest.param(1, 80, id="closing-blocked-moves-up"),
        pytest.param(2, 35, id="opening-blocked-holds-still"),
    ],
)
async def test_a_move_the_lock_allows_goes_out(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    block: int,
    target: int,
) -> None:
    """One locked direction leaves the other one working."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[82], block=block)
    mock_client.objects[82] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: POSITION_ENTITY_ID, ATTR_POSITION: target},
        blocking=True,
    )
    mock_client.set_roller_pos.assert_awaited_once_with(82, target)
