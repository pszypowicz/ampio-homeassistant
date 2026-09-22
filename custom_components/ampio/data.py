"""Runtime data for the Ampio integration: the device tree the catalogue defines."""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import logging
from typing import Final

from ampio_mqtt import (
    AmpioAdminClient,
    AmpioClient,
    AmpioConnectionError,
    AmpioModule,
    AmpioNotConfigured,
    AmpioObject,
    AmpioServerInfo,
    NotConfigured,
    ObjectRemoved,
    ObjectUpdated,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    EntityPlatform,
    async_get_current_platform,
)

from .const import DOMAIN, MODULE_KEY_STEM

_LOGGER = logging.getLogger(__name__)

# The registry identifiers carry no server mac. Object ids live in the
# Designer database, which moves to new hardware with the project; the
# server mac does not. One M-SERV per Home Assistant keeps them unique.
HUB_IDENTIFIER: Final = (DOMAIN, "hub")

# The M-SERV pushes the catalogue and the params table as separate messages
# within one second of a Designer save, so a batch waits this long for both.
RECONCILE_COOLDOWN: Final = 1.0

type Fingerprint = tuple[str, int, int | None, int, int, int, int, int]


def fingerprint(obj: AmpioObject) -> Fingerprint:
    """The catalogue fields that decide an object's platforms and its parent.

    A state push changes none of them, and neither does a rename, because
    no name composes an id. Three of the address's four fields are in, so
    a leaf edit in Designer queues a batch: the mac decides the parent
    device, and the sub-function decides how an alarm row is classified,
    with the function class beside it because the two read together. The
    fourth, ``address.channel``, stays out: it routes the object's raw
    channel events inside the library, and no platform choice and no
    parent reads it.
    """
    return (
        obj.typ_komponentu,
        obj.interpretacja,
        obj.matter_device_type,
        obj.params,
        obj.czas,
        obj.address.mac,
        obj.address.sf_id,
        obj.address.sub_sf_id,
    )


def _built_entities(platform: EntityPlatform, object_key: str) -> dict[str, Entity]:
    """The entities a platform holds for an object, keyed by unique id.

    The unique id is the object key, or the object key and a suffix such as
    ``_pulse``. The platform's own table is the truth for what is built, so
    nothing shadows it.
    """
    prefix = f"{object_key}_"
    return {
        uid: entity
        for entity in platform.entities.values()
        if (uid := entity.unique_id) is not None
        and (uid == object_key or uid.startswith(prefix))
    }


def module_identifier(mac: int) -> tuple[str, str]:
    """The registry identifier of the module device on override mac ``mac``.

    The prefix names what the number is. A Designer row id and an override
    mac are both small integers, so a bare ``module:5`` says nothing about
    which of the two it holds, and a device record an older release wrote
    under that bare key would read as a match for whichever number happened
    to collide.
    """
    return (DOMAIN, f"{MODULE_KEY_STEM}:{mac}")


# What a platform builds for one object: the entities, or nothing. The
# platform module owns the partition; the runtime data owns when it runs.
type EntityFactory = Callable[[AmpioData, AmpioObject], Iterable[Entity]]


@dataclass(frozen=True)
class _PlatformRegistration:
    """One platform's factory, and the platform that holds what it built."""

    platform: EntityPlatform
    factory: EntityFactory


# What a platform builds for one module device: the entities, or nothing.
# The int is the override mac that keys ``module_device_ids``. The admin
# client is passed in, so a factory body needs no tier guard and cannot be
# reached off-tier.
type AdminModuleFactory = Callable[[AmpioData, AmpioAdminClient, int], Iterable[Entity]]


@dataclass(frozen=True)
class _ModulePlatformRegistration:
    """One platform's module factory, and the platform that holds what it built."""

    platform: EntityPlatform
    factory: AdminModuleFactory


