"""Light platform for the Ampio integration."""

from collections.abc import Sequence
from typing import Any, override

from ampio_mqtt import (
    MAX_PANEL_FIELD,
    AmpioConnectionError,
    AmpioObject,
    AmpioTimeoutError,
    ModuleFunction,
    OutputKind,
)
import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
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

from .const import DOMAIN
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, AmpioModuleEntity, async_turn_on_honoring_pulse

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

# The touch field numbers a per-field call colors. Validated against the
# panel's own count in _AmpioPanelLight.async_set_fields, not here, because
# the count is a module-catalogue fact this schema cannot see.
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
    if kind.color or kind.dimmable:
        return True
    return kind.key == "relay" and obj.matter_device_type in LIGHT_MATTER_TYPES


def build_lights(data: AmpioData, obj: AmpioObject) -> list[AmpioLight]:
    """The light platform's entities for one object."""
    return [AmpioLight(data, obj)] if is_light(obj) else []


def build_panel_backlights(
    data: AmpioData, module_id: int
) -> list[AmpioPanelBacklight]:
    """The light platform's backlight entity for one module device.

    A standard account receives no module catalogue, so the capability is
    unknowable there. This answers for every row on that tier. On that
    tier the factory's answer reaches the withheld enumeration, and the
    tier gate means nothing is built from it. A bare capability check
    would leave an orphaned record in the repair card meant for a
    Designer deletion.
    """
    if not data.is_admin:
        return [AmpioPanelBacklight(data, module_id)]
    module = data.module_row(module_id)
    if module is None or ModuleFunction.BACKLIGHT_RGBW not in module.capabilities:
        return []
    return [AmpioPanelBacklight(data, module_id)]


