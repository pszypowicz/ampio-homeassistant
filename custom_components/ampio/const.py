"""Constants for the Ampio integration."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "ampio"

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
]

DEFAULT_HOST: Final = "ampio.local"

# The wire counts 10 ms ticks in a 16-bit field, so a single timed value on
# the bus tops out at 655.35 seconds. The buzzer's per-step ceiling and the
# touch lock's duration both come from this field.
MAX_WIRE_SECONDS: Final = 655.35

# Registry records the last setup left unclaimed, and could not explain.
STALE_RECORDS_ISSUE: Final = "stale_records"
# Entity records the administrator rule withholds from a standard account.
# Separate, because the integration knows exactly why these went.
ADMIN_ONLY_RECORDS_ISSUE: Final = "admin_only_records"
