"""Tests for the Ampio diagnostics platform."""

from dataclasses import asdict
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
from homeassistant.components.diagnostics import REDACTED, async_redact_data
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
# carrying the default test server's identity with the host identifiers the
# library masks.
DIAGNOSTICS_SNAPSHOT = {
    "available": True,
    "auth_failure": None,
    "server_info": {
        **asdict(SERVER_INFO),
        "local_ip": REDACTED,
        "device_id": REDACTED,
    },
    "connection": {
        "started_at": "2026-08-27T06:00:00+00:00",
        "reconnect_count": 0,
        "last_message_at": "2026-08-27T06:05:00+00:00",
        "last_error": None,
        "subscribe_failures": {},
        "protocol_violations": {},
    },
    "params_gap": [],
    "not_configured": [],
    "mac_collisions": [],
    # One row per module: the relay has pushed since the connect and carries
    # its health broadcast, the sensor module has not spoken yet.
    "modules": [
        {
            "id": 3,
            "mac": "0xBE82",
            "typ_urzadzenia": 4,
            "model": "M-REL-8s",
            "last_seen": 1782108300.0,
            "supply_voltage": 13.9,
            "temperature": 31.5,
        },
        {
            "id": 17,
            "mac": "0xCB8F",
            "typ_urzadzenia": 44,
            "model": "M-SENS",
            "last_seen": None,
            "supply_voltage": None,
            "temperature": None,
        },
    ],
    # The library masks this payload at the source and keeps a safelist.
    # The fixture carries fake location facts outside that safelist so the
    # snapshot proves the integration masks the whole string again.
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
    """Snapshot the diagnostics payload with the entry and the info payload redacted."""
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
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
    topic, and ``last_error`` can name one, with the username in the middle
    where key-based redaction cannot reach it. ampio-mqtt masks that
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
            "last_error": "Publish to ampio/control/<account>/data timed out",
            "subscribe_failures": {"ampio/fromDB/<account>/ob/+/state": 135},
            "protocol_violations": {
                "ampio/fromDB/<account>/md5/devices": "missing column"
            },
        },
    }
    await setup_integration(hass, config_entry)

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert username not in json.dumps(result)


async def test_config_entry_diagnostics_redacts_refused_object_name(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A refused object's Designer name is redacted while its row ID survives."""
    designer_name = "Private room window"
    mock_client.diagnostics_snapshot.return_value = {
        **DIAGNOSTICS_SNAPSHOT,
        "not_configured": [[901, designer_name]],
    }
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert designer_name not in json.dumps(result)
    assert result["snapshot"]["not_configured"] == [[901, REDACTED]]


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
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
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
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result["designer_config"] == snapshot


async def test_designer_config_emits_only_the_reviewed_keys(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The two library dataclasses emit these keys and no others.

    A user attaches this download to a public bug report. Both entries go
    out through ``dataclasses.asdict``, so a field added to ``PanelSettings``
    or ``CoverParameters`` in a later ampio-mqtt release reaches the file
    the moment the pin moves.

    Every key below is a color, a flag, a timing, or a count, and none of
    them needs redaction. Read a new key for identifiers, names, addresses,
    and credentials before adding it here. A secret that is itself a
    dictionary key defeats ``async_redact_data``, which matches key names
    alone, so a topic-shaped or account-shaped key needs its own handling
    rather than an entry in a redaction set.
    """
    with_panel_settings(mock_client)
    with_cover_parameters(mock_client)
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    settings = next(
        entry["panel_settings"]
        for entry in result["designer_config"]["modules"]
        if entry["panel_settings"] is not None
    )
    assert set(settings) == {
        "touch_field_color",
        "status_color",
        "light_signal",
        "beep_time",
        "sound_signal",
        "backlight_active",
        "multitouch_lock",
        "multitouch_send_count",
        "dim_after_s",
        "dim_brightness",
    }
    parameters = next(
        entry["cover_parameters"]
        for entry in result["designer_config"]["covers"]
        if entry["cover_parameters"] is not None
    )
    assert set(parameters) == {
        "with_slats",
        "open_time_s",
        "close_time_s",
        "calibration_percent",
        "slat_time_ms",
        "reversal_lag_ms",
        "start_lag_same_ms",
        "start_lag_other_ms",
    }


async def test_designer_config_names_known_capability_and_numbers_unknown(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A capability id the library names reads as a name, and one it does not as a number."""
    with_buzzer(mock_client)
    module_mac = mock_client.modules[17].mac
    mock_client.capabilities[module_mac] = {
        **mock_client.capabilities[module_mac],
        200: 11,
    }
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
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
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
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
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
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
    mock_client.diagnostics_snapshot.return_value = {**DIAGNOSTICS_SNAPSHOT}
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
