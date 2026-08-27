"""Tests for the Ampio switch platform."""

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

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit

PLAIN_ENTITY_ID = "switch.ampio_object_leaf_0_cb8f_rel_0_4"
OUTLET_ENTITY_ID = "switch.gniazdo_taras"
FLAG_ENTITY_ID = "switch.m_sens_salon_podlewanie"


@pytest.fixture(autouse=True)
def switch_only() -> Generator[None]:
    """Limit setup to the switch platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.SWITCH]):
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


async def test_turn_on_off_maps_to_verbs(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The switch services map to the plain on and off verbs."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: OUTLET_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(75)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: OUTLET_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(75)


async def test_light_tagged_relay_is_not_a_switch(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A relay whose Matter tag says light belongs to the light platform."""
    await setup_integration(hass, mock_config_entry)

    # Two relays plus the two writable flags.
    states = [s for s in hass.states.async_all() if s.domain == "switch"]
    assert len(states) == 4
    assert not any("kinkiet" in s.entity_id for s in states)


async def test_flag_services_map_to_verbs(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A writable flag answers the switch services with the plain verbs."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(61)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(61)


async def test_flag_push_update_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed flag update flips the switch between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_ON

    obj = replace(mock_client.objects[61], value="0")
    mock_client.objects[61] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_OFF


async def test_push_echo_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed relay echo flips the entity between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(PLAIN_ENTITY_ID).state == STATE_ON

    obj = replace(mock_client.objects[74], value="0")
    mock_client.objects[74] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(PLAIN_ENTITY_ID).state == STATE_OFF


async def test_read_only_object_rejects_writes(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Designer read-only object raises instead of sending a doomed write.

    The M-SERV drops such writes silently on both account tiers, so the
    entity rejects them up front and keeps its platform.
    """
    obj = mock_client.objects[61]
    # Designer's read-only checkbox is params bit 6; ``read_only`` derives.
    mock_client.objects[61] = replace(obj, params=obj.params | (1 << 6))
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
            blocking=True,
        )
    mock_client.turn_off.assert_not_called()