def build_panel_status_lights(
    data: AmpioData, module_id: int
) -> list[AmpioPanelStatusLight]:
    """The light platform's status-light entity for one module device.

    Follows the same tier-gated shape as :func:`build_panel_backlights`.
    """
    if not data.is_admin:
        return [AmpioPanelStatusLight(data, module_id)]
    module = data.module_row(module_id)
    if module is None or ModuleFunction.STATUSLIGHT_RGB not in module.capabilities:
        return []
    return [AmpioPanelStatusLight(data, module_id)]


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
    entry.runtime_data.async_add_module_platform(
        build_panel_backlights, async_add_entities, admin_only=True
    )
    entry.runtime_data.async_add_module_platform(
        build_panel_status_lights, async_add_entities, admin_only=True
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
    """A light backed by an Ampio output object."""

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize in the one color mode the object's kind supports."""
        super().__init__(data, obj)
        kind = obj.kind
        if isinstance(kind, OutputKind) and kind.color:
            mode = ColorMode.RGBW
        elif isinstance(kind, OutputKind) and kind.dimmable:
            mode = ColorMode.BRIGHTNESS
        else:
            mode = ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the light is on, or None once the object is gone."""
        if (obj := self._object) is None:
            return None
        return obj.is_on

    @property
    @override
    def brightness(self) -> int | None:
        """The 0-255 level for a dimmer, the peak channel for rgbw."""
        if (obj := self._object) is None:
            return None
        if self._attr_color_mode is ColorMode.RGBW:
            return None if (rgbw := obj.rgbw) is None else max(rgbw)
        if self._attr_color_mode is ColorMode.BRIGHTNESS:
            return None if (level := obj.numeric_value) is None else int(level)
        return None

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """The four channels of an rgbw output."""
        if (obj := self._object) is None:
            return None
        return obj.rgbw

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, honoring brightness and rgbw color.

        Resolved rgbw channels that are all zero mean off; an explicit
        all-zero color is a request for darkness, and turn_off is its
        honest execution.
        """
        client = self._data.client
        if self._attr_color_mode is ColorMode.RGBW:
            rgbw: tuple[int, int, int, int] | None = kwargs.get(ATTR_RGBW_COLOR)
            if rgbw is None:
                current = self._object.rgbw if self._object else None
                rgbw = current if current and any(current) else _DEFAULT_RGBW
            if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
                peak = max(rgbw) or 255
                red, green, blue, white = (
                    channel * brightness // peak for channel in rgbw
                )
                rgbw = (red, green, blue, white)
            if not any(rgbw):
                await self.async_turn_off()
                return
            await client.set_colors(self._object_id, *rgbw)
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
        """Turn the light off; the client routes rgbw off to setColors."""
        await self._data.client.turn_off(self._object_id)


class _AmpioPanelLight(AmpioModuleEntity, LightEntity):
    """Shared behavior for a panel's two color-holding surfaces.

    Neither the backlight nor the status light reports its color on the
    bus: both writes are runtime overrides, not settings, so this entity
    holds whatever it last asked for. It starts from
    :pyattr:`AmpioModule.panel_settings`, Ampio Designer's stored default,
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

    def __init__(self, data: AmpioData, module_id: int, *, key_suffix: str) -> None:
        """Attach to the module device, and seed state from panel_settings."""
        super().__init__(data, module_id, key_suffix=key_suffix)
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

        Brightness then scales every channel toward the peak, the same
        arithmetic ``AmpioLight.async_turn_on`` uses. An explicit all-zero
        color means darkness, and routes to turn_off the same way.
        """
        color: tuple[int, ...] | None = kwargs.get(self._color_attr)
        if color is None:
            stored = self._stored_default()
            fallback = (
                stored if stored is not None and any(stored) else self._plain_default
            )
            color = self._color if any(self._color) else fallback
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            peak = max(color) or 255
            color = tuple(channel * brightness // peak for channel in color)
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
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
            ) from err
        except (AmpioConnectionError, AmpioTimeoutError) as err:
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
        module = self._data.module_row(self._module_id)
        # _panel_mask is one function serving both frames, so the backlight
        # and the status light address the same touch fields under the same
        # numbering. BACKLIGHT_RGBW and STATUSLIGHT_RGB agree on every panel
        # measured, but the library reads STATUSLIGHT_RGB nowhere and
        # documents BACKLIGHT_RGBW alone as the touch field count, the one
        # resolve_panel_settings reads. Reading the documented field keeps
        # this code off an agreement the library never promises.
        count = (
            module.capabilities.get(ModuleFunction.BACKLIGHT_RGBW) if module else None
        )
        # count comes back None down one path here: the row left the
        # catalogue mid-session, because Designer deletes a device at once
        # and only unassigns its objects. module_row's other None path, a
        # standard account never served the catalogue, cannot reach this
        # method, since both panel entities are admin_only. Not observed:
        # a row present without a BACKLIGHT_RGBW entry. MAX_PANEL_FIELD is
        # the library's own ceiling, the highest field any frame carries,
        # so it stands in for the count a missing row loses.
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

    def __init__(self, data: AmpioData, module_id: int) -> None:
        """Attach to the module device of Designer row ``module_id``."""
        super().__init__(data, module_id, key_suffix="backlight")

    @override
    def _stored_default(self) -> tuple[int, ...] | None:
        """The touch field color Ampio Designer stored, or None."""
        module = self._data.module_row(self._module_id)
        settings = module.panel_settings if module else None
        return settings.touch_field_color if settings else None

    @override
    async def _publish(
        self, color: tuple[int, ...], *, fields: Sequence[int] | None = None
    ) -> None:
        await self._data.client.set_panel_backlight(
            self._module_id, *color, fields=fields
        )

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int]:
        """The four channels currently asked for."""
        red, green, blue, white = self._color
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

    def __init__(self, data: AmpioData, module_id: int) -> None:
        """Attach to the module device of Designer row ``module_id``."""
        super().__init__(data, module_id, key_suffix="status_light")

    @override
    def _stored_default(self) -> tuple[int, ...] | None:
        """The status indicator color Ampio Designer stored, or None."""
        module = self._data.module_row(self._module_id)
        settings = module.panel_settings if module else None
        return settings.status_color if settings else None

    @override
    async def _publish(
        self, color: tuple[int, ...], *, fields: Sequence[int] | None = None
    ) -> None:
        await self._data.client.set_panel_status_light(
            self._module_id, *color, fields=fields
        )

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int]:
        """The three channels currently asked for."""
        red, green, blue = self._color
        return (red, green, blue)
