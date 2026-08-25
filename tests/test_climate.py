"""Tests for the Ampio climate platform."""

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

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit

THERMOSTAT_ENTITY_ID = "climate.m_sens_salon_termostat_salon"


@pytest.fixture(autouse=True)
def climate_only() -> Generator[None]:
    """Limit setup to the climate platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.CLIMATE]):
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


async def test_set_temperature_is_optimistic(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The setpoint command passes through and is remembered optimistically.

    The library surfaces no setpoint readback (ampio-mqtt#73), so the
    entity reports the last commanded value.
    """
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_TEMPERATURE] is None

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: THERMOSTAT_ENTITY_ID, ATTR_TEMPERATURE: 21.5},
        blocking=True,
    )
    mock_client.set_temperature.assert_awaited_once_with(91, 21.5)
    assert hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_TEMPERATURE] == 21.5


async def test_push_echo_updates_action(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The running-flag echo flips hvac_action between heating and idle."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(THERMOSTAT_ENTITY_ID)
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING

    obj = replace(mock_client.objects[91], value="0")
    mock_client.objects[91] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    state = hass.states.get(THERMOSTAT_ENTITY_ID)
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE
