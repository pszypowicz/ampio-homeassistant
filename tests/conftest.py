"""Fixtures for the Ampio integration tests.

Tests script broker behavior at the aiomqtt boundary so the real
``ampio_mqtt.AmpioClient`` protocol path is exercised. The previous
``FakeAmpioClient`` subclass reached into private library methods
(``_notify``, ``_set_available``) and is intentionally not used here.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
import json
from typing import Any, Self
from unittest.mock import MagicMock, patch

import aiomqtt
from ampio_mqtt import AmpioClient
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ampio.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the Ampio custom component in every test."""


USER = "user"
MSERV_MAC = "47846"

USER_INPUT = {
    CONF_HOST: "ampio.test",
    CONF_PORT: 1883,
    CONF_USERNAME: USER,
    CONF_PASSWORD: "pass",
}


@dataclass(slots=True)
class _Msg:
    """Minimal stand-in for ``aiomqtt.Message``."""

    topic: str
    payload: bytes


@dataclass(slots=True)
class _Poison:
    """Sentinel that causes the next ``__anext__`` to raise ``error``."""

    error: BaseException


def _topic(suffix: str) -> str:
    return f"ampio/fromDB/{USER}/{suffix}"


def _details_payload(items: list[dict[str, Any]]) -> bytes:
    return json.dumps({"Status": 0, "List": items}).encode()


def _devices_payload(items: list[dict[str, Any]]) -> bytes:
    return json.dumps({"List": items}).encode()


def _states_payload(items: list[dict[str, Any]]) -> bytes:
    return json.dumps({"List": items}).encode()


def _info_payload(**fields: Any) -> bytes:
    return json.dumps({"Results": fields}).encode()


# Initial broker state that the fixture seeds into discovery responses.
# Mirrors the shape of the previous conftest's ``_sample_state``.
DEFAULT_DEVICES: list[dict[str, Any]] = [
    {
        "id": 17,
        "mac": 52111,
        "typ_urzadzenia": 44,
        "nazwa_urzadzenia": "m-sens salon",
        "wersja_softu": 63,
        "wersja_pcb": 7,
    },
    {
        "id": 3,
        "mac": 48770,
        "typ_urzadzenia": 4,
        "nazwa_urzadzenia": "MREL 3",
        "wersja_softu": 11000,
        "wersja_pcb": 2,
    },
    {
        "id": 1,
        "mac": 1,
        "mac_global": 47846,
        "typ_urzadzenia": 10,
        "nazwa_urzadzenia": "MSERV",
        "wersja_softu": 11639,
        "wersja_pcb": 7,
    },
]

# Default initial-state timestamp matches the prior snapshot fixtures.
_DEFAULT_ON_MS = 1779565263000

DEFAULT_DETAILS: list[dict[str, Any]] = [
    {
        "id": 36,
        "id_urzadzenia": 17,
        "typ_komponentu": "temp",
        "interpretacja": 1,
        "opis_menu": "Temperatura",
        "stan_json": json.dumps({"state": "24.4", "on": _DEFAULT_ON_MS}),
    },
    {
        "id": 37,
        "id_urzadzenia": 17,
        "typ_komponentu": "lin_wej",
        "interpretacja": 1,
        "opis_menu": "Wilgotność",
        "stan_json": json.dumps({"state": "42.000000", "on": _DEFAULT_ON_MS}),
    },
    {
        # CO2 channel: no opis_menu, but has a value -> still exposed.
        "id": 43,
        "id_urzadzenia": 17,
        "typ_komponentu": "lin_wej",
        "interpretacja": 7,
        "stan_json": json.dumps({"state": "900.5", "on": _DEFAULT_ON_MS}),
    },
    {
        # Phantom: no opis, no stan_json -> filtered out by the sensor platform.
        "id": 99,
        "id_urzadzenia": 17,
        "typ_komponentu": "lin_wej",
        "interpretacja": 2,
    },
]

DEFAULT_INFO: dict[str, Any] = {
    "mac": 47846,
    "serverVersion": "1865",
    "serverRevision": "409",
    "mqttVersion": "5.133.11",
    "local_ip": "10.0.0.1",
    "device_id": "0011223344556677",
}

