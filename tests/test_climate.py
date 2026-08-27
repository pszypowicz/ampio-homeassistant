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
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODES,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit

THERMOSTAT_ENTITY_ID = "climate.termostat_salon"


@pytest.fixture(autouse=True)
def climate_only() -> Generator[None]:
    """Limit setup to the climate platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.CLIMATE]):
        yield


async def _push_thermostat(
    hass: HomeAssistant, client: MagicMock, **changes: object
) -> None:
    """Replace fields on the reg object's readback and push the update."""
    obj = client.objects[91]
    updated = replace(obj, thermostat=replace(obj.thermostat, **changes))
    client.objects[91] = updated
    emit(client, ObjectUpdated(object=updated))
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


async def test_set_temperature_waits_for_the_echo(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The setpoint command passes through; the state follows the readback."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_TEMPERATURE] == 22.5

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: THERMOSTAT_ENTITY_ID, ATTR_TEMPERATURE: 21.5},
        blocking=True,
    )
    mock_client.set_temperature.assert_awaited_once_with(91, 21.5)
    assert hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_TEMPERATURE] == 22.5

    await _push_thermostat(hass, mock_client, target_temperature=21.5)
    assert hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_TEMPERATURE] == 21.5


async def test_set_preset_mode_maps_to_heating_mode(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Choosing a preset sends the matching wire letter."""
    await setup_integration(hass, mock_config_entry)
    assert (
        hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_PRESET_MODE] == "schedule"
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: THERMOSTAT_ENTITY_ID, ATTR_PRESET_MODE: "manual"},
        blocking=True,
    )
    mock_client.set_heating_mode.assert_awaited_once_with(91, "M")


async def test_action_follows_running_and_cooling_flags(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Idle, heating, and cooling derive from the running and cooling flags."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(THERMOSTAT_ENTITY_ID)
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING

    obj = replace(mock_client.objects[91], value="0")
    mock_client.objects[91] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()
    assert (
        hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_HVAC_ACTION]
        == HVACAction.IDLE
    )

    obj = replace(
        mock_client.objects[91],
        value="1",
        thermostat=replace(mock_client.objects[91].thermostat, cooling=True),
    )
    mock_client.objects[91] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()
    state = hass.states.get(THERMOSTAT_ENTITY_ID)
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING
    assert state.state == HVACMode.COOL
    assert state.attributes[ATTR_HVAC_MODES] == [HVACMode.COOL]


async def test_unknown_mode_letter_reads_no_preset(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A wire letter outside the vocabulary maps to no preset, not a guess."""
    await setup_integration(hass, mock_config_entry)
    assert (
        hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_PRESET_MODE] == "schedule"
    )

    await _push_thermostat(hass, mock_client, mode="X")
    assert hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_PRESET_MODE] is None


async def test_missing_readback_reads_no_temperature_or_preset(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A reg object with no readback reads no temperature or preset."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[91], thermostat=None)
    mock_client.objects[91] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    state = hass.states.get(THERMOSTAT_ENTITY_ID)
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] is None
    assert state.attributes[ATTR_TEMPERATURE] is None
    assert state.attributes[ATTR_PRESET_MODE] is None


@pytest.mark.parametrize(
    ("letter", "preset"),
    [("A", "auto"), ("S", "schedule"), ("M", "manual"), ("H", "holiday")],
)
async def test_preset_round_trip(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    letter: str,
    preset: str,
) -> None:
    """Every wire letter reads back as its preset, and setting it sends the letter."""
    await setup_integration(hass, mock_config_entry)

    await _push_thermostat(hass, mock_client, mode=letter)
    assert hass.states.get(THERMOSTAT_ENTITY_ID).attributes[ATTR_PRESET_MODE] == preset

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: THERMOSTAT_ENTITY_ID, ATTR_PRESET_MODE: preset},
        blocking=True,
    )
    mock_client.set_heating_mode.assert_awaited_once_with(91, letter)
