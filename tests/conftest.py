"""Fixtures for the Ampio integration tests.

The library client is mocked at the integration boundary and seeded with
real ``ampio_mqtt`` model instances, so tests drive the integration through
the same public surface the library exposes: the state properties and the
``subscribe`` event stream (dispatched via :func:`emit`).
"""

from collections.abc import Generator
from dataclasses import replace
from typing import Any, Final
from unittest.mock import MagicMock, create_autospec, patch

from ampio_mqtt import (
    AccessTier,
    AmpioAdminClient,
    AmpioClient,
    AmpioModule,
    AmpioObject,
    AmpioScene,
    AmpioServerInfo,
    CoverParameters,
    ModuleFunction,
    PanelLightSignal,
    PanelSettings,
    RecordSweep,
    ThermostatState,
    parse_module_address,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN, MODULE_KEY_STEM
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
# The hub has a constant identifier. Modules use their bus mac, and objects
# use their Designer id.
HUB_IDENTIFIER = (DOMAIN, "hub")
MSENS_IDENTIFIER = (DOMAIN, "module_mac:52111")
# The module device is named from the admin-only module catalogue, and a
# standard account reads its bus mac instead.
MSENS_DEVICE_NAME = "m-sens salon"
MSENS_ROW_NAME = "Ampio module 52111"


def unique_id(oid: int, suffix: str = "") -> str:
    """The unique id of an object's entity: the object key alone."""
    return f"obj_{oid}{suffix}"


def pinned_id(domain: str, oid: int, suffix: str = "") -> str:
    """The pinned entity id of an object's entity in ``domain``.

    The integration carries this id into the add, so no device name and no
    area name compose it. It is the unique id with the domain in front.
    """
    return f"{domain}.ampio_{unique_id(oid, suffix)}"


def module_pinned_id(domain: str, module_id: int, suffix: str) -> str:
    """The pinned entity id built from a module mac and entity suffix."""
    return f"{domain}.ampio_{MODULE_KEY_STEM}_{module_id}{suffix}"


# A sweep that read every module and joined nothing.
EMPTY_SWEEP = RecordSweep(
    records={}, answered_macs=frozenset(), silent_macs=frozenset()
)

USER_INPUT = {
    CONF_HOST: "ampio.test",
    CONF_USERNAME: "admin",
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

# A second M-SERV, for the flow that re-points an entry at other hardware.
# 52990 is 0xCEFE, which is what the confirmation step prints.
OTHER_MSERV_MAC = "52990"
OTHER_SERVER_INFO = replace(SERVER_INFO, mac=52990)


def make_object(
    oid: int,
    typ: str,
    interpretacja: int,
    *,
    leaf_id: str,
    funkcja: int = 1,
    name: str | None = None,
    state: str | None = None,
    params: int = 0,
    matter_device_type: int | None = None,
    lammel: int | None = None,
    thermostat: ThermostatState | None = None,
    czas: int = 0,
    url: str = "",
    string_format: str = "",
) -> AmpioObject:
    """Build a classified object the way discovery would."""
    return AmpioObject(
        id=oid,
        typ_komponentu=typ,
        name=name,
        interpretacja=interpretacja,
        funkcja=funkcja,
        address=parse_module_address(leaf_id),
        leaf_key=f"leaf_{leaf_id}",
        params=params,
        state=state,
        matter_device_type=matter_device_type,
        lammel=lammel,
        thermostat=thermostat,
        czas=czas,
        url=url,
        format=string_format,
    )


# The admitted catalogue covers the supported platform kinds. Object 121
# belongs to mac 1, so its child device belongs to the hub. Rows for hidden
# objects, system objects, and empty leaves belong at the admission door.
# Verified sfIds follow ampio_mqtt's documented leaf classes and temperature
# fixture. The identity table is incomplete, so unlisted values below
# remain unverified.
DEFAULT_OBJECTS = (
    make_object(
        36,
        "temp",
        1,
        leaf_id="0_cb8f_76_0_1",
        name="Temperatura",
        state="24.4",
    ),
    # sfId 74 represents analog inputs within the documented M-SENS range
    # 73-76 and matches the library's store fixtures. The identity table
    # does not establish each measurement's sfId, so this representative
    # assignment is unverified for individual measurements.
    make_object(
        37,
        "lin_wej",
        1,
        leaf_id="0_cb8f_74_0_2",
        funkcja=2,
        name="Wilgotność",
        state="42.000000",
    ),
    make_object(43, "lin_wej", 7, leaf_id="0_cb8f_74_0_3", funkcja=3, state="900.5"),
    make_object(44, "lin_wej", 2, leaf_id="0_cb8f_74_0_4", funkcja=5, state="1013.2"),
    make_object(45, "lin_wej", 6, leaf_id="0_cb8f_74_0_5", funkcja=6, state="1019.7"),
    make_object(46, "lin_wej", 3, leaf_id="0_cb8f_74_0_6", funkcja=7, state="38.5"),
    make_object(47, "lin_wej", 4, leaf_id="0_cb8f_74_0_7", funkcja=8, state="742"),
    make_object(48, "lin_wej", 5, leaf_id="0_cb8f_74_0_8", funkcja=9, state="23"),
    make_object(
        61,
        "flaga",
        0,
        leaf_id="0_cb8f_3_0_1",
        name="Podlewanie",
        state="1",
    ),
    # The two bell-marked objects (params bit 15): a named relay and an
    # unnamed flag, the button platform's populations.
    make_object(
        149,
        "flaga",
        0,
        leaf_id="0_cb8f_3_0_3",
        funkcja=12,
        state="0",
        params=1 << 15,
    ),
    make_object(
        150,
        "przekaznik",
        0,
        leaf_id="0_cb8f_257_2_6",
        funkcja=13,
        name="Dzwonek",
        state="0",
        params=1 << 15,
        czas=300,
    ),
    # A second Designer view of the output the previous object drives.
    # Designer lets one output carry several views, and every view repeats
    # the leafId, so this pair differs in the database id alone.
    make_object(
        151,
        "przekaznik",
        0,
        leaf_id="0_cb8f_257_2_6",
        funkcja=13,
        name="Dzwonek",
        state="0",
        params=1 << 15,
        czas=300,
    ),
    make_object(
        146,
        "wej",
        15,
        leaf_id="0_cb8f_257_1_9",
        name="Przycisk kino",
        state="0",
    ),
    make_object(
        71,
        "led",
        0,
        leaf_id="0_cb8f_67_0_1",
        funkcja=3,
        name="Taras LED",
        state="128",
    ),
    make_object(
        72,
        "rgbw",
        0,
        leaf_id="0_cb8f_30_0_2",
        funkcja=4,
        name="Salon RGBW",
        state=str(60 | 120 << 8 | 180 << 16 | 240 << 24),
    ),
    # sfId 81 is retained as an unverified LEDWW value absent from the table.
    make_object(
        76,
        "ledww",
        0,
        leaf_id="0_cb8f_81_0_1",
        funkcja=14,
        name="Sypialnia CCT",
        state=str(84 | 85 << 8),
    ),
    make_object(
        73,
        "przekaznik",
        0,
        leaf_id="0_cb8f_257_2_3",
        funkcja=5,
        name="Kinkiet",
        state="0",
        matter_device_type=0x0100,
    ),
    make_object(74, "przekaznik", 0, leaf_id="0_cb8f_257_2_4", funkcja=6, state="1"),
    make_object(
        75,
        "przekaznik",
        0,
        leaf_id="0_cb8f_257_2_5",
        funkcja=10,
        name="Gniazdo Taras",
        state="0",
        matter_device_type=0x010A,
    ),
    make_object(
        81,
        "roleta",
        0,
        leaf_id="0_cb8f_5_0_1",
        funkcja=7,
        name="Roleta Sypialnia",
    ),
    make_object(
        82,
        "roleta_procenty",
        0,
        leaf_id="0_cb8f_5_0_2",
        funkcja=8,
        name="Roleta Kuchnia",
        state="35",
    ),
    make_object(
        83,
        "roleta_lamelki",
        0,
        leaf_id="0_cb8f_5_0_3",
        funkcja=9,
        name="Zaluzja Goscinny",
        state="70",
        lammel=40,
    ),
    make_object(
        91,
        "reg",
        0,
        leaf_id="0_cb8f_13_0_1",
        funkcja=11,
        name="Termostat Salon",
        state="1",
        thermostat=ThermostatState(
            measure_temp=21.8,
            set_temperature=22.5,
            mode="S",
            cooling=False,
        ),
    ),
    make_object(
        121,
        "flaga",
        0,
        leaf_id="0_1_3_0_9",
        name="Dom pusty",
        state="0",
    ),
    # Integer sensor slots, the shape an M-CON-485 gives a Modbus
    # reading: current with a format-tail unit, energy with a Unit field,
    # and a bare 16-bit counter.
    make_object(
        160,
        "bit32",
        1,
        leaf_id="0_cb8f_1005_0_0",
        funkcja=14,
        name="Prąd L1",
        state="0.370000",
        string_format="%.3f A",
    ),
    make_object(
        161,
        "bit32",
        2,
        leaf_id="0_cb8f_1005_0_1",
        funkcja=15,
        name="Energia",
        state="23512.09",
        url="kWh",
    ),
    # sfId 1006 is retained as an unverified bit16 value absent from the table.
    make_object(
        162,
        "bit16",
        3,
        leaf_id="0_cb8f_1006_0_0",
        funkcja=16,
        name="Licznik",
        state="42",
    ),
    # The two analog flags, the module's own u8 and signed 16-bit variables.
    # The u8 flag carries a Designer turn-on time the M-SERV ignores on this
    # type, which is what keeps the no-pulse guard honest. The 16-bit flag
    # holds a negative value, which its wider field allows and the u8 field
    # cannot.
    # sfIds 17 and 18 are placeholders with unknown wire provenance for
    # the two analog flag types.
    make_object(
        164,
        "flaga_liniowa",
        0,
        leaf_id="0_cb8f_17_0_1",
        funkcja=18,
        name="Poziom jasnosci",
        state="120",
        czas=50,
    ),
    make_object(
        165,
        "flaga_liniowa16",
        0,
        leaf_id="0_cb8f_18_0_2",
        funkcja=19,
        name="Korekta temperatury",
        state="-44",
    ),
    # Alarm halves use sub-function 3 for armed and 4 for alarmed.
    make_object(
        166,
        "satel_alarm",
        0,
        leaf_id="0_cb8f_296_3_1",
        funkcja=20,
        name="Alarm strefa parter",
        state="1",
    ),
    make_object(
        167,
        "satel_alarm",
        0,
        leaf_id="0_cb8f_296_4_1",
        funkcja=21,
        name="Alarm naruszenie parter",
        state="0",
    ),
)

# Raw Designer rows that cannot become AmpioObject instances.
LEAFLESS_ALARM_ROW = {
    "id": 168,
    "typ_komponentu": "satel_alarm",
    "interpretacja": 0,
    "funkcja": 22,
    "leafId": "",
    "opis_menu": "Alarm strefa garaz",
    "type": None,
    "format": "",
}
HIDDEN_LEAFLESS_ROW = {
    "id": 99,
    "typ_komponentu": "lin_wej",
    "interpretacja": 2,
    "funkcja": 4,
    "leafId": "",
    "opis_menu": None,
    "type": None,
    "format": "",
}

# The default module catalogue an administrator account receives.
DEFAULT_MODULES = (
    AmpioModule(
        id=17,
        mac=52111,
        mac_global=152111,
        nazwa_urzadzenia="m-sens salon",
        typ_urzadzenia=44,
        wersja_softu=63,
        wersja_pcb=7,
    ),
    AmpioModule(
        id=3,
        mac=48770,
        mac_global=148770,
        nazwa_urzadzenia="MREL 3",
        typ_urzadzenia=4,
        wersja_softu=11000,
        wersja_pcb=2,
    ),
    AmpioModule(
        id=1,
        mac=1,
        mac_global=47846,
        nazwa_urzadzenia="MSERV",
        typ_urzadzenia=10,
        wersja_softu=11639,
        wersja_pcb=7,
    ),
)

# The scene catalogue the mocked fetch returns: one enabled scene the
# platform exposes and one disabled scene it must skip.
DEFAULT_SCENES = (
    AmpioScene(id=5, scene_name="Wieczór", active=True, object_ids=frozenset({71, 72})),
    AmpioScene(id=6, scene_name="Nieaktywna", active=False),
)

# The room map the mocked fetch returns: a named sensor, a light, a cover,
# and the server-owned flag get rooms; every other object stays roomless on
# purpose.
DEFAULT_ROOMS = {36: "Salon", 71: "Taras", 81: "Sypialnia", 121: "Techniczne"}


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


# The administrator surface is absent from the base client class.
ADMIN_MEMBERS: Final = frozenset(dir(AmpioAdminClient)) - frozenset(dir(AmpioClient))


def set_access_tier(client: MagicMock, tier: AccessTier) -> None:
    """Give the mock the public surface and identity of the selected client class."""
    client_class = AmpioAdminClient if tier is AccessTier.ADMIN else AmpioClient
    client.mock_add_spec(client_class)
    client.__class__ = client_class
    if tier is AccessTier.ADMIN:
        template = create_autospec(AmpioAdminClient, instance=True)
        for name in ADMIN_MEMBERS:
            setattr(client, name, getattr(template, name))
        client.modules = {module.id: module for module in DEFAULT_MODULES}
        client.mserv = client.modules[1]
        client.capabilities = {}
        client.panel_settings = {}
        client.cover_parameters = {}
        client.records = {}
        client.module_records = {}
        client.last_sweep = EMPTY_SWEEP
        client.resolve_records.return_value = EMPTY_SWEEP
        client.server_info = SERVER_INFO
    else:
        for name in ADMIN_MEMBERS:
            if name in vars(client):
                delattr(client, name)
        client.server_info = replace(SERVER_INFO, user_id=2)


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
        patch("custom_components.ampio.AmpioAdminClient", autospec=True) as admin_class,
    ):
        client_class.check_connection.return_value = SERVER_INFO
        client = client_class.return_value
        admin_class.return_value = client
        client.connect.return_value = True
        client.available = True
        client.objects = {obj.id: obj for obj in DEFAULT_OBJECTS}
        client.server_info = SERVER_INFO
        set_access_tier(client, AccessTier.ADMIN)
        client.fetch_scenes.return_value = list(DEFAULT_SCENES)
        client.fetch_rooms.return_value = dict(DEFAULT_ROOMS)
        client.resolve_records.return_value = EMPTY_SWEEP

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


def with_buzzer(client: MagicMock, module_id: int = 17) -> None:
    """Give a seeded module the buzzer capability, as a panel reports it.

    Merges into the mac's existing capabilities rather than replacing them,
    because a real panel can carry a buzzer and a touch lock together.
    """
    module = client.modules[module_id]
    client.capabilities[module.mac] = {
        **client.capabilities.get(module.mac, {}),
        ModuleFunction.BUZZER: 4,
    }


def with_key_lock(client: MagicMock, module_id: int = 17) -> None:
    """Give a seeded module the touch-lock capability, as a panel reports it.

    Merges into the mac's existing capabilities rather than replacing them,
    because a real panel can carry a buzzer and a touch lock together.
    """
    module = client.modules[module_id]
    client.capabilities[module.mac] = {
        **client.capabilities.get(module.mac, {}),
        ModuleFunction.KEY_LOCK: 1,
    }


def with_panel_colors(client: MagicMock, module_id: int = 17) -> None:
    """Give a seeded module the backlight and status-light capabilities.

    Merges into the mac's existing capabilities rather than replacing them,
    because a real M-DOT panel carries a buzzer, a touch lock, and a
    backlight all at once.

    No panel reports ``BACKLIGHT_RGBW`` and ``STATUSLIGHT_RGB`` with
    different counts. This fixture gives them different values anyway, so
    a test can prove which capability the code reads.
    """
    module = client.modules[module_id]
    client.capabilities[module.mac] = {
        **client.capabilities.get(module.mac, {}),
        ModuleFunction.BACKLIGHT_RGBW: 6,
        ModuleFunction.STATUSLIGHT_RGB: 3,
    }


# A stored default for a six-field panel. The backlight and the status
# light carry distinct values, so a test that reads the wrong field fails.
TOUCH_FIELD_COLOR: Final = (10, 20, 30, 40)
STATUS_COLOR: Final = (200, 210, 220)


def with_panel_settings(client: MagicMock, module_id: int = 17) -> None:
    """Give a seeded module a swept panel-settings row, as a proven M-DOT reports it.

    Every per-field tuple is six long, the field count of the fixture panel.
    """
    module = client.modules[module_id]
    settings = PanelSettings(
        touch_field_color=TOUCH_FIELD_COLOR,
        status_color=STATUS_COLOR,
        light_signal=(PanelLightSignal.CHANGE_STATE,) * 6,
        beep_time=5,
        sound_signal=(True,) * 6,
        backlight_active=(True,) * 6,
        multitouch_lock=(False,) * 6,
        multitouch_send_count=False,
        dim_after_s=30,
        dim_brightness=20,
    )
    client.panel_settings[module.mac] = settings


# A stored travel configuration with every field a distinct value, so a test
# that reads the wrong one fails.
COVER_PARAMETERS: Final = CoverParameters(
    with_slats=True,
    open_time_s=18,
    close_time_s=24,
    calibration_percent=6,
    slat_time_ms=1400,
    reversal_lag_ms=250,
    start_lag_same_ms=120,
    start_lag_other_ms=160,
)


def with_cover_parameters(client: MagicMock, object_id: int = 83) -> None:
    """Seed the administrator travel parameters for one object."""
    client.cover_parameters[object_id] = COVER_PARAMETERS
