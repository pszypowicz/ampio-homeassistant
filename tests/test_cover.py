"""Tests for the Ampio cover platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import AccessTier, AmpioValueError, ObjectRemoved, ObjectUpdated
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
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
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotSupported, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit, pinned_id, set_access_tier

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


@pytest.mark.parametrize(
    ("block", "expected_missing"),
    [
        pytest.param(0, CoverEntityFeature(0), id="no-lock"),
        pytest.param(
            1,
            CoverEntityFeature.CLOSE | CoverEntityFeature.CLOSE_TILT,
            id="closing-blocked",
        ),
        pytest.param(
            2,
            CoverEntityFeature.OPEN | CoverEntityFeature.OPEN_TILT,
            id="opening-blocked",
        ),
        pytest.param(
            3,
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
            | CoverEntityFeature.OPEN_TILT
            | CoverEntityFeature.CLOSE_TILT
            | CoverEntityFeature.SET_TILT_POSITION,
            id="both-blocked",
        ),
    ],
)
async def test_a_lock_drops_the_tilt_feature_it_refuses(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    block: int,
    expected_missing: CoverEntityFeature,
) -> None:
    """Each lock bit removes the slat service the module would drop.

    Measured on a blind under a Designer rule that blocks one direction:
    a slat turn toward open was dropped in silence and one toward closed
    ran, matching what the same lock did to the travel. Both stop services
    stay, because the allowed direction can still be running.
    """
    await setup_integration(hass, mock_config_entry)
    unlocked = hass.states.get(TILT_ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]

    obj = replace(mock_client.objects[83], block=block)
    mock_client.objects[83] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    features = hass.states.get(TILT_ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]
    assert features == unlocked & ~expected_missing
    assert CoverEntityFeature.STOP & features
    assert CoverEntityFeature.STOP_TILT & features


@pytest.mark.parametrize(
    ("block", "target", "expected_key"),
    [
        pytest.param(2, 80, "cover_blocked_opening", id="opening-blocked-turns-up"),
        pytest.param(1, 10, "cover_blocked_closing", id="closing-blocked-turns-down"),
    ],
)
async def test_a_blocked_tilt_move_is_refused(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    block: int,
    target: int,
    expected_key: str,
) -> None:
    """A slat turn in the locked direction raises instead of going out.

    The fixture's slats sit at 40, so the target decides the direction the
    same way a position move does.
    """
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[83], block=block)
    mock_client.objects[83] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_SET_COVER_TILT_POSITION,
            {ATTR_ENTITY_ID: TILT_ENTITY_ID, ATTR_TILT_POSITION: target},
            blocking=True,
        )
    assert excinfo.value.translation_key == expected_key
    mock_client.set_roller_lamella.assert_not_awaited()


@pytest.mark.parametrize(
    ("block", "target"),
    [
        pytest.param(2, 10, id="opening-blocked-turns-down"),
        pytest.param(1, 80, id="closing-blocked-turns-up"),
    ],
)
async def test_a_tilt_move_the_lock_allows_goes_out(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    block: int,
    target: int,
) -> None:
    """One locked direction leaves the other one running on the slat axis too."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[83], block=block)
    mock_client.objects[83] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_TILT_POSITION,
        {ATTR_ENTITY_ID: TILT_ENTITY_ID, ATTR_TILT_POSITION: target},
        blocking=True,
    )
    mock_client.set_roller_lamella.assert_awaited_once_with(83, target)


async def test_an_unknown_tilt_allows_the_move(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Without a reported slat angle, nothing can decide a direction."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[83], lammel=None, block=2)
    mock_client.objects[83] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_TILT_POSITION,
        {ATTR_ENTITY_ID: TILT_ENTITY_ID, ATTR_TILT_POSITION: 90},
        blocking=True,
    )
    mock_client.set_roller_lamella.assert_awaited_once_with(83, 90)


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


