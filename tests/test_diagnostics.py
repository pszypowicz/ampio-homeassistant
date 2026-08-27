"""Tests for the Ampio diagnostics platform."""

from dataclasses import asdict
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from homeassistant.core import HomeAssistant

from . import setup_integration
from .conftest import SERVER_INFO

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
    "last_payloads": {"info": '{"protocol": 1}'},
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
