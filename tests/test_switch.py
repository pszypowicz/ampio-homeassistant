"""Tests for the Ampio switch platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import AccessTier, AmpioValueError, ObjectUpdated
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    EntityCategory,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import setup_integration
from .conftest import (
    HUB_IDENTIFIER,
    MSENS_IDENTIFIER,
    emit,
    make_object,
    pinned_id,
    set_access_tier,
)

PLAIN_ENTITY_ID = pinned_id("switch", 74)
OUTLET_ENTITY_ID = pinned_id("switch", 75)
FLAG_ENTITY_ID = pinned_id("switch", 61)
OPENING_LOCK_ENTITY_ID = pinned_id("switch", 83, "_lock_opening")
CLOSING_LOCK_ENTITY_ID = pinned_id("switch", 83, "_lock_closing")


@pytest.fixture(autouse=True)
def switch_only() -> Generator[None]:
    """Limit setup to the switch platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.SWITCH]):
        yield


@pytest.mark.usefixtures("mock_client")
async def test_all_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every entity's registry entry and state."""
    await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_turn_on_off_maps_to_verbs(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The switch services map to the plain on and off verbs."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: OUTLET_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(75)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: OUTLET_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(75)


async def test_light_tagged_relay_is_not_a_switch(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A relay whose Matter tag says light belongs to the light platform."""
    await setup_integration(hass, mock_config_entry)

    # Two relays, the two writable flags, and two lock switches per cover.
    states = [s for s in hass.states.async_all() if s.domain == "switch"]
    assert len(states) == 10
    assert not any("kinkiet" in s.entity_id for s in states)


async def test_flag_services_map_to_verbs(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A writable flag answers the switch services with the plain verbs."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(61)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(61)


async def test_flag_push_update_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed flag update flips the switch between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_ON

    obj = replace(mock_client.objects[61], state="0")
    mock_client.objects[61] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_OFF


async def test_push_echo_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed relay echo flips the entity between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(PLAIN_ENTITY_ID).state == STATE_ON

    obj = replace(mock_client.objects[74], state="0")
    mock_client.objects[74] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(PLAIN_ENTITY_ID).state == STATE_OFF


async def test_timed_relay_pulses_on_turn_on(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A configured Designer time turns the on write into a timed pulse.

    The M-SERV never applies the time server-side, so the integration
    sends it, as the Ampio app does. The off write stays plain.
    """
    obj = mock_client.objects[74]
    mock_client.objects[74] = replace(obj, czas=9000)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: PLAIN_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(74, 255, pulse_ms=90000)
    mock_client.turn_on.assert_not_called()

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: PLAIN_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(74)


async def test_read_only_object_rejects_writes(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Designer read-only object raises instead of sending a doomed write.

    The M-SERV drops such writes silently on both account tiers, so the
    entity rejects them up front and keeps its platform.
    """
    obj = mock_client.objects[61]
    # Designer's read-only checkbox is params bit 6; ``read_only`` derives.
    mock_client.objects[61] = replace(obj, params=obj.params | (1 << 6))
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
            blocking=True,
        )
    mock_client.turn_off.assert_not_called()


async def test_leafless_object_keeps_its_module(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A relay whose leaf id Designer cleared still hangs under its module.

    The tree reads the Designer module row id, which every object carries
    on both account tiers whatever its leaf says.
    """
    mock_client.objects[98] = make_object(
        98, "przekaznik", 0, leaf_id="", opis_menu="Leafless", state="0"
    )
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(pinned_id("switch", 98))
    assert entry is not None
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert entry.device_id is not None
    child = device_registry.async_get(entry.device_id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.parent_device_id == module.id
    assert hass.states.get(pinned_id("switch", 98)).state == STATE_OFF


@pytest.mark.parametrize("restricted", [False, True], ids=["admin", "restricted"])
async def test_leafless_server_object_parents_to_the_hub(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    restricted: bool,
) -> None:
    """An M-SERV output without a leaf hangs under the hub on both tiers.

    The admin catalogue names the M-SERV row; a restricted account learns
    it from any server-owned object whose leaf embeds the M-SERV mac.
    """
    if restricted:
        set_access_tier(mock_client, AccessTier.RESTRICTED)
    mock_client.objects[99] = make_object(
        99, "przekaznik", 0, leaf_id="", id_urzadzenia=1, opis_menu="Pompa", state="0"
    )
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(pinned_id("switch", 99))
    assert entry is not None
    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert entry.device_id is not None
    child = device_registry.async_get(entry.device_id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.parent_device_id == hub.id


@pytest.mark.usefixtures("mock_client")
async def test_cover_lock_switches_read_both_directions(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Each direction reads its own bit, and both are configuration entities.

    ``block`` rides the object state push, which both account tiers
    receive, so the read needs no administrator login.
    """
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(OPENING_LOCK_ENTITY_ID).state == STATE_OFF
    assert hass.states.get(CLOSING_LOCK_ENTITY_ID).state == STATE_OFF

    entry = entity_registry.async_get(OPENING_LOCK_ENTITY_ID)
    assert entry is not None
    assert entry.entity_category is EntityCategory.CONFIG

    locked = replace(mock_client.objects[83], block=2)
    mock_client.objects[83] = locked
    emit(mock_client, ObjectUpdated(object=locked))
    await hass.async_block_till_done()

    assert hass.states.get(OPENING_LOCK_ENTITY_ID).state == STATE_ON
    assert hass.states.get(CLOSING_LOCK_ENTITY_ID).state == STATE_OFF


@pytest.mark.usefixtures("mock_client")
async def test_cover_lock_is_unavailable_where_the_firmware_has_no_lock(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A module whose firmware drops the lock gets a grayed control.

    ``block_writable`` False is the sweep's answer that the three lock
    sub-functions are absent from that module's firmware. None is not an
    answer, so a standard account, which never receives the sweep, keeps
    the switch available and learns from the refusal instead.
    """
    mock_client.objects[83] = replace(mock_client.objects[83], block_writable=False)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(OPENING_LOCK_ENTITY_ID).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("mock_client")
async def test_cover_lock_with_no_sweep_stays_available(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """``block_writable`` None means no sweep covered the module, not a refusal.

    A restricted account never receives a sweep, so its covers must read
    None forever, never False, or every one of them would gray out.
    """
    mock_client.objects[83] = replace(mock_client.objects[83], block_writable=None)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(OPENING_LOCK_ENTITY_ID).state != STATE_UNAVAILABLE


@pytest.mark.usefixtures("mock_client")
async def test_cover_lock_swept_true_stays_available_and_writes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """``block_writable`` True is the sweep confirming the module carries the lock.

    This is the realistic administrator path: the switch stays available,
    and a write reaches the client.
    """
    mock_client.objects[83] = replace(mock_client.objects[83], block_writable=True)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(OPENING_LOCK_ENTITY_ID).state != STATE_UNAVAILABLE

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: OPENING_LOCK_ENTITY_ID},
        blocking=True,
    )
    mock_client.block_opening.assert_awaited_once_with(83)


@pytest.mark.usefixtures("mock_client")
async def test_cover_lock_writes_reach_the_matching_verb(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Each switch writes its own direction and leaves the other alone."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: OPENING_LOCK_ENTITY_ID},
        blocking=True,
    )
    mock_client.block_opening.assert_awaited_once_with(83)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: CLOSING_LOCK_ENTITY_ID},
        blocking=True,
    )
    mock_client.unblock_closing.assert_awaited_once_with(83)
    mock_client.unblock_opening.assert_not_awaited()


@pytest.mark.usefixtures("mock_client")
async def test_cover_lock_refusal_names_both_causes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A module without a roller channel count refuses, and the error says so.

    The one translation key covers this cause and the tier gate alike, so
    neither needs its own message.
    """
    await setup_integration(hass, mock_config_entry)
    mock_client.block_closing.side_effect = AmpioValueError("no roller count")

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: CLOSING_LOCK_ENTITY_ID},
            blocking=True,
        )

    assert err.value.translation_key == "cover_lock_unavailable"


async def test_cover_lock_write_refused_on_a_standard_account(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A standard account is refused before any client method runs.

    The lock write rides the raw tree, which the library reserves for the
    administrator login and refuses with a bare ``RuntimeError`` on any
    other tier - a programming error in the library's own terms, not a
    condition it expects a caller to catch. So the entity checks its own
    tier first and never makes the call.
    """
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: OPENING_LOCK_ENTITY_ID},
            blocking=True,
        )

    assert err.value.translation_key == "cover_lock_unavailable"
    mock_client.block_opening.assert_not_awaited()


async def test_cover_lock_still_reads_on_a_standard_account(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The tier gate covers the write alone; the read keeps working.

    ``block`` rides the object state push on both tiers, so a standard
    account must see the same lock state an administrator does, even
    though it cannot change one.
    """
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    mock_client.objects[83] = replace(mock_client.objects[83], block=2)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(OPENING_LOCK_ENTITY_ID).state == STATE_ON
    assert hass.states.get(CLOSING_LOCK_ENTITY_ID).state == STATE_OFF
