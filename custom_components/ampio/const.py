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

# The M-SERV reserves this login for the administrator, and the Ampio app
# refuses to create a user under the name, so an account's login is what
# decides which surfaces the server serves it. ``AmpioAdminClient`` carries
# the reserved name itself and takes no username, and every other account
# passes its own. The config flow refuses a login the server answers as the
# other tier, because the spelling is the whole of the rule.
ADMIN_USERNAME: Final = "admin"

# The wire counts 10 ms ticks in a 16-bit field, so a single timed value on
# the bus tops out at 655.35 seconds. The buzzer's per-step ceiling and the
# touch lock's duration both come from this field.
MAX_WIRE_SECONDS: Final = 655.35

# Registry records the last setup left unclaimed, and could not explain.
STALE_RECORDS_ISSUE: Final = "stale_records"
# Entity records the administrator rule withholds from a standard account.
# Separate, because the integration knows exactly why these went.
ADMIN_ONLY_RECORDS_ISSUE: Final = "admin_only_records"
# Catalogue rows the library cannot address: an object whose Designer leaf
# is gone, or an override mac two module rows share. The installer fixes
# each one in Ampio Designer.
NOT_CONFIGURED_ISSUE: Final = "not_configured"

# The stem every module-keyed string is built from: the device identifier
# ``module_mac:<mac>`` and the entity unique id ``module_mac_<mac>_<suffix>``.
# One constant for both, so a module device and the entities on it cannot
# drift apart, and so the stale report can tell a record of this shape from
# one that predates it.
MODULE_KEY_STEM: Final = "module_mac"

# What every module entity's unique id starts with, ``module_mac_``. The
# entity builds its key on it and the stale report reads a record back
# through it, so the two cannot answer differently about what a module
# record looks like.
MODULE_KEY_PREFIX: Final = f"{MODULE_KEY_STEM}_"

# The stem of a scene entity's unique id, ``scene_<id>``. The platform
# mints it and the stale report reads it back, so one constant keeps the
# report from mistaking a scene record for a shape nothing mints.
SCENE_KEY_STEM: Final = "scene"
