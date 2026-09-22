"""Tests for the Ampio binary sensor platform."""

from collections.abc import Generator
from dataclasses import replace
from typing import Final
from unittest.mock import MagicMock, patch

from ampio_mqtt import (
    INPUT_KIND_KEYS,
    AccessTier,
    AmpioObject,
    InputKind,
    ObjectRemoved,
    ObjectUpdated,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.binary_sensor import BINARY_SENSOR_DESCRIPTIONS
from custom_components.ampio.number import is_number
from custom_components.ampio.switch import is_switch
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit, make_object, pinned_id, set_access_tier

WEJ_ENTITY_ID = pinned_id("binary_sensor", 146)
ARMED_ENTITY_ID = pinned_id("binary_sensor", 166)
ALARMED_ENTITY_ID = pinned_id("binary_sensor", 167)
LOCK_OPENING_ENTITY_ID = pinned_id("binary_sensor", 82, "_blocks_opening")
LOCK_CLOSING_ENTITY_ID = pinned_id("binary_sensor", 82, "_blocks_closing")


@pytest.fixture(autouse=True)
def binary_sensor_only() -> Generator[None]:
    """Limit setup to the binary sensor platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.BINARY_SENSOR]):
        yield


# The alarm family is the one input family whose kind key does not name a
# component type. All three keys come from `satel_alarm`: the leaf
# sub-function picks the half, and any other sub-function reads as the base
# kind. Every other key is its own `typ_komponentu`.
_ALARM_SUB_SF: Final = {"alarm_armed": 3, "alarm_alarmed": 4, "alarm": 0}
# Binary flag 3 and input 257/1 follow the library's identity table.
# Analog flags 17 and 18 are placeholders with no documented counterpart.
_INPUT_LEAF_FIELDS: Final = {
    "flaga": (3, 0),
    "wej": (257, 1),
    "flaga_liniowa": (17, 0),
    "flaga_liniowa16": (18, 0),
}


def _object_for(key: str) -> AmpioObject:
    """An object that classifies into the given input kind."""
    if (sub_sf := _ALARM_SUB_SF.get(key)) is not None:
        return make_object(1, "satel_alarm", 0, leaf_id=f"0_1_296_{sub_sf}_1")
    assert key in _INPUT_LEAF_FIELDS, (
        f"Input kind {key!r} has no leaf fixture. "
        "Add its address fields and review its platform mapping."
    )
    sf_id, sub_sf_id = _INPUT_LEAF_FIELDS[key]
    return make_object(1, key, 0, leaf_id=f"0_1_{sf_id}_{sub_sf_id}_1")


def test_input_kind_vocabulary_is_mapped_or_excluded() -> None:
    """A library upgrade that adds an input kind forces a mapping decision.

    Switchable inputs (the writable flags) belong to the switch platform.
    Ranged inputs (the analog flags) belong to the number platform. The
    system types (``symulacja``, ``detekcja``) are the M-SERV's own objects
    and are deliberately not exposed as entities.
    """
    for key in sorted(INPUT_KIND_KEYS):
        obj = _object_for(key)
        assert isinstance(obj.kind, InputKind)
        # A key that no longer names its own type must fail here rather
        # than pass while testing some other object.
        assert obj.kind.key == key
        # The switch and number platforms partition on these two checks as
        # mutually exclusive branches, so no kind may satisfy both.
        assert not (obj.kind.switchable and obj.kind.value_range is not None)
        if obj.kind.switchable:
            assert is_switch(obj)
            assert key not in BINARY_SENSOR_DESCRIPTIONS
        elif obj.kind.value_range is not None:
            assert is_number(obj)
            assert key not in BINARY_SENSOR_DESCRIPTIONS
        else:
            assert key in BINARY_SENSOR_DESCRIPTIONS


@pytest.mark.usefixtures("mock_client")
async def test_alarm_halves_surface_with_valid_leaves(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Each admitted alarm half gets a sensor without a device class."""
    await setup_integration(hass, mock_config_entry)

    armed = hass.states.get(ARMED_ENTITY_ID)
    assert armed is not None
    assert armed.state == STATE_ON
    assert ATTR_DEVICE_CLASS not in armed.attributes

    alarmed = hass.states.get(ALARMED_ENTITY_ID)
    assert alarmed is not None
    assert alarmed.state == STATE_OFF


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


@pytest.mark.parametrize("restricted", [False, True], ids=["admin", "restricted"])
async def test_cover_lock_sensors_exist_on_both_tiers(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    restricted: bool,
) -> None:
    """A cover reports both lock directions as diagnostic binary sensors, on either tier."""
    if restricted:
        set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(LOCK_OPENING_ENTITY_ID) is not None
    assert hass.states.get(LOCK_CLOSING_ENTITY_ID) is not None


@pytest.mark.parametrize("restricted", [False, True], ids=["admin", "restricted"])
async def test_cover_lock_sensor_reads_the_state_bit(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    restricted: bool,
) -> None:
    """The sensor follows the object's block bits, on either tier.

    ``block`` rides the object state push, which both account tiers
    receive, so the read needs no administrator login.
    """
    if restricted:
        set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(LOCK_OPENING_ENTITY_ID).state == STATE_UNKNOWN
    assert hass.states.get(LOCK_CLOSING_ENTITY_ID).state == STATE_UNKNOWN

    released = replace(mock_client.objects[82], block=0)
    mock_client.objects[82] = released
    emit(mock_client, ObjectUpdated(object=released))
    await hass.async_block_till_done()

    assert hass.states.get(LOCK_OPENING_ENTITY_ID).state == STATE_OFF
    assert hass.states.get(LOCK_CLOSING_ENTITY_ID).state == STATE_OFF

    opening_blocked = replace(mock_client.objects[82], block=2)
    mock_client.objects[82] = opening_blocked
    emit(mock_client, ObjectUpdated(object=opening_blocked))
    await hass.async_block_till_done()

    assert hass.states.get(LOCK_OPENING_ENTITY_ID).state == STATE_ON
    assert hass.states.get(LOCK_CLOSING_ENTITY_ID).state == STATE_OFF

    closing_blocked = replace(mock_client.objects[82], block=1)
    mock_client.objects[82] = closing_blocked
    emit(mock_client, ObjectUpdated(object=closing_blocked))
    await hass.async_block_till_done()

    assert hass.states.get(LOCK_OPENING_ENTITY_ID).state == STATE_OFF
    assert hass.states.get(LOCK_CLOSING_ENTITY_ID).state == STATE_ON


@pytest.mark.usefixtures("mock_client")
async def test_cover_lock_sensor_is_diagnostic(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The lock reading is configuration-adjacent, not a control."""
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(LOCK_OPENING_ENTITY_ID)
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_wej_push_update_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed wired-button input update flips the entity between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_OFF

    obj = replace(mock_client.objects[146], state="1")
    mock_client.objects[146] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_ON


async def test_nonzero_values_read_as_on(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The per-object form pushes "255" for on; it must read as on."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[146], state="255")
    mock_client.objects[146] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_ON


async def test_removed_object_becomes_unavailable(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Evicting the backing object makes the entity unavailable."""
    await setup_integration(hass, mock_config_entry)

    obj = mock_client.objects.pop(146)
    emit(mock_client, ObjectRemoved(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_UNAVAILABLE
