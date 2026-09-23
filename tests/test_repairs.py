"""Tests for the stale-record repair of the Ampio integration."""

from dataclasses import replace
from datetime import timedelta
from http import HTTPStatus
from unittest.mock import MagicMock

from ampio_mqtt import AccessTier, AmpioConnectionError, RecordSweep
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.ampio.const import DOMAIN
from custom_components.ampio.stale import (
    async_remove_stale_records,
    async_report_stale_records,
    find_stale_records,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_RESTORED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from . import setup_integration
from .conftest import (
    DEFAULT_SCENES,
    HUB_IDENTIFIER,
    MSENS_IDENTIFIER,
    entity_id_of,
    make_object,
    module_unique_id,
    set_access_tier,
    unique_id,
    with_buzzer,
)

ISSUE_ID = "stale_records"
ADMIN_ISSUE_ID = "admin_only_records"
MSENS_MAC = 52111
IDENTIFY_KEY = module_unique_id(MSENS_MAC, "_identify")
SCENE_ENTITY_ID = "scene.m_serv_wieczor"


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


def _leave_records_behind(mock_client: MagicMock) -> None:
    """Soft-delete the relay, evict the dimmer, and retire the active scene."""
    del mock_client.objects[74]
    del mock_client.objects[71]
    mock_client.fetch_scenes.return_value = [DEFAULT_SCENES[1]]


async def _submit_fix(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, issue_id: str
) -> None:
    """Drive the repair flow for one issue over the HTTP API, to completion."""
    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert resp.status == HTTPStatus.OK
    flow = await resp.json()
    assert flow["type"] == "form"
    assert flow["step_id"] == "confirm"

    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert resp.status == HTTPStatus.OK
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()


async def test_no_issue_without_stale_records(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A setup whose catalogues account for every record raises nothing."""
    await setup_integration(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_stale_records_raise_a_fixable_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Records the catalogues stopped accounting for are listed, one issue for all."""
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "stale_records_deleted"
    assert issue.translation_placeholders == {
        "names": "- Object 74\n- scene.m_serv_wieczor\n- Taras LED",
    }


async def test_restricted_account_gets_the_not_served_wording(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A restricted account cannot tell a delete from a grant change."""
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "stale_records_not_served"


async def test_issue_clears_once_the_records_are_claimed_again(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An object that comes back takes its record with it, and the issue goes."""
    await setup_integration(hass, mock_config_entry)
    relay = mock_client.objects[74]
    del mock_client.objects[74]
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None

    mock_client.objects[74] = relay
    await _reload(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_disabled_entity_is_not_stale(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An entity the user disabled stands while its object is in the catalogue."""
    await setup_integration(hass, mock_config_entry)
    entity_registry.async_update_entity(
        entity_id_of(hass, "switch", unique_id(74)),
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await _reload(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_a_deferred_platform_keeps_its_live_records_out(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A live record of a platform that deferred is neither named nor deleted."""
    await setup_integration(hass, mock_config_entry)
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None

    mock_client.fetch_scenes.side_effect = AmpioConnectionError("server unreachable")
    await _reload(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
    # The submit path reads the records again, so it is guarded too.
    async_remove_stale_records(hass, mock_config_entry, ISSUE_ID)
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None


async def test_a_deferred_platform_does_not_silence_the_others(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An unknown scene catalogue costs the report nothing about the rest."""
    await setup_integration(hass, mock_config_entry)
    del mock_client.objects[74]
    mock_client.fetch_scenes.side_effect = AmpioConnectionError("server unreachable")
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_placeholders == {"names": "- Object 74"}
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None


async def test_a_deferred_platform_reports_once_it_lands(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A scene record waits for a fetch rather than being dropped for good."""
    await setup_integration(hass, mock_config_entry)
    mock_client.fetch_scenes.side_effect = AmpioConnectionError("server unreachable")
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None

    # The catalogue answers on the platform's first background retry, and
    # the scene the entry was set up with is gone from it. The retry is
    # the first moment the scenes can be spoken for, so it raises the card
    # itself; nothing else would, because no object changed.
    mock_client.fetch_scenes.side_effect = None
    mock_client.fetch_scenes.return_value = [DEFAULT_SCENES[1]]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_placeholders == {"names": f"- {SCENE_ENTITY_ID}"}


async def test_a_deactivated_scene_keeps_its_record(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A scene switched off in the app is served, so its record waits for it.

    ``active`` is a toggle the app flips both ways, and the server keeps
    serving the row either way, so the record is what the scene comes back
    under and the id every automation names survives the season.
    """
    await setup_integration(hass, mock_config_entry)
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None

    mock_client.fetch_scenes.return_value = [
        replace(DEFAULT_SCENES[0], active=False),
        DEFAULT_SCENES[1],
    ]
    await _reload(hass, mock_config_entry)

    # No entity is built for it, so the record reads as restored, which
    # is the state its entity comes back from when the app switches the
    # scene on again.
    state = hass.states.get(SCENE_ENTITY_ID)
    assert state is not None
    assert state.attributes.get(ATTR_RESTORED) is True
    stale = find_stale_records(hass, mock_config_entry)
    assert SCENE_ENTITY_ID not in {record.entity_id for record in stale.entities}
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
    # The submit path reads the records again, so it is answered too.
    async_remove_stale_records(hass, mock_config_entry, ISSUE_ID)
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None


async def test_a_disabled_record_is_offered_once_its_row_leaves_the_catalogue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Disabling an entity says nothing about what the server still serves.

    The record sits on the hub, which stands, so it is offered on its own
    rather than through a device.
    """
    await setup_integration(hass, mock_config_entry)
    entity_registry.async_update_entity(
        SCENE_ENTITY_ID, disabled_by=er.RegistryEntryDisabler.USER
    )
    mock_client.fetch_scenes.return_value = [DEFAULT_SCENES[1]]
    await _reload(hass, mock_config_entry)

    stale = find_stale_records(hass, mock_config_entry)
    assert SCENE_ENTITY_ID in {record.entity_id for record in stale.entities}
    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert f"- {SCENE_ENTITY_ID}" in issue.translation_placeholders["names"]


async def test_an_emptied_object_catalogue_offers_the_whole_install(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An empty object catalogue is an answer, so every record it explained goes.

    A revoked app grant empties what a standard account is served, and
    the server asserts that emptiness rather than failing the read. The
    card says the account is served nothing rather than that Ampio
    Designer deleted anything, and the hub stays.
    """
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    mock_client.objects = {}
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await _reload(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "stale_records_not_served"
    offered = {
        device.id for device in find_stale_records(hass, mock_config_entry).devices
    }
    hub = device_registry.async_get_device_by_identifier(HUB_IDENTIFIER, entry_id)
    module = device_registry.async_get_device_by_identifier(MSENS_IDENTIFIER, entry_id)
    assert hub is not None
    assert module is not None
    assert module.id in offered
    assert hub.id not in offered


async def test_the_submit_drops_a_removed_module_device_from_the_tree(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A module device the repair deletes leaves the tree with it.

    The repair flow reloads right after, and the tree must not depend on
    that reload to stop handing out the id of a device that is gone.
    """
    await setup_integration(hass, mock_config_entry)
    data = mock_config_entry.runtime_data
    # The catalogue dropped the module's objects and the batch behind it
    # has not run, which is the window a submit can land in.
    for oid in [
        obj.id for obj in mock_client.objects.values() if obj.address.mac == MSENS_MAC
    ]:
        del mock_client.objects[oid]
    assert MSENS_MAC in data.module_device_ids

    async_remove_stale_records(hass, mock_config_entry, ISSUE_ID)

    assert MSENS_MAC not in data.module_device_ids
    assert (
        device_registry.async_get_device_by_identifier(
            MSENS_IDENTIFIER, mock_config_entry.entry_id
        )
        is None
    )


async def test_removing_the_entry_clears_both_issues(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The records go with the entry, so neither issue has anything left to fix."""
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None
    assert issue_registry.async_get_issue(DOMAIN, ADMIN_ISSUE_ID) is not None

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
    assert issue_registry.async_get_issue(DOMAIN, ADMIN_ISSUE_ID) is None


async def test_fix_flow_removes_the_stale_records(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Confirming the fix deletes the listed records and reloads the entry."""
    assert await async_setup_component(hass, "repairs", {})
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None

    await _submit_fix(hass, hass_client, ISSUE_ID)

    entry_id = mock_config_entry.entry_id
    for oid in (71, 74):
        assert (
            device_registry.async_get_child_device_by_identifier(
                (DOMAIN, unique_id(oid)), entry_id
            )
            is None
        )
    assert entity_registry.async_get_entity_id("switch", DOMAIN, unique_id(74)) is None
    assert entity_registry.async_get_entity_id("light", DOMAIN, unique_id(71)) is None
    assert entity_registry.async_get(SCENE_ENTITY_ID) is None
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
    assert mock_config_entry.state is ConfigEntryState.LOADED

    # Every claimed record is untouched.
    assert (
        entity_registry.async_get(entity_id_of(hass, "sensor", unique_id(36)))
        is not None
    )
    assert (
        device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(36)), entry_id
        )
        is not None
    )
    assert (
        device_registry.async_get_device_by_identifier(MSENS_IDENTIFIER, entry_id)
        is not None
    )


async def test_fix_flow_removes_a_module_without_objects(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A module whose every object is gone is stale with its children."""
    assert await async_setup_component(hass, "repairs", {})
    await setup_integration(hass, mock_config_entry)
    for oid in [
        obj.id for obj in mock_client.objects.values() if obj.address.mac == 52111
    ]:
        del mock_client.objects[oid]
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    names = issue.translation_placeholders["names"]
    assert "- m-sens salon\n" in names
    assert "Identify" not in names

    await _submit_fix(hass, hass_client, ISSUE_ID)

    entry_id = mock_config_entry.entry_id
    assert (
        device_registry.async_get_device_by_identifier(MSENS_IDENTIFIER, entry_id)
        is None
    )
    assert (
        device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(36)), entry_id
        )
        is None
    )
    # The server-owned flag and its hub are not on the module and stay.
    assert (
        device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(121)), entry_id
        )
        is not None
    )


async def test_a_module_offer_names_every_record_its_delete_takes(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A module's children are named beside it, whatever state their records are in.

    Removing a device removes every child under it, so a child the offer
    passes over is destroyed by a submit that never named it.
    """
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(36)), entry_id
    )
    assert child is not None
    entity_registry.async_update_entity(
        entity_id_of(hass, "sensor", unique_id(36)),
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    for oid in [
        obj.id for obj in mock_client.objects.values() if obj.address.mac == MSENS_MAC
    ]:
        del mock_client.objects[oid]
    await _reload(hass, mock_config_entry)

    module = device_registry.async_get_device_by_identifier(MSENS_IDENTIFIER, entry_id)
    assert module is not None
    offered = {
        device.id for device in find_stale_records(hass, mock_config_entry).devices
    }
    assert module.id in offered
    assert child.id in offered
    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert "- Temperatura" in issue.translation_placeholders["names"]


async def test_a_moved_child_is_offered_whatever_sits_on_it(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A child the object outgrew is offered, and the records on it are not.

    Deleting the child is how the object reaches its new module, and the
    records it carries come back with it. A record that no platform
    rebuilds, disabled here, is no reason to leave the child stuck.
    """
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(36)), entry_id
    )
    assert child is not None
    # A pulse-time sensor from before a Designer edit cleared the column.
    leftover = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id(36, "_pulse"),
        config_entry=mock_config_entry,
        device_id=child.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    # The object is moved to another module in Ampio Designer.
    mock_client.objects[36] = make_object(
        36, "temp", 1, leaf_id="0_d009_76_0_1", name="Temperatura", state="24.4"
    )
    await _reload(hass, mock_config_entry)

    stale = find_stale_records(hass, mock_config_entry)
    assert child.id in {device.id for device in stale.devices}
    # The object is in the catalogue, so neither record is offered on
    # its own. The child carries both, and the rebuild restores the ids.
    assert not any(
        record.unique_id in {unique_id(36), leftover.unique_id}
        for record in stale.entities
    )


async def test_a_silent_capability_sweep_keeps_the_buzzer_record(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A panel that answered no sweep says nothing about the buzzer it carries.

    The siren platform builds nothing without the capability, and the
    module row and every object on it are where they were, so there is no
    cause to name for the record and none is offered.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)
    buzzer_key = module_unique_id(MSENS_MAC, "_buzzer")
    entity_id = entity_registry.async_get_entity_id("siren", DOMAIN, buzzer_key)
    assert entity_id is not None

    mock_client.capabilities[MSENS_MAC] = {}
    mock_client.resolve_records.return_value = RecordSweep(
        records={}, answered_macs=frozenset(), silent_macs=frozenset({MSENS_MAC})
    )
    await _reload(hass, mock_config_entry)

    stale = find_stale_records(hass, mock_config_entry)
    assert buzzer_key not in {
        record.unique_id for record in (*stale.entities, *stale.withheld)
    }
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
    # The submit path reads the records again, so it is answered too.
    async_remove_stale_records(hass, mock_config_entry, ISSUE_ID)
    assert entity_registry.async_get(entity_id) is not None


async def test_a_key_this_release_no_longer_mints_is_offered(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A record under a key nothing mints again is named, and never rewritten.

    This integration does not migrate, so the key an earlier release
    minted a module entity under is reported as it stands.
    """
    await setup_integration(hass, mock_config_entry)
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    legacy = entity_registry.async_get_or_create(
        "button",
        DOMAIN,
        "module_17_identify",
        config_entry=mock_config_entry,
        device_id=module.id,
    )

    async_report_stale_records(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert f"- {legacy.entity_id}" in issue.translation_placeholders["names"]
    still_there = entity_registry.async_get(legacy.entity_id)
    assert still_there is not None
    assert still_there.unique_id == "module_17_identify"


async def test_downgrade_raises_the_admin_only_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A standard account gets its own card, which names the tier as the cause."""
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ADMIN_ISSUE_ID) is None

    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ADMIN_ISSUE_ID)
    assert issue is not None
    assert issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "admin_only_records"
    assert issue.translation_placeholders == {
        "names": (
            "- button.m_sens_salon_identify\n"
            "- sensor.m_sens_salon_supply_voltage\n"
            "- sensor.m_sens_salon_temperature"
        ),
    }
    # Nothing else went, so the other card stays away.
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_upgrade_clears_the_admin_only_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The buttons come back with their own ids, and the card clears itself."""
    await setup_integration(hass, mock_config_entry)
    before = entity_registry.async_get(entity_id_of(hass, "button", IDENTIFY_KEY))
    assert before is not None

    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ADMIN_ISSUE_ID) is not None

    set_access_tier(mock_client, AccessTier.ADMIN)
    await _reload(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ADMIN_ISSUE_ID) is None
    after = entity_registry.async_get(entity_id_of(hass, "button", IDENTIFY_KEY))
    assert after is not None
    assert after.id == before.id
    assert after.unique_id == before.unique_id


async def test_the_two_issues_split_their_records(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A downgrade that also loses objects fills both cards, each with its own."""
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await _reload(hass, mock_config_entry)

    admin_issue = issue_registry.async_get_issue(DOMAIN, ADMIN_ISSUE_ID)
    assert admin_issue is not None
    assert admin_issue.translation_placeholders == {
        "names": (
            "- button.m_sens_salon_identify\n"
            "- sensor.m_sens_salon_supply_voltage\n"
            "- sensor.m_sens_salon_temperature"
        ),
    }
    stale_issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert stale_issue is not None
    assert stale_issue.translation_key == "stale_records_not_served"
    assert stale_issue.translation_placeholders == {
        "names": "- Object 74\n- scene.m_serv_wieczor\n- Taras LED",
    }


async def test_admin_only_fix_leaves_the_other_records_alone(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Submitting one card deletes its own records and nothing else."""
    assert await async_setup_component(hass, "repairs", {})
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await _reload(hass, mock_config_entry)

    await _submit_fix(hass, hass_client, ADMIN_ISSUE_ID)

    assert entity_registry.async_get_entity_id("button", DOMAIN, IDENTIFY_KEY) is None
    # The other card and every record it names are untouched.
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None


async def test_unrecognized_issue_id_removes_nothing(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An id neither dispatch branch names deletes no device and no entity.

    Guards against a future third repair id, raised under the same domain,
    falling into the stale-records delete by default.
    """
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    await _reload(hass, mock_config_entry)

    async_remove_stale_records(hass, mock_config_entry, "some_future_issue")

    entry_id = mock_config_entry.entry_id
    for oid in (71, 74):
        assert (
            device_registry.async_get_child_device_by_identifier(
                (DOMAIN, unique_id(oid)), entry_id
            )
            is not None
        )
    assert (
        entity_registry.async_get(entity_id_of(hass, "switch", unique_id(74)))
        is not None
    )
    assert (
        entity_registry.async_get(entity_id_of(hass, "light", unique_id(71)))
        is not None
    )
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None


async def test_fix_flow_for_unrecognized_issue_removes_nothing(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A repair id this integration does not know still runs the same flow.

    Flows are keyed on the domain rather than the issue id, so a fix
    confirmation for an unrelated repair under this domain must not reach
    either delete branch.
    """
    assert await async_setup_component(hass, "repairs", {})
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    await _reload(hass, mock_config_entry)
    unrelated_issue_id = "some_future_issue"
    ir.async_create_issue(
        hass,
        DOMAIN,
        unrelated_issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="stale_records_deleted",
        translation_placeholders={"names": ""},
    )

    await _submit_fix(hass, hass_client, unrelated_issue_id)

    entry_id = mock_config_entry.entry_id
    for oid in (71, 74):
        assert (
            device_registry.async_get_child_device_by_identifier(
                (DOMAIN, unique_id(oid)), entry_id
            )
            is not None
        )
    assert (
        entity_registry.async_get(entity_id_of(hass, "switch", unique_id(74)))
        is not None
    )
    assert (
        entity_registry.async_get(entity_id_of(hass, "light", unique_id(71)))
        is not None
    )
    assert entity_registry.async_get(SCENE_ENTITY_ID) is not None
    assert mock_config_entry.state is ConfigEntryState.LOADED
