"""Light platform for the Ampio integration."""

from collections.abc import Sequence
from typing import Any, override

from ampio_mqtt import (
    MAX_PANEL_FIELD,
    AmpioAdminClient,
    AmpioConnectionError,
    AmpioObject,
    AmpioTimeoutError,
    AmpioValueError,
    ModuleFunction,
    OutputKind,
)
import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import VolDictType
from homeassistant.util.color import color_rgb_to_rgbw, color_rgbw_to_rgb

from .const import CONF_BLEND_WHITE, DOMAIN
from .data import AmpioConfigEntry, AmpioData
from .entity import (
    AmpioEntity,
    AmpioModuleEntity,
    async_turn_on_honoring_pulse,
    raise_if_read_only,
)

PARALLEL_UPDATES = 0

# Matter "Lighting" device types from the Designer's optional per-output tag,
# mirrored into the catalogue ``type`` column both account tiers receive and
# surfaced as ``AmpioObject.matter_device_type``. A relay carrying one is
# installer intent that it drives a light; untagged relays are left for the
# switch platform.
LIGHT_MATTER_TYPES = frozenset({0x0100, 0x0101, 0x010C, 0x010D})

# A bare turn_on (no requested color) on an all-zero rgbw output raises the
# white channel instead of writing back the dark state it started from.
_DEFAULT_RGBW = (0, 0, 0, 255)


def _at_level(color: tuple[int, ...], level: int) -> tuple[int, ...]:
    """``color`` scaled so its peak channel reads ``level``.

    Home Assistant reads a light's color apart from its brightness, so an
    output that carries its level in the channels reports the color at
    level 255 and takes a requested color back down to its brightness. An
    all-zero color has no hue to scale and comes back unchanged. Rounding
    keeps the round trip stable where truncation drops a channel by one.
    """
    if not (peak := max(color)):
        return color
    return tuple(round(channel * level / peak) for channel in color)


def _kelvin_from_coldness(coldness: int, minimum: int, maximum: int) -> int:
    """The kelvin value a raw coldness byte presents as.

    A non-DALI object reports `min` 0 and `max` 255, so the scale is a
    presentation choice rather than a measurement. Rounding is what keeps
    the round trip stable: truncation sends coldness 85 to 3511 K, and
    3511 K maps back to 84.
    """
    return minimum + round(coldness * (maximum - minimum) / 255)


def _coldness_from_kelvin(kelvin: int, minimum: int, maximum: int) -> int:
    """The raw coldness byte a kelvin value writes, held inside 0-255.

    An out-of-range kelvin arrives from Home Assistant's unbounded
    ``color_xy_to_temperature``, converting an rgb, hs, or xy color request
    on this color-temperature-only light. The clamp keeps ``set_ww`` from
    raising ``AmpioValueError`` on one.
    """
    return max(0, min(255, round((kelvin - minimum) * 255 / (maximum - minimum))))


# The touch field numbers a per-field call colors. Validated against the
# panel's own count in _AmpioPanelLight.async_set_fields, not here, because
# the count comes from the administrator's capability map.
_FIELDS_VALIDATOR = vol.All(cv.ensure_list, [vol.Coerce(int)])
SET_BACKLIGHT_FIELDS_SCHEMA: VolDictType = {
    vol.Required("fields"): _FIELDS_VALIDATOR,
    vol.Required(ATTR_RGBW_COLOR): vol.All(
        vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 4)
    ),
}
SET_STATUS_FIELDS_SCHEMA: VolDictType = {
    vol.Required("fields"): _FIELDS_VALIDATOR,
    vol.Required(ATTR_RGB_COLOR): vol.All(
        vol.Coerce(tuple), vol.ExactSequence((cv.byte,) * 3)
    ),
}


def is_light(obj: AmpioObject) -> bool:
    """Whether the object belongs to the light platform.

    ``matter_device_type`` is the catalogue-column tag - the
    tier-independent classification source. The admin-only record tag
    (``record.matter_device_type``) never feeds the partition. A
    bell-marked relay belongs to the button platform whatever its tag.
    """
    if obj.bell:
        return False
    if not isinstance(kind := obj.kind, OutputKind):
        return False
    if kind.color or kind.dimmable or kind.color_temp:
        return True
    return kind.key == "relay" and obj.matter_device_type in LIGHT_MATTER_TYPES


