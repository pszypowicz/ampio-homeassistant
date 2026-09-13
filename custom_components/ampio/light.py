"""Light platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import (
    AmpioConnectionError,
    AmpioObject,
    AmpioTimeoutError,
    ModuleFunction,
    OutputKind,
)

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

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
    its stored default field: it sets ``_color_attr``, ``_plain_default``,
    and ``_failure_key``, and implements ``_stored_default`` and
    ``_publish``.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # The kwargs key carrying a requested color, the color sent when
    # neither a call nor Designer supplies one, and the translation key
    # for a broker failure on this surface.
    _color_attr: str
    _plain_default: tuple[int, ...]
    _failure_key: str

    def __init__(self, data: AmpioData, module_id: int, *, key_suffix: str) -> None:
        """Attach to the module device, and seed state from panel_settings."""
        super().__init__(data, module_id, key_suffix=key_suffix)
        stored = self._stored_default()
        self._color: tuple[int, ...] = (
            stored if stored is not None else tuple(0 for _ in self._plain_default)
        )

    def _stored_default(self) -> tuple[int, ...] | None:
        """The color Ampio Designer stored for this surface, or None."""
        raise NotImplementedError

    async def _publish(self, color: tuple[int, ...]) -> None:
        """Send ``color`` to the panel."""
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
        """Send the requested color, the stored default, or plain white.

        Brightness scales every channel toward the peak, the same
        arithmetic ``AmpioLight.async_turn_on`` uses. An explicit all-zero
        color means darkness, and routes to turn_off the same way.
        """
        color: tuple[int, ...] | None = kwargs.get(self._color_attr)
        if color is None:
            stored = self._stored_default()
            color = stored if stored is not None else self._plain_default
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
        await self._send(tuple(0 for _ in self._color))

    async def _send(self, color: tuple[int, ...]) -> None:
        """Publish ``color``, translate a failure, and remember what was sent."""
        try:
            await self._publish(color)
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
            ) from err
        except (AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=self._failure_key
            ) from err
        self._color = color
        self.async_write_ha_state()


class AmpioPanelBacklight(_AmpioPanelLight):
    """Resting color of an Ampio touch panel's icons.

    The frame rides the CAN write tree, which answers the administrator
    login alone, so this entity is built on that account alone.
    """

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
    async def _publish(self, color: tuple[int, ...]) -> None:
        await self._data.client.set_panel_backlight(self._module_id, *color)

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
    async def _publish(self, color: tuple[int, ...]) -> None:
        await self._data.client.set_panel_status_light(self._module_id, *color)

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int]:
        """The three channels currently asked for."""
        red, green, blue = self._color
        return (red, green, blue)
