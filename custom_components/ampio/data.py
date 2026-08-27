"""Runtime data for the Ampio integration."""

from dataclasses import dataclass, field

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
    # Registry ids of the pre-registered module parent devices, by module mac.
    module_device_ids: dict[int, str] = field(default_factory=dict)
    # Object id -> Ampio app room name, fetched once per setup.
    rooms: dict[int, str] = field(default_factory=dict)
    # Catalogue-column Matter tags, captured before the description sweep
    # refines the objects. The platform partition reads these and never the
    # sweep-refined field: the sweep answers the admin login only, and an
    # entity's platform must build identically on both account tiers.
    matter_tags: dict[int, int | None] = field(default_factory=dict)


type AmpioConfigEntry = ConfigEntry[AmpioData]