def build_lights(data: AmpioData, obj: AmpioObject) -> list[AmpioLight]:
    """The light platform's entities for one object."""
    return [AmpioLight(data, obj)] if is_light(obj) else []


def build_panel_backlights(
    data: AmpioData, admin: AmpioAdminClient, mac: int
) -> list[AmpioPanelBacklight]:
    """The backlight entity for a module that supports the command."""
    if ModuleFunction.BACKLIGHT_RGBW not in admin.capabilities.get(mac, {}):
        return []
    return [AmpioPanelBacklight(data, admin, mac)]


def build_panel_status_lights(
    data: AmpioData, admin: AmpioAdminClient, mac: int
) -> list[AmpioPanelStatusLight]:
    """The status-light entity for a module that supports the command."""
    if ModuleFunction.STATUSLIGHT_RGB not in admin.capabilities.get(mac, {}):
        return []
    return [AmpioPanelStatusLight(data, admin, mac)]


async def _async_set_backlight_fields(entity: LightEntity, call: ServiceCall) -> None:
    """Color named touch fields on the backlight, or say why this entity cannot.

    A callable handler, not the entity's own method name, because it must
    answer for every light entity the platform has - an object-backed
    ``AmpioLight`` and the status light included - and name the surface
    ``ampio.set_backlight_fields`` drives when the target is neither.
    """
    if not isinstance(entity, AmpioPanelBacklight):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_panel_backlight"
        )
    await entity.async_set_fields(call.data["fields"], call.data[ATTR_RGBW_COLOR])


async def _async_set_status_fields(entity: LightEntity, call: ServiceCall) -> None:
    """Color named touch fields on the status light, or say why this entity cannot.

    The mirror of :func:`_async_set_backlight_fields`, for
    ``ampio.set_status_fields``.
    """
    if not isinstance(entity, AmpioPanelStatusLight):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_panel_status_light"
        )
    await entity.async_set_fields(call.data["fields"], call.data[ATTR_RGB_COLOR])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the light platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_lights, async_add_entities)
    entry.runtime_data.async_add_admin_module_platform(
        build_panel_backlights, async_add_entities
    )
    entry.runtime_data.async_add_admin_module_platform(
        build_panel_status_lights, async_add_entities
    )
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_backlight_fields",
        SET_BACKLIGHT_FIELDS_SCHEMA,
        _async_set_backlight_fields,
    )
    platform.async_register_entity_service(
        "set_status_fields", SET_STATUS_FIELDS_SCHEMA, _async_set_status_fields
    )


