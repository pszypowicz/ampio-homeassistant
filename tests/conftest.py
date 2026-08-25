"""Fixtures for the Ampio integration tests.

The library client is mocked at the integration boundary and seeded with
real ``ampio_mqtt`` model instances, so tests drive the integration through
the same public surface the library exposes: the state properties and the
``subscribe`` event stream (dispatched via :func:`emit`).
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

from ampio_mqtt import AmpioModule, AmpioObject, AmpioServerInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the Ampio custom component in every test."""


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Pin the snapshot fixture to the Home Assistant extension.

    The plugin-delivered override in pytest-homeassistant-custom-component
    only wins when it registers after syrupy, and plugin registration order
    is not deterministic across environments. A conftest fixture takes
    precedence over both plugins, so the Home Assistant serializer and the
    snapshots directory apply everywhere.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


MSERV_MAC = "47846"
MSENS_IDENTIFIER = (DOMAIN, f"{MSERV_MAC}:52111")
MSENS_FALLBACK_NAME = "Ampio module 0xCB8F"

USER_INPUT = {
    CONF_HOST: "ampio.test",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "pass",
}

# The identity reply of the default test server.
SERVER_INFO = AmpioServerInfo(
    mac=47846,
    user_id=-1,
    server_version="1865",
    server_revision="409",
    mqtt_version="5.133.11",
    local_ip="10.0.0.1",
    device_id="0011223344556677",
)


def make_object(
    oid: int,
    typ: str,
    interpretacja: int,
    *,
    leaf_id: str,
    device_id: int | None = 17,
    funkcja: int = 1,
    name: str | None = None,
    value: str | None = None,
    params: int = 0,
    matter_device_type: int | None = None,
) -> AmpioObject:
    """Build a classified object the way discovery would."""
    return AmpioObject(
        id=oid,
        device_id=device_id,
        typ_komponentu=typ,
        name=name,
        interpretacja=interpretacja,
        funkcja=funkcja,
        leaf_id=leaf_id,
        params=params,
        value=value,
        matter_device_type=matter_device_type,
    )


# The default object catalogue: one visible sensor per supported kind on
# module 17, so the entity snapshot pins every description's device class,
# unit, precision, and display name. The hidden phantom mirrors a real M-SENS
# where adding a CO2 object in Designer leaves an unnamed stub sharing the
# leafId behind; the ghost is a removed-but-still-returned row with no leafId.
# The two input objects (a named flag, an unnamed motion detection) feed the
# binary_sensor platform the same way. The four output objects (a named
# dimmer, an rgbw, a Matter-tagged relay light, an untagged relay) feed the
# light platform; the untagged relay is exposed by no platform yet.
DEFAULT_OBJECTS = (
    make_object(
        36,
        "temp",
        1,
        leaf_id="0_cb8f_temp_0_1",
        name="Temperatura",
        value="24.4",
    ),
    make_object(
        37,
        "lin_wej",
        1,
        leaf_id="0_cb8f_lin_0_2",
        funkcja=2,
        name="Wilgotność",
        value="42.000000",
    ),
    make_object(43, "lin_wej", 7, leaf_id="0_cb8f_lin_0_3", funkcja=3, value="900.5"),
    make_object(44, "lin_wej", 2, leaf_id="0_cb8f_lin_0_4", funkcja=5, value="1013.2"),
    make_object(45, "lin_wej", 6, leaf_id="0_cb8f_lin_0_5", funkcja=6, value="1019.7"),
    make_object(46, "lin_wej", 3, leaf_id="0_cb8f_lin_0_6", funkcja=7, value="38.5"),
    make_object(47, "lin_wej", 4, leaf_id="0_cb8f_lin_0_7", funkcja=8, value="742"),
    make_object(48, "lin_wej", 5, leaf_id="0_cb8f_lin_0_8", funkcja=9, value="23"),
    make_object(
        61,
        "flaga",
        0,
        leaf_id="0_cb8f_flaga_0_1",
        name="Podlewanie",
        value="1",
    ),
    make_object(62, "detekcja", 0, leaf_id="0_cb8f_det_0_2", funkcja=2, value="0"),
    make_object(
        71,
        "led",
        0,
        leaf_id="0_cb8f_led_0_1",
        funkcja=3,
        name="Taras LED",
        value="128",
    ),
    make_object(
        72,
        "rgbw",
        0,
        leaf_id="0_cb8f_rgbw_0_2",
        funkcja=4,
        name="Salon RGBW",
        value=str(60 | 120 << 8 | 180 << 16 | 240 << 24),
    ),
    make_object(
        73,
        "przekaznik",
        0,
        leaf_id="0_cb8f_rel_0_3",
        funkcja=5,
        name="Kinkiet",
        value="0",
        matter_device_type=0x0100,
    ),
    make_object(74, "przekaznik", 0, leaf_id="0_cb8f_rel_0_4", funkcja=6, value="1"),
    make_object(132, "lin_wej", 7, leaf_id="0_cb8f_lin_0_3", funkcja=3, params=16),
    make_object(99, "lin_wej", 2, leaf_id="", funkcja=4),
)

