"""Tests for the Ampio config flow."""

from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock, _patch, patch

from ampio_mqtt import AmpioAuthError, AmpioConnectionError, AmpioServerInfo
from ampio_mqtt.discovery import DiscoveryResult
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ampio.const import DOMAIN
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .conftest import USER_INPUT

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


_INFO_WITH_MAC = AmpioServerInfo(mac=47846, server_version="1865")
_INFO_NO_MAC = AmpioServerInfo()


def _patch_test_connection(
    return_value: AmpioServerInfo = _INFO_WITH_MAC,
    side_effect: BaseException | None = None,
) -> _patch[AsyncMock]:
    return patch(
        "custom_components.ampio.config_flow.AmpioClient.test_connection",
        new=AsyncMock(return_value=return_value, side_effect=side_effect),
    )


_ERROR_CASES = [
    pytest.param(
        _INFO_WITH_MAC,
        AmpioConnectionError("boom"),
        "cannot_connect",
        id="cannot_connect",
    ),
    pytest.param(
        _INFO_WITH_MAC, AmpioAuthError("bad creds"), "invalid_auth", id="invalid_auth"
    ),
    pytest.param(_INFO_NO_MAC, None, "no_server_info", id="no_server_info"),
]


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """A valid connection creates the entry with the server mac as unique_id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Ampio (ampio.test)"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "47846"


@pytest.mark.parametrize(("info", "side_effect", "expected_error"), _ERROR_CASES)
async def test_user_flow_errors_and_recovers(
    hass: HomeAssistant,
    info: AmpioServerInfo,
    side_effect: BaseException | None,
    expected_error: str,
) -> None:
    """Each error shape stays on the user form; a valid retry creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    with _patch_test_connection(info, side_effect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}

    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(hass: HomeAssistant) -> None:
    """Re-adding an M-SERV that already has an entry aborts."""
    _entry_with_mac().add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _entry_with_mac(
    mac: str = "47846", *, dhcp_mac: str | None = None
) -> MockConfigEntry:
    """Build an entry whose unique_id is mac-based, matching `_INFO_WITH_MAC`.

    Pass ``dhcp_mac`` to simulate an entry that has already been seen via DHCP
    and has the Ethernet MAC stored alongside the credentials.
    """
    data = dict(USER_INPUT)
    if dhcp_mac is not None:
        data[CONF_MAC] = dhcp_mac
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Ampio ({USER_INPUT[CONF_HOST]})",
        data=data,
        unique_id=mac,
    )


async def test_reauth_flow_success(hass: HomeAssistant) -> None:
    """A successful reauth updates the entry's credentials."""
    entry = _entry_with_mac()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "user2", CONF_PASSWORD: "newpw"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_USERNAME] == "user2"
    assert entry.data[CONF_PASSWORD] == "newpw"
    # Host/port are not touched.
    assert entry.data[CONF_HOST] == USER_INPUT[CONF_HOST]


@pytest.mark.parametrize(("info", "side_effect", "expected_error"), _ERROR_CASES)
async def test_reauth_errors_and_recovers(
    hass: HomeAssistant,
    info: AmpioServerInfo,
    side_effect: BaseException | None,
    expected_error: str,
) -> None:
    """Each error shape stays on the reauth form; a valid retry aborts as successful."""
    entry = _entry_with_mac()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)

    with _patch_test_connection(info, side_effect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "user", CONF_PASSWORD: "wrong"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}

    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "user", CONF_PASSWORD: "right"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_aborts_when_mac_differs(hass: HomeAssistant) -> None:
    """Reauth against a different M-SERV mac aborts to protect identity."""
    entry = _entry_with_mac("47846")
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)

    with _patch_test_connection(AmpioServerInfo(mac=99999)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "user", CONF_PASSWORD: "pw"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"


async def test_reconfigure_flow_success(hass: HomeAssistant) -> None:
    """Reconfigure updates host/port and reloads, preserving identity."""
    entry = _entry_with_mac()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    new_data = {
        CONF_HOST: "ampio.new.test",
        "port": 1883,
        CONF_USERNAME: "user",
        CONF_PASSWORD: "pass",
    }
    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], new_data
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "ampio.new.test"
    # The entry's unique_id is unchanged.
    assert entry.unique_id == "47846"


async def test_reconfigure_preserves_stored_mac(hass: HomeAssistant) -> None:
    """A reconfigure that changes only the host must not drop a backfilled CONF_MAC."""
    entry = _entry_with_mac(dhcp_mac="b8:27:eb:b2:83:df")
    entry.add_to_hass(hass)
    assert entry.data[CONF_MAC] == "b8:27:eb:b2:83:df"

    result = await entry.start_reconfigure_flow(hass)
    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "ampio.new.test",
                "port": 1883,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "ampio.new.test"
    assert entry.data[CONF_MAC] == "b8:27:eb:b2:83:df"


