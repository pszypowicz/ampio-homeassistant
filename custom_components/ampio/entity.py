"""Base entity for the Ampio integration."""

from collections.abc import Iterator
from typing import override

from ampio_mqtt import (
    AmpioClient,
    AmpioObject,
    AvailabilityChanged,
    ObjectRemoved,
    ObjectUpdated,
)

from homeassistant.core import callback
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .data import AmpioData


def eligible_objects(client: AmpioClient) -> Iterator[AmpioObject]:
    """The objects any platform may expose as entities.

    ``visible`` is the M-SERV's own predicate for what the user still sees
    in Ampio Designer; ghost rows that survived removal fail it. A missing
    ``stable_key`` would otherwise leak into the unique_id.
    """
    return (
        obj
        for obj in client.objects.values()
        if obj.visible and obj.stable_key is not None
    )


class AmpioEntity(Entity):
    """Entity backed by one Ampio object."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize from the discovery-time object snapshot."""
        self._data = data
        self._object_id = obj.id
        # ``stable_key`` survives a module swap; the prefix scopes it per server.
        self._attr_unique_id = f"{data.prefix}_{obj.stable_key}"
        mac = obj.module_mac
        parent_device_id = (
            None
            if obj.is_server_owned or mac is None
            else data.module_device_ids.get(mac)
        )
        if parent_device_id is None:
            # Server-owned objects live on the hub device, which has its own
            # name, so the entity keeps the object's name.
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, data.prefix)})
            if obj.name:
                self._attr_name = obj.name
        else:
            # The child device carries the object's name and room. An entity
            # name of None then takes the device name instead of repeating
            # it; unnamed objects keep their translated description name.
            self._attr_device_info = ChildDeviceInfo(
                identifiers={(DOMAIN, f"{data.prefix}:obj:{obj.stable_key}")},
                name=obj.name or f"Ampio object {obj.stable_key}",
                parent_device_id=parent_device_id,
                suggested_area=data.rooms.get(obj.id),
            )
            if obj.name:
                self._attr_name = None

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to the pushes that affect this entity's state."""
        client = self._data.client
        self.async_on_remove(
            client.subscribe(
                self._push_received,
                of=(ObjectUpdated, ObjectRemoved),
                object_id=self._object_id,
            )
        )
        self.async_on_remove(
            client.subscribe(self._push_received, of=AvailabilityChanged)
        )

    @callback
    def _push_received(
        self, event: ObjectUpdated | ObjectRemoved | AvailabilityChanged
    ) -> None:
        """Write state when the backing object or the connection changes."""
        self.async_write_ha_state()

    @property
    def _object(self) -> AmpioObject | None:
        """The backing object, or None once the catalogue dropped it."""
        return self._data.client.objects.get(self._object_id)

    @property
    @override
    def available(self) -> bool:
        """Available while the broker is connected and the object exists."""
        return self._data.client.available and self._object is not None
