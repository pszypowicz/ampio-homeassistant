"""Number platform for the Ampio integration."""

from typing import override

from ampio_mqtt import AmpioObject, InputKind

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, raise_if_read_only

PARALLEL_UPDATES = 0


def value_range_of(obj: AmpioObject) -> tuple[int, int] | None:
    """The inclusive write range the object's kind declares, or None.

    An input kind carrying a range is a module variable with a value axis:
    the analog flags. The range is the field width the M-SERV truncates a
    write to rather than refusing it, so it is the entity's bounds and not
    a hint. A flag without a range is a boolean, and it belongs to the
    switch or the binary sensor platform.
    """
    kind = obj.kind
    return kind.value_range if isinstance(kind, InputKind) else None


def is_number(obj: AmpioObject) -> bool:
    """Whether the object belongs to the number platform."""
    return value_range_of(obj) is not None


def build_numbers(data: AmpioData, obj: AmpioObject) -> list[AmpioNumber]:
    """The number platform's entities for one object."""
    if (value_range := value_range_of(obj)) is None:
        return []
    return [AmpioNumber(data, obj, value_range)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the number platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_numbers, async_add_entities)


class AmpioNumber(AmpioEntity, NumberEntity):
    """A number backed by an Ampio analog flag object."""

    _attr_native_step = 1
    # An unnamed 8-bit flag reads the translated name; a named one takes the
    # device name the base class assigns. The 16-bit width overrides this in
    # __init__, so the two widths read apart in the entity list.
    _attr_translation_key = "analog_flag"

    def __init__(
        self, data: AmpioData, obj: AmpioObject, value_range: tuple[int, int]
    ) -> None:
        """Initialize with the bounds the object's field width allows.

        The mode stays at the Home Assistant default. The frontend renders a
        slider up to 256 steps and a box above it, which suits the u8 flag
        and the 16-bit flag respectively without this entity stating either.
        """
        super().__init__(data, obj)
        self._attr_native_min_value, self._attr_native_max_value = value_range
        # The kind's own key tells the two widths apart; a typ_komponentu
        # string would carry the same protocol knowledge the library
        # already classified.
        kind = obj.kind
        if isinstance(kind, InputKind) and kind.key == "flaga_liniowa16":
            self._attr_translation_key = "analog_flag_16bit"

    @property
    @override
    def native_value(self) -> float | None:
        """The value the flag holds, or None once the object is gone.

        ``numeric_value`` parses the wire string through ``float()``, which
        for a whole-number field leaves a trailing ``.0``. Home Assistant
        stringifies a ``float`` state at machine precision, so a plain float
        here would show ``120.0`` for a field that never carries a
        fraction. Rounding to ``int`` keeps the displayed state an integer.
        """
        if (obj := self._object) is None or (value := obj.numeric_value) is None:
            return None
        return round(value)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Write the value, which the flag then holds.

        Home Assistant hands over a float, and the field holds an integer,
        so the value is rounded rather than truncated.

        No pulse rides the write. Designer offers a turn-on time on an
        analog flag, and the M-SERV ignores it: a timed write measured on
        hardware left the object holding the written value long past the
        configured window, where the same form reverts a relay or a flag.

        A Designer read-only object raises instead of sending a write the
        M-SERV would silently drop.
        """
        raise_if_read_only(self._object)
        await self._data.client.set_value(self._object_id, round(value))
