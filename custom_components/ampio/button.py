"""Button platform for the Ampio integration."""

from collections.abc import Callable, Coroutine
from datetime import datetime
import logging
from typing import Any, Final, override

from ampio_mqtt import (
    AmpioAdminClient,
    AmpioConnectionError,
    AmpioObject,
    AmpioTimeoutError,
    AmpioValueError,
    ModuleFunction,
    format_mac,
)
import voluptuous as vol

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import VolDictType

from .const import DOMAIN, MAX_WIRE_SECONDS
from .data import AmpioConfigEntry, AmpioData
from .entity import (
    AmpioEntity,
    AmpioModuleEntity,
    async_turn_on_honoring_pulse,
    raise_if_read_only,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# How long a module holds identify after a press. Ampio Designer's own
# button stops after the same 30 s, so the LED behaves as installers know it.
IDENTIFY_HOLD_SECONDS: Final = 30

# The lock's duration field runs from 0.01 s to the wire's per-field
# ceiling. There is no indefinite form.
MIN_LOCK_SECONDS: Final = 0.01
MAX_LOCK_SECONDS: Final = MAX_WIRE_SECONDS

LOCK_TOUCH_SCHEMA: VolDictType = {
    vol.Required("seconds"): vol.All(
        vol.Coerce(float), vol.Range(min=MIN_LOCK_SECONDS, max=MAX_LOCK_SECONDS)
    ),
}


def is_button(obj: AmpioObject) -> bool:
    """Whether the object belongs to the button platform.

    Designer's bell checkbox (``params`` bit 15 on relays and flags) marks
    an object meant for a single press; the Ampio app renders it as a
    press-only button instead of a toggle. The bit is served to both
    account tiers, so it may decide the platform. Bell wins over the relay
    Matter tag: press-only display intent makes a toggle entity wrong
    however the output is tagged.
    """
    return obj.bell


def build_buttons(data: AmpioData, obj: AmpioObject) -> list[AmpioButton]:
    """The button platform's entities for one object."""
    return [AmpioButton(data, obj)] if is_button(obj) else []


def build_identify_buttons(
    data: AmpioData, admin: AmpioAdminClient, mac: int
) -> list[AmpioIdentifyButton]:
    """The button platform's entities for one module device."""
    return [AmpioIdentifyButton(data, admin, mac)]


def build_touch_unlock_buttons(
    data: AmpioData, admin: AmpioAdminClient, mac: int
) -> list[AmpioTouchUnlockButton]:
    """The touch-lock button for a module that supports the command."""
    if ModuleFunction.KEY_LOCK not in admin.capabilities.get(mac, {}):
        return []
    return [AmpioTouchUnlockButton(data, admin, mac)]


async def _async_lock_touch(entity: ButtonEntity, call: ServiceCall) -> None:
    """Lock a panel's touch fields, or say why this entity cannot.

    A callable handler, not the entity's own method name, because it must
    answer for every button entity the platform has - a bell button and an
    identify button included - and name the surface ``ampio.lock_touch``
    drives when the target is neither. Every Ampio button is offered in the
    service picker, because the target selector filters by integration and
    domain alone.
    """
    if not isinstance(entity, AmpioTouchUnlockButton):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_touch_panel"
        )
    await entity.async_lock_touch(call.data["seconds"])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the button platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_buttons, async_add_entities)
    entry.runtime_data.async_add_admin_module_platform(
        build_identify_buttons, async_add_entities
    )
    entry.runtime_data.async_add_admin_module_platform(
        build_touch_unlock_buttons, async_add_entities
    )
    entity_platform.async_get_current_platform().async_register_entity_service(
        "lock_touch", LOCK_TOUCH_SCHEMA, _async_lock_touch
    )


class AmpioButton(AmpioEntity, ButtonEntity):
    """A press-only control backed by a bell-marked Ampio object."""

    _attr_translation_key = "bell"

    @override
    async def async_press(self) -> None:
        """Send the single press the bell object is meant for.

        A configured Designer time makes the press a timed pulse; without
        one the press latches, matching the app. A Designer read-only
        object raises instead of sending a write the M-SERV would
        silently drop.
        """
        obj = self._object
        raise_if_read_only(obj)
        await async_turn_on_honoring_pulse(self._data.client, obj, self._object_id)


