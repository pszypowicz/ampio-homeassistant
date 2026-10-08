"""Config flow for the Ampio integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

from ampio_mqtt import (
    AccessTier,
    AmpioAuthError,
    AmpioClient,
    AmpioConnectionError,
    AmpioServerInfo,
)
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from .const import ADMIN_USERNAME, CONF_BLEND_WHITE, DEFAULT_HOST, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

OPTIONS_SCHEMA = vol.Schema({vol.Optional(CONF_BLEND_WHITE, default=False): bool})


class AmpioConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ampio."""

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return AmpioOptionsFlow()

    async def _async_check(
        self, user_input: dict[str, Any]
    ) -> tuple[AmpioServerInfo | None, dict[str, str]]:
        """Validate one set of credentials, and name the failure.

        Returns the server identity and no errors, or None and the one
        error key the form shows. ``AmpioTimeoutError`` subclasses
        ``AmpioConnectionError``, so a slow broker and a transport failure
        read alike: something is wrong with the connection, not the
        account.

        The account tier is the login name, and setup gives the reserved
        name the administrator client and every other name the standard
        one. The server decides the same question from the account id it
        answers with, so the two verdicts have to agree before the name is
        stored. They disagree when the administrator signs in under a
        spelling that is not the reserved one, which setup would serve as
        a standard account, taking every administrator entity with it and
        saying nothing. Every step that takes credentials comes through
        here, so create, reauth and reconfigure are all held to it.
        """
        try:
            info = await AmpioClient.check_connection(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
        except AmpioAuthError:
            return None, {"base": "invalid_auth"}
        except AmpioConnectionError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected exception")
            return None, {"base": "unknown"}
        else:
            if (info.access_tier is AccessTier.ADMIN) != (
                user_input[CONF_USERNAME] == ADMIN_USERNAME
            ):
                return None, {CONF_USERNAME: "admin_login_name"}
            return info, {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info, errors = await self._async_check(user_input)
            if info is not None:
                await self.async_set_unique_id(info.server_key)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or {CONF_HOST: DEFAULT_HOST}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Answer the credential rejection the running entry reported."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the credentials to use after a rejection."""
        return await self._async_step_credentials(
            user_input, "reauth_confirm", self._get_reauth_entry()
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a new address or a new account for the entry."""
        return await self._async_step_credentials(
            user_input, "reconfigure", self._get_reconfigure_entry()
        )

    async def _async_step_credentials(
        self, user_input: dict[str, Any] | None, step_id: str, entry: ConfigEntry
    ) -> ConfigFlowResult:
        """Point the entry at a host and an account, from one shared form.

        The reauth dialog and the reconfigure dialog differ in their step
        id alone, which is what gives each its own title and description.
        The entry id does not move, so the reload keeps every device
        record and every entity record. A host that answers with a
        different M-SERV aborts the flow.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            info, errors = await self._async_check(user_input)
            if info is not None:
                await self.async_set_unique_id(info.server_key)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=user_input,
                    title=user_input[CONF_HOST],
                )

        # The first form offers what the entry holds, minus the password.
        # A retry after an error offers what the user submitted, as the
        # user step does.
        suggested = user_input or {
            CONF_HOST: entry.data[CONF_HOST],
            CONF_USERNAME: entry.data[CONF_USERNAME],
        }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested
            ),
            errors=errors,
        )


class AmpioOptionsFlow(OptionsFlowWithReload):
    """Choose how rgbw outputs present their white channel.

    A changed option reloads the entry, because a light's supported color
    modes are fixed when it is built.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the one switch, or store it."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
