"""Switch platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import AmpioObject, OutputKind

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, eligible_objects
from .light import LIGHT_MATTER_TYPES

PARALLEL_UPDATES = 0

# Matter "Plugs" device types: a relay tagged as a plug-in unit reads as an
# outlet. Every other non-light relay reads as a generic switch.
PLUG_MATTER_TYPES = frozenset({0x010A, 0x010B})


def is_switch(obj: AmpioObject) -> bool:
    """Whether the object belongs to the switch platform.

    The complement of the light platform's relay rule: a relay whose
    Matter tag is in ``LIGHT_MATTER_TYPES`` is a light; every other relay
    lands here.
    """
    if not isinstance(kind := obj.kind, OutputKind):
        return False
    return kind.key == "relay" and obj.matter_device_type not in LIGHT_MATTER_TYPES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio switches from the discovery-time object catalogue."""
    data = entry.runtime_data
    async_add_entities(
        AmpioSwitch(data, obj)
        for obj in eligible_objects(data.client)
        if is_switch(obj)
    )


class AmpioSwitch(AmpioEntity, SwitchEntity):
    """A switch backed by an Ampio relay object."""

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize with the device class the Matter tag suggests."""
        super().__init__(data, obj)
        self._attr_device_class = (
            SwitchDeviceClass.OUTLET
            if obj.matter_device_type in PLUG_MATTER_TYPES
            else SwitchDeviceClass.SWITCH
        )

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the relay is on, or None once the object is gone."""
        if (obj := self._object) is None:
            return None
        return obj.is_on

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the relay on."""
        await self._data.client.turn_on(self._object_id)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the relay off."""
        await self._data.client.turn_off(self._object_id)
