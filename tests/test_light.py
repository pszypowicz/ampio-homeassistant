"""Tests for the Ampio light platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import (
    OUTPUT_KIND_KEYS,
    AccessTier,
    AmpioConnectionError,
    AmpioTimeoutError,
    AmpioValueError,
    ModuleFunction,
    ObjectUpdated,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import CONF_BLEND_WHITE, DOMAIN
from custom_components.ampio.light import (
    LIGHT_MATTER_TYPES,
    _coldness_from_kelvin,
    _kelvin_from_coldness,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    CONF_USERNAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import setup_integration
from .conftest import (
    MSENS_IDENTIFIER,
    STATUS_COLOR,
    TOUCH_FIELD_COLOR,
    emit,
    entity_id_of,
    module_unique_id,
    set_access_tier,
    unique_id,
    with_panel_colors,
    with_panel_settings,
)

STATUS_LIGHT_KEY = module_unique_id(52111, "_status_light")


def DIMMER_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for object 71, composed from the registry."""
    return entity_id_of(hass, "light", unique_id(71))


def RGBW_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for object 72, composed from the registry."""
    return entity_id_of(hass, "light", unique_id(72))


def RELAY_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for object 73, composed from the registry."""
    return entity_id_of(hass, "light", unique_id(73))


def CCT_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for object 76, composed from the registry."""
    return entity_id_of(hass, "light", unique_id(76))


def BACKLIGHT_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for the m-sens module's backlight."""
    return entity_id_of(hass, "light", module_unique_id(52111, "_backlight"))


def STATUS_LIGHT_ENTITY_ID(hass: HomeAssistant) -> str:
    """Entity id for the m-sens module's status light."""
    return entity_id_of(hass, "light", STATUS_LIGHT_KEY)


@pytest.fixture(autouse=True)
def light_only() -> Generator[None]:
    """Limit setup to the light platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.LIGHT]):
        yield


async def _set_backlight_fields(
    hass: HomeAssistant,
    entity_id: str,
    fields: list[int],
    color: tuple[int, int, int, int],
) -> None:
    """Call ``ampio.set_backlight_fields`` against ``entity_id``."""
    await hass.services.async_call(
        DOMAIN,
        "set_backlight_fields",
        {ATTR_ENTITY_ID: entity_id, "fields": fields, ATTR_RGBW_COLOR: color},
        blocking=True,
    )


async def _set_status_fields(
    hass: HomeAssistant,
    entity_id: str,
    fields: list[int],
    color: tuple[int, int, int],
) -> None:
    """Call ``ampio.set_status_fields`` against ``entity_id``."""
    await hass.services.async_call(
        DOMAIN,
        "set_status_fields",
        {ATTR_ENTITY_ID: entity_id, "fields": fields, ATTR_RGB_COLOR: color},
        blocking=True,
    )


def test_output_kind_vocabulary_is_split_or_deferred() -> None:
    """A library upgrade that adds an output kind forces a platform decision.

    The light platform takes ``dimmer``, ``rgbw``, and ``cct`` outright,
    plus ``relay`` when its Matter tag is in ``LIGHT_MATTER_TYPES``. The
    cover kinds wait for the cover platform, and untagged relays wait for
    the switch platform.
    """
    assert {
        "relay",
        "dimmer",
        "rgbw",
        "cct",
        "cover",
        "cover_position",
        "cover_tilt",
    } == OUTPUT_KIND_KEYS
    assert {0x0100, 0x0101, 0x010C, 0x010D} == LIGHT_MATTER_TYPES


async def test_all_entities(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every entity's registry entry and state, including the panel lights."""
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_relay_light_turn_on_off(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An on/off light maps the services to the plain switch verbs."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(73)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(73)


async def test_read_only_object_rejects_writes(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Designer read-only object raises instead of sending a doomed write.

    The M-SERV drops such writes silently on both account tiers, so the
    entity rejects them up front and keeps its platform.
    """
    obj = mock_client.objects[73]
    # Designer's read-only checkbox is params bit 6; ``read_only`` derives.
    mock_client.objects[73] = replace(obj, params=obj.params | (1 << 6))
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: RELAY_ENTITY_ID(hass)},
            blocking=True,
        )
    mock_client.turn_on.assert_not_called()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: RELAY_ENTITY_ID(hass)},
            blocking=True,
        )
    mock_client.turn_off.assert_not_called()


async def test_dimmer_brightness_maps_to_set_value(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning a dimmer on with a brightness sets the 0-255 level."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID(hass), ATTR_BRIGHTNESS: 200},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 200, pulse_ms=None)


async def test_timed_lights_pulse_on_turn_on(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A configured Designer time turns the on writes into timed pulses.

    The M-SERV never applies the time server-side, so the integration
    sends it, as the Ampio app does - the staircase-timer case.
    """
    for oid, pulse in ((73, 120000), (71, 30000)):
        mock_client.objects[oid] = replace(mock_client.objects[oid], czas=pulse // 10)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(73, 255, pulse_ms=120000)
    mock_client.turn_on.assert_not_called()

    mock_client.set_value.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID(hass), ATTR_BRIGHTNESS: 180},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 180, pulse_ms=30000)


async def test_dimmer_turn_on_without_brightness(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A dimmer with no remembered level turns on with the plain verb."""
    mock_client.objects[71] = replace(mock_client.objects[71], state="0")
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(71)


async def test_rgbw_color_and_brightness_scale(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color with a brightness scales so the peak channel hits it."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass),
            ATTR_RGBW_COLOR: (10, 20, 40, 80),
            ATTR_BRIGHTNESS: 160,
        },
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 20, 40, 80, 160)


async def test_rgbw_reports_the_color_at_full_scale(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A dimmed output reports its color with the peak channel at 255."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[72], state=str(30 | 15 << 8))
    mock_client.objects[72] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    state = hass.states.get(RGBW_ENTITY_ID(hass))
    assert state is not None
    assert state.attributes[ATTR_BRIGHTNESS] == 30
    assert state.attributes[ATTR_RGBW_COLOR] == (255, 128, 0, 0)


async def test_rgbw_color_without_brightness_keeps_the_level(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color with no brightness scales to the level the output holds."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[72], state=str(30 | 15 << 8))
    mock_client.objects[72] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass), ATTR_RGBW_COLOR: (0, 0, 255, 0)},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 0, 0, 30, 0)


async def test_rgbw_color_without_brightness_on_a_dark_output_is_full(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color with no brightness and no memory turns on at full scale."""
    mock_client.objects[72] = replace(mock_client.objects[72], state="0")
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass), ATTR_RGBW_COLOR: (0, 10, 20, 0)},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 0, 128, 255, 0)


