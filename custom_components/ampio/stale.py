"""Registry records a setup left unclaimed, and the repairs that answer for them.

A record no platform claimed is offered for deletion. The records of a
Designer row the library's admission door refused are not, because the
install still needs them. That repair names the Designer fix instead, and
the records it covers stay out of the deletion lists while it stands.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import logging

from ampio_mqtt import format_mac

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    entity_registry as er,
    issue_registry as ir,
)

from .const import (
    ADMIN_ONLY_RECORDS_ISSUE,
    DOMAIN,
    MODULE_KEY_STEM,
    NOT_CONFIGURED_ISSUE,
    STALE_RECORDS_ISSUE,
)
from .data import AmpioConfigEntry, RefusedRows

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StaleRecords:
    """What the registries hold for the entry beyond what the setup built."""

    # Children before full devices: removing a module takes its children
    # with it, and a child removed twice raises.
    devices: list[dr.AnyDeviceEntry]
    # Entities on no stale device, such as a scene that left the catalogue.
    entities: list[er.RegistryEntry]
    # Entity records the administrator rule withholds from this account.
    # Separate, because the integration knows exactly why these went and
    # can only guess about the rest.
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
    ``MODULE_KEY_STEM`` and the override mac, which is what the door names
    on a collision.
    """
    refused = entry.runtime_data.not_configured
    if refused is None:
        return _RefusedRecords(frozenset(), ())
    return _RefusedRecords(
        frozenset(f"obj_{oid}" for oid in refused.objects),
        tuple(f"{MODULE_KEY_STEM}_{format_mac(mac)}_" for mac, _ in refused.collisions),
    )


def _live_platforms(hass: HomeAssistant) -> dict[str, entity_platform.EntityPlatform]:
    """The platform object that is serving each of the integration's domains.

    Home Assistant builds an ``EntityPlatform`` and publishes it under the
    integration name before it runs that platform's setup, and it leaves
    the object published when a config entry unloads. So the list
    ``async_get_platforms`` returns gains one object per domain on every
    reload, in the order they were built, and the one serving now is the
    last of its domain. A superseded object holds no entity, because the
    unload removed every one of them, so keeping the last per domain is
    what asks the platform the entities of this setup are on.
    """
    return {
        platform.domain: platform
        for platform in entity_platform.async_get_platforms(hass, DOMAIN)
    }


def _unready_domains(
    platforms: Mapping[str, entity_platform.EntityPlatform],
) -> set[str]:
    """The domains whose platform has not finished setting up.

    ``EntityPlatform._setup_complete`` is False from the object's
    construction and True once that platform's ``async_setup_entry`` has
    returned and every entity it handed over has been added. It is
    private, and Home Assistant publishes nothing else that carries the
    fact. The platform object is registered before its setup runs;
    ``async_forward_entry_setups`` returns the same way whether a platform
    set up, deferred on ``PlatformNotReady``, timed out, or raised; and the
    ``<integration>.<domain>`` string ``hass.config.components`` gains on a
    successful setup is never taken out again, so it reports a first
    success forever and cannot speak for a later reload.

    Reading it matters because a platform that did not finish holds no
    entity at all. Every registry record of its domain would read as
    claimed by nobody, and the repair would offer live entities for
    deletion while their platform is waiting to be retried. A deferred
    platform is retried in the background on a growing delay, and its
    domain stays out of the report until one of those retries lands.
    """
    return {
        domain
        for domain, platform in platforms.items()
        if not platform._setup_complete  # noqa: SLF001
    }


@callback
def find_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> StaleRecords:
    """Collect the entry's records that no platform claimed on this setup.

    An entity record is stale when it is enabled and no loaded platform
    holds an entity under its id. A child device is stale when every
    entity on it is stale, a device without entities included. A module
    device is stale when no eligible object resolves to it, and its
    entities are covered by the device rather than listed on their own. A
    disabled entity is skipped by its platform on purpose, so the entity
    itself is never stale, and neither is a child device that depends on
    every one of its entities reading stale. A module device answers to
    liveness alone, so one with no live object left reaches the list
    whether or not the entities on it are disabled.

    The records of a Designer row the admission door refused are none of
    those, and they are left out of all three lists, the module device
    that parents a refused object's child included. The row is missing
    because Ampio Designer left it unaddressable, the installer repair
    says which row and what to change, and the records come back when
    that change lands. The exclusion names those ids alone, so a row
    Designer really did delete is still reported while the installer
    repair stands.

    A domain whose platform has not finished setting up is left out
    whole, because a platform that is still on its way holds no entity
    and every record it owns would read as claimed by nobody. The skip is
    per domain rather than over the whole report, so one platform that
    defers costs the report nothing about the others. It suppresses no
    real leftover either: a platform with nothing to build finishes its
    setup, so an install whose scene catalogue is empty still has its
    leftover scene records reported, and a domain the integration no
    longer ships gets no platform object and is not skipped at all. A
    domain that is skipped is reported on the next pass after its
    platform lands.
    """
    platforms = _live_platforms(hass)
    unready = _unready_domains(platforms)
    claimed = {
        entity_id for platform in platforms.values() for entity_id in platform.entities
    }
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    refused = _refused_records(entry)
    stale_entities = {
        entity.entity_id: entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if entity.disabled_by is None
        and entity.entity_id not in claimed
        and entity.domain not in unready
        and not refused.holds_entity(entity.unique_id)
    }

    devices: list[dr.AnyDeviceEntry] = []
    covered: set[str] = set()
    refused_parents: set[str] = set()
    for child in dr.async_child_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        # A refused row's child is skipped on its own identifier, because
        # a child that has lost every entity record would otherwise read
        # as stale on the empty set its entity test passes.
        if refused.holds_child(child):
            refused_parents.add(child.parent_device_id)
            continue
        child_entities = er.async_entries_for_device(
            entity_registry, child.id, include_disabled_entities=True
        )
        if all(entity.entity_id in stale_entities for entity in child_entities):
            devices.append(child)
            covered.update(entity.entity_id for entity in child_entities)
    live, _ = entry.runtime_data.live_identifiers()
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        # A module device whose every object was refused resolves from no
        # live object, so liveness alone would offer it for deletion, and
        # the registry takes a parent's children and their entity records
        # with it. That would destroy the records the rows above are kept
        # for. Parenthood of a kept child is what holds the device, since
        # the door names a refused object by id and never by its mac.
        if device.identifiers & live or device.id in refused_parents:
            continue
        devices.append(device)
        covered.update(
            entity.entity_id
            for entity in er.async_entries_for_device(
                entity_registry, device.id, include_disabled_entities=True
            )
        )
    withheld_ids = entry.runtime_data.withheld_unique_ids()
    entities: list[er.RegistryEntry] = []
    withheld: list[er.RegistryEntry] = []
    for entity_id, entity in stale_entities.items():
        if entity_id in covered:
            continue
        target = withheld if entity.unique_id in withheld_ids else entities
        target.append(entity)
    return StaleRecords(devices, entities, withheld)


@callback
def async_report_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """Raise the repair issues for what the setup left unclaimed, or clear them.

    Two issues, because the two causes are not equally well understood.
    The administrator rule withholds a known, closed set, and that text
    names the account tier. What is left may be a Designer delete or a lost
    app permission, and its wording follows the tier as before.
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
