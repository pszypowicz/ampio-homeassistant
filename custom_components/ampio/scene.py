"""Scene platform for the Ampio integration."""

from typing import Any

from ampio_mqtt import AmpioConnectionError, AmpioScene

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SCENE_KEY_STEM
from .data import HUB_IDENTIFIER, AmpioConfigEntry, AmpioData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio scenes from the app-defined scene catalogue.

    Scenes are not push objects: the catalogue is fetched once here. A
    fetch failure defers the platform so Home Assistant retries it, and
    the runtime data keeps no catalogue, which is what tells the
    stale-record report that the scenes are unknown rather than gone.
    """
    data = entry.runtime_data
    try:
        scenes = await data.client.fetch_scenes()
    except AmpioConnectionError as err:
        raise PlatformNotReady(
            f"Fetching the Ampio scene catalogue failed: {err}"
        ) from err
    active = [scene for scene in scenes if scene.active]
    data.scene_ids = frozenset(scene.id for scene in active)
    async_add_entities(AmpioSceneEntity(data, scene) for scene in active)


class AmpioSceneEntity(Scene):
    """An activate-only scene backed by an Ampio app scene."""

    _attr_has_entity_name = True

    def __init__(self, data: AmpioData, scene: AmpioScene) -> None:
        """Initialize from the fetched catalogue entry.

        The scene id is the app's own identifier.
        """
        self._data = data
        self._scene_id = scene.id
        self._attr_name = scene.scene_name
        self._attr_unique_id = f"{SCENE_KEY_STEM}_{scene.id}"
        self._attr_device_info = DeviceInfo(identifiers={HUB_IDENTIFIER})

    async def async_activate(self, **kwargs: Any) -> None:
        """Apply the scene's actions."""
        await self._data.client.run_scene(self._scene_id)
