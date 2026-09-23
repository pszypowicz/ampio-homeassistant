"""Repair flows for the Ampio integration."""

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    ADMIN_ONLY_RECORDS_ISSUE,
    DOMAIN,
    NOT_CONFIGURED_ISSUE,
    STALE_RECORDS_ISSUE,
)
from .data import AmpioConfigEntry
from .stale import async_remove_stale_records


class StaleRecordsRepairFlow(RepairsFlow):
    """Confirm, then delete the records the issue that opened this flow names."""

    def __init__(self, entry: AmpioConfigEntry) -> None:
        """Bind the flow to the one Ampio entry."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Go straight to the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show what goes, and delete it on submit.

        The records are read again at submit time, because the list in the
        issue is as old as the last setup. The reload that follows rebuilds
        a moved object's child under its new module.
        """
        if self._entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            async_remove_stale_records(self.hass, self._entry, self.issue_id)
            # The reload decision names both issue ids removal handles, and
            # does nothing for any other, matching what async_remove_stale_
            # records itself deletes. A repair id neither of them recognizes
            # deletes no record, so there is nothing here for a reload to
            # rebuild.
            if self.issue_id == STALE_RECORDS_ISSUE:
                # A moved object's child is rebuilt under its new parent.
                self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
            elif self.issue_id == ADMIN_ONLY_RECORDS_ISSUE:
                # A withheld record rebuilds nothing: the account is
                # still a standard one, so its entities stay withheld.
                pass
            else:
                # async_remove_stale_records already logged that nothing
                # was removed for this id.
                pass
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=issue.translation_placeholders if issue else None,
        )


class NotConfiguredRepairFlow(RepairsFlow):
    """Explain the Ampio Designer fix for the refused rows, and delete nothing.

    The registry records of a refused row are what the entities come back
    under once the installer corrects the project, so this flow offers no
    deletion and touches neither registry. Submitting closes the notice.
    The library reports a change of the refused set and nothing else, so
    a project that still reads the same way raises the notice again at
    the next setup, or when the refused set changes.
    """

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Go straight to the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show which rows the server could not serve, and close on submit.

        The rows are read off the issue rather than from the runtime data,
        so an install whose entry is unloaded is still told what to fix.
        """
        if user_input is not None:
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=issue.translation_placeholders if issue else None,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for whichever repair issue opened it.

    The installer repair gets a flow that deletes nothing, because its
    records are the ones the install still needs. Handing it to the
    deleting flow would offer a button that throws away exactly what comes
    back when Ampio Designer is fixed.

    One config entry is allowed, so the issue names none. The deleting flow
    aborts on its own when that entry is not loaded.
    """
    if issue_id == NOT_CONFIGURED_ISSUE:
        return NotConfiguredRepairFlow()
    entry: AmpioConfigEntry = hass.config_entries.async_entries(DOMAIN)[0]
    return StaleRecordsRepairFlow(entry)
