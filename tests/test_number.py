"""Tests for the Ampio number platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import ObjectRemoved, ObjectUpdated
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit, make_object, pinned_id

U8_ENTITY_ID = pinned_id("number", 164)
I16_ENTITY_ID = pinned_id("number", 165)


@pytest.fixture(autouse=True)
def number_only() -> Generator[None]:
    """Limit setup to the number platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.NUMBER]):
        yield


@pytest.mark.usefixtures("mock_client")
async def test_analog_flag_bounds(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Each analog flag takes the bounds its own field width allows."""
    await setup_integration(hass, mock_config_entry)

    u8 = hass.states.get(U8_ENTITY_ID)
    assert u8 is not None
    assert u8.state == "120"
    assert u8.attributes[ATTR_MIN] == 0
    assert u8.attributes[ATTR_MAX] == 255

    i16 = hass.states.get(I16_ENTITY_ID)
    assert i16 is not None
    assert i16.state == "-44"
    assert i16.attributes[ATTR_MIN] == -32768
    assert i16.attributes[ATTR_MAX] == 32767


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


async def test_write_rounds_to_the_field_integer(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A write rounds to the field's integer and carries no pulse.

    Object 164 carries a Designer turn-on time. The M-SERV ignores it on
    an analog flag, measured on hardware, so the write must not send it.

    The assertion's ``==`` would also accept a float like ``201.0``;
    ``mypy --strict`` is what holds the value an integer, through the
    library's ``set_value(value: int)`` signature.
    """
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: U8_ENTITY_ID, ATTR_VALUE: 200.6},
        blocking=True,
    )

    mock_client.set_value.assert_awaited_once_with(164, 201)


async def test_write_carries_a_negative_value(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The 16-bit field takes a negative value, which the wire carries signed."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: I16_ENTITY_ID, ATTR_VALUE: -300},
        blocking=True,
    )

    mock_client.set_value.assert_awaited_once_with(165, -300)


async def test_read_only_object_rejects_writes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A Designer read-only object raises instead of sending a doomed write.

    The M-SERV drops such writes silently on both account tiers, so the
    entity rejects them up front and keeps its platform.
    """
    obj = mock_client.objects[164]
    # Designer's read-only checkbox is params bit 6; ``read_only`` derives.
    mock_client.objects[164] = replace(obj, params=obj.params | (1 << 6))
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: U8_ENTITY_ID, ATTR_VALUE: 200.6},
            blocking=True,
        )
    mock_client.set_value.assert_not_called()


async def test_value_follows_a_state_push(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A pushed state moves the number without a poll."""
    await setup_integration(hass, mock_config_entry)

    moved = make_object(
        164,
        "flaga_liniowa",
        0,
        # sfId 17 is the default fixture's unverified numeric placeholder.
        leaf_id="0_cb8f_17_0_1",
        funkcja=18,
        name="Poziom jasnosci",
        state="7",
        czas=50,
    )
    mock_client.objects[164] = moved
    emit(mock_client, ObjectUpdated(object=moved))
    await hass.async_block_till_done()

    state = hass.states.get(U8_ENTITY_ID)
    assert state is not None
    assert state.state == "7"


async def test_removed_object_becomes_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Evicting the backing object makes the entity unavailable."""
    await setup_integration(hass, mock_config_entry)

    obj = mock_client.objects.pop(164)
    emit(mock_client, ObjectRemoved(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(U8_ENTITY_ID).state == STATE_UNAVAILABLE
