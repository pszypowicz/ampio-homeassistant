"""Cover platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import AmpioObject, OutputKind

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity

PARALLEL_UPDATES = 0


def is_cover(obj: AmpioObject) -> bool:
    """Whether the object belongs to the cover platform."""
    kind = obj.kind
    return isinstance(kind, OutputKind) and kind.cover


def build_covers(data: AmpioData, obj: AmpioObject) -> list[AmpioCover]:
    """The cover platform's entities for one object."""
    return [AmpioCover(data, obj)] if is_cover(obj) else []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the cover platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_covers, async_add_entities)


class AmpioCover(AmpioEntity, CoverEntity):
    """A cover backed by an Ampio output object.

    Ampio and Home Assistant share the percent convention: 0 is fully
    closed, 100 is fully open, on both the travel and the slat axis.
    """

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize with the feature set the object's kind supports."""
        super().__init__(data, obj)
        kind = obj.kind
        has_position = isinstance(kind, OutputKind) and kind.position
        has_tilt = isinstance(kind, OutputKind) and kind.tilt
        features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        if has_position:
            features |= CoverEntityFeature.SET_POSITION
        if has_tilt:
            features |= (
                CoverEntityFeature.SET_TILT_POSITION
                | CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.STOP_TILT
            )
        self._unblocked_features = features
        self._attr_device_class = (
            CoverDeviceClass.BLIND if has_tilt else CoverDeviceClass.SHUTTER
        )

    @property
    @override
    def supported_features(self) -> CoverEntityFeature:
        """The feature set, minus whatever the module's lock refuses.

        A Designer logic rule locks a cover's travel through its "Disable
        movement", "Disable closing" and "Disable opening" roller actions,
        and a rule holds the lock for as long as its trigger holds. The
        module then drops every command for the blocked direction, the
        ``/api`` verbs included, with no error and no reply. Dropping the
        feature is what tells the difference, so the dashboard greys the
        arrow and core refuses the service call rather than reporting a
        move that will not happen.

        The slats answer the same two bits as the travel, measured on a
        blind under a one-directional rule: it refused a slat turn toward
        open and ran one toward closed, while its travel followed the same
        split. So each tilt feature drops with its own direction.

        Position and tilt position survive one blocked direction, because
        the other direction still runs. ``_reject_blocked_move`` decides
        that case on the direction of the requested move. Both stop
        features stay: a stop is not a move, and the allowed direction can
        still be running.
        """
        features = self._unblocked_features
        if (obj := self._object) is None:
            return features
        if obj.blocks_opening:
            features &= ~(CoverEntityFeature.OPEN | CoverEntityFeature.OPEN_TILT)
        if obj.blocks_closing:
            features &= ~(CoverEntityFeature.CLOSE | CoverEntityFeature.CLOSE_TILT)
        if obj.blocks_opening and obj.blocks_closing:
            features &= ~(
                CoverEntityFeature.SET_POSITION | CoverEntityFeature.SET_TILT_POSITION
            )
        return features

    @property
    @override
    def current_cover_position(self) -> int | None:
        """Travel percent, or None without a position axis or feedback."""
        if (obj := self._object) is None:
            return None
        return obj.position

    @property
    @override
    def current_cover_tilt_position(self) -> int | None:
        """Slat angle percent; the library populates it only for tilt-capable covers."""
        if (obj := self._object) is None:
            return None
        return obj.lammel

    @property
    @override
    def is_closed(self) -> bool | None:
        """Whether the cover is fully closed; None without position feedback."""
        if (position := self.current_cover_position) is None:
            return None
        return position == 0

    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Drive the cover fully open."""
        await self._data.client.open(self._object_id)

    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Drive the cover fully closed."""
        await self._data.client.close(self._object_id)

    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Halt travel wherever the cover is."""
        await self._data.client.stop(self._object_id)

    @override
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Drive the cover to the requested percent, unless the lock refuses."""
        target: int = kwargs[ATTR_POSITION]
        self._reject_blocked_move(target, self.current_cover_position)
        await self._data.client.set_roller_pos(self._object_id, target)

    def _reject_blocked_move(self, target: int, current: int | None) -> None:
        """Raise when the requested move runs in a direction the module refuses.

        Serves both axes, which answer the same two lock bits. The feature
        set already covers a cover locked both ways. One locked direction
        keeps the feature, because the other direction still runs, so the
        move's own direction decides. Without a reported ``current``
        nothing can decide, and the command goes out.
        """
        obj = self._object
        if obj is None or current is None:
            return
        if target > current and obj.blocks_opening:
            key = "cover_blocked_opening"
        elif target < current and obj.blocks_closing:
            key = "cover_blocked_closing"
        else:
            return
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key=key)

    @override
    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Turn the slats to the requested percent, unless the lock refuses."""
        target: int = kwargs[ATTR_TILT_POSITION]
        self._reject_blocked_move(target, self.current_cover_tilt_position)
        await self._data.client.set_roller_lamella(self._object_id, target)

    @override
    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the slats fully."""
        await self._data.client.set_roller_lamella(self._object_id, 100)

    @override
    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the slats fully."""
        await self._data.client.set_roller_lamella(self._object_id, 0)

    @override
    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Halt slat rotation; the stop verb halts either axis."""
        await self._data.client.stop(self._object_id)
