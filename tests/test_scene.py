"""Tests for the Ampio scene platform."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

from ampio_mqtt import AmpioTimeoutError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.scene import DOMAIN as SCENE_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration

SCENE_ENTITY_ID = "scene.mserv_wieczor"


@pytest.fixture(autouse=True)
def scene_only() -> Generator[None]:
    """Limit setup to the scene platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.SCENE]):
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


async def test_activate_maps_to_run_scene(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning the scene on applies it through the scene verb."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SCENE_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: SCENE_ENTITY_ID},
        blocking=True,
    )
    mock_client.run_scene.assert_awaited_once_with(5)


async def test_fetch_failure_defers_the_platform(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A catalog fetch timeout defers the platform without failing the entry."""
    mock_client.fetch_scenes.side_effect = AmpioTimeoutError("timeout")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not hass.states.async_entity_ids(SCENE_DOMAIN)