class AmpioLight(AmpioEntity, LightEntity):
    """A light backed by an Ampio output object.

    An rgbw output presents its four channels as ``ColorMode.RGBW`` by
    default. With the ``blend_white`` option it presents ``ColorMode.RGB``
    instead: a written color takes its white channel from the common part
    of red, green, and blue, and the reported color adds white back in.
    """

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize in the one color mode the object's kind supports."""
        super().__init__(data, obj)
        kind = obj.kind
        self._rgbw_output = isinstance(kind, OutputKind) and kind.color
        if self._rgbw_output:
            mode = (
                ColorMode.RGB
                if data.entry.options.get(CONF_BLEND_WHITE, False)
                else ColorMode.RGBW
            )
        elif isinstance(kind, OutputKind) and kind.color_temp:
            mode = ColorMode.COLOR_TEMP
        elif isinstance(kind, OutputKind) and kind.dimmable:
            mode = ColorMode.BRIGHTNESS
        else:
            mode = ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the light is on, or None once the object is gone.

        A CCT light holds its color temperature while off, so its raw
        state value stays non-zero and the power axis is the only honest
        answer.
        """
        if (obj := self._object) is None:
            return None
        if self._attr_color_mode is ColorMode.COLOR_TEMP:
            return None if (cct := obj.cct) is None else cct[0] > 0
        return obj.is_on

    @property
    @override
    def brightness(self) -> int | None:
        """The 0-255 level a light reports.

        A dimmer's own level, an rgbw output's peak channel, or a CCT
        light's power axis.
        """
        if (obj := self._object) is None:
            return None
        if self._rgbw_output:
            return None if (rgbw := obj.rgbw) is None else max(rgbw)
        if self._attr_color_mode is ColorMode.COLOR_TEMP:
            return None if (cct := obj.cct) is None else cct[0]
        if self._attr_color_mode is ColorMode.BRIGHTNESS:
            return None if (level := obj.numeric_value) is None else int(level)
        return None

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """The four channels of an rgbw output, with the peak at 255."""
        if (obj := self._object) is None or (rgbw := obj.rgbw) is None:
            return None
        red, green, blue, white = _at_level(rgbw, 255)
        return (red, green, blue, white)

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int] | None:
        """The blended color of an rgbw output, with the peak at 255.

        ``AmpioObject.rgbw`` reads None for every other kind, so no mode
        check belongs here.
        """
        if (obj := self._object) is None or (rgbw := obj.rgbw) is None:
            return None
        return color_rgbw_to_rgb(*_at_level(rgbw, 255))

    @property
    @override
    def color_temp_kelvin(self) -> int | None:
        """The presented color temperature of a CCT output.

        ``AmpioObject.cct`` reads None for every other kind, so no mode
        check belongs here.
        """
        if (obj := self._object) is None or (cct := obj.cct) is None:
            return None
        return _kelvin_from_coldness(
            cct[1], self.min_color_temp_kelvin, self.max_color_temp_kelvin
        )

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, honoring brightness, color, and color temperature.

        Resolved rgbw channels that are all zero mean off; an explicit
        all-zero color is a request for darkness, and turn_off is its
        honest execution. A blended rgb color becomes rgbw channels
        before any of that, and a brightness-only call scales the
        channels the output holds, whatever their white share. A CCT turn-on that names no temperature writes
        the power axis alone, which leaves the temperature where it
        stands. One that names no brightness writes the temperature axis
        alone, on top of whatever power the light already stands at, and
        never reads that power back first - unless the light is off, in
        which case a bare temperature must still turn it on, so the
        write carries the full-power constant into the same setWW frame
        instead of a stale or absent power byte.

        A Designer read-only object raises instead of sending a write the
        M-SERV would silently drop.
        """
        raise_if_read_only(self._object)
        client = self._data.client
        if self._rgbw_output:
            current = self._object.rgbw if self._object else None
            lit = current if current and any(current) else None
            rgbw: tuple[int, int, int, int] | None = kwargs.get(ATTR_RGBW_COLOR)
            if (rgb := kwargs.get(ATTR_RGB_COLOR)) is not None:
                rgbw = color_rgb_to_rgbw(*rgb)
            if rgbw is None:
                rgbw = lit or _DEFAULT_RGBW
            brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
            if brightness is None:
                brightness = max(lit) if lit else 255
            red, green, blue, white = _at_level(rgbw, brightness)
            rgbw = (red, green, blue, white)
            if not any(rgbw):
                await self.async_turn_off()
                return
            await client.set_colors(self._object_id, *rgbw)
            return
        if self._attr_color_mode is ColorMode.COLOR_TEMP:
            power: int | None = kwargs.get(ATTR_BRIGHTNESS)
            kelvin: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
            if kelvin is None:
                if power is None:
                    current_cct = self._object.cct if self._object else None
                    power = current_cct[0] if current_cct and current_cct[0] else 255
                await client.set_ww_power(self._object_id, power)
                return
            coldness = _coldness_from_kelvin(
                kelvin, self.min_color_temp_kelvin, self.max_color_temp_kelvin
            )
            # self.is_on reads the same cached object state as every
            # property here, so a coldness-only write can land with no
            # power byte if the object has already gone dark through
            # another path, such as a Designer rule, a scene, or a
            # physical override. Local push closes that window in
            # milliseconds, so no read-back guards this write.
            if power is None and self.is_on:
                await client.set_ww_coldness(self._object_id, coldness)
                return
            await client.set_ww(
                self._object_id, 255 if power is None else power, coldness
            )
            return
        obj = self._object
        if (
            self._attr_color_mode is ColorMode.BRIGHTNESS
            and (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None
        ):
            await client.set_value(
                self._object_id,
                brightness,
                pulse_ms=(obj.pulse_ms or None) if obj else None,
            )
            return
        await async_turn_on_honoring_pulse(client, obj, self._object_id)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off; the client routes rgbw off to setColors.

        A Designer read-only object raises instead of sending a write the
        M-SERV would silently drop.
        """
        raise_if_read_only(self._object)
        await self._data.client.turn_off(self._object_id)


class _AmpioPanelLight(AmpioModuleEntity, LightEntity):
    """Shared behavior for a panel's two color-holding surfaces.

    Neither the backlight nor the status light reports its color on the
    bus: both writes are runtime overrides, not settings, so this entity
    holds whatever it last asked for. It starts from
    :pyattr:`AmpioAdminClient.panel_settings`, Ampio Designer's stored default,
    when the library has proven the panel's layout, and at all channels
    zero otherwise.

    A subclass differs only in its channel count, its client command, and
    its stored default field: it sets ``_channels``, ``_color_attr``,
    ``_plain_default``, and ``_failure_key``, and implements
    ``_stored_default`` and ``_publish``.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Neither surface reads back, so the color this entity reports is never
    # confirmed. This tells the frontend to stop presenting it as such, and
    # to split the toggle into separate on and off buttons.
    _attr_assumed_state = True
    # The channel count, the kwargs key carrying a requested color, the
    # color sent when neither a call nor Designer supplies one, and the
    # translation key for a broker failure on this surface.
    _channels: int
    _color_attr: str
    _plain_default: tuple[int, ...]
    _failure_key: str

    def __init__(
        self, data: AmpioData, admin: AmpioAdminClient, mac: int, *, key_suffix: str
    ) -> None:
        """Attach to the module device, and seed state from panel_settings."""
        super().__init__(data, mac, key_suffix=key_suffix)
        self._admin = admin
        stored = self._stored_default()
        self._color: tuple[int, ...] = (
            stored if stored is not None else (0,) * self._channels
        )

    def _stored_default(self) -> tuple[int, ...] | None:
        """The color Ampio Designer stored for this surface, or None."""
        raise NotImplementedError

    async def _publish(
        self, color: tuple[int, ...], *, fields: Sequence[int] | None = None
    ) -> None:
        """Send ``color`` to the panel, every field by default."""
        raise NotImplementedError

    @property
    @override
    def is_on(self) -> bool:
        """Whether any channel is non-zero."""
        return any(self._color)

    @property
    @override
    def brightness(self) -> int:
        """The peak channel, the way ``AmpioLight`` reads an rgbw level."""
        return max(self._color)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the requested color, or restore what this entity held.

        With no requested color, a bare turn_on restores whatever this
        entity currently holds; only when that is dark does it fall back
        to the stored default, and only when that default is absent or
        itself all-black does it fall back further to plain white. This
        is what keeps a brightness-only call (the more-info dialog's
        slider sends exactly that) from jumping to an unrelated hue, the
        same guarantee ``AmpioLight.async_turn_on`` gives an object light.

        The color is then scaled so its peak channel reads the requested
        brightness, or the level this entity holds when none is requested,
        or 255 from dark, the same arithmetic ``AmpioLight.async_turn_on``
        uses. An explicit all-zero color means darkness, and routes to
        turn_off the same way.
        """
        color: tuple[int, ...] | None = kwargs.get(self._color_attr)
        if color is None:
            stored = self._stored_default()
            fallback = (
                stored if stored is not None and any(stored) else self._plain_default
            )
            color = self._color if any(self._color) else fallback
        brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            brightness = max(self._color) or 255
        color = _at_level(color, brightness)
        if not any(color):
            await self.async_turn_off()
            return
        await self._send(color)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send every channel at zero."""
        await self._send((0,) * self._channels)

    async def _send(self, color: tuple[int, ...]) -> None:
        """Publish ``color`` to every field, and remember what was sent."""
        await self._publish_translated(color)
        self._color = color
        self.async_write_ha_state()

    async def _publish_translated(
        self, color: tuple[int, ...], *, fields: Sequence[int] | None = None
    ) -> None:
        """Publish ``color``, translating a broker failure into a message."""
        try:
            await self._publish(color, fields=fields)
        except (AmpioValueError, AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=self._failure_key
            ) from err

    async def async_set_fields(
        self, fields: Sequence[int], color: tuple[int, ...]
    ) -> None:
        """Color the named touch fields, leaving this entity's own color alone.

        A per-field call colors part of the panel, while this entity holds
        one color for the whole surface, so there is no honest single color
        to store afterwards. The frame goes out and the state this entity
        reports stays whatever it was before the call.

        An empty ``fields`` would color nothing and report success, so it is
        refused rather than sent. A field number outside the panel's own
        touch field count raises a translated error naming the offending
        field and that count, because the panel would otherwise ignore the
        field in silence rather than refuse it.
        """
        if not fields:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="panel_no_fields"
            )
        # resolve_panel_settings is the library's only reader of the touch
        # field count and uses the documented BACKLIGHT_RGBW capability.
        # The library does not promise that STATUSLIGHT_RGB agrees with it.
        count = self._admin.capabilities.get(self._mac, {}).get(
            ModuleFunction.BACKLIGHT_RGBW
        )
        # Without a capability count, use the frame's field limit.
        ceiling = MAX_PANEL_FIELD if count is None else count
        for number in fields:
            if not 1 <= number <= ceiling:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="panel_field_out_of_range",
                    translation_placeholders={
                        "field": str(number),
                        "count": str(ceiling),
                    },
                )
        await self._publish_translated(color, fields=fields)


