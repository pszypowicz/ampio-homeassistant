"""Base entity for the Ampio integration."""

from ampio_mqtt import AmpioObject

from homeassistant.const import CONF_MAC
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AmpioLocalCoordinator


def identifier_prefix(coordinator: AmpioLocalCoordinator) -> str:
    """Stable identifier prefix sourced from the M-SERV MAC.

    The config flow refuses to create an entry without a server identity, so
    ``unique_id`` is always set by the time entities are built.
    """
    unique_id = coordinator.config_entry.unique_id
    assert unique_id is not None
    return unique_id


def _is_load_attached(
    coordinator: AmpioLocalCoordinator,
    obj: AmpioObject,
) -> bool:
    """Whether the object physically lives elsewhere than its host module.

    Sensors always live at the module (an M-SENS reading is wherever the box
    is bolted). Future platforms may flip this on: a relay output drives a
    load that may live anywhere, and a wire-back input on an output-bearing
    module (M-REL) is the same shape. The coordinator argument is here so
    those future branches can consult ``module.capabilities`` without a
    signature churn. Today only sensor kinds reach this function, all of
    them module-attached.
    """
    return False


def _module_room_hint(coordinator: AmpioLocalCoordinator, module_id: int) -> str | None:
    """Return the unique room name shared by a module's module-attached objects.

    Walks every object the broker has classified under ``module_id``. Objects
    flagged load-attached by ``_is_load_attached`` are deliberately excluded
    so a multi-room relay board does not block the area hint on its own
    co-located sensor or input. Returns the room iff every module-attached
    object that has a room maps to the same one; otherwise ``None``.
    """
    if not coordinator.room_map:
        return None
    rooms: set[str] = set()
    for obj in coordinator.client.objects.values():
        if obj.device_id != module_id or obj.kind is None:
            continue
        if _is_load_attached(coordinator, obj):
            continue
        room = coordinator.room_map.get(obj.id)
        if room:
            rooms.add(room)
    if len(rooms) == 1:
        return rooms.pop()
    return None


def module_device_info(
    coordinator: AmpioLocalCoordinator, module_id: int | None
) -> DeviceInfo:
    """Build the device info for a physical module, or the generic hub.

    ``module_id is None`` produces the fallback "Ampio" hub device used when an
    object has no ``id_urzadzenia``. The M-SERV module gets its sw_version and
    configuration_url from the server info reply; all other modules are
    linked to the M-SERV through ``via_device`` so HA renders the topology.
    """
    prefix = identifier_prefix(coordinator)
    if module_id is None:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{prefix}:hub")},
            name="Ampio",
            manufacturer="Ampio",
        )
    module = coordinator.client.modules.get(module_id)
    mserv_id = coordinator.client.mserv_id
    server_info = coordinator.client.server_info
    is_mserv = mserv_id is not None and module_id == mserv_id

    info: DeviceInfo = {
        "identifiers": {(DOMAIN, f"{prefix}:{module_id}")},
        "name": (
            module.name
            if module is not None and module.name
            else f"Ampio module {module_id}"
        ),
        "manufacturer": "Ampio",
    }
    if module is not None and module.model:
        info["model"] = module.model
    if module is not None:
        # The globally-unique CAN id is the module's stable hardware address.
        # Prefer it over the local bus address, which is just a position (the
        # M-SERV's local mac is 1 while its global id carries the real value).
        can_mac = module.mac_global if module.mac_global is not None else module.mac
        if can_mac is not None:
            info["serial_number"] = f"0x{can_mac:X}"
    if is_mserv and server_info is not None and server_info.server_version:
        info["sw_version"] = server_info.server_version
    elif module is not None and module.sw_version is not None:
        info["sw_version"] = str(module.sw_version)
    if module is not None and module.hw_version is not None:
        info["hw_version"] = str(module.hw_version)
    if is_mserv and server_info is not None and server_info.local_ip:
        info["configuration_url"] = f"http://{server_info.local_ip}"
    if is_mserv:
        ethernet_mac = coordinator.config_entry.data.get(CONF_MAC)
        if ethernet_mac:
            info["connections"] = {(CONNECTION_NETWORK_MAC, ethernet_mac)}
    if not is_mserv and mserv_id is not None:
        info["via_device"] = (DOMAIN, f"{prefix}:{mserv_id}")
    if not is_mserv:
        # The M-SERV is infrastructure (server cabinet, comms closet); even
        # when its module-attached objects share a room, an area there is a
        # worse hint than none.
        room = _module_room_hint(coordinator, module_id)
        if room is not None:
            info["suggested_area"] = room
    return info


class AmpioEntity(CoordinatorEntity[AmpioLocalCoordinator]):
    """Base class for Ampio entities backed by a DB object."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AmpioLocalCoordinator, object_id: int) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._object_id = object_id
        obj = coordinator.client.objects[object_id]
        self._attr_unique_id = f"{identifier_prefix(coordinator)}_obj_{object_id}"
        self._attr_device_info = module_device_info(coordinator, obj.device_id)

    @property
    def object(self) -> AmpioObject | None:
        """The current backing object, if still present."""
        return self.coordinator.client.objects.get(self._object_id)

    @property
    def available(self) -> bool:
        """Available when connected and the object exists."""
        return (
            super().available
            and self.coordinator.client.available
            and self.object is not None
        )