async def test_rgbw_turn_on_from_dark_defaults_to_white(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A bare turn_on with no memory raises the white channel."""
    mock_client.objects[72] = replace(mock_client.objects[72], state="0")
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 0, 0, 0, 255)


async def test_rgbw_explicit_zero_color_turns_off(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An explicit all-zero color is a request for darkness and routes to off."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[72], state="0")
    mock_client.objects[72] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass),
            ATTR_RGBW_COLOR: (0, 0, 0, 0),
            ATTR_BRIGHTNESS: 100,
        },
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(72)
    mock_client.set_colors.assert_not_awaited()


async def test_rgbw_turn_off_uses_turn_off(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entity delegates rgbw off to the client, which owns the routing."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(72)


def _blended(entry: MockConfigEntry) -> MockConfigEntry:
    """The same entry with the blend_white option turned on."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=entry.title,
        data=entry.data,
        unique_id=entry.unique_id,
        options={CONF_BLEND_WHITE: True},
    )


def _go_dark(mock_client: MagicMock) -> None:
    """Start the rgbw output dark, so the light has no lit state to remember."""
    mock_client.objects[72] = replace(mock_client.objects[72], state="0")


async def test_blended_rgbw_state(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """A blended rgbw output presents one rgb color with white added back in.

    The color reads at full scale, and the peak channel is the brightness.
    """
    await setup_integration(hass, _blended(mock_config_entry))

    state = hass.states.get(RGBW_ENTITY_ID(hass))
    assert state is not None
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.RGB
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.RGB]
    assert state.attributes[ATTR_RGB_COLOR] == (182, 219, 255)
    assert state.attributes[ATTR_BRIGHTNESS] == 240
    assert ATTR_RGBW_COLOR not in state.attributes
    assert state == snapshot


