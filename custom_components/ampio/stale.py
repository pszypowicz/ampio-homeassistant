"""Registry records the catalogue does not account for, and the repairs for them.

A record is offered for deletion only when the integration can name why
it should go, and every reason is one of the catalogues this account
received. What the integration was not served, it does not report,
because an unknown is not an absence.

The records of a Designer row the library's admission door refused are
not offered either, because the install still needs them. That repair
names the Designer fix instead, and the records it covers stay out of the
deletion lists while it stands.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import logging
from typing import Final

from ampio_mqtt import format_mac

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from .const import (
    ADMIN_ONLY_RECORDS_ISSUE,
    DOMAIN,
    MODULE_KEY_PREFIX,
    NOT_CONFIGURED_ISSUE,
    SCENE_KEY_STEM,
    STALE_RECORDS_ISSUE,
)
from .data import AmpioConfigEntry, AmpioData, RefusedRows

_LOGGER = logging.getLogger(__name__)

# The prefix of every record an object stands behind: its entities' unique
# ids and its child device's identifier are built from
# ``AmpioObject.object_key``, which the library spells ``obj_<id>``. The
# refusal list arrives as bare ids and is spelled back into this shape.
OBJECT_KEY_PREFIX: Final = "obj_"
# The prefix of a scene entity's unique id, ``scene_<id>``.
SCENE_KEY_PREFIX: Final = f"{SCENE_KEY_STEM}_"


@dataclass(frozen=True)
class StaleRecords:
    """What the registries hold for the entry that its catalogues do not explain."""

    # Children before full devices: removing a module takes its children
    # with it, and a child removed twice raises.
    devices: list[dr.AnyDeviceEntry]
    # Entities on no offered device, such as a scene that left the catalogue.
    entities: list[er.RegistryEntry]
    # Entity records the administrator rule withholds from this account.
    # Separate, because the account tier is why these went and the text
    # can say so.
    withheld: list[er.RegistryEntry]


def _record_names(
    devices: Iterable[dr.AnyDeviceEntry], entities: Iterable[er.RegistryEntry]
) -> list[str]:
    """What the issue text lists for each record, sorted.

    A device carries a name. An entity carries its entity id instead,
    which is the string an automation names. A user can rename either
    one, and the issue's stored text carries whichever name was current
    when this report last ran, not a live read of the registry.
    """
    names = [
        device.name_by_user or device.name or next(iter(device.identifiers))[1]
        for device in devices
    ]
    names.extend(entity.entity_id for entity in entities)
    return sorted(names, key=str.casefold)


@dataclass(frozen=True)
class _RefusedRecords:
    """The registry records the installer repair speaks for.

    Two shapes, because the door refuses two things. An object row with no
    Designer leaf takes its entities and its child device out of the
    build, and a colliding override mac empties the capability map of that
    mac, so every capability-gated module entity on it stops being built
    too. Both sets of records come back when Ampio Designer is fixed.
    """

    # ``obj_<id>`` for each refused object row: the unique id its entities
    # are keyed on, and the identifier of its child device.
    objects: frozenset[str]
    # ``module_mac_<mac>_`` for each colliding mac: the prefix of every
    # module entity's unique id on it. The trailing separator is what
    # keeps mac 0xCB8 from claiming ``module_mac_0xCB8F_buzzer``.
    module_prefixes: tuple[str, ...]

    def holds_entity(self, unique_id: str) -> bool:
        """Whether an entity record belongs to a row the door refused.

        An object entity's unique id is the object's token, or that token
        and a suffix such as ``_pulse``. A module entity's always carries
        a suffix, so the prefix test needs no exact-match arm.
        """
        if unique_id.startswith(self.module_prefixes):
            return True
        return any(
            unique_id == key or unique_id.startswith(f"{key}_") for key in self.objects
        )

    def holds_child(self, child: dr.ChildDeviceEntry) -> bool:
        """Whether a child device stands for an object row the door refused."""
        return any(key in self.objects for _domain, key in child.identifiers)


def _refused_records(entry: AmpioConfigEntry) -> _RefusedRecords:
    """The records of every Designer row the admission door refused.

    An admitted object row carries its own token on
    ``AmpioObject.object_key``, which reads ``obj_<id>``. A refused row is
    not in ``client.objects``, so there is no object to ask and the token
    is built from the id. A module entity's unique id is built from
    ``MODULE_KEY_PREFIX`` and the override mac, which is what the door
    names on a collision.
    """
    refused = entry.runtime_data.not_configured
    if refused is None:
        return _RefusedRecords(frozenset(), ())
    return _RefusedRecords(
        frozenset(f"{OBJECT_KEY_PREFIX}{oid}" for oid in refused.objects),
        tuple(
            f"{MODULE_KEY_PREFIX}{format_mac(mac)}_" for mac, _ in refused.collisions
        ),
    )


@dataclass(frozen=True)
class _LiveCatalogue:
    """The entity keys this account's catalogues account for.

    One field per catalogue a record can be explained by, spelled in the
    keys the records carry rather than in the ids the catalogues do, so
    that a lookup is a comparison rather than a parse.

    ``scenes`` is None while the scene catalogue is unknown, which is what
    a failed fetch leaves behind. The other two fields carry no unknown,
    because an object catalogue is a state the server asserts rather than
    a read that can fail. It arrives with the connection, and an empty one
    is an answer, which is what a restricted account meets when its app
    grant is revoked. Both fields then read empty, so every record they
    would explain is offered, and the wording the tier picks says the
    account is served nothing rather than that Ampio Designer deleted
    anything.
    """

    # ``obj_<id>`` for every object the catalogue carries.
    objects: frozenset[str]
    # ``module_mac_<mac>_`` for every override mac an admitted object
    # names: the prefix every module entity's unique id on it starts with.
    # The trailing separator is what keeps mac 0xCB8 from claiming
    # ``module_mac_0xCB8F_buzzer``.
    module_prefixes: tuple[str, ...]
    # ``scene_<id>`` for every active scene the last fetch returned, or
    # None while no fetch has landed on this setup.
    scenes: frozenset[str] | None

    def explains(self, unique_id: str) -> bool:
        """Whether a catalogue accounts for an entity record, or might yet.

        True keeps the record out of the report. Each arm is one of the
        key shapes this release mints, and the unknown scene catalogue
        answers True for a scene record, because an unknown is not an
        absence.

        Three shapes reach a registry record today: ``obj_<id>`` with an
        optional suffix, ``module_mac_<mac>_<suffix>``, and ``scene_<id>``.
        A key that matches none of them is one this release does not mint,
        and the report names it, so a fourth shape added to the entities
        owes this method an arm. This integration does not migrate, so a
        record an older key left behind is reported as it stands, never
        rewritten into the shape that replaced it.
        """
        if unique_id.startswith(OBJECT_KEY_PREFIX):
            return any(
                unique_id == key or unique_id.startswith(f"{key}_")
                for key in self.objects
            )
        if unique_id.startswith(MODULE_KEY_PREFIX):
            return unique_id.startswith(self.module_prefixes)
        if unique_id.startswith(SCENE_KEY_PREFIX):
            return self.scenes is None or unique_id in self.scenes
        return False


def _live_catalogue(data: AmpioData) -> _LiveCatalogue:
    """Spell each catalogue this account received into the keys it explains.

    Every scene the fetch returned counts, the inactive ones included.
    ``active`` is a toggle the app flips both ways and the library passes
    the column through, so a scene switched off for a season is a row the
    server still serves, and its record is the one its entity comes back
    under. Only a scene the catalogue no longer carries is offered.
    """
    scene_ids = data.scene_ids
    return _LiveCatalogue(
        objects=frozenset(obj.object_key for obj in data.client.objects.values()),
        module_prefixes=tuple(
            f"{MODULE_KEY_PREFIX}{format_mac(mac)}_" for mac in data.live_macs()
        ),
        scenes=(
            None
            if scene_ids is None
            else frozenset(f"{SCENE_KEY_PREFIX}{sid}" for sid in scene_ids)
        ),
    )


def _catalogue_accounts_for_child(
    child: dr.ChildDeviceEntry,
    live: set[tuple[str, str]],
    expected_parent: Mapping[tuple[str, str], str],
) -> bool:
    """Whether the catalogue still accounts for a child device as it stands.

    False on two counts, which are the two the removal hook permits a
    delete for. The catalogue carries no object under the child's
    identifier, so nothing is left to hang there. Or it carries one that
    resolves to another module now: the registry cannot re-parent a child,
    so the delete is the move, and the batch behind the repair's reload
    builds the child again under the parent the object resolves to, with
    its id, its name and its area restored from the deleted record.
    """
    if not child.identifiers & live:
        return False
    return not any(
        identifier in expected_parent
        and expected_parent[identifier] != child.parent_device_id
        for identifier in child.identifiers
    )


@callback
def find_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> StaleRecords:
    """Collect the records this account's catalogues do not account for.

    A record is offered only when the integration can name why it should
    go, and every reason is one of the catalogues. An entity record goes
    when its object left the object catalogue, when it is a module record
    for an override mac no admitted object names, or when its key is a
    shape this release no longer mints. A device record answers to the
    same catalogues: a child device whose object left, or that hangs under
    a parent the object no longer resolves to, and a module device on a
    mac no admitted object names. The hub answers to the entry rather
    than to a catalogue, so it stands while the entry is set up.

    What the integration was not served, it reports nothing about. The
    scene catalogue is fetched rather than pushed, so a fetch that failed
    leaves it unknown for this setup and every scene record stays out of
    the report until a fetch lands. Nothing here reads what the platforms
    built, so a platform that holds no entity says nothing about the
    records of its domain, whether it deferred, is still loading, or built
    nothing on purpose. A disabled record is read like any other: the
    catalogue decides, and its platform's skip of it says nothing either
    way.

    Two consequences of that narrowing are deliberate. A record whose
    object is in the catalogue is not offered even when no platform builds
    an entity under its key, so a Designer edit that clears an object's
    pulse time leaves the pulse sensor's record behind. An enabled record
    in that state reads on its own page as provided by nothing, which is
    where a user deletes it. A module entity a capability gate stopped
    building is kept for the same reason, its mac still being named by an
    object.

    The records of a Designer row the admission door refused are none of
    the causes, and they are left out of all three lists. The row is
    missing because Ampio Designer left it unaddressable, the installer
    repair says which row and what to change, and the records come back
    when that change lands. The exclusion names those ids alone, so a row
    Designer really did delete is still reported while the installer
    repair stands.

    A parent device is offered only when every child under it is offered
    too, because removing a parent removes its children. A module device
    on a mac the catalogue dropped therefore stays while it parents a
    child the report keeps, which is what a refused row's child is. The
    entities of an offered device go with it, and are named once, as the
    device.
    """
    data = entry.runtime_data
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    refused = _refused_records(entry)
    catalogue = _live_catalogue(data)
    live, expected_parent = data.live_identifiers()

    devices: list[dr.AnyDeviceEntry] = []
    parents_of_kept: set[str] = set()
    for child in dr.async_child_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        if refused.holds_child(child) or _catalogue_accounts_for_child(
            child, live, expected_parent
        ):
            parents_of_kept.add(child.parent_device_id)
            continue
        devices.append(child)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.identifiers & live or device.id in parents_of_kept:
            continue
        devices.append(device)

    # Every record an offered device takes with it, named by the device
    # rather than on its own. Disabled records included: the registry
    # removes them with the device like any other.
    covered = {
        record.entity_id
        for device in devices
        for record in er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        )
    }
    withheld_ids = data.withheld_unique_ids()
    entities: list[er.RegistryEntry] = []
    withheld: list[er.RegistryEntry] = []
    for record in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if record.entity_id in covered or refused.holds_entity(record.unique_id):
            continue
        if not catalogue.explains(record.unique_id):
            entities.append(record)
        elif record.unique_id in withheld_ids:
            # A module record on a mac the catalogue still names, on an
            # account that is served no module surface. The tier is the
            # whole of the reason, so it is the other card that says so.
            withheld.append(record)
    return StaleRecords(devices, entities, withheld)


@callback
def async_report_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """Raise the repair issues for the records the catalogues left over, or clear them.

    Two issues, because the two causes differ in what can be said about
    them. The administrator rule withholds a known, closed set, and that
    text names the account tier. A record the catalogue stopped accounting
    for may be a Designer delete or a lost app permission, and its wording
    follows the tier as before.
    """
    stale = find_stale_records(hass, entry)
    _async_report(
        hass,
        ADMIN_ONLY_RECORDS_ISSUE,
        "admin_only_records",
        _record_names([], stale.withheld),
    )
    if entry.runtime_data.admin is not None:
        translation_key = "stale_records_deleted"
    else:
        translation_key = "stale_records_not_served"
    _async_report(
        hass,
        STALE_RECORDS_ISSUE,
        translation_key,
        _record_names(stale.devices, stale.entities),
    )


@callback
def _async_report(
    hass: HomeAssistant, issue_id: str, translation_key: str, names: list[str]
) -> None:
    """Raise one repair issue for a list of records, or clear it when empty."""
    if not names:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    # This issue must stay non-persistent. ``names`` carries Designer
    # object names and the names a user gave their devices, and Home
    # Assistant publishes an issue's ``translation_placeholders`` in the
    # diagnostics download once the issue is persistent, which would break
    # the promise docs/debugging.md makes that the download carries no
    # object, room, or module name.
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=translation_key,
        translation_placeholders={
            "names": "\n".join(f"- {name}" for name in names),
        },
    )


@callback
def async_remove_stale_records(
    hass: HomeAssistant, entry: AmpioConfigEntry, issue_id: str
) -> None:
    """Delete the records the issue covers, as they stand now.

    Read again at submit time, because the list in the issue is as old as
    the last setup. The dispatch names both known issue ids and removes
    nothing for any other, because a repair that deletes registry records
    must be asked for by the id it was raised under, not by falling through
    to whichever branch happens to be last. The installer repair is the id
    that must stay inert: nothing it covers is the integration's to delete,
    and its flow asks for no deletion in the first place.
    """
    stale = find_stale_records(hass, entry)
    entity_registry = er.async_get(hass)
    if issue_id == ADMIN_ONLY_RECORDS_ISSUE:
        for entity in stale.withheld:
            entity_registry.async_remove(entity.entity_id)
    elif issue_id == STALE_RECORDS_ISSUE:
        device_registry = dr.async_get(hass)
        for device in stale.devices:
            # A module's removal already took its children.
            if device_registry.async_get(device.id) is not None:
                device_registry.async_remove_device(device.id)
            # The tree keeps a mac pointing at the device it built, so a
            # removal it did not hear about would leave an object
            # resolving to a device that is gone. The removal hook says
            # the same thing for a delete from the device page, and this
            # says it for a delete from the repair, whether or not the
            # caller reloads afterwards. A child's id keys no mac.
            entry.runtime_data.forget_module_device(device.id)
        for entity in stale.entities:
            entity_registry.async_remove(entity.entity_id)
    else:
        _LOGGER.error(
            "Not removing any record for unrecognized repair issue id %s",
            issue_id,
        )


@callback
def async_report_not_configured(
    hass: HomeAssistant, entry: AmpioConfigEntry, refused: RefusedRows | None
) -> None:
    """Record what the admission door refuses, and raise or clear its repair.

    The record and the repair move together, because the stale report
    reads the record to keep a refused row's leftovers out of its lists.
    None is the door refusing nothing, which takes the repair down.

    Ids alone, and structurally so: ``RefusedRows`` carries no name to
    put in a placeholder, because the projection that builds it keeps the
    Designer names inside the library.

    Either fault can stand without the other, so the text follows which of
    the two the door reports. An account that is not the administrator is
    served no module list and therefore meets the object fault alone.

    Both callers, the connect-time catch and the subscription, reach the
    warning through here, so a fault logs identically regardless of when
    it appears. The clear path logs nothing.
    """
    entry.runtime_data.not_configured = refused
    # The stale report reads this record to decide which records a refused
    # row keeps out of its lists, so a change to either side re-reads
    # both. A row deleted in Ampio Designer while it stood refused leaves
    # the catalogue without a second removal event, and this is what
    # surfaces its records. It waits for the platforms like every other
    # report.
    entry.runtime_data.async_report_records()
    if refused is None:
        ir.async_delete_issue(hass, DOMAIN, NOT_CONFIGURED_ISSUE)
        return
    if refused.objects:
        _LOGGER.warning(
            "Ampio objects %s carry no Designer leaf, so the integration "
            "cannot address them and left them out. Restore each in Ampio "
            "Designer and save",
            sorted(refused.objects),
        )
    for mac, ids in refused.collisions:
        _LOGGER.warning(
            "Ampio module rows %s share override mac %s, so the integration "
            "cannot tell their frames apart and left them out. Give each "
            "module its own mac in Ampio Designer and save",
            sorted(ids),
            format_mac(mac),
        )
    objects = [str(oid) for oid in refused.objects]
    # One line per shared mac: the address, then the Designer device ids
    # that carry it. The mac is written the way the config flow writes the
    # server's, so one install reads one way.
    collisions = [
        f"{format_mac(mac)}: {', '.join(str(row) for row in ids)}"
        for mac, ids in refused.collisions
    ]
    if objects and collisions:
        translation_key = "not_configured_both"
    elif collisions:
        translation_key = "not_configured_modules"
    else:
        translation_key = "not_configured_objects"
    ir.async_create_issue(
        hass,
        DOMAIN,
        NOT_CONFIGURED_ISSUE,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=translation_key,
        translation_placeholders={
            "object_count": str(len(objects)),
            "objects": "\n".join(f"- {oid}" for oid in objects),
            "module_count": str(len(collisions)),
            "modules": "\n".join(f"- {line}" for line in collisions),
        },
    )