@dataclass(frozen=True)
class RefusedRows:
    """The Designer rows the library's admission door refused, in ids alone.

    The ``AmpioNotConfigured`` setup catches and the ``NotConfigured``
    event are both projected into this, and neither is kept. Both carry
    the Designer name of every row they report, and docs/debugging.md
    promises that no object, room or module name leaves the install, so
    the projection drops the names and nothing downstream holds one to put
    in an issue, a log line or a translation placeholder.

    The field names are the library's own. ``objects`` is the row ids with
    no Designer leaf, and ``collisions`` pairs each override mac more than
    one module row carries with the ids of those rows.
    """

    objects: tuple[int, ...]
    collisions: tuple[tuple[int, tuple[int, ...]], ...]

    @classmethod
    def from_error(cls, err: AmpioNotConfigured) -> RefusedRows:
        """Keep the ids the door refused and let the names go."""
        return cls(tuple(oid for oid, _ in err.objects), err.collisions)

    @classmethod
    def from_event(cls, event: NotConfigured) -> RefusedRows | None:
        """The ids the door refuses now, or None when it refuses nothing.

        Every event carries both sides of the door's answer, so two empty
        sides are the recovery signal and read here as no refusal at all.
        """
        if not event.objects and not event.collisions:
            return None
        return cls(tuple(oid for oid, _ in event.objects), event.collisions)


