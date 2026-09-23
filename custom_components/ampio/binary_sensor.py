"""Binary sensor platform for the Ampio integration."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, override

from ampio_mqtt import AmpioObject, InputKind

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .cover import is_cover
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity

PARALLEL_UPDATES = 0

# Descriptions for the read-only input kinds, keyed by ``InputKind.key``.
# Switchable inputs (the writable flags) belong to the switch platform, the
# ranged inputs (the analog flags) belong to the number platform, the
# system kinds (the M-SERV's own detection and simulation objects) never
# reach a platform, and objects classified into any other kind are not
# exposed.
BINARY_SENSOR_DESCRIPTIONS: dict[str, BinarySensorEntityDescription] = {
    description.key: description
    for description in (
        BinarySensorEntityDescription(
            key="wej",
            translation_key="input",
        ),
        # The alarm partition. No half takes a device class: the alarmed
        # half also reads on through the panel's exit delay, so it is not a
        # safety indicator on its own. Other sub-functions use the base
        # alarm kind.
        BinarySensorEntityDescription(
            key="alarm",
            translation_key="alarm",
        ),
        BinarySensorEntityDescription(
            key="alarm_armed",
            translation_key="alarm_armed",
        ),
        BinarySensorEntityDescription(
            key="alarm_alarmed",
            translation_key="alarm_alarmed",
        ),
    )
}


@dataclass(kw_only=True, frozen=True)
class AmpioCoverLockSensorDescription(BinarySensorEntityDescription):
    """One direction of a cover's roller lock, as a reading."""

    is_locked_fn: Callable[[AmpioObject], bool]


# The lock is a property of the cover channel that the cover entity can
# only report by dropping a feature. It reads on both account tiers,
# because the block bits ride the object state push. The write is the
# ampio.set_roller_lock action, which the administrator login alone can
# use, and which explains every refusal and every failure to reach the
# server.
COVER_LOCK_SENSORS: Final = (
    AmpioCoverLockSensorDescription(
        key="blocks_opening",
        translation_key="blocks_opening",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_locked_fn=lambda obj: obj.blocks_opening,
    ),
    AmpioCoverLockSensorDescription(
        key="blocks_closing",
        translation_key="blocks_closing",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_locked_fn=lambda obj: obj.blocks_closing,
    ),
)


def build_binary_sensors(data: AmpioData, obj: AmpioObject) -> list[BinarySensorEntity]:
    """The binary sensor platform's entities for one object."""
    if is_cover(obj):
        return [
            AmpioCoverLockSensor(data, obj, description)
            for description in COVER_LOCK_SENSORS
        ]
    if not isinstance(kind := obj.kind, InputKind):
        return []
    if (description := BINARY_SENSOR_DESCRIPTIONS.get(kind.key)) is None:
        return []
    return [AmpioBinarySensor(data, obj, description)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the binary sensor platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_binary_sensors, async_add_entities)


class AmpioBinarySensor(AmpioEntity, BinarySensorEntity):
    """A binary sensor backed by an Ampio input object."""

    def __init__(
        self,
        data: AmpioData,
        obj: AmpioObject,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(data, obj)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the input reads on, or None once the object is gone."""
        if (obj := self._object) is None:
            return None
        return obj.is_on


class AmpioCoverLockSensor(AmpioEntity, BinarySensorEntity):
    """One direction of a cover's roller lock."""

    entity_description: AmpioCoverLockSensorDescription

    def __init__(
        self,
        data: AmpioData,
        obj: AmpioObject,
        description: AmpioCoverLockSensorDescription,
    ) -> None:
        """Initialize beside the cover, with a suffixed key per direction."""
        super().__init__(data, obj, key_suffix=f"_{description.key}")
        self.entity_description = description
        # The base class silences a named object's primary entity in favor
        # of the device name; each lock reading keeps its translated name.
        if hasattr(self, "_attr_name"):
            del self._attr_name

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether this direction is held, or None without an object or lock report."""
        if (obj := self._object) is None or obj.block is None:
            return None
        return self.entity_description.is_locked_fn(obj)