async def test_blended_leaves_other_kinds_alone(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The option changes rgbw outputs and no other light."""
    await setup_integration(hass, _blended(mock_config_entry))

    modes = {
        entity_id(hass): hass.states.get(entity_id(hass)).attributes[ATTR_COLOR_MODE]
        for entity_id in (DIMMER_ENTITY_ID, CCT_ENTITY_ID)
    }
    assert modes == {
        DIMMER_ENTITY_ID(hass): ColorMode.BRIGHTNESS,
        CCT_ENTITY_ID(hass): ColorMode.COLOR_TEMP,
    }


@pytest.mark.parametrize(
    ("request_data", "channels"),
    [
        pytest.param({ATTR_RGB_COLOR: (255, 200, 100)}, (255, 165, 0, 165), id="mix"),
        pytest.param({ATTR_RGB_COLOR: (255, 255, 255)}, (0, 0, 0, 255), id="white"),
        pytest.param({ATTR_RGB_COLOR: (255, 0, 0)}, (255, 0, 0, 0), id="saturated"),
        pytest.param(
            {ATTR_RGB_COLOR: (255, 255, 255), ATTR_BRIGHTNESS: 128},
            (0, 0, 0, 128),
            id="white_dimmed",
        ),
        pytest.param(
            {ATTR_COLOR_TEMP_KELVIN: 6500}, (5, 4, 0, 255), id="color_temperature"
        ),
    ],
)
async def test_blended_turn_on_derives_the_white_channel(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    request_data: dict[str, object],
    channels: tuple[int, int, int, int],
) -> None:
    """A requested color sends white as the share red, green, and blue have in common."""
    _go_dark(mock_client)
    await setup_integration(hass, _blended(mock_config_entry))

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass), **request_data},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, *channels)


async def test_blended_brightness_keeps_the_held_channels(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A brightness-only call scales the channels the output holds."""
    await setup_integration(hass, _blended(mock_config_entry))

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass), ATTR_BRIGHTNESS: 120},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 30, 60, 90, 120)


@pytest.mark.parametrize(
    ("rgbw", "channels"),
    [
        pytest.param((0, 0, 0, 255), (0, 0, 0, 255), id="on_the_blend"),
        pytest.param((10, 20, 40, 80), (0, 29, 86, 255), id="off_the_blend"),
    ],
)
async def test_blended_rgbw_request_lands_on_the_blend(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    rgbw: tuple[int, int, int, int],
    channels: tuple[int, int, int, int],
) -> None:
    """An rgbw_color request arrives as its rgb blend and is written back blended."""
    _go_dark(mock_client)
    await setup_integration(hass, _blended(mock_config_entry))

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass), ATTR_RGBW_COLOR: rgbw},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, *channels)


