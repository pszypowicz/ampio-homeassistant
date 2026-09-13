"""Tests for the Ampio diagnostics platform."""

from dataclasses import asdict, replace
from unittest.mock import MagicMock

from ampio_mqtt import AccessTier
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.diagnostics import TO_REDACT_ENTRY, TO_REDACT_SNAPSHOT
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import setup_integration
from .conftest import (
    SERVER_INFO,
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


async def test_designer_config_standard_account_is_empty_and_does_not_raise(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A standard account gets an empty section instead of a raised error.

    ``client.modules`` raises on that tier, so this proves the section
    gates its own read instead of assuming the account is an administrator.
    """
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result["designer_config"] == {"modules": [], "covers": []}


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


async def test_designer_config_omits_object_without_cover_parameters(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A cover the fixture never gave travel parameters is absent from the list."""
    with_cover_parameters(mock_client)
    mock_client.diagnostics_snapshot.return_value = DIAGNOSTICS_SNAPSHOT
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    cover_ids = {entry["id"] for entry in result["designer_config"]["covers"]}
    assert cover_ids == {83}


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
