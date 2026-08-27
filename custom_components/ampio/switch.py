"""Switch platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import AmpioObject, InputKind, OutputKind

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

    Two populations land here. A relay whose Matter tag is not in
    ``LIGHT_MATTER_TYPES`` - the complement of the light platform's relay
    rule. And any input kind that declares itself switchable: the writable
    flags, a promised target of the client's switch verbs.
    """
    if isinstance(kind := obj.kind, InputKind):
        return kind.switchable
    if not isinstance(kind, OutputKind):
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
    """A switch backed by an Ampio relay or writable flag object."""

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize with the device class the Matter tag suggests."""
        super().__init__(data, obj)
        if isinstance(obj.kind, InputKind):
            # A writable flag has no socket semantics; unnamed flags take
            # the translated name.
            self._attr_translation_key = "flag"
        else:
            self._attr_device_class = (
                SwitchDeviceClass.OUTLET
                if obj.matter_device_type in PLUG_MATTER_TYPES
                else SwitchDeviceClass.SWITCH
            )

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the object is on, or None once it is gone."""
        if (obj := self._object) is None:
            return None
        return obj.is_on

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the object on."""
        await self._data.client.turn_on(self._object_id)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the object off."""
        await self._data.client.turn_off(self._object_id)
