"""Tests for the Ampio light platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import (
    OUTPUT_KIND_KEYS,
    AccessTier,
    AmpioConnectionError,
    AmpioTimeoutError,
    ModuleFunction,
    ObjectUpdated,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from custom_components.ampio.light import LIGHT_MATTER_TYPES
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import setup_integration
from .conftest import (
    MSENS_IDENTIFIER,
    STATUS_COLOR,
    TOUCH_FIELD_COLOR,
    emit,
    module_pinned_id,
    pinned_id,
    set_access_tier,
    with_panel_colors,
    with_panel_settings,
)

DIMMER_ENTITY_ID = pinned_id("light", 71)
RGBW_ENTITY_ID = pinned_id("light", 72)
RELAY_ENTITY_ID = pinned_id("light", 73)
BACKLIGHT_ENTITY_ID = module_pinned_id("light", 17, "_backlight")
STATUS_LIGHT_ENTITY_ID = module_pinned_id("light", 17, "_status_light")


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

    The light platform takes ``dimmer`` and ``rgbw`` outright, plus ``relay``
    when its Matter tag is in ``LIGHT_MATTER_TYPES``. The cover kinds wait
    for the cover platform, and untagged relays wait for the switch platform.
    """
    assert {
        "relay",
        "dimmer",
        "rgbw",
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
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(73)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(73)


async def test_dimmer_brightness_maps_to_set_value(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning a dimmer on with a brightness sets the 0-255 level."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID, ATTR_BRIGHTNESS: 200},
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
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(73, 255, pulse_ms=120000)
    mock_client.turn_on.assert_not_called()

    mock_client.set_value.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID, ATTR_BRIGHTNESS: 180},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 180, pulse_ms=30000)


async def test_dimmer_turn_on_without_brightness(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning a dimmer on without a brightness uses the plain verb."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID},
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
            ATTR_ENTITY_ID: RGBW_ENTITY_ID,
            ATTR_RGBW_COLOR: (10, 20, 40, 80),
            ATTR_BRIGHTNESS: 160,
        },
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 20, 40, 80, 160)


async def test_rgbw_turn_on_from_dark_defaults_to_white(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning an all-zero rgbw on without arguments raises the white channel."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[72], state="0")
    mock_client.objects[72] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID},
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
            ATTR_ENTITY_ID: RGBW_ENTITY_ID,
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
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(72)


async def test_push_echo_updates_brightness(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed dimmer echo updates state and brightness."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[71], state="255")
    mock_client.objects[71] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    state = hass.states.get(DIMMER_ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 255


async def test_panel_lights_exist_with_both_capabilities(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A panel carrying both capabilities gets a backlight and a status light."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(BACKLIGHT_ENTITY_ID) is not None
    assert hass.states.get(STATUS_LIGHT_ENTITY_ID) is not None


async def test_panel_lights_report_assumed_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Neither panel light's color is confirmed, so both report assumed_state."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    backlight = hass.states.get(BACKLIGHT_ENTITY_ID)
    assert backlight is not None
    assert backlight.attributes[ATTR_ASSUMED_STATE] is True
    status_light = hass.states.get(STATUS_LIGHT_ENTITY_ID)
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
    backlight = entity_registry.async_get(BACKLIGHT_ENTITY_ID)
    assert backlight is not None
    assert backlight.device_id == module.id
    status_light = entity_registry.async_get(STATUS_LIGHT_ENTITY_ID)
    assert status_light is not None
    assert status_light.device_id == module.id


async def test_panel_light_built_only_for_its_own_capability(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module reporting only the backlight capability gets no status light."""
    module = mock_client.modules[17]
    mock_client.modules[17] = replace(
        module, capabilities={**module.capabilities, ModuleFunction.BACKLIGHT_RGBW: 6}
    )
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(BACKLIGHT_ENTITY_ID) is not None
    assert hass.states.get(STATUS_LIGHT_ENTITY_ID) is None


async def test_panel_lights_withheld_on_a_standard_account(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A standard account gets neither panel light, and withheld_unique_ids names both."""
    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(BACKLIGHT_ENTITY_ID) is None
    assert hass.states.get(STATUS_LIGHT_ENTITY_ID) is None
    withheld = mock_config_entry.runtime_data.withheld_unique_ids()
    assert withheld == {"module_17_backlight", "module_17_status_light"}


async def test_initial_color_reads_panel_settings(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Each entity's initial color reads its own panel_settings field."""
    with_panel_colors(mock_client)
    with_panel_settings(mock_client)
    await setup_integration(hass, mock_config_entry)

    backlight = hass.states.get(BACKLIGHT_ENTITY_ID)
    assert backlight is not None
    assert backlight.attributes[ATTR_RGBW_COLOR] == TOUCH_FIELD_COLOR
    status_light = hass.states.get(STATUS_LIGHT_ENTITY_ID)
    assert status_light is not None
    assert status_light.attributes[ATTR_RGB_COLOR] == STATUS_COLOR


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

    backlight = hass.states.get(BACKLIGHT_ENTITY_ID)
    assert backlight is not None
    assert backlight.state == STATE_OFF
    status_light = hass.states.get(STATUS_LIGHT_ENTITY_ID)
    assert status_light is not None
    assert status_light.state == STATE_OFF


async def test_backlight_turn_on_with_color_sends_it(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An explicit color is sent as the four backlight channels."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID, ATTR_RGBW_COLOR: (10, 20, 30, 40)},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 10, 20, 30, 40, fields=None
    )


async def test_status_light_turn_on_with_color_sends_it(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An explicit color is sent as the three status light channels."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID, ATTR_RGB_COLOR: (50, 60, 70)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 50, 60, 70, fields=None
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
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID},
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
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID},
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
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 0, 0, 255, fields=None
    )

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID},
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
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID, ATTR_RGBW_COLOR: (255, 0, 0, 0)},
        blocking=True,
    )
    mock_client.set_panel_backlight.reset_mock()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 128, 0, 0, 0, fields=None
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
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID},
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
    assert module.panel_settings is not None
    black_settings = replace(
        module.panel_settings, touch_field_color=(0, 0, 0, 0), status_color=(0, 0, 0)
    )
    mock_client.modules[17] = replace(module, panel_settings=black_settings)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 0, 0, 255, fields=None
    )

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID},
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
            ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID,
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
            ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID,
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
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 0, 0, 0, 0, fields=None
    )

    mock_client.set_panel_backlight.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID, ATTR_RGBW_COLOR: (0, 0, 0, 0)},
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
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 0, 0, 0, fields=None
    )

    mock_client.set_panel_status_light.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID, ATTR_RGB_COLOR: (0, 0, 0)},
        blocking=True,
    )
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 0, 0, 0, fields=None
    )