class AmpioData:
    """Runtime data for one Ampio server: the device tree the catalogue defines.

    ``async_create`` builds the hub, one module device per override mac the
    objects carry, and the room map. ``ensure_module_device`` creates the
    module device of a mac the tree meets later, through the same path, and
    ``forget_module_device`` drops one the user deleted.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AmpioConfigEntry,
        client: AmpioClient,
        hub_device_id: str,
    ) -> None:
        """Hold the tree's fixed points; the module devices and rooms fill in after."""
        self.hass = hass
        self.entry = entry
        self.client = client
        # The tier is the client's class, fixed at construction. Holding
        # the narrowed reference means an admin-only read is a type error
        # off-tier rather than a convention every call site must honor.
        self.admin: AmpioAdminClient | None = (
            client if isinstance(client, AmpioAdminClient) else None
        )
        # The rows the library's admission door refuses, so that the
        # repair can name them. Setup writes what it caught at connect,
        # and the ``NotConfigured`` event keeps it current: the library
        # reports every change of either side, a change to empty included.
        #
        # ``RefusedRows`` holds ids and nothing else, which is what keeps
        # the Designer names of those rows inside the library. That also
        # rules out ``diagnostics_snapshot()["not_configured"]``, which
        # carries the names too.
        self.not_configured: RefusedRows | None = None
        # Registry ids the object child devices parent to: the hub, and one
        # module device per override mac.
        self.hub_device_id = hub_device_id
        self.module_device_ids: dict[int, str] = {}
        # The app room of each object, from the tier-shared room tables. It
        # seeds a child device's area once, at the device's first creation.
        self.rooms: dict[int, str] = {}
        self._platforms: list[_PlatformRegistration] = []
        self._module_platforms: list[_ModulePlatformRegistration] = []
        # One fingerprint per object in the catalogue, so that a state push
        # from any object costs one dictionary lookup.
        self._fingerprints: dict[int, Fingerprint] = {}
        # The object keys queued for the next batch, by object id.
        self._pending: dict[int, str] = {}
        self._reconcile_lock = asyncio.Lock()
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=RECONCILE_COOLDOWN,
            immediate=False,
            function=self._async_schedule_reconcile,
            background=True,
        )
        # The stale-record report, handed over once every platform loaded.
        # A batch that runs while a platform is still loading would report
        # every entity that platform has not built yet, so nothing reports
        # before setup says so.
        self._report: Callable[[], None] | None = None

    @classmethod
    async def async_create(
        cls,
        hass: HomeAssistant,
        entry: AmpioConfigEntry,
        client: AmpioClient,
        info: AmpioServerInfo,
    ) -> AmpioData:
        """Build the tree for the catalogue as it stands after discovery."""
        # The hub is built from the server-info reply both account tiers
        # receive. Its name is the product name, because one M-SERV runs one
        # install and its catalogue row names it no better. The row
        # decorates the model.
        device_registry = dr.async_get(hass)
        # The module catalogue answers the administrator login alone, so the
        # read itself is gated rather than its result.
        admin = client if isinstance(client, AmpioAdminClient) else None
        mserv = admin.mserv if admin else None
        hub = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={HUB_IDENTIFIER},
            manufacturer="Ampio",
            name="M-SERV",
            model=mserv.model if mserv and mserv.model else "M-SERV",
            sw_version=info.server_version,
            serial_number=info.device_id,
            configuration_url=f"http://{info.local_ip}" if info.local_ip else None,
        )

        data = cls(hass, entry, client, hub.id)

        # One device per module the catalogue names, registered before the
        # platforms load. The first object on a mac stands for its module;
        # every object carries the mac on both tiers, so any of them will
        # do, and the M-SERV's own objects reach the hub instead through
        # ``is_server_owned``.
        for obj in client.objects.values():
            data.ensure_module_device(obj)

        # The room map seeds each object child's area at its first creation,
        # and the diagnostics download carries it. Nothing in the entity or
        # device path depends on it after that, so a failure costs the seed
        # and must not fail setup.
        try:
            data.rooms = await client.fetch_rooms()
        except AmpioConnectionError:
            _LOGGER.warning(
                "Could not fetch the Ampio room map; the devices get no area suggestion"
            )
        return data

    @callback
    def module_row_for(self, mac: int) -> AmpioModule | None:
        """The module catalogue row on an override mac, or None.

        None covers two cases that need the same answer. The account is not
        served the module catalogue, or no admitted row carries that mac.
        Ampio Designer deletes a device at once and only unassigns its
        objects, so a live object can name a mac the list no longer holds.

        The mac names at most one admitted row, because the library's door
        refuses two module rows that share one override mac.
        """
        if (admin := self.admin) is None:
            return None
        return next(
            (module for module in admin.modules.values() if module.mac == mac), None
        )

    def _module_name(self, mac: int, module: AmpioModule | None) -> str:
        """The installer's name when the catalogue has one, else the mac.

        ``nazwa_urzadzenia`` comes from the module catalogue, which answers
        the administrator login alone, so this name follows the account
        tier.

        Home Assistant composes an entity id once, at first registration,
        from the device name as it stands then. A mac the catalogue has no
        row for keeps this fallback on both tiers, so its module entities
        compose from ``Ampio module <mac>``. A row served later renames the
        device and leaves the registered ids where they are.

        The row is passed in rather than looked up, because the caller
        holds it to decorate the device with.
        """
        if module is not None and module.nazwa_urzadzenia:
            return module.nazwa_urzadzenia
        return f"Ampio module {mac}"

    @callback
    def ensure_module_device(self, obj: AmpioObject) -> int | None:
        """Create the module device of the object's module, unless it exists.

        Returns the override mac when this call put the module into the
        tree, which is when the module platforms owe it their entities, and
        None when the object hangs on the hub or its module already has a
        device.

        The override mac rides every object's address on both account
        tiers, and Ampio Designer re-stamps it onto a replacement unit, so
        the tree holds still across a tier change and across a module swap.
        The admin catalogue names the module and decorates the model, the
        versions, and the serial; a standard account gets the mac in the
        name and no decoration. None of those reaches an entity id.

        """
        mac = obj.address.mac
        if obj.is_server_owned or mac in self.module_device_ids:
            return None
        module = self.module_row_for(mac)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={module_identifier(mac)},
            name=self._module_name(mac, module),
            manufacturer="Ampio",
            via_device_id=self.hub_device_id,
            model=module.model if module else None,
            sw_version=str(module.wersja_softu) if module else None,
            hw_version=str(module.wersja_pcb) if module else None,
            serial_number=str(module.mac_global) if module else None,
        )
        self.module_device_ids[mac] = device.id
        return mac

    @callback
    def forget_module_device(self, device_id: str) -> None:
        """Drop a module device from the tree, so that the next object builds it again.

        The removal hook calls it when it permits the delete of a module
        device. The registry keeps the deleted record, so the next object
        on the mac gets the device back through ``ensure_module_device``,
        with its id, its user name, and its area.
        """
        self.module_device_ids = {
            mac: known
            for mac, known in self.module_device_ids.items()
            if known != device_id
        }

    @callback
    def async_add_platform(
        self, factory: EntityFactory, async_add_entities: AddConfigEntryEntitiesCallback
    ) -> None:
        """Register a platform, and build its entities for the catalogue as it stands.

        Valid inside the platform's ``async_setup_entry`` alone, where Home
        Assistant sets the current platform.
        """
        registration = _PlatformRegistration(async_get_current_platform(), factory)
        self._platforms.append(registration)
        entities: list[Entity] = []
        for obj in self.client.objects.values():
            entities.extend(self._expected_entities(registration, obj).values())
        async_add_entities(entities)

    @callback
    def async_add_admin_module_platform(
        self,
        factory: AdminModuleFactory,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        """Register a module factory, and build its entities for every module device.

        Valid inside the platform's ``async_setup_entry`` alone. Every module
        entity reaches a surface the M-SERV serves the administrator login
        alone, so a standard account is given none of them: an entity that can
        never do its job is worse than no entity. The tier is narrowed here,
        once, and the factory receives the admin client as an argument.

        A factory gets a mac, not a catalogue row. A module device exists
        for every mac an object names, and Ampio Designer deletes a device
        before it unassigns that device's objects, so a factory can be
        handed a mac whose ``module_row_for`` reads None and must build
        something sensible for it, or nothing.

        A standard account records the registration all the same, even
        though nothing can run it: the record says which platforms own
        module entities, and the gate stays in one place for the setup path
        and the reconcile batch alike.
        """
        self._module_platforms.append(
            _ModulePlatformRegistration(async_get_current_platform(), factory)
        )
        entities: list[Entity] = []
        if (admin := self.admin) is not None:
            for mac in self.module_device_ids:
                entities.extend(factory(self, admin, mac))
        async_add_entities(entities)

    def withheld_unique_ids(self) -> set[str]:
        """The unique ids the administrator rule withholds from this account.

        Empty on the administrator login. Every module entity reaches an
        administrator-only surface, so on a standard account no factory
        runs and none can, and the records those factories would have
        built are recognized by their unique id alone.

        The convention is the contract: a module entity's unique id reads
        ``module_mac_<mac>_<suffix>``, built from ``MODULE_KEY_STEM``, which
        also builds the module device's identifier. A record that does not
        carry the stem was minted before it and reads as stale, which is
        what it is.
        """
        if self.admin is not None:
            return set()
        entity_registry = er.async_get(self.hass)
        prefix = f"{MODULE_KEY_STEM}_"
        return {
            record.unique_id
            for record in er.async_entries_for_config_entry(
                entity_registry, self.entry.entry_id
            )
            if record.unique_id.startswith(prefix)
        }

    def _expected_entities(
        self, registration: _PlatformRegistration, obj: AmpioObject | None
    ) -> dict[str, Entity]:
        """The entities a platform builds for an object, keyed by unique id.

        Nothing for an object that left the catalogue, and nothing for an
        object whose child device hangs under a parent it has outgrown.
        Home Assistant cannot re-parent a child, so the delete of that
        device is the move, and the repair offers it.
        """
        if obj is None:
            return {}
        entities = {
            uid: entity
            for entity in registration.factory(self, obj)
            if (uid := entity.unique_id) is not None
        }
        if entities and self._misparented(obj):
            _LOGGER.warning(
                "The device of Ampio object %s hangs under a different module "
                "than the object resolves to now, so its entities are held "
                "back. Home Assistant cannot re-parent a device, so use the "
                "repair on the Settings page, or delete the device, and the "
                "entities come back under the module the object resolves to",
                obj.id,
            )
            return {}
        return entities

    @callback
    def _misparented(self, obj: AmpioObject) -> bool:
        """Whether the object's child device hangs under a parent it has outgrown."""
        child = dr.async_get(self.hass).async_get_child_device_by_identifier(
            (DOMAIN, obj.object_key), self.entry.entry_id
        )
        return child is not None and child.parent_device_id != self.parent_for(obj)

    @callback
    def async_subscribe(self) -> Callable[[], None]:
        """Watch the catalogue, and return the callable that stops watching."""
        for obj in self.client.objects.values():
            self._fingerprints[obj.id] = fingerprint(obj)
        unsubscribe_objects = self.client.subscribe(
            self._catalogue_event, of=(ObjectUpdated, ObjectRemoved)
        )

        @callback
        def _stop() -> None:
            unsubscribe_objects()
            self._debouncer.async_shutdown()

        return _stop

    @callback
    def async_mark_ready(self, report: Callable[[], None]) -> None:
        """Let every batch from now on end by calling ``report``.

        A batch that runs while a platform is still loading would report
        every entity that platform has not built yet, so the handover is
        what says the platforms are done.
        """
        self._report = report

    @callback
    def _catalogue_event(self, event: ObjectUpdated | ObjectRemoved) -> None:
        """Queue an object whose catalogue row changed, and let a state push pass."""
        obj = event.object
        if isinstance(event, ObjectUpdated) and self._fingerprints.get(
            obj.id
        ) == fingerprint(obj):
            return
        self.async_request_reconcile(obj)

    @callback
    def async_request_reconcile(self, obj: AmpioObject) -> None:
        """Queue an object for the next batch, as a catalogue change would.

        The removal hook calls it for a moved object's child: the delete is
        the move, and the batch that follows builds the child again under
        the parent the object resolves to now.
        """
        self._pending[obj.id] = obj.object_key
        self._debouncer.async_schedule_call()

    @callback
    def _async_schedule_reconcile(self) -> None:
        """Run the batch as an entry task, so that an unload cancels it."""
        self.entry.async_create_background_task(
            self.hass, self._async_reconcile(), "ampio_reconcile"
        )

    async def _async_reconcile(self) -> None:
        """Bring every pending object's entities in line with the catalogue.

        One rule, expected versus built, covers a new object, a deletion, a
        hide, an un-hide, a re-tag, a pulse time, and a move.
        """
        async with self._reconcile_lock:
            pending, self._pending = self._pending, {}
            if not pending:
                return
            buildable: list[AmpioObject] = []
            for oid in pending:
                obj = self.client.objects.get(oid)
                if obj is None:
                    self._fingerprints.pop(oid, None)
                    continue
                self._fingerprints[oid] = fingerprint(obj)
                if not self._misparented(obj):
                    buildable.append(obj)
            # An entity reads the room map and the module device when it is
            # built, so both precede the factories.
            new_macs: list[int] = []
            if buildable:
                await self._async_refresh_rooms()
                new_macs.extend(
                    mac
                    for obj in buildable
                    if (mac := self.ensure_module_device(obj)) is not None
                )
            for registration in self._platforms:
                to_add: list[Entity] = []
                to_remove: list[Entity] = []
                for oid, object_key in pending.items():
                    built = _built_entities(registration.platform, object_key)
                    expected = self._expected_entities(
                        registration, self.client.objects.get(oid)
                    )
                    to_add.extend(
                        entity for uid, entity in expected.items() if uid not in built
                    )
                    to_remove.extend(
                        entity for uid, entity in built.items() if uid not in expected
                    )
                for entity in to_remove:
                    # The registry record stays, so the stale repair lists it
                    # and a re-add restores the id.
                    await entity.async_remove()
                if to_add:
                    # Awaited, so that the platform's table holds the entities
                    # before the batch ends.
                    await registration.platform.async_add_entities(to_add)
            # A module device the batch created owes the module platforms
            # their entities, awaited for the same reason as the objects.
            # Every module factory is administrator-only, so a standard
            # session has no client to run one with.
            if (admin := self.admin) is not None:
                for module_registration in self._module_platforms:
                    module_entities = [
                        entity
                        for mac in new_macs
                        for entity in module_registration.factory(self, admin, mac)
                    ]
                    if module_entities:
                        await module_registration.platform.async_add_entities(
                            module_entities
                        )
            if self._report is not None:
                self._report()

    async def _async_refresh_rooms(self) -> None:
        """Re-read the room map, so that a new child takes its app room."""
        try:
            self.rooms = await self.client.fetch_rooms()
        except AmpioConnectionError:
            _LOGGER.warning(
                "Could not fetch the Ampio room map; the new devices get no area suggestion"
            )

    def parent_for(self, obj: AmpioObject) -> str:
        """The device an object's child hangs under: its module, or the hub.

        The owning module's override mac rides every object's address on
        both account tiers, so the tree never depends on the module
        catalogue. The M-SERV's own objects sit on the hub.
        """
        if obj.is_server_owned:
            return self.hub_device_id
        return self.module_device_ids.get(obj.address.mac, self.hub_device_id)

    def live_identifiers(
        self,
    ) -> tuple[set[tuple[str, str]], dict[tuple[str, str], str]]:
        """The device identifiers the catalogue keeps, and each child's parent.

        The hub is always live. A module device is live while an object
        resolves to it, and an object's child is live while the catalogue
        carries the object. The parent map says where each child belongs
        now, which is what a moved object's stuck child is compared against.
        """
        live: set[tuple[str, str]] = {HUB_IDENTIFIER}
        expected_parent: dict[tuple[str, str], str] = {}
        for obj in self.client.objects.values():
            parent = self.parent_for(obj)
            if parent != self.hub_device_id:
                live.add(module_identifier(obj.address.mac))
            live.add((DOMAIN, obj.object_key))
            expected_parent[(DOMAIN, obj.object_key)] = parent
        return live, expected_parent


type AmpioConfigEntry = ConfigEntry[AmpioData]
