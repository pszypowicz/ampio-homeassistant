"""Base entity for the Ampio integration."""

from typing import override

from ampio_mqtt import (
    AmpioClient,
    AmpioObject,
    AvailabilityChanged,
    ObjectRemoved,
    ObjectUpdated,
    format_mac,
)

from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MODULE_KEY_PREFIX
from .data import AmpioData, module_identifier


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


def raise_if_read_only(obj: AmpioObject | None) -> None:
    """Raise before a write the M-SERV would silently drop.

    Designer's read-only marker is enforced server-side on both account
    tiers, which drops such a write with no error and no echo. The entity
    keeps its platform because the checkbox can change at any time.
    """
    if obj is not None and obj.read_only:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="read_only_object",
        )


class AmpioBaseEntity(Entity):
    """Entity that carries this integration's naming and key conventions.

    Home Assistant composes the entity id once, at first registration,
    from the area name, the device name and the entity name by default,
    leaving out any part that is empty. The user's Entity ID format setting
    can leave out the area or add the floor, and it cannot leave out the
    device or the entity name. This class writes no id, so an Ampio entity
    follows that setting like any other.

    Every device this integration creates carries a name, so Home
    Assistant's own ``<platform>_<unique id>`` fallback is not reached: an
    object with no Designer name takes the ``object`` device translation.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    # The unique id. A subclass sets it before the add, and sets
    # ``_attr_unique_id`` to the same string.
    _key: str


class AmpioEntity(AmpioBaseEntity):
    """Entity backed by one Ampio object."""

    def __init__(
        self, data: AmpioData, obj: AmpioObject, *, key_suffix: str = ""
    ) -> None:
        """Initialize from the discovery-time object snapshot.

        ``key_suffix`` separates a second entity built from one object, and
        it reaches the unique id alone: Home Assistant composes the entity
        id from the area name, the device name and the entity name by
        default. No server scope: object ids are unique per M-SERV, and one
        M-SERV is allowed.
        """
        self._data = data
        self._object_id = obj.id
        # Designer exposes one physical output as several objects, and every
        # such view repeats the ``leaf_id`` that ``leaf_key`` is built
        # from. ``object_key`` identifies the row instead, so each view
        # keeps its own entity.
        self._key = f"{obj.object_key}{key_suffix}"
        self._attr_unique_id = self._key
        # An object is a channel of its module, and its child device hangs
        # under that module. The registry cannot re-parent a child, so an
        # object moved in Designer registers under the parent its child
        # already has, until a delete, which the removal hook permits,
        # lets the child come back under the new one.
        parent = data.registered_parent_for(obj)
        device_info = ChildDeviceInfo(
            identifiers={(DOMAIN, obj.object_key)}, parent_device_id=parent
        )
        # ``name`` is the Designer ``opis_menu`` column, which is the name
        # the user gave the object in the Ampio app. The device carries it,
        # so the primary entity adds no name of its own. An unnamed object
        # reads a translated placeholder, and its entity keeps the
        # platform's kind name.
        if obj.name:
            device_info["name"] = obj.name
            self._attr_name = None
        else:
            device_info["translation_key"] = "object"
            device_info["translation_placeholders"] = {"id": str(obj.id)}
        # The app room seeds the area once, at the device's first creation.
        # The registry never moves a device on a later suggestion.
        if (room := data.rooms.get(obj.id)) is not None:
            device_info["suggested_area"] = room
        self._attr_device_info = device_info

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
        """Available while the broker is connected and the object is still served.

        The library's admission door evicts a hidden row before any
        consumer sees it, so a Designer delete reads the same on either
        account tier: the object leaves ``objects`` and ``_object`` goes
        None. There is no hidden bit left for an entity to read, and no
        tier the delete signal differs on.
        """
        return self._data.client.available and self._object is not None


class AmpioModuleEntity(AmpioBaseEntity):
    """Entity that attaches to a module device rather than to an object.

    The key is the module's override mac, which every object carries in
    its address on both account tiers and which Ampio Designer re-stamps
    onto a replacement unit, so a swapped module keeps its device and its
    entities. The Designer row id is no identity here: it is reassigned
    when a module is replaced, and only the administrator login is served
    it at all. It is the argument the module commands take, and
    ``_require_module_id`` resolves it at call time.

    Availability follows the connection and nothing else. A reading of
    module data whose catalogue row is missing reports an unknown value,
    and the entity stays available.
    """

    def __init__(self, data: AmpioData, mac: int, *, key_suffix: str) -> None:
        """Attach to the module device on override mac ``mac``.

        ``key_suffix`` names what the entity does on the module, and it
        reaches the unique id alone: Home Assistant composes the entity id
        from the area name, the device name and the entity name by default.
        The key is built from ``MODULE_KEY_PREFIX``, which is also what the
        stale-record report reads a module record back through.
        """
        self._data = data
        self._mac = mac
        self._key = f"{MODULE_KEY_PREFIX}{format_mac(mac)}_{key_suffix}"
        self._attr_unique_id = self._key
        self._attr_device_info = DeviceInfo(identifiers={module_identifier(mac)})

    def _require_module_id(self) -> int:
        """The Designer row id the module commands address, resolved now.

        The name carries the raise, because a caller that cannot accept one
        has no business here. It raises when the catalogue holds no
        admitted row on this entity's mac, which is the right answer to a
        press that cannot reach the module and the wrong answer to a read:
        a surface that reports module data asks
        ``AmpioData.module_row_for`` with ``self._mac`` instead and reports
        an unknown reading on None. Losing the connection is what makes the
        entity unavailable; a missing catalogue row does not.

        Never cached. The row id is reassigned when a module is replaced,
        while the mac this entity keys on survives the swap.
        """
        module = self._data.module_row_for(self._mac)
        if module is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="module_not_addressable",
            )
        return module.id

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the connection, which is what availability reads."""
        self.async_on_remove(
            self._data.client.subscribe(
                self._connection_changed, of=AvailabilityChanged
            )
        )

    @callback
    def _connection_changed(self, event: AvailabilityChanged) -> None:
        """Write state when the connection comes up or goes down."""
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        """Available while the broker is connected."""
        return self._data.client.available