async def test_reconfigure_aborts_on_mac_mismatch(hass: HomeAssistant) -> None:
    """Pointing reconfigure at a different M-SERV aborts as unique_id_mismatch."""
    entry = _entry_with_mac("47846")
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    with _patch_test_connection(AmpioServerInfo(mac=99999)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "ampio.other.test",
                "port": 1883,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"


@pytest.mark.parametrize(("info", "side_effect", "expected_error"), _ERROR_CASES)
async def test_reconfigure_errors_and_recovers(
    hass: HomeAssistant,
    info: AmpioServerInfo,
    side_effect: BaseException | None,
    expected_error: str,
) -> None:
    """Each error shape stays on the reconfigure form; a valid retry succeeds."""
    entry = _entry_with_mac()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    bad_input = {
        CONF_HOST: "ampio.bad.test",
        "port": 1883,
        CONF_USERNAME: "user",
        CONF_PASSWORD: "wrong",
    }
    with _patch_test_connection(info, side_effect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], bad_input
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}

    good_input = {
        CONF_HOST: USER_INPUT[CONF_HOST],
        "port": 1883,
        CONF_USERNAME: "user",
        CONF_PASSWORD: "right",
    }
    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], good_input
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


_DHCP_DISCOVERY = DhcpServiceInfo(
    ip="192.0.2.20",
    hostname="ampio",
    macaddress="b827ebb283df",
)
_DHCP_FORMATTED_MAC = "b8:27:eb:b2:83:df"


def _dhcp_init(
    hass: HomeAssistant, info: DhcpServiceInfo = _DHCP_DISCOVERY
) -> Coroutine[Any, Any, Any]:
    return hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=info
    )


async def test_dhcp_discovery_prefills_host(hass: HomeAssistant) -> None:
    """DHCP discovery hands the user step a host field defaulted to the M-SERV IP."""
    result = await _dhcp_init(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert _host_suggested(result) == "192.0.2.20"


async def test_dhcp_discovery_completes_flow(hass: HomeAssistant) -> None:
    """The DHCP flow lands on the user step and creates the entry on submit."""
    result = await _dhcp_init(hass)
    user_input = {
        CONF_HOST: "192.0.2.20",
        "port": 1883,
        CONF_USERNAME: "user",
        CONF_PASSWORD: "pass",
    }
    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "192.0.2.20"
    assert result["data"][CONF_MAC] == _DHCP_FORMATTED_MAC
    assert result["result"].unique_id == "47846"


async def test_dhcp_discovery_silently_updates_known_host(hass: HomeAssistant) -> None:
    """A renewal for an entry with stored MAC updates the host without a form."""
    entry = _entry_with_mac(dhcp_mac=_DHCP_FORMATTED_MAC)
    entry.add_to_hass(hass)
    assert entry.data[CONF_HOST] == "ampio.test"

    result = await _dhcp_init(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.20"


async def test_dhcp_discovery_no_change_when_host_already_matches(
    hass: HomeAssistant,
) -> None:
    """A renewal that re-reports the same IP aborts without rewriting the entry."""
    entry = _entry_with_mac(dhcp_mac=_DHCP_FORMATTED_MAC)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data=entry.data | {CONF_HOST: "192.0.2.20"}
    )

    result = await _dhcp_init(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.20"


async def test_dhcp_discovery_dedupes_concurrent_flows(
    hass: HomeAssistant,
) -> None:
    """A second DHCP discovery for the same M-SERV aborts as already_in_progress."""
    first = await _dhcp_init(hass)
    assert first["type"] is FlowResultType.FORM
    assert first["step_id"] == "user"

    second = await _dhcp_init(hass)
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"


async def test_dhcp_discovery_backfills_mac_for_legacy_entry(
    hass: HomeAssistant,
) -> None:
    """A pre-DHCP entry with no stored MAC falls through to creds, then gets backfilled."""
    entry = _entry_with_mac()
    entry.add_to_hass(hass)
    assert CONF_MAC not in entry.data

    result = await _dhcp_init(hass)
    with _patch_test_connection(_INFO_WITH_MAC):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.0.2.20",
                "port": 1883,
                CONF_USERNAME: "user",
                CONF_PASSWORD: "pass",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.20"
    assert entry.data[CONF_MAC] == _DHCP_FORMATTED_MAC


async def test_user_step_prefills_from_discover(
    hass: HomeAssistant, mock_discover: MagicMock
) -> None:
    """When `ampio_mqtt.discover()` finds a broker, the host field defaults to it."""
    mock_discover.return_value = [
        DiscoveryResult(host="ampio.local", port=1883, address="192.0.2.50")
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert _host_suggested(result) == "ampio.local"


async def test_user_step_default_host_when_discover_empty(
    hass: HomeAssistant,
) -> None:
    """No discovery hit -> field defaults to the well-known `ampio.local`."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert _host_suggested(result) == "ampio.local"


async def test_user_step_default_host_when_discover_raises(
    hass: HomeAssistant, mock_discover: MagicMock
) -> None:
    """A raising `discover()` is treated as no result; the default still works."""
    mock_discover.side_effect = OSError("network unreachable")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert _host_suggested(result) == "ampio.local"


async def test_user_step_caches_default_host(
    hass: HomeAssistant, mock_discover: MagicMock
) -> None:
    """A bad-credential retry reuses the cached default host (no second probe)."""
    mock_discover.return_value = [
        DiscoveryResult(host="ampio.local", port=1883, address="192.0.2.50")
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert mock_discover.call_count == 1

    with _patch_test_connection(side_effect=AmpioAuthError("bad creds")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_discover.call_count == 1
    assert _host_suggested(result) == "ampio.local"


def _host_suggested(result: dict[str, Any]) -> str | None:
    """Pull the host field's suggested value out of a config-flow result."""
    schema = result["data_schema"].schema
    marker = next(m for m in schema if m.schema == CONF_HOST)
    description = marker.description or {}
    return description.get("suggested_value")