async def test_push_echo_updates_brightness(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed dimmer echo updates state and brightness."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[71], state="255")
    mock_client.objects[71] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    state = hass.states.get(DIMMER_ENTITY_ID(hass))
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 255


async def test_panel_lights_exist_with_both_capabilities(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A panel carrying both capabilities gets a backlight and a status light."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(BACKLIGHT_ENTITY_ID(hass)) is not None
    assert hass.states.get(STATUS_LIGHT_ENTITY_ID(hass)) is not None


async def test_panel_lights_report_assumed_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Neither panel light's color is confirmed, so both report assumed_state."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    backlight = hass.states.get(BACKLIGHT_ENTITY_ID(hass))
    assert backlight is not None
    assert backlight.attributes[ATTR_ASSUMED_STATE] is True
    status_light = hass.states.get(STATUS_LIGHT_ENTITY_ID(hass))
    assert status_light is not None
    assert status_light.attributes[ATTR_ASSUMED_STATE] is True


async def test_panel_lights_attach_to_the_module_device(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Both panel lights attach to their module device, not the hub or an object."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    backlight = entity_registry.async_get(BACKLIGHT_ENTITY_ID(hass))
    assert backlight is not None
    assert backlight.device_id == module.id
    status_light = entity_registry.async_get(STATUS_LIGHT_ENTITY_ID(hass))
    assert status_light is not None
    assert status_light.device_id == module.id


async def test_panel_light_built_only_for_its_own_capability(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module reporting only the backlight capability gets no status light."""
    module = mock_client.modules[17]
    mock_client.capabilities[module.mac] = {ModuleFunction.BACKLIGHT_RGBW: 6}
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(BACKLIGHT_ENTITY_ID(hass)) is not None
    assert (
        er.async_get(hass).async_get_entity_id("light", DOMAIN, STATUS_LIGHT_KEY)
        is None
    )


async def test_panel_lights_withheld_on_a_standard_account(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An account downgrade withholds both existing panel-light records."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(BACKLIGHT_ENTITY_ID(hass)) is not None
    assert hass.states.get(STATUS_LIGHT_ENTITY_ID(hass)) is not None

    set_access_tier(mock_client, AccessTier.RESTRICTED)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_USERNAME: "user"}
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(BACKLIGHT_ENTITY_ID(hass)) is not None
    assert entity_registry.async_get(STATUS_LIGHT_ENTITY_ID(hass)) is not None
    assert hass.states.get(BACKLIGHT_ENTITY_ID(hass)).state == STATE_UNAVAILABLE
    assert hass.states.get(STATUS_LIGHT_ENTITY_ID(hass)).state == STATE_UNAVAILABLE
    withheld = mock_config_entry.runtime_data.withheld_unique_ids()
    assert withheld == {module_unique_id(52111, "_backlight"), STATUS_LIGHT_KEY}


async def test_initial_color_reads_panel_settings(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Each entity's initial color reads its own panel_settings field.

    The peak channel is the brightness, and the color reads at full scale.
    """
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    backlight = hass.states.get(BACKLIGHT_ENTITY_ID(hass))
    assert backlight is not None
    assert backlight.attributes[ATTR_BRIGHTNESS] == max(TOUCH_FIELD_COLOR)
    assert backlight.attributes[ATTR_RGBW_COLOR] == (64, 128, 191, 255)
    status_light = hass.states.get(STATUS_LIGHT_ENTITY_ID(hass))
    assert status_light is not None
    assert status_light.attributes[ATTR_BRIGHTNESS] == max(STATUS_COLOR)
    assert status_light.attributes[ATTR_RGB_COLOR] == (232, 243, 255)


async def test_missing_panel_settings_starts_at_zero(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A panel whose panel_settings is None starts at zero on both entities.

    ``is_on`` reads whether any channel is non-zero, so an off state is the
    observable proof that every channel starts there: Home Assistant omits
    the color attributes for a light it reports off.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    backlight = hass.states.get(BACKLIGHT_ENTITY_ID(hass))
    assert backlight is not None
    assert backlight.state == STATE_OFF
    status_light = hass.states.get(STATUS_LIGHT_ENTITY_ID(hass))
    assert status_light is not None
    assert status_light.state == STATE_OFF


async def test_backlight_turn_on_with_color_sends_it(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An explicit color on a dark backlight is sent with its peak at 255."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass), ATTR_RGBW_COLOR: (10, 20, 30, 40)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 64, 128, 191, 255, fields=None
    )


async def test_status_light_turn_on_with_color_sends_it(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An explicit color on a dark status light is sent with its peak at 255."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass), ATTR_RGB_COLOR: (50, 60, 70)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 182, 219, 255, fields=None
    )


async def test_backlight_turn_on_without_color_sends_stored_default(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A bare turn_on sends the color Ampio Designer stored for the icons."""
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, *TOUCH_FIELD_COLOR, fields=None
    )


async def test_status_light_turn_on_without_color_sends_stored_default(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A bare turn_on sends the color Ampio Designer stored for the indicators."""
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, *STATUS_COLOR, fields=None
    )


async def test_turn_on_without_color_or_settings_sends_plain_white(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Without a stored default, a bare turn_on sends plain white on both surfaces."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 0, 0, 255, fields=None
    )

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 255, 255, 255, fields=None
    )


async def test_brightness_only_turn_on_keeps_the_held_color(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A brightness-only turn_on scales the color this entity is holding.

    The more-info dialog's brightness slider sends brightness alone. A
    bare fallback to the stored default (rather than what the entity
    currently holds) would jump the panel to an unrelated hue on every
    brightness change.
    """
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass), ATTR_RGBW_COLOR: (255, 0, 0, 0)},
        blocking=True,
    )
    mock_client.set_panel_backlight.reset_mock()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass), ATTR_BRIGHTNESS: 128},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 128, 0, 0, 0, fields=None
    )


async def test_panel_color_without_brightness_keeps_the_level(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A dimmed panel reports its color at full scale and keeps its level."""
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass),
            ATTR_RGBW_COLOR: (255, 0, 0, 0),
            ATTR_BRIGHTNESS: 64,
        },
        blocking=True,
    )
    state = hass.states.get(BACKLIGHT_ENTITY_ID(hass))
    assert state is not None
    assert state.attributes[ATTR_BRIGHTNESS] == 64
    assert state.attributes[ATTR_RGBW_COLOR] == (255, 0, 0, 0)
    mock_client.set_panel_backlight.reset_mock()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass), ATTR_RGBW_COLOR: (0, 255, 0, 0)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 64, 0, 0, fields=None
    )


async def test_bare_turn_on_from_the_seeded_state_sends_the_held_color(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A bare turn_on with no prior change sends the color seeded at construction.

    Complements the stored-default test above by exercising the
    self-held-color branch directly from the value ``__init__`` seeds,
    rather than from a value a previous turn_on set.
    """
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, *TOUCH_FIELD_COLOR, fields=None
    )


async def test_bare_turn_on_falls_through_to_plain_white_when_default_is_black(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An all-black stored default is not None, so it must not block the white fallback.

    Without this rule, a panel whose Designer default happens to be black
    could never be turned on from Home Assistant with a bare call: the
    color would resolve to all zero and route straight to turn_off.
    """
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    module = mock_client.modules[17]
    assert mock_client.panel_settings[module.mac] is not None
    black_settings = replace(
        mock_client.panel_settings[module.mac],
        touch_field_color=(0, 0, 0, 0),
        status_color=(0, 0, 0),
    )
    mock_client.panel_settings[module.mac] = black_settings
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 0, 0, 255, fields=None
    )

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 255, 255, 255, fields=None
    )


