"""Coordinator for the Ampio (local MQTT) integration."""

import logging

from ampio_mqtt import AmpioAuthError, AmpioClient, AmpioConnectionError, AmpioObject

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type AmpioConfigEntry = ConfigEntry[AmpioLocalCoordinator]


class AmpioLocalCoordinator(DataUpdateCoordinator[dict[int, AmpioObject]]):
    """Maintains the Ampio connection and pushes object updates to entities."""

    config_entry: AmpioConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AmpioConfigEntry, client: AmpioClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.client = client
        self.room_map: dict[int, str] = {}

    async def _async_setup(self) -> None:
        """Connect to the broker and start discovery (push-based)."""
        self.client.add_object_listener(self._handle_object)
        self.client.add_availability_listener(self._handle_availability)
        try:
            await self.client.start()
        except AmpioAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except AmpioConnectionError as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from err
        try:
            self.room_map = await self.client.fetch_rooms()
        except AmpioConnectionError:
            # Rooms are nice-to-have hints for DeviceInfo.suggested_area; a
            # broker that fails this round-trip just leaves devices unhinted.
            _LOGGER.debug("Failed to fetch Ampio room map", exc_info=True)

    async def _async_update_data(self) -> dict[int, AmpioObject]:
        """Return the current object snapshot (data arrives via push)."""
        return self.client.objects

    @callback
    def _handle_object(self, obj: AmpioObject) -> None:
        """Push an updated object snapshot to listeners/entities.

        ``client.objects`` is the live dict the library mutates in place, so
        passing the same reference every time is deliberate - copying or
        snapshotting would defeat the push design and add cost per message.
        """
        self.async_set_updated_data(self.client.objects)

    @callback
    def _handle_availability(self, available: bool) -> None:
        """Log connection availability transitions and refresh entities.

        Calling ``async_update_listeners`` (rather than ``async_set_updated_data``)
        re-evaluates each entity's ``available`` property without churning the
        cached data dict or flipping ``last_update_success``.
        """
        if available:
            _LOGGER.debug("Reconnected to Ampio broker")
        else:
            _LOGGER.warning("Lost connection to Ampio broker; retrying")
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Stop the client on unload."""
        await super().async_shutdown()
        await self.client.stop()
