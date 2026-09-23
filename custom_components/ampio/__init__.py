"""The Ampio integration."""

from collections.abc import Mapping
from functools import partial
import logging
from typing import Any, cast

from ampio_mqtt import (
    AmpioAdminClient,
    AmpioAuthError,
    AmpioClient,
    AmpioConnectionError,
    AmpioNotConfigured,
    AmpioTimeoutError,
    AmpioValueError,
    AuthFailed,
    AvailabilityChanged,
    ConnectionDied,
    NotConfigured,
    format_mac,
)
import voluptuous as vol

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    ADMIN_ONLY_RECORDS_ISSUE,
    ADMIN_USERNAME,
    DOMAIN,
    NOT_CONFIGURED_ISSUE,
    PLATFORMS,
    STALE_RECORDS_ISSUE,
)
from .data import AmpioConfigEntry, AmpioData, RefusedRows
from .stale import async_report_not_configured, async_report_stale_records

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_NOTIFICATION = "send_notification"
ATTR_MESSAGE = "message"


def _build_client(data: Mapping[str, Any]) -> AmpioClient:
    """The client class the stored account is served by."""
    if data[CONF_USERNAME] == ADMIN_USERNAME:
        return AmpioAdminClient(data[CONF_HOST], data[CONF_PASSWORD])
    return AmpioClient(data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD])


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions that address the install rather than an entity."""

    async def send_notification(call: ServiceCall) -> None:
        """Push a message to every user of the install's mobile app."""
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        # The manifest allows a single config entry, and only its runtime
        # data type carries the client.
        entry = cast(AmpioConfigEntry, entries[0])
        try:
            await entry.runtime_data.client.send_notification(call.data[ATTR_MESSAGE])
        except AmpioValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="notification_rejected"
            ) from err
        except (AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="notification_failed"
            ) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_NOTIFICATION,
        send_notification,
        schema=vol.Schema({vol.Required(ATTR_MESSAGE): cv.string}),
    )
    return True


async def _async_sweep_records(client: AmpioAdminClient) -> None:
    """Fill the admin-guarded record bundles, and log what the pass covered.

    Two replies, the name table and the list, so the pass is one round trip
    each rather than a walk over the modules. Setup waits for it because the
    capability map it fills decides which modules carry a buzzer, and a
    platform that loaded first would build none of them.
    """
    try:
        sweep = await client.resolve_records()
    except (AmpioConnectionError, AmpioTimeoutError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="records_unavailable"
        ) from err
    _LOGGER.debug(
        "The Designer description sweep read %d modules and %d stayed silent",
        len(sweep.answered_macs),
        len(sweep.silent_macs),
    )