async def test_backlight_unknown_module_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module the catalogue cannot address raises, not a bare ValueError."""
    with_panel_colors(mock_client)
    mock_client.set_panel_backlight.side_effect = ValueError("module id 17 has no mac")
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID, ATTR_RGBW_COLOR: (1, 2, 3, 4)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "module_not_addressable"


@pytest.mark.parametrize(
    "error", [AmpioConnectionError("Not connected"), AmpioTimeoutError("no ack")]
)
async def test_backlight_command_failure_raises(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """A connection or timeout failure raises the backlight's own message."""
    with_panel_colors(mock_client)
    mock_client.set_panel_backlight.side_effect = error
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: BACKLIGHT_ENTITY_ID, ATTR_RGBW_COLOR: (1, 2, 3, 4)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "backlight_command_failed"


async def test_status_light_unknown_module_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module the catalogue cannot address raises, not a bare ValueError."""
    with_panel_colors(mock_client)
    mock_client.set_panel_status_light.side_effect = ValueError(
        "module id 17 has no mac"
    )
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID, ATTR_RGB_COLOR: (1, 2, 3)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "module_not_addressable"


@pytest.mark.parametrize(
    "error", [AmpioConnectionError("Not connected"), AmpioTimeoutError("no ack")]
)
async def test_status_light_command_failure_raises(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """A connection or timeout failure raises the status light's own message."""
    with_panel_colors(mock_client)
    mock_client.set_panel_status_light.side_effect = error
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: STATUS_LIGHT_ENTITY_ID, ATTR_RGB_COLOR: (1, 2, 3)},
            blocking=True,
        )
    assert excinfo.value.translation_key == "status_light_command_failed"


async def test_set_backlight_fields_sends_the_named_fields_and_color(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The service sends exactly the named fields and color, not every field."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID, [2, 4], (10, 20, 30, 40))
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 10, 20, 30, 40, fields=[2, 4]
    )


async def test_set_status_fields_sends_the_named_fields_and_color(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The service sends exactly the named fields and color, not every field."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_status_fields(hass, STATUS_LIGHT_ENTITY_ID, [1, 3], (50, 60, 70))
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 50, 60, 70, fields=[1, 3]
    )


async def test_set_backlight_fields_on_an_object_light_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An object-backed light drives no panel, and the service names the surface."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(hass, RGBW_ENTITY_ID, [1], (10, 20, 30, 40))
    assert excinfo.value.translation_key == "not_a_panel_backlight"
    mock_client.set_panel_backlight.assert_not_awaited()