class AmpioPanelBacklight(_AmpioPanelLight):
    """Resting color of an Ampio touch panel's icons.

    The frame rides the CAN write tree, which answers the administrator
    login alone, so this entity is built on that account alone.
    """

    _channels = 4
    _attr_color_mode = ColorMode.RGBW
    _attr_supported_color_modes = {ColorMode.RGBW}
    _attr_translation_key = "backlight"
    _color_attr = ATTR_RGBW_COLOR
    _plain_default = _DEFAULT_RGBW
    _failure_key = "backlight_command_failed"

    def __init__(self, data: AmpioData, admin: AmpioAdminClient, mac: int) -> None:
        """Attach to the module device on override mac ``mac``."""
        super().__init__(data, admin, mac, key_suffix="backlight")

    @override
    def _stored_default(self) -> tuple[int, ...] | None:
        """The touch field color Ampio Designer stored, or None."""
        settings = self._admin.panel_settings.get(self._mac)
        return settings.touch_field_color if settings else None

    @override
    async def _publish(
        self, color: tuple[int, ...], *, fields: Sequence[int] | None = None
    ) -> None:
        module_id = self._require_module_id()
        await self._admin.set_panel_backlight(module_id, *color, fields=fields)

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int]:
        """The four channels currently asked for, with the peak at 255."""
        red, green, blue, white = _at_level(self._color, 255)
        return (red, green, blue, white)


class AmpioPanelStatusLight(_AmpioPanelLight):
    """Color an Ampio touch panel's status indicators show on a touch.

    The same runtime-override behavior as :class:`AmpioPanelBacklight`, on
    the surface with no white channel.
    """

    _channels = 3
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_translation_key = "status_light"
    _color_attr = ATTR_RGB_COLOR
    _plain_default = (255, 255, 255)
    _failure_key = "status_light_command_failed"

    def __init__(self, data: AmpioData, admin: AmpioAdminClient, mac: int) -> None:
        """Attach to the module device on override mac ``mac``."""
        super().__init__(data, admin, mac, key_suffix="status_light")

    @override
    def _stored_default(self) -> tuple[int, ...] | None:
        """The status indicator color Ampio Designer stored, or None."""
        settings = self._admin.panel_settings.get(self._mac)
        return settings.status_color if settings else None

    @override
    async def _publish(
        self, color: tuple[int, ...], *, fields: Sequence[int] | None = None
    ) -> None:
        module_id = self._require_module_id()
        await self._admin.set_panel_status_light(module_id, *color, fields=fields)

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int]:
        """The three channels currently asked for, with the peak at 255."""
        red, green, blue = _at_level(self._color, 255)
        return (red, green, blue)
