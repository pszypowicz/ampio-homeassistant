"""Runtime data for the Ampio integration."""

from dataclasses import dataclass

from ampio_mqtt import AmpioClient

from homeassistant.config_entries import ConfigEntry


@dataclass
class AmpioData:
    """Runtime data for one Ampio server."""

    client: AmpioClient
    # The server's identity key; scopes unique_ids and device identifiers so
    # two servers on one Home Assistant instance never collide.
    prefix: str
    hub_device_id: str


type AmpioConfigEntry = ConfigEntry[AmpioData]
