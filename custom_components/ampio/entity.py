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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .data import AmpioData


def eligible_objects(client: AmpioClient) -> Iterator[AmpioObject]:
    """The objects any platform may expose as entities.

    ``visible`` is the M-SERV's own predicate for what the user still sees
    in Ampio Designer; ghost rows that survived removal fail it. The
    ``stable_key`` test then holds back the system objects, which the
    M-SERV exposes without a ``leaf_id`` and which no platform covers.
    """
    return (
        obj
        for obj in client.objects.values()
        if obj.visible and obj.stable_key is not None
    )


async def async_turn_on_honoring_pulse(
    client: AmpioClient, obj: AmpioObject | None, object_id: int
) -> None:
    """Send the on write, timed when Designer configures a pulse length.

    The M-SERV never applies the configured time server-side; the app
    reads the column and sends the timed command itself, and so does the
    integration. Without a configured time (or once the object is gone),
    the plain verb latches, which is the server truth either way.
    """
    if obj is not None and obj.pulse_ms:
        await client.set_value(object_id, 255, pulse_ms=obj.pulse_ms)
    else:
        await client.turn_on(object_id)


class AmpioEntity(Entity):
    """Entity backed by one Ampio object."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize from the discovery-time object snapshot."""
        self._data = data
        self._object_id = obj.id
        # Designer exposes one physical output as several objects, and every
        # such view repeats the ``leaf_id`` that ``stable_key`` is built
        # from. ``unique_key`` identifies the row instead, so each view keeps
        # its own entity. The prefix scopes it per server.
        self._attr_unique_id = f"{data.prefix}_{obj.unique_key}"
        # An object is a channel of the module that carries it, not a
        # deployed device of its own. Both the parent and its name derive
        # from the leaf-embedded mac, which every account tier receives, so
        # the tree and the entity ids it mints are identical on both tiers.
        mac = obj.module_mac
        on_hub = obj.is_server_owned or mac is None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.prefix if on_hub else f"{data.prefix}:{mac}")}
        )
        if obj.name:
            self._attr_name = obj.name

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