async def test_backlight_brightness_scales_channels(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color with a brightness scales so the peak channel hits it."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass),
            ATTR_RGBW_COLOR: (10, 20, 40, 80),
            ATTR_BRIGHTNESS: 160,
        },
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 20, 40, 80, 160, fields=None
    )


async def test_status_light_brightness_scales_channels(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color with a brightness scales so the peak channel hits it."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass),
            ATTR_RGB_COLOR: (20, 40, 80),
            ATTR_BRIGHTNESS: 160,
        },
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 40, 80, 160, fields=None
    )


async def test_backlight_turn_off_and_explicit_zero_send_all_zero(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """turn_off, and an explicit all-zero turn_on, both send every channel at zero."""
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 0, 0, 0, fields=None
    )

    mock_client.set_panel_backlight.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass), ATTR_RGBW_COLOR: (0, 0, 0, 0)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 0, 0, 0, fields=None
    )


async def test_status_light_turn_off_and_explicit_zero_send_all_zero(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """turn_off, and an explicit all-zero turn_on, both send every channel at zero."""
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 0, 0, 0, fields=None
    )

    mock_client.set_panel_status_light.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass), ATTR_RGB_COLOR: (0, 0, 0)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 0, 0, 0, fields=None
    )


async def test_backlight_unknown_module_raises(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A missing module row prevents the backlight command and names the address error."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)
    del mock_client.modules[17]

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass), ATTR_RGBW_COLOR: (1, 2, 3, 4)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "module_not_addressable"
    mock_client.set_panel_backlight.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        AmpioConnectionError("Not connected"),
        AmpioTimeoutError("no ack"),
        AmpioValueError("command rejected"),
    ],
)
async def test_backlight_command_failure_raises(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """A library command failure raises the backlight's own message."""
    with_panel_colors(mock_client)
    mock_client.set_panel_backlight.side_effect = error
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID(hass), ATTR_RGBW_COLOR: (1, 2, 3, 4)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "backlight_command_failed"


async def test_status_light_unknown_module_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A missing module row prevents the status-light command and names the address error."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)
    del mock_client.modules[17]

    with pytest.raises(ServiceValidationError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass), ATTR_RGB_COLOR: (1, 2, 3)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "module_not_addressable"
    mock_client.set_panel_status_light.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        AmpioConnectionError("Not connected"),
        AmpioTimeoutError("no ack"),
        AmpioValueError("command rejected"),
    ],
)
async def test_status_light_command_failure_raises(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """A library command failure raises the status light's own message."""
    with_panel_colors(mock_client)
    mock_client.set_panel_status_light.side_effect = error
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID(hass), ATTR_RGB_COLOR: (1, 2, 3)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "status_light_command_failed"


async def test_set_backlight_fields_sends_the_named_fields_and_color(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The service sends exactly the named fields and color, not every field."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_backlight_fields(
        hass, BACKLIGHT_ENTITY_ID(hass), [2, 4], (10, 20, 30, 40)
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 10, 20, 30, 40, fields=[2, 4]
    )


async def test_set_status_fields_sends_the_named_fields_and_color(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The service sends exactly the named fields and color, not every field."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_status_fields(hass, STATUS_LIGHT_ENTITY_ID(hass), [1, 3], (50, 60, 70))
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 50, 60, 70, fields=[1, 3]
    )


async def test_set_backlight_fields_on_an_object_light_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An object-backed light drives no panel, and the service names the surface."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(hass, RGBW_ENTITY_ID(hass), [1], (10, 20, 30, 40))
    assert excinfo.value.translation_key == "not_a_panel_backlight"
    mock_client.set_panel_backlight.assert_not_awaited()


async def test_set_status_fields_on_an_object_light_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An object-backed light drives no panel, and the service names the surface."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_status_fields(hass, RGBW_ENTITY_ID(hass), [1], (10, 20, 30))
    assert excinfo.value.translation_key == "not_a_panel_status_light"
    mock_client.set_panel_status_light.assert_not_awaited()


async def test_set_backlight_fields_on_the_status_light_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The backlight service refuses the status light entity, naming the backlight."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(
            hass, STATUS_LIGHT_ENTITY_ID(hass), [1], (10, 20, 30, 40)
        )
    assert excinfo.value.translation_key == "not_a_panel_backlight"
    mock_client.set_panel_backlight.assert_not_awaited()


