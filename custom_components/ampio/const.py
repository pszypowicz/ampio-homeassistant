"""Constants for the Ampio integration."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "ampio"

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

DEFAULT_HOST: Final = "ampio.local"
