"""The Ampio integration."""

from ampio_mqtt import AmpioClient

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .coordinator import AmpioConfigEntry, AmpioLocalCoordinator
from .entity import module_device_info


async def async_setup_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Set up Ampio from a config entry."""
    client = AmpioClient(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = AmpioLocalCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    mserv_id = coordinator.client.mserv_id
    if mserv_id is None:
        # The broker accepted the connection but never reported a server
        # identity. Only realistic cause is an account without permission to
        # read the Ampio config; surface as reauth rather than retry forever.
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        )

    # client.start() blocks until the initial discovery completes, so the
    # M-SERV module is present in coordinator.client.modules by the time we
    # reach this point. Pre-register so any subsequent module's via_device
    # link resolves regardless of the order discovery messages arrive in.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **module_device_info(coordinator, mserv_id),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