async def test_set_status_fields_on_an_object_light_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An object-backed light drives no panel, and the service names the surface."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_status_fields(hass, RGBW_ENTITY_ID, [1], (10, 20, 30))
    assert excinfo.value.translation_key == "not_a_panel_status_light"
    mock_client.set_panel_status_light.assert_not_awaited()


async def test_set_backlight_fields_on_the_status_light_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The backlight service refuses the status light entity, naming the backlight."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(hass, STATUS_LIGHT_ENTITY_ID, [1], (10, 20, 30, 40))
    assert excinfo.value.translation_key == "not_a_panel_backlight"
    mock_client.set_panel_backlight.assert_not_awaited()


async def test_set_status_fields_on_the_backlight_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The status light service refuses the backlight entity, naming the status light."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_status_fields(hass, BACKLIGHT_ENTITY_ID, [1], (10, 20, 30))
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
            hass, BACKLIGHT_ENTITY_ID, [field], (10, 20, 30, 40)
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
        await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID, [], (10, 20, 30, 40))
    assert excinfo.value.translation_key == "panel_no_fields"
    mock_client.set_panel_backlight.assert_not_awaited()


async def test_set_status_fields_accepts_a_field_within_the_backlight_count(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The status light validates against BACKLIGHT_RGBW, not its own STATUSLIGHT_RGB.

    ``with_panel_colors`` gives the fixture panel ``BACKLIGHT_RGBW: 6`` (the
    real touch field count) and ``STATUSLIGHT_RGB: 3`` (an unrelated channel
    count) on purpose. Field 5 is past a 3-field range but within the real
    6-field one, so acceptance here proves the check reads the field count,
    not the status light's own capability value.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_status_fields(hass, STATUS_LIGHT_ENTITY_ID, [5], (10, 20, 30))
    mock_client.set_panel_status_light.assert_awaited_once_with(
        17, 10, 20, 30, fields=[5]
    )


async def test_set_status_fields_out_of_range_names_the_backlight_count(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A status-light field beyond BACKLIGHT_RGBW's count raises, naming that count.

    Not the status light's own ``STATUSLIGHT_RGB: 3`` - if the check read
    that capability instead, the message would wrongly claim a 3-field
    panel and field 5 above would have raised in the companion test.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_status_fields(hass, STATUS_LIGHT_ENTITY_ID, [7], (10, 20, 30))
    assert excinfo.value.translation_key == "panel_field_out_of_range"
    assert excinfo.value.translation_placeholders == {"field": "7", "count": "6"}
    mock_client.set_panel_status_light.assert_not_awaited()


async def test_set_backlight_fields_at_the_panel_count_is_accepted(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The panel's own last field number is in range, not one past it."""
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID, [6], (10, 20, 30, 40))
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

    before = hass.states.get(BACKLIGHT_ENTITY_ID)
    assert before is not None
    assert before.attributes[ATTR_RGBW_COLOR] == TOUCH_FIELD_COLOR

    await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID, [2], (1, 2, 3, 4))

    after = hass.states.get(BACKLIGHT_ENTITY_ID)
    assert after is not None
    assert after.attributes[ATTR_RGBW_COLOR] == TOUCH_FIELD_COLOR


async def test_set_backlight_fields_uses_the_wire_ceiling_when_the_module_row_is_missing(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A missing module row falls back to the wire's ceiling, not the panel's count.

    ``AmpioData.module_row`` returns None for a row that left the catalogue
    after this entity was built. Field 20 is past the panel's real 6-field
    count but within the ceiling every panel's frame can carry, so it still
    reaches the client.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)
    del mock_client.modules[17]

    await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID, [20], (10, 20, 30, 40))
    mock_client.set_panel_backlight.assert_awaited_once_with(
        17, 10, 20, 30, 40, fields=[20]
    )


async def test_set_backlight_fields_still_refuses_past_the_wire_ceiling(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A missing module row loses the panel's own count, not the wire's hard ceiling.

    Without this bound, field 99 would reach ``set_panel_backlight``, which
    the real library refuses with a bare ``ValueError`` - a failure
    ``_publish_translated`` would mistranslate as an unaddressable module
    rather than an out-of-range field.
    """
    with_panel_colors(mock_client)
    await setup_integration(hass, mock_config_entry)
    del mock_client.modules[17]

    with pytest.raises(ServiceValidationError) as excinfo:
        await _set_backlight_fields(hass, BACKLIGHT_ENTITY_ID, [99], (10, 20, 30, 40))
    assert excinfo.value.translation_key == "panel_field_out_of_range"
    assert excinfo.value.translation_placeholders == {"field": "99", "count": "24"}
    mock_client.set_panel_backlight.assert_not_awaited()