async def test_set_status_fields_on_the_backlight_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The status light service refuses the backlight entity, naming the status light."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_status_fields(hass, BACKLIGHT_ENTITY_ID(hass), [1], (10, 20, 30))
    assert excinfo.value.translation_key == "not_a_panel_status_light"
    mock_client.set_panel_status_light.assert_not_awaited()


@pytest.mark.parametrize("field", [0, 7])
async def test_set_backlight_fields_out_of_range_raises(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    field: int,
) -> None:
    """A field below 1 or above the panel's own 6-field count raises, naming both.

    The library would otherwise drop an out-of-range field in silence, so
    this validation is what turns that silence into a message.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(
            hass, BACKLIGHT_ENTITY_ID(hass), [field], (10, 20, 30, 40)
        )
    assert excinfo.value.translation_key == "panel_field_out_of_range"
    assert excinfo.value.translation_placeholders == {
        "field": str(field),
        "count": "6",
    }
    mock_client.set_panel_backlight.assert_not_awaited()


async def test_set_backlight_fields_with_no_fields_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An empty fields list would color nothing and report success, so it is refused."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(
            hass, BACKLIGHT_ENTITY_ID(hass), [], (10, 20, 30, 40)
        )
    assert excinfo.value.translation_key == "panel_no_fields"
    mock_client.set_panel_backlight.assert_not_awaited()


async def test_set_status_fields_accepts_a_field_within_the_backlight_count(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The status light validates against BACKLIGHT_RGBW, not its own STATUSLIGHT_RGB.

    ``with_panel_colors`` gives the fixture panel ``BACKLIGHT_RGBW: 6`` (the
    real touch field count) and ``STATUSLIGHT_RGB: 3``, values no panel
    reports on real hardware but chosen here so a passing check proves
    which capability it read. Field 5 is past a 3-field range but within
    the real 6-field one, so acceptance here proves the check reads the
    field count, not the status light's own capability value.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_status_fields(hass, STATUS_LIGHT_ENTITY_ID(hass), [5], (10, 20, 30))
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 10, 20, 30, fields=[5]
    )


async def test_set_status_fields_out_of_range_names_the_backlight_count(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A status-light field beyond BACKLIGHT_RGBW's count raises, naming that count.

    Not the status light's own ``STATUSLIGHT_RGB: 3``, a value no panel
    reports on real hardware. If the check read that capability instead,
    the message would wrongly claim a 3-field panel and field 5 above
    would have raised in the companion test.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_status_fields(hass, STATUS_LIGHT_ENTITY_ID(hass), [7], (10, 20, 30))
    assert excinfo.value.translation_key == "panel_field_out_of_range"
    assert excinfo.value.translation_placeholders == {"field": "7", "count": "6"}
    mock_client.set_panel_status_light.assert_not_awaited()


async def test_set_backlight_fields_at_the_panel_count_is_accepted(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The panel's own last field number is in range, not one past it."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID(hass), [6], (10, 20, 30, 40))
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 10, 20, 30, 40, fields=[6]
    )


async def test_set_backlight_fields_leaves_the_entity_state_unchanged(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A per-field call colors part of the panel and reports no new color.

    The backlight entity holds one color for the whole surface, so a call
    that colors only some of its fields must not make the entity claim a
    color the panel does not uniformly wear. The color the entity reports
    stays exactly what it was before the call.
    """
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    before = hass.states.get(BACKLIGHT_ENTITY_ID(hass))
    assert before is not None
    assert before.attributes[ATTR_RGBW_COLOR] == (64, 128, 191, 255)

    await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID(hass), [2], (1, 2, 3, 4))

    after = hass.states.get(BACKLIGHT_ENTITY_ID(hass))
    assert after is not None
    assert after.attributes[ATTR_BRIGHTNESS] == before.attributes[ATTR_BRIGHTNESS]
    assert after.attributes[ATTR_RGBW_COLOR] == (64, 128, 191, 255)


async def test_set_backlight_fields_uses_the_wire_ceiling_when_the_count_is_missing(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An absent capability count uses the wire's ceiling for field validation."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)
    del mock_client.capabilities[52111][ModuleFunction.BACKLIGHT_RGBW]

    await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID(hass), [20], (10, 20, 30, 40))
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 10, 20, 30, 40, fields=[20]
    )


