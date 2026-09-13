"""Tests for the Ampio diagnostics platform."""

from dataclasses import asdict, replace
import json
from unittest.mock import MagicMock

from ampio_mqtt import AccessTier
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from custom_components.ampio.diagnostics import TO_REDACT_ENTRY, TO_REDACT_SNAPSHOT
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import setup_integration
from .conftest import (
    MSERV_MAC,
    SERVER_INFO,
    USER_INPUT,
    set_access_tier,
    with_buzzer,
    with_cover_parameters,
    with_panel_settings,
)

# A connected-state report shaped like the library's diagnostics_snapshot(),
# carrying the default test server's identity.
DIAGNOSTICS_SNAPSHOT = {
    "access_tier": "admin",
    "available": True,
    "auth_failure": None,
    "server_info": asdict(SERVER_INFO),
    "connection": {
        "started_at": "2026-08-27T06:00:00+00:00",
        "reconnect_count": 0,
        "last_message_at": "2026-08-27T06:05:00+00:00",
        "last_error": None,
        "subscribe_failures": {},
    },
    "mac_collisions": [],
    # One row per module: the relay has pushed since the connect and carries
    # its health broadcast, the sensor module has not spoken yet.
    "modules": [
        {
            "id": 3,
            "mac": 48770,
            "typ_urzadzenia": 4,
            "model": "M-REL-8s",
            "last_seen": 1782108300.0,
            "supply_voltage": 13.9,
            "temperature": 31.5,
        },
        {
            "id": 17,
            "mac": 52111,
            "typ_urzadzenia": 44,
            "model": "M-SENS",
            "last_seen": None,
            "supply_voltage": None,
            "temperature": None,
        },
    ],
    # The raw server reply embeds location facts key-based redaction cannot
    # reach inside a string; the fixture carries fake ones so the snapshot
    # proves the whole payload is masked.
    "last_payloads": {
        "info": '{"protocol": 1, "local_ip": "192.0.2.1", '
        '"lat": "0.0", "lon": "0.0", "city": "Example City 1"}'
    },
}


async def test_config_entry_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot the diagnostics payload with the host identifiers redacted."""
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result == snapshot


async def test_config_entry_diagnostics_carries_no_username(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
) -> None:
    """The account username reaches no part of the download.

    ``subscribe_failures`` and ``protocol_violations`` key on the full MQTT
    topic, and an account topic carries the username in the middle of the
    key, where key-based redaction cannot reach it. ampio-mqtt masks that
    segment before the snapshot leaves the library, so the fixture carries
    the masked form it emits. Serializing the whole result, rather than
    checking the two entries by hand, catches the username anywhere else it
    appears: this file is attached to public bug reports.
    """
    username = "ha_user"
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=USER_INPUT[CONF_HOST],
        data={**USER_INPUT, CONF_USERNAME: username},
        unique_id=MSERV_MAC,
    )
    mock_client.diagnostics_snapshot.return_value = {
        **DIAGNOSTICS_SNAPSHOT,
        "connection": {
            **DIAGNOSTICS_SNAPSHOT["connection"],
            "subscribe_failures": {"ampio/fromDB/<account>/ob/+/state": 135},
            "protocol_violations": {
                "ampio/fromDB/<account>/md5/devices": "missing column"
            },
        },
    }
    await setup_integration(hass, config_entry)

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert username not in json.dumps(result)


async def test_designer_config_standard_account_has_no_modules_and_does_not_raise(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A standard account gets an empty module list instead of a raised error.

    ``client.modules`` raises on that tier, so this proves the section
    gates its own module read instead of assuming the account is an
    administrator. The covers list is unaffected: an object's kind is
    tier-shared, so every eligible cover still appears, each reporting
    None because the sweep that would fill it in is admin-only.
    """
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result["designer_config"]["modules"] == []
    covers = {
        entry["id"]: entry["cover_parameters"]
        for entry in result["designer_config"]["covers"]
    }
    assert covers == {81: None, 82: None, 83: None}


async def test_designer_config_reports_modules_and_covers(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """The section carries every catalogue module and every covered object.

    Applying ``with_buzzer`` and ``with_panel_settings`` to the same module
    proves the two helpers merge: the second write must not drop the
    first's capability.
    """
    with_buzzer(mock_client)
    with_panel_settings(mock_client)
    with_cover_parameters(mock_client)
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result["designer_config"] == snapshot


async def test_designer_config_names_known_capability_and_numbers_unknown(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A capability id the library names reads as a name, and one it does not as a number."""
    with_buzzer(mock_client)
    module = mock_client.modules[17]
    mock_client.modules[17] = replace(
        module, capabilities={**module.capabilities, 200: 11}
    )
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    entry = next(m for m in result["designer_config"]["modules"] if m["id"] == 17)
    assert entry["capabilities"] == {"BUZZER": 4, "200": 11}


async def test_designer_config_module_without_panel_settings_reports_none(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A module the fixture never gave panel settings reports None, not an absent key."""
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    entry = next(m for m in result["designer_config"]["modules"] if m["id"] == 3)
    assert entry["panel_settings"] is None
    assert entry["capabilities"] == {}


async def test_designer_config_lists_every_cover_reporting_none_without_parameters(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Every eligible cover is listed, and one the sweep has not proven reports None.

    Dropping a cover the sweep has not covered would read the same as an
    object that is not a cover at all, which is the one case a cover bug
    report most needs to rule out.
    """
    with_cover_parameters(mock_client)
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    covers = {
        entry["id"]: entry["cover_parameters"]
        for entry in result["designer_config"]["covers"]
    }
    assert covers.keys() == {81, 82, 83}
    assert covers[81] is None
    assert covers[82] is None
    assert covers[83] is not None


async def test_designer_config_leaves_entry_data_and_snapshot_unchanged(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Adding the section changes neither the entry data nor the client snapshot block."""
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result["entry_data"] == async_redact_data(
        mock_config_entry.data, TO_REDACT_ENTRY
    )
    assert result["snapshot"] == async_redact_data(
        DIAGNOSTICS_SNAPSHOT, TO_REDACT_SNAPSHOT
    )
