"""Event platform for the Ampio integration."""

from typing import override

from ampio_mqtt import AvailabilityChanged, BusEventRaised, format_mac

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BUS_EVENT_KEY
from .data import HUB_IDENTIFIER, AmpioConfigEntry, AmpioData
from .entity import AmpioBaseEntity

PARALLEL_UPDATES = 0

EVENT_TYPE_RAISED = "raised"
ATTR_EVENT_NUMBER = "event_number"
ATTR_SOURCE = "source"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the bus event entity on the administrator login.

    The M-SERV serves received bus events on the raw tree, which answers
    the administrator login alone, so a standard account gets no entity:
    one that could never fire is worse than none. The record a downgrade
    leaves behind is named by the ``admin_only_records`` repair.
    """
    data = entry.runtime_data
    if data.admin is None:
        return
    async_add_entities([AmpioBusEvent(data)])


class AmpioBusEvent(AmpioBaseEntity, EventEntity):
    """The bus events Ampio logic and wall panels raise, on the hub.

    An event number is global on the bus: logic bound to a number runs
    whatever raised it, and an event raised through the command surface
    carries the M-SERV's own mac rather than a module's. So one entity on
    the hub carries every event, and an automation reacting to a number
    needs one trigger. The number and the mac that raised it ride the
    event's attributes.

    Nothing is filtered. The library documents event 254 from the M-SERV's
    own mac on every discovery refresh, and the entity reports it like any
    other, because which events mean what is the installer's Designer
    configuration, not something this integration knows.
    """

    _attr_translation_key = "bus_event"
    _attr_event_types = [EVENT_TYPE_RAISED]

    def __init__(self, data: AmpioData) -> None:
        """Attach to the hub, which stands for the bus."""
        self._data = data
        self._key = BUS_EVENT_KEY
        self._attr_unique_id = self._key
        self._attr_device_info = DeviceInfo(identifiers={HUB_IDENTIFIER})

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to the bus events and to the connection."""
        await super().async_added_to_hass()
        client = self._data.client
        self.async_on_remove(client.subscribe(self._event_raised, of=BusEventRaised))
        self.async_on_remove(
            client.subscribe(self._connection_changed, of=AvailabilityChanged)
        )

    @callback
    @override
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep the subscriptions, which follow the bus and the connection."""
        super().async_entity_id_changed(old_entity_id)

    @callback
    def _event_raised(self, event: BusEventRaised) -> None:
        """Fire the entity for one event on the bus."""
        self._trigger_event(
            EVENT_TYPE_RAISED,
            {
                ATTR_EVENT_NUMBER: event.event_number,
                ATTR_SOURCE: format_mac(event.mac),
            },
        )
        self.async_write_ha_state()

    @callback
    def _connection_changed(self, event: AvailabilityChanged) -> None:
        """Write state when the connection comes up or goes down."""
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        """Available while the broker is connected."""
        return self._data.client.available
