"""Button platform for the Ampio integration."""

from collections.abc import Callable, Coroutine
from datetime import datetime
import logging
from typing import Any, Final, override

from ampio_mqtt import (
    AmpioConnectionError,
    AmpioObject,
    AmpioTimeoutError,
    ModuleFunction,
)
import voluptuous as vol

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import VolDictType

from .const import DOMAIN, MAX_WIRE_SECONDS
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, AmpioModuleEntity, async_turn_on_honoring_pulse

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
    data: AmpioData, module_id: int
) -> list[AmpioIdentifyButton]:
    """The button platform's entities for one module device."""
    return [AmpioIdentifyButton(data, module_id)]


def build_touch_unlock_buttons(
    data: AmpioData, module_id: int
) -> list[AmpioTouchUnlockButton]:
    """The button platform's touch-lock entities for one module device.

    A standard account receives no module catalogue, so the capability is
    unknowable there. This answers for every row on that tier, which only
    the withheld enumeration ever reads: the tier gate means nothing is
    built from it, and a bare capability check would leave an orphaned
    record in the repair card meant for a Designer deletion.
    """
    if not data.is_admin:
        return [AmpioTouchUnlockButton(data, module_id)]
    module = data.module_row(module_id)
    if module is None or ModuleFunction.KEY_LOCK not in module.capabilities:
        return []
    return [AmpioTouchUnlockButton(data, module_id)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the button platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_buttons, async_add_entities)
    entry.runtime_data.async_add_module_platform(
        build_identify_buttons, async_add_entities, admin_only=True
    )
    entry.runtime_data.async_add_module_platform(
        build_touch_unlock_buttons, async_add_entities, admin_only=True
    )
    entity_platform.async_get_current_platform().async_register_entity_service(
        "lock_touch", LOCK_TOUCH_SCHEMA, "async_lock_touch"
    )


class _NoTouchLock:
    """Refuses the touch-lock service, for a button that is not a panel's.

    Home Assistant resolves an entity service by attribute lookup, so a
    button without the method fails with an unhandled error rather than
    something a user can act on. Every Ampio button is offered in the
    service picker, because the target selector filters by integration and
    domain alone.
    """

    async def async_lock_touch(self, seconds: float) -> None:
        """Refuse a lock aimed at a button that drives no panel."""
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_touch_panel"
        )


class AmpioButton(_NoTouchLock, AmpioEntity, ButtonEntity):
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
        if obj is not None and obj.read_only:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="read_only_object",
            )
        await async_turn_on_honoring_pulse(self._data.client, obj, self._object_id)


class AmpioIdentifyButton(_NoTouchLock, AmpioModuleEntity, ButtonEntity):
    """Lights a module's CAN LED, so that the module can be found by eye.

    The Designer's "Identify device" button. The frame rides the CAN write
    tree, which answers the administrator login alone, so this entity is
    built on that account alone. The module holds identify until a stop
    frame, which this entity sends after ``IDENTIFY_HOLD_SECONDS``. No
    readback exists, so the state is never more than the connection.
    """

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, data: AmpioData, module_id: int) -> None:
        """Attach to the module device, and start with no stop pending.

        The identify frame is addressed by the Designer row id, which is the
        fact that makes it the button's whole key.
        """
        super().__init__(data, module_id, key_suffix="identify")
        self._cancel_stop: Callable[[], None] | None = None

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Send a pending stop now, so that an unload leaves no LED lit."""
        if self._cancel_pending_stop():
            await self._async_stop()

    @override
    async def async_press(self) -> None:
        """Send the identify start, and schedule the stop.

        A row the catalogue cannot address surfaces as an error with a
        message rather than a bare ValueError.
        """
        try:
            await self._data.client.identify(self._module_id)
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
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
            await self._data.client.identify_stop(self._module_id)
        except AmpioConnectionError, AmpioTimeoutError, ValueError:
            _LOGGER.warning(
                "Could not send the identify stop to Ampio module %s; its LED "
                "stays lit until Ampio Designer sends one or the module restarts",
                self._module_id,
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

    def __init__(self, data: AmpioData, module_id: int) -> None:
        """Attach to the module device of Designer row ``module_id``."""
        super().__init__(data, module_id, key_suffix="unlock_touch")

    @override
    async def async_press(self) -> None:
        """Release the panel's touch lock now."""
        await self._send(
            self._data.client.unlock_panel(self._module_id), "touch_unlock_failed"
        )

    async def async_lock_touch(self, seconds: float) -> None:
        """Make the panel ignore every touch for ``seconds``.

        The lock always expires, so a long hold is an automation that
        repeats rather than a latch this entity keeps.
        """
        await self._send(
            self._data.client.lock_panel(self._module_id, seconds=seconds),
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
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
            ) from err
        except (AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=failure_key
            ) from err
