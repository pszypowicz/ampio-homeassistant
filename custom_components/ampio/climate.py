"""Climate platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import ThermostatKind

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AmpioConfigEntry
from .entity import AmpioEntity, eligible_objects

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio thermostats from the discovery-time object catalogue."""
    data = entry.runtime_data
    async_add_entities(
        AmpioClimate(data, obj)
        for obj in eligible_objects(data.client)
        if isinstance(obj.kind, ThermostatKind)
    )


class AmpioClimate(AmpioEntity, ClimateEntity):
    """A heating regulator backed by an Ampio ``reg`` object.

    The library surfaces the regulator's running flag and the
    ``setTemperature`` verb; the rich readback (measured and target
    temperature, mode) is tracked in ampio-mqtt#73. Until it lands, the
    target temperature is the last value commanded from this entity and
    the current temperature stays unknown.
    """

    _attr_hvac_mode = HVACMode.HEAT
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature: float | None = None

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        """Heating while the running flag is set, idle otherwise."""
        if (obj := self._object) is None:
            return None
        return HVACAction.HEATING if obj.is_on else HVACAction.IDLE

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send the setpoint and remember it optimistically."""
        temperature: float = kwargs[ATTR_TEMPERATURE]
        await self._data.client.set_temperature(self._object_id, temperature)
        self._attr_target_temperature = temperature
        self.async_write_ha_state()