class AmpioIdentifyButton(AmpioModuleEntity, ButtonEntity):
    """Lights a module's CAN LED, so that the module can be found by eye.

    The Designer's "Identify device" button. The frame rides the CAN write
    tree, which answers the administrator login alone, so this entity is
    built on that account alone. The module holds identify until a stop
    frame, which this entity sends after ``IDENTIFY_HOLD_SECONDS``. No
    readback exists, so the state is never more than the connection.
    """

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, data: AmpioData, admin: AmpioAdminClient, mac: int) -> None:
        """Attach to the module device, and start with no stop pending."""
        super().__init__(data, mac, key_suffix="identify")
        self._admin = admin
        self._cancel_stop: Callable[[], None] | None = None

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Send a pending stop now, so that an unload leaves no LED lit."""
        if self._cancel_pending_stop():
            await self._async_stop()

    @override
    async def async_press(self) -> None:
        """Send the identify start, and schedule the stop."""
        module_id = self._require_module_id()
        try:
            await self._admin.identify(module_id)
        except (AmpioValueError, AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="identify_failed"
            ) from err
        self._cancel_pending_stop()
        self._cancel_stop = async_call_later(
            self.hass, IDENTIFY_HOLD_SECONDS, self._async_stop
        )

    @callback
    def _cancel_pending_stop(self) -> bool:
        """Cancel a scheduled stop, and say whether one was pending."""
        if self._cancel_stop is None:
            return False
        self._cancel_stop()
        self._cancel_stop = None
        return True

    async def _async_stop(self, _now: datetime | None = None) -> None:
        """Send the identify stop; a stop that fails leaves the LED lit, and says so."""
        self._cancel_stop = None
        try:
            module_id = self._require_module_id()
            await self._admin.identify_stop(module_id)
        except (
            AmpioConnectionError,
            AmpioTimeoutError,
            AmpioValueError,
            ServiceValidationError,
        ):
            _LOGGER.warning(
                "Could not send the identify stop to the Ampio module on mac %s "
                "(entity %s). Its LED stays lit until Ampio Designer sends one "
                "or the module restarts",
                format_mac(self._mac),
                self.entity_id,
            )


class AmpioTouchUnlockButton(AmpioModuleEntity, ButtonEntity):
    """Releases an Ampio touch panel's lock on its touch fields.

    A locked panel ignores every touch and broadcasts nothing at all, not
    even the press. The frame rides the CAN write tree, which answers the
    administrator login alone, so this entity is built on that account
    alone.

    The lock has no readback, so this entity holds no state. It is useful
    even when Home Assistant sent no lock, because a person can set one at
    the panel with its touch field combination.

    ``ampio.lock_touch`` carries the lock, because a press carries no
    duration and every lock expires.
    """

    _attr_translation_key = "unlock_touch"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, data: AmpioData, admin: AmpioAdminClient, mac: int) -> None:
        """Attach to the module device on override mac ``mac``."""
        super().__init__(data, mac, key_suffix="unlock_touch")
        self._admin = admin

    @override
    async def async_press(self) -> None:
        """Release the panel's touch lock now."""
        module_id = self._require_module_id()
        await self._send(self._admin.unlock_panel(module_id), "touch_unlock_failed")

    async def async_lock_touch(self, seconds: float) -> None:
        """Make the panel ignore every touch for ``seconds``.

        The lock always expires, so a long hold is an automation that
        repeats rather than a latch this entity keeps.
        """
        module_id = self._require_module_id()
        await self._send(
            self._admin.lock_panel(module_id, seconds=seconds),
            "touch_lock_failed",
        )

    async def _send(self, command: Coroutine[Any, Any, None], failure_key: str) -> None:
        """Await a panel write, turning its failures into messages.

        ``failure_key`` picks the message, because a lock that does not
        arrive leaves the panel usable while an unlock that does not arrive
        leaves it deaf until the lock runs out.
        """
        try:
            await command
        except (AmpioValueError, AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=failure_key
            ) from err
