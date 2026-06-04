"""Sensor platform for the Ampio integration."""

from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfPressure,
    UnitOfSoundPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import AmpioConfigEntry, AmpioLocalCoordinator
from .entity import AmpioEntity, identifier_prefix, module_device_info

PARALLEL_UPDATES = 0

# Static descriptions for the known sensor kinds the library can report.
# Translation keys map into strings.json -> entity.sensor.<key>.name.
_SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    "temperature": SensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "humidity": SensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "pressure_abs": SensorEntityDescription(
        key="pressure_abs",
        translation_key="pressure_abs",
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "pressure_rel": SensorEntityDescription(
        key="pressure_rel",
        translation_key="pressure_rel",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "loudness": SensorEntityDescription(
        key="loudness",
        translation_key="loudness",
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "illuminance": SensorEntityDescription(
        key="illuminance",
        translation_key="illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    "iaq": SensorEntityDescription(
        key="iaq",
        translation_key="iaq",
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    "co2": SensorEntityDescription(
        key="co2",
        translation_key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio sensors, adding new ones as they are discovered."""
    coordinator = entry.runtime_data
    known_objects: set[int] = set()
    known_modules: set[int] = set()

    @callback
    def _discover() -> None:
        new_modules = [
            mid for mid in coordinator.client.modules if mid not in known_modules
        ]
        new_sensors: list[tuple[int, SensorEntityDescription]] = []
        for oid, obj in coordinator.client.sensors.items():
            if oid in known_objects or obj.kind is None:
                continue
            description = _SENSOR_DESCRIPTIONS.get(obj.kind.key)
            if description is None or (obj.value is None and not obj.name):
                continue
            new_sensors.append((oid, description))
        if not new_sensors and not new_modules:
            return
        known_objects.update(oid for oid, _ in new_sensors)
        known_modules.update(new_modules)
        entities: list[SensorEntity] = [
            AmpioModuleLastSeenSensor(coordinator, mid) for mid in new_modules
        ]
        entities.extend(
            AmpioSensor(coordinator, oid, description)
            for oid, description in new_sensors
        )
        async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(_discover))
    _discover()


class AmpioSensor(AmpioEntity, SensorEntity):
    """A sensor backed by an Ampio DB object."""

    def __init__(
        self,
        coordinator: AmpioLocalCoordinator,
        object_id: int,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor from its classified object."""
        super().__init__(coordinator, object_id)
        self.entity_description = description
        obj = coordinator.client.objects[object_id]
        if obj.name:
            self._attr_name = obj.name

    @property
    def native_value(self) -> float | str | None:
        """Return the current value (numeric where possible)."""
        obj = self.object
        if obj is None or obj.value is None:
            return None
        try:
            return float(obj.value)
        except ValueError:
            return obj.value


class AmpioModuleLastSeenSensor(CoordinatorEntity[AmpioLocalCoordinator], SensorEntity):
    """Diagnostic timestamp of the last state any module object reported."""

    _attr_has_entity_name = True
    _attr_translation_key = "module_last_seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AmpioLocalCoordinator, module_id: int) -> None:
        """Initialize the diagnostic sensor for a physical module."""
        super().__init__(coordinator)
        self._module_id = module_id
        prefix = identifier_prefix(coordinator)
        self._attr_unique_id = f"{prefix}_module_{module_id}_last_seen"
        self._attr_device_info = module_device_info(coordinator, module_id)

    @property
    def available(self) -> bool:
        """Available when the broker is connected and the module is known."""
        return (
            super().available
            and self.coordinator.client.available
            and self._module_id in self.coordinator.client.modules
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the last-seen timestamp as a timezone-aware datetime."""
        module = self.coordinator.client.modules.get(self._module_id)
        if module is None or module.last_seen is None:
            return None
        return datetime.fromtimestamp(module.last_seen, tz=UTC)