async def test_set_backlight_fields_still_refuses_past_the_wire_ceiling(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An absent capability count still enforces the wire's field limit."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)
    del mock_client.capabilities[52111][ModuleFunction.BACKLIGHT_RGBW]

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(
            hass, BACKLIGHT_ENTITY_ID(hass), [99], (10, 20, 30, 40)
        )
    assert excinfo.value.translation_key == "panel_field_out_of_range"
    assert excinfo.value.translation_placeholders == {"field": "99", "count": "24"}
    mock_client.set_panel_backlight.assert_not_awaited()


def test_cct_kelvin_map_round_trips_every_byte() -> None:
    """Every coldness byte survives the trip to kelvin and back.

    Integer division does not: coldness 85 truncates to 3511 K, which
    maps back to 84.
    """
    minimum, maximum = 2000, 6535
    for coldness in range(256):
        kelvin = _kelvin_from_coldness(coldness, minimum, maximum)
        assert _coldness_from_kelvin(kelvin, minimum, maximum) == coldness

    assert _kelvin_from_coldness(0, minimum, maximum) == 2000
    assert _kelvin_from_coldness(85, minimum, maximum) == 3512
    assert _kelvin_from_coldness(128, minimum, maximum) == 4276
    assert _kelvin_from_coldness(255, minimum, maximum) == 6535


def test_cct_kelvin_map_clamps_out_of_range_requests() -> None:
    """A kelvin value outside the bounds writes a byte inside 0-255.

    ``set_ww`` raises ``AmpioValueError`` outside that range.
    """
    assert _coldness_from_kelvin(1000, 2000, 6535) == 0
    assert _coldness_from_kelvin(9000, 2000, 6535) == 255


async def test_cct_light_reads_both_axes(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A warm/cold white object reports one color-temperature mode."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(CCT_ENTITY_ID(hass))
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes[ATTR_COLOR_MODE] is ColorMode.COLOR_TEMP
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.COLOR_TEMP]
    assert state.attributes[ATTR_BRIGHTNESS] == 84
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 3512


async def test_cct_light_is_off_at_power_zero(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An off CCT light holds its color temperature, so the raw state is not zero.

    Proven on the wire: ``setWWPower 0`` moved the object from 65364 to
    65280, and ``AmpioObject.is_on`` reads that as on.
    """
    mock_client.objects[76] = replace(mock_client.objects[76], state="65280")
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(CCT_ENTITY_ID(hass))
    assert state is not None
    assert state.state == STATE_OFF


async def test_cct_light_brightness_writes_the_power_axis(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A brightness with no temperature moves the power axis alone."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass), ATTR_BRIGHTNESS: 200},
        blocking=True,
    )
    mock_client.set_ww_power.assert_awaited_once_with(76, 200)
    mock_client.set_ww.assert_not_awaited()


async def test_cct_light_temperature_writes_both_axes(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A requested color temperature writes both axes in one frame."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: CCT_ENTITY_ID(hass),
            ATTR_BRIGHTNESS: 200,
            ATTR_COLOR_TEMP_KELVIN: 4276,
        },
        blocking=True,
    )
    mock_client.set_ww.assert_awaited_once_with(76, 200, 128)


async def test_cct_light_temperature_alone_writes_the_coldness_axis(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color temperature with no brightness moves the coldness axis alone.

    The power axis is never read back and never sent, so a concurrent
    power change cannot be reasserted stale.
    """
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass), ATTR_COLOR_TEMP_KELVIN: 4276},
        blocking=True,
    )
    mock_client.set_ww_coldness.assert_awaited_once_with(76, 128)
    mock_client.set_ww.assert_not_awaited()
    mock_client.set_ww_power.assert_not_awaited()


