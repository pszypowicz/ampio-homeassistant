"""Runtime data for the Ampio integration: the device tree the catalogue defines."""

import asyncio
from collections.abc import Callable, Coroutine, Iterable
from contextlib import suppress
from dataclasses import dataclass
import logging
from typing import Any, Final

from ampio_mqtt import (
    AmpioAdminClient,
    AmpioClient,
    AmpioConnectionError,
    AmpioModule,
    AmpioNotConfigured,
    AmpioObject,
    AmpioServerInfo,
    AmpioTimeoutError,
    NotConfigured,
    ObjectRemoved,
    ObjectUpdated,
    format_mac,
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

from .const import DOMAIN, MODULE_KEY_PREFIX, MODULE_KEY_STEM

_LOGGER = logging.getLogger(__name__)

# The registry identifiers carry no server mac. Object ids live in the
# Designer database, which moves to new hardware with the project; the
# server mac does not. One M-SERV per Home Assistant keeps them unique.
HUB_IDENTIFIER: Final = (DOMAIN, "hub")

# The M-SERV pushes the catalogue and the params table as separate messages
# within one second of a Designer save, so a batch waits this long for both.
# The stale-record report rides the end of the batch, so the same wait keeps
# it off a devices table read against the params table from before the save.
RECONCILE_COOLDOWN: Final = 1.0

type Fingerprint = tuple[str, int, int | None, int, int, int, int, int]


def fingerprint(obj: AmpioObject) -> Fingerprint:
    """The catalogue fields that decide an object's platforms and its parent.

    A state push changes none of them, and neither does a rename: a
    name-only catalogue change needs no rebuild, because the device
    registry picks the new name up on its own, at the next setup or
    reload of the entry, or the moment a new entity for the object is
    added. Three of the address's four fields are in, so a leaf edit in
    Designer queues a batch: the mac decides the parent device, and the
    sub-function decides how an alarm row is classified, with the
    function class beside it because the two read together. The fourth,
    ``address.channel``, stays out: it routes the object's raw channel
    events inside the library, and no platform choice and no parent
    reads it.
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


def _built_module_entities(platform: EntityPlatform, mac: int) -> dict[str, Entity]:
    """The entities a platform holds for one module device, keyed by unique id.

    A module entity's unique id is the prefix, the mac, and what the entity
    does on the module, so the mac with its separator is what tells one
    module's entities from another's. The platform's own table is the truth
    for what is built, as it is for the objects.
    """
    prefix = f"{MODULE_KEY_PREFIX}{format_mac(mac)}_"
    return {
        uid: entity
        for entity in platform.entities.values()
        if (uid := entity.unique_id) is not None and uid.startswith(prefix)
    }


def module_identifier(mac: int) -> tuple[str, str]:
    """The registry identifier of the module device on override mac ``mac``.

    The prefix names what the number is. A Designer row id and an override
    mac are both small integers, so a bare ``module:5`` says nothing about
    which of the two it holds, and a device record an older release wrote
    under that bare key would read as a match for whichever number happened
    to collide.
    """
    return (DOMAIN, f"{MODULE_KEY_STEM}:{format_mac(mac)}")


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
    the Designer name of every object row they report in ``objects``;
    ``collisions`` is an override mac and Designer row ids, with no name
    anywhere in it. docs/debugging.md promises that no object, room or
    module name leaves the install, so the projection drops the object
    names and nothing downstream holds one to put in an issue, a log
    line or a translation placeholder.

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

    ``create`` builds the hub and one module device per override mac the
    objects carry. ``ensure_module_device`` creates the module device of a
    mac the tree meets later, through the same path, and
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
        # the Designer names of those rows inside the library.
        self.not_configured: RefusedRows | None = None
        # Registry ids the object child devices parent to: the hub, and one
        # module device per override mac.
        self.hub_device_id = hub_device_id
        self.module_device_ids: dict[int, str] = {}
        # The app room of each object, from the tier-shared room tables. It
        # seeds a child device's area once, at the device's first creation.
        # Setup fills it before the platforms load, and every batch that
        # builds a child refreshes it first.
        self.rooms: dict[int, str] = {}
        # Every scene id the last fetch returned, or None while no fetch
        # has landed on this setup. Scenes are fetched rather than pushed,
        # and a failed fetch defers the scene platform, which leaves the
        # catalogue unknown rather than empty. The stale-record report
        # speaks for a scene record only while this holds a catalogue.
        # The inactive rows are in, because the platform builds no entity
        # for one and the app switches a scene back on without the server
        # ever having stopped serving the row.
        self.scene_ids: frozenset[int] | None = None
        self._platforms: list[_PlatformRegistration] = []
        self._module_platforms: list[_ModulePlatformRegistration] = []
        # One fingerprint per object in the catalogue, so that a state push
        # from any object costs one dictionary lookup.
        self._fingerprints: dict[int, Fingerprint] = {}
        # The object keys queued for the next batch, by object id.
        self._pending: dict[int, str] = {}
        # The objects whose misparent warning is logged, so that a batch
        # does not repeat it while the child stays where it is.
        self._warned_misparented: set[int] = set()
        self._reconcile_lock = asyncio.Lock()
        # The batch tasks that have not finished, so that an unload can
        # wait for them before the platforms go. A batch is never
        # canceled, because a cancel inside an entity removal leaves the
        # entity half removed. The waits a batch may abandon run as tasks
        # of their own, which ``async_shutdown`` cancels, and the batch
        # returns once it sees ``_stopping``.
        self._reconcile_tasks: set[asyncio.Task[None]] = set()
        self._abandonable: set[asyncio.Task[None]] = set()
        self._stopping = False
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=RECONCILE_COOLDOWN,
            immediate=False,
            function=self._async_schedule_reconcile,
            background=True,
        )
        # The stale-record report, handed over once every platform has
        # loaded. Setup raises the card itself at the end of that pass, so
        # anything that would report before the handover leaves the first
        # card to setup rather than raising and clearing one midway.
        self._report: Callable[[], None] | None = None

    @classmethod
    def create(
        cls,
        hass: HomeAssistant,
        entry: AmpioConfigEntry,
        client: AmpioClient,
        info: AmpioServerInfo,
    ) -> AmpioData:
        """Build the tree for the catalogue as it stands after discovery.

        Not a coroutine, and it must stay that way. Setup subscribes to
        the library's admission door as soon as this returns, and the
        library dispatches a refusal on the event loop, so an await here
        would hand the loop back while no subscription exists yet and a
        row refused in that window would go unreported. Keeping the
        signature synchronous makes an await inside a syntax error rather
        than a silent gap; the room map, the one thing the tree wants from
        the server, is fetched by ``async_refresh_rooms`` after the
        subscription stands.
        """
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
        compose from ``Ampio module <mac>``. ``ensure_module_device``
        returns early once the mac is already in the tree, so a row served
        later renames the device only the next time setup or a reload
        builds it fresh, and the registered ids stay where they are.

        The row is passed in rather than looked up, because the caller
        holds it to decorate the device with.
        """
        if module is not None and module.nazwa_urzadzenia:
            return module.nazwa_urzadzenia
        return f"Ampio module {format_mac(mac)}"

    @callback
    def ensure_module_device(self, obj: AmpioObject) -> int | None:
        """Create the module device of the object's module, unless it exists.

        Returns the override mac when this call put the module into the
        tree, and None when the object hangs on the hub or its module
        already has a device.

        The override mac rides every object's address on both account
        tiers, and Ampio Designer re-stamps it onto a replacement unit, so
        the tree holds still across a tier change and across a module swap.
        The admin catalogue names the module and decorates the model, the
        versions, and the serial; a standard account gets the mac in the
        name and no decoration. The device name reaches the ids of the
        entities on it, at first registration; the model, the versions, and
        the serial do not.
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

        Both callers answer for a record that is going. The removal hook
        permits a delete from the device page, which Home Assistant
        performs as soon as the hook returns, and the repair has just
        taken one itself. The registry keeps the deleted record, so the
        next object on the mac gets the device back through
        ``ensure_module_device``, with its id, its user name, and its area.

        Nothing else drops a mac. A tree that forgot one whose record still
        stands would resolve an object on that mac to the hub, while its
        child device kept hanging under the module.
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

        The convention is the contract. A module entity's unique id reads
        ``module_mac_<mac>_<suffix>``, built from ``MODULE_KEY_PREFIX``,
        whose stem also builds the module device's identifier. A module
        record an older release minted under another key carries neither,
        so this leaves it alone and the stale report names it as a shape
        nothing mints.
        """
        if self.admin is not None:
            return set()
        entity_registry = er.async_get(self.hass)
        return {
            record.unique_id
            for record in er.async_entries_for_config_entry(
                entity_registry, self.entry.entry_id
            )
            if record.unique_id.startswith(MODULE_KEY_PREFIX)
        }

    def _expected_entities(
        self, registration: _PlatformRegistration, obj: AmpioObject | None
    ) -> dict[str, Entity]:
        """The entities a platform builds for an object, keyed by unique id.

        Nothing for an object that left the catalogue. An object whose
        child device hangs under a parent it has outgrown keeps its
        entities, which register under the parent the child already has,
        and a warning names the object once while the child stays there.
        """
        if obj is None:
            return {}
        entities = {
            uid: entity
            for entity in registration.factory(self, obj)
            if (uid := entity.unique_id) is not None
        }
        if not entities:
            return entities
        if not self._misparented(obj):
            self._warned_misparented.discard(obj.id)
        elif obj.id not in self._warned_misparented:
            self._warned_misparented.add(obj.id)
            _LOGGER.warning(
                "The device of Ampio object %s hangs under a different module "
                "than the object resolves to now. Its entities keep working "
                "under that module. Home Assistant cannot re-parent a device, "
                "so use the repair on the Settings page, or delete the device, "
                "and the device comes back under the module the object "
                "resolves to",
                obj.id,
            )
        return entities

    def _expected_module_entities(
        self, factories: list[AdminModuleFactory], mac: int, live: set[int]
    ) -> dict[str, Entity]:
        """The entities one platform's module factories build for an override mac.

        Every factory on the platform at once, because what is built is
        read off the platform and two factories can share one. The
        identify button and the touch-unlock button are both on the button
        platform, and the backlight and the status light are both on the
        light platform.

        Nothing for a mac no object this account receives names, because a
        module device answers to the objects on both tiers, and a mac with
        none of them left is one the catalogue no longer accounts for.
        Nothing either on an account that is served no module surface,
        where no module factory runs at all.
        """
        if (admin := self.admin) is None or mac not in live:
            return {}
        return {
            uid: entity
            for factory in factories
            for entity in factory(self, admin, mac)
            if (uid := entity.unique_id) is not None
        }

    @callback
    def registered_parent_for(self, obj: AmpioObject) -> str:
        """The device an object's entities register under.

        The parent the object's child device already hangs under, while
        that child is registered, and ``parent_for`` otherwise. The
        registry refuses to re-parent a child, and an entity registered
        under any other parent would not be added, so a misparented
        object's entities stay under the child's old parent until someone
        deletes that device.
        """
        child = dr.async_get(self.hass).async_get_child_device_by_identifier(
            (DOMAIN, obj.object_key), self.entry.entry_id
        )
        if child is not None:
            return child.parent_device_id
        return self.parent_for(obj)

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
        """Let the batches and the catalogue reads from now on report.

        Setup hands the report over once the platforms have loaded and
        then raises the card itself, so the handover is what says the
        setup pass owns the first one.
        """
        self._report = report

    @callback
    def async_report_records(self) -> None:
        """Report the leftover records, once setup has handed the report over.

        A catalogue this account is served reaches the report through
        here, so that a read which changes what the report can account for
        refreshes the card on its own. The scene platform calls it when a
        fetch lands, and every batch calls it at its end.
        """
        if self._report is not None:
            self._report()

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
    def async_request_pass(self) -> None:
        """Queue a batch that no object change carried.

        A row that was an object leaves the catalogue as an object event,
        whether Ampio Designer deleted it or the door refused it, so a
        refusal that lands while the entry runs already queues a batch.
        A row the door refused at connect never became an object, and
        deleting that row in Designer gives the library nothing to evict,
        so the door's own change is the only signal it carries. This is
        the batch that follows it. The batch brings the module entities in
        line with the macs the catalogue names, and reports at its end.

        Nothing is queued before setup hands the report over, because the
        setup pass builds the tree from the catalogue it has just read.
        """
        if self._report is not None:
            self._debouncer.async_schedule_call()

    @callback
    def _async_schedule_reconcile(self) -> None:
        """Run the batch as an entry task, and keep it for ``async_shutdown``."""
        task = self.entry.async_create_background_task(
            self.hass, self._async_reconcile(), "ampio_reconcile"
        )
        self._reconcile_tasks.add(task)
        task.add_done_callback(self._reconcile_tasks.discard)

    async def async_shutdown(self) -> None:
        """Stop the batches, and wait until none is running.

        The unload calls this before the platforms unload. Home Assistant
        cancels an entry's background tasks only after the platforms have
        unloaded, and a batch that went on in that gap would add entities
        to platforms that are gone. The sweep and the room fetch are cut
        short, and whatever a batch is removing or adding at that moment
        finishes first.
        """
        self._stopping = True
        self._debouncer.async_shutdown()
        for wait in self._abandonable:
            wait.cancel()
        await asyncio.gather(*self._reconcile_tasks, return_exceptions=True)

    async def _async_abandonable(self, coro: Coroutine[Any, Any, None]) -> None:
        """Await a wait that ``async_shutdown`` may cancel, without canceling the batch.

        The caller checks ``_stopping`` afterwards, because a canceled
        wait returns here like a finished one.
        """
        task = self.hass.async_create_task(coro, "ampio_reconcile_wait")
        self._abandonable.add(task)
        try:
            await asyncio.wait((task,))
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            self._abandonable.discard(task)
        if not task.cancelled():
            task.result()

    async def _async_reconcile(self) -> None:
        """Bring the entities in line with the catalogue, and report.

        One rule, expected versus built, covers a new object, a deletion,
        a hide, an un-hide, a re-tag, a pulse time, and a move, and the
        module entities answer to it per override mac.

        A batch with nothing pending still has work, because a row the door
        refused at connect never became an object, and deleting it in
        Ampio Designer leaves no object event to queue. So the module pass
        and the report run whether or not an object is queued.
        """
        async with self._reconcile_lock:
            if self._stopping:
                return
            pending, self._pending = self._pending, {}
            buildable = False
            new_macs: list[int] = []
            for oid in pending:
                obj = self.client.objects.get(oid)
                if obj is None:
                    self._fingerprints.pop(oid, None)
                    self._warned_misparented.discard(oid)
                    continue
                self._fingerprints[oid] = fingerprint(obj)
                # The module device goes into the tree before the entities
                # are built, because the build compares the child's parent
                # with ``parent_for``. A mac the tree does not hold resolves
                # to the hub, so a child whose record still hangs under that
                # module, such as one whose row the door refused at the
                # last setup, would read as outgrown.
                if (mac := self.ensure_module_device(obj)) is not None:
                    new_macs.append(mac)
                buildable = True
            # An entity reads the room map when it is built, so the map is
            # refreshed before the factories run.
            if buildable:
                await self._async_abandonable(self.async_refresh_rooms())
                if self._stopping:
                    return
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
                    # The registry record stays behind, so the id survives
                    # a Designer edit that takes the entity away and gives
                    # it back. While the object is in the catalogue the
                    # stale report has no cause to name for that record,
                    # and an enabled one reads on its own page as provided
                    # by nothing, which is where a user deletes it.
                    await entity.async_remove()
                if to_add:
                    if self._stopping:
                        return
                    # Awaited, so that the platform's table holds the entities
                    # before the batch ends.
                    await registration.platform.async_add_entities(to_add)
            # A module device this batch created has no entry in the
            # capability map, because the last sweep ran before the module
            # was in the tree, and the gated module factories read that
            # map. One sweep covers every new mac in the batch. It runs
            # after the object entities are built, so a slow reply cannot
            # delay those, but it holds the lock for up to both reply
            # timeouts, so the module controls, the report, and the next
            # batch wait for it.
            if new_macs and (admin := self.admin) is not None:
                await self._async_abandonable(self._async_sweep_for(admin, new_macs))
                if self._stopping:
                    return
            # The module entities follow the objects' rule, expected
            # versus built, over every mac the tree holds, narrowed in one
            # place. Removal waits for a mac the catalogue no longer
            # names, which is an absence the object catalogue answered for
            # on both tiers. A mac it still names only gains what it is
            # missing, because a factory that builds nothing there has
            # read something other than that catalogue, such as the
            # capability map a module that stayed silent through the sweep
            # leaves unknown, and an unknown is no reason to take a
            # working control away.
            #
            # So one pass covers a mac this batch put in the tree, a mac
            # whose last object left, and a mac whose object came back,
            # and the controls of a dropped mac come down before the
            # report offers the device they sit on.
            #
            # The mac keeps its place in the tree here. Dropping it would
            # resolve an object returning to that mac to the hub while its
            # child device still hangs under the module, so the object
            # would read as outgrown and the repair would offer a device
            # that sits where it belongs. The two paths that delete the
            # device record drop the mac, and a reload builds the tree
            # from the catalogue again.
            live = self.live_macs()
            factories_by_platform: dict[EntityPlatform, list[AdminModuleFactory]] = {}
            for module_registration in self._module_platforms:
                factories_by_platform.setdefault(
                    module_registration.platform, []
                ).append(module_registration.factory)
            for module_platform, factories in factories_by_platform.items():
                module_add: list[Entity] = []
                module_remove: list[Entity] = []
                for mac in list(self.module_device_ids):
                    on_mac = _built_module_entities(module_platform, mac)
                    wanted = self._expected_module_entities(factories, mac, live)
                    module_add.extend(
                        entity for uid, entity in wanted.items() if uid not in on_mac
                    )
                    if mac not in live:
                        module_remove.extend(on_mac.values())
                for entity in module_remove:
                    # The registry record stays behind, so the report can
                    # offer it, with the device it sits on or on its own.
                    await entity.async_remove()
                if module_add:
                    if self._stopping:
                        return
                    # Awaited for the same reason as the objects above.
                    await module_platform.async_add_entities(module_add)
            self.async_report_records()

    async def _async_sweep_for(
        self, admin: AmpioAdminClient, new_macs: list[int]
    ) -> None:
        """Run the description sweep again for the module devices a batch added.

        A new module with no capability entry after the sweep, failed or
        completed, goes without its capability-gated controls and the
        roller lock action on its covers, and a warning names it. A module
        that has an entry gets its controls from it and no warning, even
        when this sweep left it out. The batch goes on either way, so the
        Identify button and the object entities still stand.
        """
        with suppress(AmpioConnectionError, AmpioTimeoutError):
            await admin.resolve_records()
        missing = [mac for mac in new_macs if mac not in admin.capabilities]
        if missing:
            _LOGGER.warning(
                "No capability data for new Ampio modules %s after the Designer "
                "description sweep. Their buzzer, Unlock touch, and panel lights "
                "are left out until a reload sweeps again",
                ", ".join(format_mac(mac) for mac in missing),
            )

    async def async_refresh_rooms(self) -> None:
        """Read the room map, so that a new child takes its app room.

        The map seeds each object child's area at that device's first
        creation, and the diagnostics download carries it. Nothing in the
        entity or the device path depends on it afterwards, so a failure
        costs the seed and must not fail setup or a batch.
        """
        try:
            self.rooms = await self.client.fetch_rooms()
        except AmpioConnectionError:
            _LOGGER.warning(
                "Could not fetch the Ampio room map; the devices get no area suggestion"
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

    @callback
    def live_macs(self) -> set[int]:
        """Every override mac an object this account receives names.

        The module device of such a mac, and every module entity keyed on
        it, is what the catalogue still accounts for. ``live_identifiers``
        carries the same fact as a device identifier, for the device tree;
        this is the number a module entity's unique id is built from. The
        M-SERV's own objects name no module, because they hang on the hub.
        """
        return {
            obj.address.mac
            for obj in self.client.objects.values()
            if not obj.is_server_owned
        }

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