# Default room map seed: one group "Salon" containing every classified
# sensor object in ``DEFAULT_DETAILS`` (36, 37, 43). The 99 phantom stays
# out so the sensor platform's filter is what excludes it, not the room
# join.
DEFAULT_GROUPS: list[dict[str, Any]] = [
    {"id": 1, "opis_menu": "Salon"},
]
DEFAULT_GROUP_DEVICES: list[dict[str, Any]] = [
    {"id_grupy": 1, "id_obiektu": 36},
    {"id_grupy": 1, "id_obiektu": 37},
    {"id_grupy": 1, "id_obiektu": 43},
]


class FakeMqttBroker:
    """Per-test controller for the patched ``aiomqtt.Client``."""

    def __init__(self) -> None:
        """Initialise broker state with the default discovery payloads."""
        self.devices: list[dict[str, Any]] = [dict(d) for d in DEFAULT_DEVICES]
        self.details: list[dict[str, Any]] = [dict(d) for d in DEFAULT_DETAILS]
        self.info: dict[str, Any] = dict(DEFAULT_INFO)
        self.groups: list[dict[str, Any]] = [dict(g) for g in DEFAULT_GROUPS]
        self.group_devices: list[dict[str, Any]] = [
            dict(gd) for gd in DEFAULT_GROUP_DEVICES
        ]
        self.subscribed: list[str] = []
        self.published: list[tuple[str, bytes]] = []
        # When set, every aiomqtt.Client connect attempt raises this error.
        # Tests that need a one-shot failure clear it (or assign a new value)
        # after the failure has been observed.
        self.connect_error: BaseException | None = None
        # When True, ``data`` request keywords are received but no response
        # is queued; the client's ``fetch_rooms()`` then times out.
        self.disable_room_response: bool = False
        # FIFO of messages handed to the runner via the async iterator. May
        # also contain ``_Poison`` sentinels.
        self._messages: asyncio.Queue[_Msg | _Poison | None] = asyncio.Queue()

    def _seed_discovery(self) -> None:
        """Push the four initial discovery payloads."""
        self._messages.put_nowait(
            _Msg(_topic("config/devices"), _devices_payload(self.devices))
        )
        self._messages.put_nowait(
            _Msg(_topic("config/devicesDetails"), _details_payload(self.details))
        )
        self._messages.put_nowait(_Msg(_topic("data/states"), _states_payload([])))
        self._messages.put_nowait(_Msg(_topic("data/info"), _info_payload(**self.info)))

    def push_state(self, object_id: int, value: str, on_ms: int | None = None) -> None:
        """Send a live state push for one object."""
        payload: dict[str, Any] = {"state": value}
        if on_ms is not None:
            payload["on"] = on_ms
        self._messages.put_nowait(
            _Msg(_topic(f"ob/{object_id}/state"), json.dumps(payload).encode())
        )

    def push_devices(self, modules: list[dict[str, Any]]) -> None:
        """Re-publish the devices list (for dynamic discovery tests)."""
        self.devices = [dict(m) for m in modules]
        self._messages.put_nowait(
            _Msg(_topic("config/devices"), _devices_payload(self.devices))
        )

    def push_details(self, details: list[dict[str, Any]]) -> None:
        """Re-publish devicesDetails (for dynamic discovery tests)."""
        self.details = [dict(d) for d in details]
        self._messages.put_nowait(
            _Msg(_topic("config/devicesDetails"), _details_payload(self.details))
        )

    def trigger_disconnect(self, error: BaseException | None = None) -> None:
        """Force the runner to see a mid-iteration MqttError and reconnect."""
        self._messages.put_nowait(
            _Poison(error or aiomqtt.MqttError("simulated disconnect"))
        )

    def respond_to_data_request(self, keyword: bytes) -> None:
        """Queue the matching ``data/<keyword>`` response payload, if known."""
        if self.disable_room_response:
            return
        if keyword == b"groups":
            payload = json.dumps({"List": self.groups}).encode()
            self._messages.put_nowait(_Msg(_topic("data/groups"), payload))
        elif keyword == b"group_devices":
            payload = json.dumps({"List": self.group_devices}).encode()
            self._messages.put_nowait(_Msg(_topic("data/group_devices"), payload))