async def test_cct_light_temperature_alone_from_dark_uses_full_power(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A dark CCT light asked for a color temperature alone still turns on.

    The coldness-alone verb holds the power axis, so it cannot be the one
    that turns a dark light on; the write falls back to ``set_ww`` with
    the full-power constant instead of a stale or absent power byte.
    """
    mock_client.objects[76] = replace(mock_client.objects[76], state="65280")
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass), ATTR_COLOR_TEMP_KELVIN: 4276},
        blocking=True,
    )
    mock_client.set_ww.assert_awaited_once_with(76, 255, 128)
    mock_client.set_ww_coldness.assert_not_awaited()


async def test_cct_light_bare_turn_on_keeps_the_last_power(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A turn-on naming neither axis repeats the power the light stands at."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_ww_power.assert_awaited_once_with(76, 84)


async def test_cct_light_bare_turn_on_from_dark_uses_full_power(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A dark CCT light turns on at full power rather than writing zero back."""
    mock_client.objects[76] = replace(mock_client.objects[76], state="65280")
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_ww_power.assert_awaited_once_with(76, 255)


async def test_cct_light_turn_off_routes_through_the_client(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A CCT light's turn_off awaits client.turn_off, like every other color mode."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(76)


def _push(mock_client: MagicMock, object_id: int, state: str) -> None:
    """Emit a state update for ``object_id`` as the bus would."""
    obj = replace(mock_client.objects[object_id], state=state)
    mock_client.objects[object_id] = obj
    emit(mock_client, ObjectUpdated(object=obj))


async def test_rgbw_bare_turn_on_replays_the_last_lit_channels(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A bare turn_on from dark sends the channels the output last held lit.

    The lit value arrives as a push, as a wall button or the Ampio app
    would send it, so the memory follows every source of change.
    """
    await setup_integration(hass, mock_config_entry)
    _push(mock_client, 72, str(40 | 5 << 8 | 20 << 24))
    _push(mock_client, 72, "0")
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 40, 5, 0, 20)


async def test_rgbw_color_from_dark_uses_the_last_level(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color with no brightness brings a dark output back at its last level."""
    await setup_integration(hass, mock_config_entry)
    _push(mock_client, 72, str(30 | 15 << 8))
    _push(mock_client, 72, "0")
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass), ATTR_RGBW_COLOR: (0, 0, 255, 0)},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 0, 0, 30, 0)


async def test_rgbw_brightness_from_dark_uses_the_last_color(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A brightness alone brings a dark output back in its last color."""
    await setup_integration(hass, mock_config_entry)
    _push(mock_client, 72, "0")
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass), ATTR_BRIGHTNESS: 120},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 30, 60, 90, 120)


async def test_rgbw_memory_survives_a_restart(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The last lit channels come back from the restore cache."""
    mock_restore_cache_with_extra_data(
        hass, [(State("light.salon_rgbw", STATE_OFF), {"rgbw": [5, 0, 0, 0]})]
    )
    mock_client.objects[72] = replace(mock_client.objects[72], state="0")
    await setup_integration(hass, mock_config_entry)
    assert RGBW_ENTITY_ID(hass) == "light.salon_rgbw"

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 5, 0, 0, 0)


async def test_dimmer_bare_turn_on_replays_the_last_level(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A bare turn_on from dark sends the level the dimmer last held lit."""
    await setup_integration(hass, mock_config_entry)
    _push(mock_client, 71, "0")
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 128, pulse_ms=None)
    mock_client.turn_on.assert_not_called()


async def test_timed_dimmer_replays_the_last_level_as_a_pulse(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A dimmer with a Designer time pulses at its remembered level."""
    mock_client.objects[71] = replace(mock_client.objects[71], czas=3000)
    await setup_integration(hass, mock_config_entry)
    _push(mock_client, 71, "0")
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 128, pulse_ms=30000)


async def test_dimmer_memory_survives_a_restart(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The last lit level comes back from the restore cache."""
    mock_restore_cache_with_extra_data(
        hass, [(State("light.taras_taras_led", STATE_OFF), {"level": 42})]
    )
    mock_client.objects[71] = replace(mock_client.objects[71], state="0")
    await setup_integration(hass, mock_config_entry)
    assert DIMMER_ENTITY_ID(hass) == "light.taras_taras_led"

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 42, pulse_ms=None)


async def test_cct_bare_turn_on_from_dark_replays_the_last_power(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A dark CCT light turns on at the power it last held lit."""
    await setup_integration(hass, mock_config_entry)
    _push(mock_client, 76, str(85 << 8))
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_ww_power.assert_awaited_once_with(76, 84)


async def test_cct_temperature_from_dark_uses_the_last_power(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A temperature alone brings a dark CCT light back at its last power."""
    await setup_integration(hass, mock_config_entry)
    _push(mock_client, 76, str(85 << 8))
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass), ATTR_COLOR_TEMP_KELVIN: 4276},
        blocking=True,
    )
    mock_client.set_ww.assert_awaited_once_with(76, 84, 128)


async def test_cct_memory_survives_a_restart(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The last lit power comes back from the restore cache."""
    mock_restore_cache_with_extra_data(
        hass, [(State("light.sypialnia_cct", STATE_OFF), {"level": 60})]
    )
    mock_client.objects[76] = replace(mock_client.objects[76], state=str(85 << 8))
    await setup_integration(hass, mock_config_entry)
    assert CCT_ENTITY_ID(hass) == "light.sypialnia_cct"

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CCT_ENTITY_ID(hass)},
        blocking=True,
    )
    mock_client.set_ww_power.assert_awaited_once_with(76, 60)
