"""The Ampio integration."""

import logging

from ampio_mqtt import (
    AmpioAuthError,
    AmpioClient,
    AmpioConnectionError,
    AuthFailed,
    AvailabilityChanged,
    ConnectionDied,
    InputKind,
    SensorKind,
)

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .data import AmpioConfigEntry, AmpioData
from .entity import eligible_objects

_LOGGER = logging.getLogger(__name__)


def _opt_str(value: object | None) -> str | None:
    """Stringify a catalogue field, passing None through."""
    return None if value is None else str(value)


async def async_setup_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Set up Ampio from a config entry."""
    client = AmpioClient(
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    entry.async_on_unload(client.stop)

    # Home Assistant does not unload entries when it stops, so without this the
    # connection dies by task cancellation and is reported as a lost connection.
    async def _async_stop_client(event: Event) -> None:
        await client.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop_client)
    )

    try:
        discovered = await client.start()
    except AmpioAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except AmpioConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err
    # A True start() guarantees the server identity; the None check narrows the type.
    if not discovered or (info := client.server_info) is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="discovery_timeout"
        )
    prefix = info.key
    # A different M-SERV answering at the stored host must fail setup instead
    # of silently re-keying every unique_id and device under its prefix.
    if prefix != entry.unique_id:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="unexpected_device"
        )

    # The hub is built from the server-info reply both account tiers receive;
    # an administrator's M-SERV module row contributes the user-given name.
    device_registry = dr.async_get(hass)
    mserv = client.mserv
    hub = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, prefix)},
        manufacturer="Ampio",
        name=mserv.name if mserv and mserv.name else "M-SERV",
        model=mserv.model if mserv and mserv.model else "M-SERV",
        sw_version=info.server_version,
        serial_number=info.device_id,
        configuration_url=f"http://{info.local_ip}" if info.local_ip else None,
    )

    # One parent device per module, registered before the platforms load so
    # every object entity can reference its parent by registry id. Keyed on
    # the leaf-derived mac both account tiers receive; the admin-only module
    # catalogue contributes metadata only, so a tier downgrade degrades the
    # whole device coherently instead of mixing the fallback name with stale
    # metadata.
    module_device_ids: dict[int, str] = {}
    for obj in eligible_objects(client):
        if obj.is_server_owned or (mac := obj.module_mac) is None:
            continue
        if mac in module_device_ids:
            continue
        module = client.module_for(obj)
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{prefix}:{mac}")},
            name=(module.name if module else None) or f"Ampio module 0x{mac:X}",
            manufacturer="Ampio",
            via_device_id=hub.id,
            model=module.model if module else None,
            sw_version=_opt_str(module.sw_version) if module else None,
            hw_version=_opt_str(module.hw_version) if module else None,
            serial_number=_opt_str(module.mac_global) if module else None,
        )
        module_device_ids[mac] = device.id

    # The room map only seeds suggested areas at device creation, so a
    # failed fetch degrades to no suggestions instead of failing setup.
    rooms: dict[int, str] = {}
    try:
        rooms = await client.fetch_rooms()
    except AmpioConnectionError:
        _LOGGER.warning(
            "Could not fetch the Ampio room map; "
            "new devices register without a suggested area"
        )

    entry.runtime_data = AmpioData(client, prefix, hub.id, module_device_ids, rooms)

    was_unavailable = False

    @callback
    def _availability_changed(event: AvailabilityChanged) -> None:
        """Log a real outage once on loss and once on restore."""
        nonlocal was_unavailable
        if not event.available:
            was_unavailable = True
            _LOGGER.warning("Connection to the Ampio server lost; reconnecting")
        elif was_unavailable:
            was_unavailable = False
            _LOGGER.info("Connection to the Ampio server restored")

    @callback
    def _connection_ended(event: AuthFailed | ConnectionDied) -> None:
        """Recover from a terminal connection failure by re-running setup.

        Both events mean the library's reconnect loop has stopped for good;
        reloading re-raises a credential rejection as ConfigEntryAuthFailed
        and retries everything else with backoff.
        """
        _LOGGER.error(
            "Connection to the Ampio server ended (%s); reloading", event.reason
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        client.subscribe(_availability_changed, of=AvailabilityChanged)
    )
    entry.async_on_unload(
        client.subscribe(_connection_ended, of=(AuthFailed, ConnectionDied))
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AmpioConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing devices whose objects the account no longer receives.

    Grant changes and tier downgrades leave devices behind by design; this
    lets the user prune them while every live device stays protected.

    Only OutputKind and ThermostatKind objects get devices of their own;
    InputKind and SensorKind objects attach as plain entities and do not need
    protection.
    """
    data = entry.runtime_data
    live = {data.prefix}
    for obj in eligible_objects(data.client):
        if not isinstance(obj.kind, InputKind | SensorKind):
            live.add(f"{data.prefix}:obj:{obj.stable_key}")
        if not obj.is_server_owned and (mac := obj.module_mac) is not None:
            live.add(f"{data.prefix}:{mac}")
    return not any(
        domain == DOMAIN and identifier in live
        for domain, identifier in device_entry.identifiers
    )