# The default module catalogue an administrator account receives.
DEFAULT_MODULES = (
    AmpioModule(
        id=17,
        mac=52111,
        mac_global=152111,
        name="m-sens salon",
        type=44,
        sw_version=63,
        hw_version=7,
    ),
    AmpioModule(
        id=3,
        mac=48770,
        name="MREL 3",
        type=4,
        sw_version=11000,
        hw_version=2,
    ),
    AmpioModule(
        id=1,
        mac=1,
        mac_global=47846,
        name="MSERV",
        type=10,
        sw_version=11639,
        hw_version=7,
    ),
)


def emit(client: MagicMock, event: Any) -> None:
    """Dispatch ``event`` to the live listeners subscribed on the mocked client.

    Iterates a snapshot: a listener whose dispatch triggers new
    subscriptions (a reload re-running setup) must not receive this event
    on its replacement registrations too.
    """
    for listener, of, object_id in list(client.live_subscriptions):
        if not isinstance(event, of):
            continue
        if object_id is not None and event.object.id != object_id:
            continue
        listener(event)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=USER_INPUT[CONF_HOST],
        data=USER_INPUT,
        unique_id=MSERV_MAC,
    )


@pytest.fixture
def mock_client_class() -> Generator[MagicMock]:
    """Patch AmpioClient with a connected, discovery-complete mock."""
    with (
        patch("custom_components.ampio.AmpioClient", autospec=True) as client_class,
        patch("custom_components.ampio.config_flow.AmpioClient", new=client_class),
    ):
        client_class.test_connection.return_value = SERVER_INFO
        client = client_class.return_value
        client.start.return_value = True
        client.available = True
        client.objects = {obj.id: obj for obj in DEFAULT_OBJECTS}
        client.modules = {module.id: module for module in DEFAULT_MODULES}
        client.server_info = SERVER_INFO
        client.mserv = client.modules[1]

        # Mirrors the real resolver's documented contract over the seeded
        # catalogue: join by device_id, gated on the leaf-derived mac.
        def module_for(obj: AmpioObject) -> AmpioModule | None:
            if obj.device_id is None:
                return None
            module = client.modules.get(obj.device_id)
            if module is None or module.mac is None or module.mac != obj.module_mac:
                return None
            return module

        client.module_for.side_effect = module_for

        # Track live registrations so unsubscribing works: emit() must not
        # reach listeners from a torn-down setup. Unsubscribing is idempotent,
        # matching the real client's documented contract.
        subscriptions: list[tuple[Any, type | tuple[type, ...], int | None]] = []

        def subscribe(
            listener: Any,
            *,
            of: type | tuple[type, ...],
            object_id: int | None = None,
        ) -> Any:
            registration = (listener, of, object_id)
            subscriptions.append(registration)

            def unsubscribe() -> None:
                if registration in subscriptions:
                    subscriptions.remove(registration)

            return unsubscribe

        client.subscribe.side_effect = subscribe
        client.live_subscriptions = subscriptions
        yield client_class


@pytest.fixture
def mock_client(mock_client_class: MagicMock) -> MagicMock:
    """The mocked AmpioClient instance the integration runs on."""
    return mock_client_class.return_value


@pytest.fixture
def mock_setup_entry() -> Generator[MagicMock]:
    """Patch the entry setup so config-flow tests don't run real setup."""
    with patch("custom_components.ampio.async_setup_entry", return_value=True) as mock:
        yield mock
