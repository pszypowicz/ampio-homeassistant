# Debugging

Two tools answer most questions: the diagnostics download shows what the integration sees, and debug logging shows what the connection does. Start with diagnostics - the log is thin by design.

## The diagnostics download

Settings -> Devices & Services -> Ampio -> the three-dot menu on the entry -> Download diagnostics. The file is JSON with three blocks:

- `entry_data`: the config entry with host, username, and password redacted.
- `snapshot`: the library's health report.
  - `access_tier`: `admin` or `restricted` - the account class the server assigned to your login. Several features differ by tier (module metadata, Designer locations), so check this first.
  - `available`, `auth_failure`: whether the broker connection is up, and the rejection reason when the connection loop stopped for auth.
  - `server_info`: the M-SERV self-report (the LAN address is redacted).
  - `connection`: liveness counters - `started_at`, `reconnect_count`, `last_message_at`, `last_error`, `subscribe_failures` (a topic the broker rejected, mapped to its reason code, usually a grant problem), and `protocol_violations` (a topic whose reply came in a shape the library would not read, mapped to the reason - the connection stays up regardless). Each topic in both maps carries a placeholder where the account name sat, not your real username.
  - `mac_collisions`: override MACs shared by more than one module row.
  - `params_gap`: objects the params table carries no row for, so every Designer config flag on them reads as unset. A non-empty list marks a server fault, since that table is meant to cover the whole catalogue. Always empty on the administrator login, whose catalogue carries the flags inline.
  - `modules`: one row per module in the catalogue, sorted by id, with the module id, MAC, type, model, `last_seen`, `supply_voltage`, and `temperature`. The last-seen time is in epoch seconds. It is the local time of the last live message from the module. It stays empty after a connect until a real push arrives, because the replay of stored values does not count. The two health values come from the module's own broadcast and stay empty on a restricted login. Not every module type sends that broadcast, see [faq.md](faq.md#a-module-shows-no-last-seen-time-in-the-diagnostics).
  - `last_payloads`: one entry per server endpoint. Each entry holds the shape of the endpoint's last reply rather than the reply itself. A table reply reads as a row count, and a reply the library could not parse reads as `**REDACTED**`. The `info` entry reads as `**REDACTED**` too. The library's own safelist would keep the server version and MAC there. The integration masks the whole value again, because neither belongs in a bug report. The parsed `server_info` above already carries the debugging value. A table reply is summarized because it carries device names, room names, and the account's street address. A key-based redactor cannot reach inside one string.
- `designer_config`: the Designer's stored configuration behind several entities, not their live state. The values inside both lists depend on an administrator-only sweep, but the `covers` list itself is present on both tiers, as the bullets below explain:
  - `modules`: one entry per module in the catalogue, sorted by id, with its `id`, its `capabilities` map, and its `panel_settings`. A capability id reads as a name where the library names it (`BUZZER`, `ROLLER`, and the rest of `ModuleFunction`) and as a number where it does not, and the paired count is a channel count, not a flag - this is what answers "why does this module have no buzzer" or "why does this panel refuse field 7". `panel_settings` is what the Backlight and Status light entities start from, and it reads `null` on a module whose panel layout the library has not proven. Its `light_signal` field is named the same way a capability id is, as `PanelLightSignal` values where the library knows them, so do not expect the raw numbers the library model holds. This whole list is empty on a standard account, because the M-SERV serves the module catalogue to the administrator login alone.
  - `covers`: one entry per cover object that Ampio Designer still shows, with its `id` and its stored `cover_parameters` or `null`. A hidden or a system cover is absent from the list rather than listed with `null`. This list is not empty on a standard account: an object's kind comes from the catalogue both accounts receive, so every visible cover still appears, each one reporting `null` because the sweep that fills the travel configuration is administrator-only. `cover_parameters` is what the Designer holds for a roller's open and close time, calibration, and slat timing, so it also reads `null` on an administrator account for a cover on a board whose roller layout the library has not proven.

### Reading the raw catalogue

The diagnostics download no longer carries catalogue rows. To read your own object's row, request the catalogue from the M-SERV with an MQTT client.

On the administrator login, publish `devicesDetails` to `ampio/control/<user>/config` and read the reply on `ampio/fromDB/<user>/config/devicesDetails`. On a standard account, publish `devices` to `ampio/control/<user>/data` and read the reply on `ampio/fromDB/<user>/data/devices`. Replace `<user>` with your Ampio login in both.

The fields the integration classifies from:

- `typ_komponentu`: the object type (`przekaznik`, `flaga`, `roleta`, ...) - decides the base kind.
- `type`: the Matter device-type tag mirror - `"256"` (0x0100) marks a relay as a light. An empty value on a relay you tagged in Designer is the half-existing-tag case: see [designer-quirks.md](designer-quirks.md).
- `params`: the Designer flag bitfield - bit 4 hides an object, bit 6 marks it read-only.
- `leafId`: the module output the object drives - it is one of two ways an object joins its Designer record. The device tree reads `id_urzadzenia`, the Designer module row, instead. An empty `leafId` (Designer's Matter box unchecked) costs neither: the record join falls back to that same module row and the channel number. See [designer-quirks.md](designer-quirks.md) for the one case that does lose the record.

Privacy note before sharing a download publicly: credentials, host, and LAN address are redacted, and server replies are summarized rather than copied. Object, room, and module names still appear in the `designer_config` block and in the entity names your own screenshots show.

## Debug logging

Settings -> Devices & Services -> Ampio -> Enable debug logging. The switch raises both `custom_components.ampio` and the `ampio_mqtt` library to debug; when you disable it again, Home Assistant offers the captured log as a download. The YAML equivalent:

```yaml
logger:
  default: warning
  logs:
    custom_components.ampio: debug
    ampio_mqtt: debug
```

What to expect in the log:

- Connection lifecycle: one warning when the broker connection is lost, one info line when it is restored, and an error with an automatic reload when the connection ends for good (for example after a credential change).
- Setup degradation warnings: a failed room-map fetch or a failed Designer-description sweep, each with its consequence.
- From the library at debug level: connection errors and messages it dropped as unparsable.

State questions ("why is this entity missing", "why is this a switch") are catalogue questions - answer them from the diagnostics download, not the log.
