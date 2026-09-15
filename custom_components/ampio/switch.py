"""Switch platform for the Ampio integration."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final, override

from ampio_mqtt import AmpioClient, AmpioObject, AmpioValueError, InputKind, OutputKind

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .cover import is_cover
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, async_turn_on_honoring_pulse, raise_if_read_only
from .light import LIGHT_MATTER_TYPES

PARALLEL_UPDATES = 0

# Matter "Plugs" device types: a relay tagged as a plug-in unit reads as an
# outlet. Every other non-light relay reads as a generic switch.
PLUG_MATTER_TYPES = frozenset({0x010A, 0x010B})


def is_switch(obj: AmpioObject) -> bool:
    """Whether the object belongs to the switch platform.

    Two populations land here. A relay whose catalogue-column Matter tag
    is not in ``LIGHT_MATTER_TYPES`` - the complement of the light
    platform's relay rule, on the same tier-independent source. And any
    input kind that declares itself switchable: the writable flags, a
    promised target of the client's switch verbs. Bell-marked objects
    belong to the button platform in both populations.
    """
    if obj.bell:
        return False
    if isinstance(kind := obj.kind, InputKind):
        return kind.switchable
    if not isinstance(kind, OutputKind):
        return False
    return kind.key == "relay" and obj.matter_device_type not in LIGHT_MATTER_TYPES


@dataclass(kw_only=True, frozen=True)
class AmpioCoverLockEntityDescription(SwitchEntityDescription):
    """One direction of a cover's roller lock."""

    is_locked_fn: Callable[[AmpioObject], bool]
    block_fn: Callable[[AmpioClient, int], Coroutine[Any, Any, None]]
    unblock_fn: Callable[[AmpioClient, int], Coroutine[Any, Any, None]]


# The lock is configuration, not a control: a house-wide switch.turn_off
# aimed at an area must not release every cover. EntityCategory.CONFIG is
# what keeps these out of area targeting, Assist, and HomeKit.
COVER_LOCK_DESCRIPTIONS: Final = (
    AmpioCoverLockEntityDescription(
        key="lock_opening",
        translation_key="lock_opening",
        entity_category=EntityCategory.CONFIG,
        is_locked_fn=lambda obj: obj.blocks_opening,
        block_fn=lambda client, object_id: client.block_opening(object_id),
        unblock_fn=lambda client, object_id: client.unblock_opening(object_id),
    ),
    AmpioCoverLockEntityDescription(
        key="lock_closing",
        translation_key="lock_closing",
        entity_category=EntityCategory.CONFIG,
        is_locked_fn=lambda obj: obj.blocks_closing,
        block_fn=lambda client, object_id: client.block_closing(object_id),
        unblock_fn=lambda client, object_id: client.unblock_closing(object_id),
    ),
)


def build_switches(data: AmpioData, obj: AmpioObject) -> list[SwitchEntity]:
    """The switch platform's entities for one object.

    A relay or a writable flag takes one switch. A cover takes one lock
    switch per direction instead: the lock is a property of the cover
    channel, and the cover entity itself can only report it by dropping a
    feature.
    """
    if is_switch(obj):
        return [AmpioSwitch(data, obj)]
    if is_cover(obj):
        return [
            AmpioCoverLockSwitch(data, obj, description)
            for description in COVER_LOCK_DESCRIPTIONS
        ]
    return []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the switch platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_switches, async_add_entities)


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
        """Turn the object on, timed when Designer configures a pulse."""
        raise_if_read_only(self._object)
        await async_turn_on_honoring_pulse(
            self._data.client, self._object, self._object_id
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the object off."""
        raise_if_read_only(self._object)
        await self._data.client.turn_off(self._object_id)


class AmpioCoverLockSwitch(AmpioEntity, SwitchEntity):
    """One direction of a cover's roller lock.

    The read works on both account tiers, because ``block`` rides the
    object state push. The write rides the raw tree, which the
    administrator login alone receives, and it needs the module's roller
    channel count: that count sizes the frame's channel mask, and one
    module generation drops a lock frame in silence without it.
    """

    entity_description: AmpioCoverLockEntityDescription

    def __init__(
        self,
        data: AmpioData,
        obj: AmpioObject,
        description: AmpioCoverLockEntityDescription,
    ) -> None:
        """Initialize beside the cover, with a suffixed key per direction."""
        super().__init__(data, obj, key_suffix=f"_{description.key}")
        self.entity_description = description
        # The base class silences a named object's primary entity in favor
        # of the device name; each lock keeps its translated name.
        if hasattr(self, "_attr_name"):
            del self._attr_name

    @property
    @override
    def available(self) -> bool:
        """False when the module's firmware carries no lock sub-functions.

        ``block_writable`` is filled by the administrator sweep, so a
        standard account reads None on every cover. None means no sweep
        covered the module, never False, and the entity stays available
        there: a write on that tier raises and the refusal explains it.
        """
        if not super().available:
            return False
        obj = self._object
        return obj is None or obj.block_writable is not False

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether this direction is held, or None once the object is gone."""
        if (obj := self._object) is None:
            return None
        return self.entity_description.is_locked_fn(obj)

    async def _write(
        self, call: Callable[[AmpioClient, int], Coroutine[Any, Any, None]]
    ) -> None:
        """Send one lock frame, and say why the module refused it."""
        try:
            await call(self._data.client, self._object_id)
        except AmpioValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="cover_lock_unavailable",
            ) from err

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Hold this direction until something releases it."""
        await self._write(self.entity_description.block_fn)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Release this direction, leaving the other one as it is."""
        await self._write(self.entity_description.unblock_fn)