async def async_setup_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Set up Ampio from a config entry."""
    client = _build_client(entry.data)
    entry.async_on_unload(client.disconnect)

    # Home Assistant does not unload entries when it stops, so without this the
    # connection dies by task cancellation and is reported as a lost connection.
    async def _async_disconnect_client(event: Event) -> None:
        await client.disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_disconnect_client)
    )

    not_configured: RefusedRows | None = None
    try:
        discovered = await client.connect()
    except AmpioAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except AmpioNotConfigured as err:
        # The library's admission door refuses a Designer row it cannot
        # address and serves every other one, with the connection up. One
        # unchecked Matter box must not take the install offline, so setup
        # runs on what was served and a repair names the refused rows. The
        # door is checked only after every initial reply has landed, so
        # the catalogue and the server info are populated here.
        # Ids alone, from here on. The exception's message embeds the
        # Designer names of the rows, and no object, room or module name
        # leaves the install, so the projection is taken first and the
        # exception is not read again.
        not_configured = RefusedRows.from_error(err)
        discovered = True
    except AmpioConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err
    # A True connect(), and an AmpioNotConfigured raised after every initial
    # reply landed, both leave the server identity in place. The None check
    # narrows the type.
    if not discovered or (info := client.server_info) is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="discovery_timeout"
        )
    # Every identity the integration writes is server-free, so a different
    # server answering at the stored host re-keys nothing. With one entry
    # allowed, it is a replacement or the user's own re-pointing, and the
    # entry takes the new server as its own.
    if info.server_key != entry.unique_id:
        _LOGGER.warning(
            "The Ampio server at %s reports mac %s; this entry was set up "
            "under server key %s, and is taking the new server over",
            entry.data[CONF_HOST],
            format_mac(info.mac),
            entry.unique_id,
        )
        hass.config_entries.async_update_entry(entry, unique_id=info.server_key)

    # From here to the subscriptions below, nothing awaits. The library
    # dispatches what its admission door refuses as an event, and an event
    # that lands while setup sits between connect and the subscription
    # reaches nobody. The installer repair would not appear, and the
    # refused row's records would read to the stale-record report as
    # leftovers. ``create`` is synchronous for that reason, and the room
    # map is fetched once the window is closed.
    entry.runtime_data = AmpioData.create(hass, entry, client, info)
    async_report_not_configured(hass, entry, not_configured)

    @callback
    def _not_configured(event: NotConfigured) -> None:
        """Raise or clear the installer repair as the admission door changes.

        The library reports every change of what the door refuses, and
        each event carries both sides, so a row that loses its Designer
        leaf and the fix that gives it back both land here. Two empty
        sides mean the door refuses nothing now.
        """
        async_report_not_configured(hass, entry, RefusedRows.from_event(event))

    entry.async_on_unload(client.subscribe(_not_configured, of=NotConfigured))

    # The subscription starts before the platforms load. An event that lands
    # while a platform is still loading queues its id like any other, and a
    # platform that registers later builds the new object in its own initial
    # pass. The one window is the loop turn between a platform's registration
    # and its initial add; a batch in that turn adds the same unique id, and
    # Home Assistant refuses the duplicate with a log line.
    entry.async_on_unload(entry.runtime_data.async_subscribe())

    # The room map seeds a child device's area, and the platforms below
    # create those devices, so the fetch precedes them. It runs here
    # rather than inside the tree so that nothing between the connect and
    # the subscriptions above awaits.
    await entry.runtime_data.async_refresh_rooms()

    # The sweep fills each module's capability map and each object's
    # record bundle. Setup waits for it: the capability map decides which
    # modules carry a buzzer, and the platforms load below. The sweep is
    # served to the administrator login alone, and the narrowed reference
    # is what says so. A batch that adds a module device sweeps again.
    if (admin := entry.runtime_data.admin) is not None:
        await _async_sweep_records(admin)

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
    # The platforms have run, so a catalogue one of them fetches has
    # either landed or left itself unknown, which is what the scene
    # platform does when it defers. The report accounts for every record
    # the catalogues it holds explain and offers the user the rest. From
    # here on, a read that changes what can be accounted for raises it
    # again.
    entry.runtime_data.async_mark_ready(
        partial(async_report_stale_records, hass, entry)
    )
    # The report reads what the door refuses, which async_report_not_configured
    # records before any platform loads, so a row the door refused is named by
    # the installer repair and kept out of the offer, and no two repairs ask
    # for opposite things about one record.
    async_report_stale_records(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Unload a config entry.

    The batches stop first, so that none adds an entity to a platform
    that has unloaded, and one that is removing an entity finishes that. The client stays connected until the on-unload
    callbacks run, after the platforms.
    """
    await entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """The records go with the entry, so no repair has anything left to fix.

    Home Assistant leaves an integration's issues standing when its entry
    goes, so every issue id the integration raises is named here.
    """
    ir.async_delete_issue(hass, DOMAIN, ADMIN_ONLY_RECORDS_ISSUE)
    ir.async_delete_issue(hass, DOMAIN, STALE_RECORDS_ISSUE)
    ir.async_delete_issue(hass, DOMAIN, NOT_CONFIGURED_ISSUE)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AmpioConfigEntry, device_entry: dr.AnyDeviceEntry
) -> bool:
    """Allow removing a device whose object the account no longer receives.

    The child of an object that now resolves to another parent goes too.
    The hub always stays. A module device stays while the account still
    receives an object on it, and an object's child device stays while the
    account still receives that object and the child sits under the parent
    the object resolves to. The batch the hook queues builds it again under
    the resolved parent within seconds, with its id, its area, and its name
    restored. A module device the hook permits to delete drops out of the
    tree, so that the next object on its mac builds it back the same way,
    and every object still parented to that device is queued first, so
    that the child the registry takes down with it comes back too.
    """
    data = entry.runtime_data
    live, expected_parent = data.live_identifiers()
    if isinstance(device_entry, dr.ChildDeviceEntry):
        # The registry cannot move a child, so a child whose object now
        # resolves elsewhere is deletable: the delete is the move, and the
        # batch queued here builds the child again under the new parent.
        for identifier in device_entry.identifiers:
            if (
                identifier in expected_parent
                and expected_parent[identifier] != device_entry.parent_device_id
            ):
                for obj in data.client.objects.values():
                    if (DOMAIN, obj.object_key) == identifier:
                        data.async_request_reconcile(obj)
                        break
                return True
    if any(identifier in live for identifier in device_entry.identifiers):
        return False
    # A module device takes every child still parented to it down with it,
    # and the registry never asks the hook about those children on the
    # way, so each one is queued here before the delete this permits
    # carries its device off. An object device has no children of its own,
    # so this is a no-op when device_entry is one.
    device_registry = dr.async_get(hass)
    for child in dr.async_entries_for_parent_device(device_registry, device_entry.id):
        for identifier in child.identifiers:
            for obj in data.client.objects.values():
                if (DOMAIN, obj.object_key) == identifier:
                    data.async_request_reconcile(obj)
                    break
    # Home Assistant removes the device right after this returns. The tree
    # forgets a module device with it, so that the next batch on that mac
    # builds the device back through the path that built it the first
    # time. A child's device id matches no module device, so forgetting it
    # changes nothing.
    data.forget_module_device(device_entry.id)
    return True