class FakeMqttClient:
    """Scripted stand-in for ``aiomqtt.Client``.

    One instance per connect attempt; the broker holds the cross-attempt
    state and the queue the runner consumes.
    """

    def __init__(self, broker: FakeMqttBroker, *args: Any, **kwargs: Any) -> None:
        """Bind to the broker controller; ignore real ``aiomqtt`` kwargs."""
        self._broker = broker

    async def __aenter__(self) -> Self:
        """Connect: raise the configured error or seed discovery."""
        if self._broker.connect_error is not None:
            raise self._broker.connect_error
        self._broker._seed_discovery()
        return self

    async def __aexit__(self, *exc: object) -> bool:
        """Disconnect; never swallow exceptions."""
        return False

    async def subscribe(self, topic: str) -> None:
        """Record subscriptions for tests that need to assert against them."""
        self._broker.subscribed.append(topic)

    async def publish(self, topic: str, payload: bytes = b"") -> None:
        """Record publishes; auto-respond to `data/<keyword>` requests."""
        self._broker.published.append((topic, payload))
        if topic == f"ampio/control/{USER}/data":
            self._broker.respond_to_data_request(payload)

    @property
    def messages(self) -> Self:
        """Return self as the async iterator the runner reads from."""
        return self

    def __aiter__(self) -> Self:
        """Iterate over messages the broker has queued."""
        return self

    async def __anext__(self) -> _Msg:
        """Yield the next queued message, or simulate a disconnect."""
        item = await self._broker._messages.get()
        if isinstance(item, _Poison):
            raise item.error
        if item is None:
            raise StopAsyncIteration
        return item


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ampio (ampio.test)",
        data=USER_INPUT,
        unique_id=MSERV_MAC,
    )


@pytest.fixture
def mock_aiomqtt(monkeypatch: pytest.MonkeyPatch) -> FakeMqttBroker:
    """Patch aiomqtt.Client with a scripted fake and shorten timeouts.

    The patch covers both the runner's connection (``ampio_mqtt.client.aiomqtt``)
    and the config-flow probe (``AmpioClient.test_connection``, which uses the
    same module-level name). ``AmpioClient.start`` defaults are also shortened
    so connect-timeout tests don't sit on the 15s production default.
    """
    broker = FakeMqttBroker()

    def _factory(*args: Any, **kwargs: Any) -> FakeMqttClient:
        return FakeMqttClient(broker, *args, **kwargs)

    original_start = AmpioClient.start
    original_init = AmpioClient.__init__
    original_fetch_rooms = AmpioClient.fetch_rooms

    async def _fast_start(
        self: AmpioClient,
        *,
        timeout: float = 0.5,
        discovery_timeout: float = 0.2,
    ) -> None:
        await original_start(self, timeout=timeout, discovery_timeout=discovery_timeout)

    def _fast_init(self: AmpioClient, *args: Any, **kwargs: Any) -> None:
        # Speed up reconnect loops so availability-transition tests aren't
        # gated on the 5s production default.
        kwargs.setdefault("reconnect_interval", 0.05)
        original_init(self, *args, **kwargs)

    async def _fast_fetch_rooms(
        self: AmpioClient, timeout: float = 0.2
    ) -> dict[int, str]:
        return await original_fetch_rooms(self, timeout=timeout)

    monkeypatch.setattr("ampio_mqtt.client.aiomqtt.Client", _factory)
    monkeypatch.setattr(AmpioClient, "start", _fast_start)
    monkeypatch.setattr(AmpioClient, "__init__", _fast_init)
    monkeypatch.setattr(AmpioClient, "fetch_rooms", _fast_fetch_rooms)
    monkeypatch.setattr("ampio_mqtt.client._RECONNECT_BACKOFF_MAX", 0.2)

    return broker


@pytest.fixture
def mock_setup_entry() -> Iterator[Any]:
    """Patch the entry setup so config-flow tests don't run real setup."""
    with patch("custom_components.ampio.async_setup_entry", return_value=True) as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_discover() -> Iterator[Any]:
    """Stub the LAN discovery probe so tests don't open real sockets.

    The user-step pre-fill calls ``ampio_mqtt.discover()``, which tries to
    TCP-probe ``ampio.local`` and (optionally) browse mDNS. Both open
    sockets that the HA test harness blocks. The default stub returns no
    candidates; tests that want the pre-fill exercised assign their own
    return value.
    """
    with patch("custom_components.ampio.config_flow.discover", return_value=[]) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _auto_mock_async_zeroconf(mock_async_zeroconf: MagicMock) -> None:
    """Autouse the framework-wide zeroconf mock.

    The integration manifest declares ``dependencies: ["zeroconf"]`` so HA
    boots its zeroconf component before ours. Without the mock that opens a
    real multicast socket, which the test harness's pytest-socket guard blocks.
    """