@pytest.mark.parametrize(
    ("block", "service", "verb"),
    [
        pytest.param(2, SERVICE_OPEN_COVER, "open", id="opening-blocked"),
        pytest.param(1, SERVICE_CLOSE_COVER, "close", id="closing-blocked"),
    ],
)
async def test_a_blocked_travel_service_is_not_supported(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    block: int,
    service: str,
    verb: str,
) -> None:
    """Calling the travel service the lock refuses raises instead of doing nothing."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[82], block=block)
    mock_client.objects[82] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    with pytest.raises(ServiceNotSupported):
        await hass.services.async_call(
            COVER_DOMAIN,
            service,
            {ATTR_ENTITY_ID: POSITION_ENTITY_ID},
            blocking=True,
        )
    getattr(mock_client, verb).assert_not_awaited()


@pytest.mark.parametrize("read_only", [False, True], ids=["writable", "read-only"])
async def test_read_only_object_drops_every_feature(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    read_only: bool,
) -> None:
    """A read-only object drops every feature and refuses every service.

    Designer's read-only checkbox refuses every verb behind the object, on
    every axis at once, unlike a lock rule that refuses one direction. With
    no control left to offer, the feature set empties rather than one
    direction dropping out, so a service call raises the same way core
    already raises for a locked direction, and the client verb it would
    have sent is never awaited. The writable case pushes the same update
    with its params unchanged, so it exercises the same rebuild rather than
    reading the state the entity started with.
    """
    await setup_integration(hass, mock_config_entry)
    unblocked = hass.states.get(POSITION_ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]
    assert unblocked

    obj = mock_client.objects[82]
    params = obj.params | (1 << 6) if read_only else obj.params
    mock_client.objects[82] = replace(obj, params=params)
    emit(mock_client, ObjectUpdated(object=mock_client.objects[82]))
    await hass.async_block_till_done()

    features = hass.states.get(POSITION_ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]

    if read_only:
        assert features == CoverEntityFeature(0)
        with pytest.raises(ServiceNotSupported):
            await hass.services.async_call(
                COVER_DOMAIN,
                SERVICE_OPEN_COVER,
                {ATTR_ENTITY_ID: POSITION_ENTITY_ID},
                blocking=True,
            )
        mock_client.open.assert_not_awaited()
    else:
        assert features == unblocked
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_OPEN_COVER,
            {ATTR_ENTITY_ID: POSITION_ENTITY_ID},
            blocking=True,
        )
        mock_client.open.assert_awaited_once_with(82)


async def test_removed_object_becomes_unavailable(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Evicting the backing object makes the entity unavailable."""
    await setup_integration(hass, mock_config_entry)

    obj = mock_client.objects.pop(81)
    emit(mock_client, ObjectRemoved(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(PLAIN_ENTITY_ID).state == STATE_UNAVAILABLE


async def _set_roller_lock(
    hass: HomeAssistant, entity_id: str, direction: str, blocked: bool
) -> None:
    """Call ``ampio.set_roller_lock`` against ``entity_id``."""
    await hass.services.async_call(
        DOMAIN,
        "set_roller_lock",
        {ATTR_ENTITY_ID: entity_id, "direction": direction, "blocked": blocked},
        blocking=True,
    )


async def test_set_roller_lock_registers_under_the_ampio_domain(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An entity service registers under the integration domain, not the platform's."""
    await setup_integration(hass, mock_config_entry)

    assert hass.services.has_service(DOMAIN, "set_roller_lock")
    assert not hass.services.has_service(COVER_DOMAIN, "set_roller_lock")


@pytest.mark.parametrize(
    ("direction", "blocked", "verb"),
    [
        pytest.param("opening", True, "block_opening", id="block-opening"),
        pytest.param("opening", False, "unblock_opening", id="unblock-opening"),
        pytest.param("closing", True, "block_closing", id="block-closing"),
        pytest.param("closing", False, "unblock_closing", id="unblock-closing"),
    ],
)
async def test_set_roller_lock_sends_the_matching_verb(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    direction: str,
    blocked: bool,
    verb: str,
) -> None:
    """Each direction and hold state maps to its own library call."""
    await setup_integration(hass, mock_config_entry)

    await _set_roller_lock(hass, POSITION_ENTITY_ID, direction, blocked)

    getattr(mock_client, verb).assert_awaited_once_with(82)


@pytest.mark.parametrize(
    ("blocked", "verbs"),
    [
        pytest.param(True, ("block_opening", "block_closing"), id="block-both"),
        pytest.param(False, ("unblock_opening", "unblock_closing"), id="unblock-both"),
    ],
)
async def test_set_roller_lock_both_sends_one_call_per_direction(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    blocked: bool,
    verbs: tuple[str, str],
) -> None:
    """Direction "both" sends one call for opening and one for closing."""
    await setup_integration(hass, mock_config_entry)

    await _set_roller_lock(hass, POSITION_ENTITY_ID, "both", blocked)

    for verb in verbs:
        getattr(mock_client, verb).assert_awaited_once_with(82)


async def test_set_roller_lock_refuses_on_a_standard_account(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A standard account is not served the raw tree, so the action says so."""
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_roller_lock(hass, POSITION_ENTITY_ID, "both", True)
    assert excinfo.value.translation_key == "cover_lock_not_admin"
    mock_client.block_opening.assert_not_awaited()
    mock_client.block_closing.assert_not_awaited()


async def test_set_roller_lock_on_an_unsupported_module_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module the roller lock does not reach raises, not a silent no-op."""
    mock_client.block_opening.side_effect = AmpioValueError(
        "the module behind object 82 advertises no roller channel count"
    )
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_roller_lock(hass, POSITION_ENTITY_ID, "opening", True)
    assert excinfo.value.translation_key == "cover_lock_unsupported"


async def test_set_roller_lock_ignores_the_read_only_marker(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The read-only marker gates the /api path, not the raw tree this write rides.

    An administrator can still hold or release a read-only cover's lock,
    the way the FAQ promises, so this write must not call
    ``raise_if_read_only`` the way the other write methods on this entity do.
    """
    await setup_integration(hass, mock_config_entry)

    obj = mock_client.objects[82]
    mock_client.objects[82] = replace(obj, params=obj.params | (1 << 6))
    emit(mock_client, ObjectUpdated(object=mock_client.objects[82]))
    await hass.async_block_till_done()

    await _set_roller_lock(hass, POSITION_ENTITY_ID, "opening", True)

    mock_client.block_opening.assert_awaited_once_with(82)
