# Debugging

Two tools answer most questions: the diagnostics download shows what the integration sees, and debug logging shows what the connection does. Start with diagnostics - the log is thin by design.

## The diagnostics download

Settings -> Devices & Services -> Ampio -> the three-dot menu on the entry -> Download diagnostics. The file is JSON. Home Assistant wraps every diagnostics download in its own report of the installation, the integration's manifest, its setup times, and any repair notices it raised, and what this integration contributes sits under `data` in three blocks:

- `entry_data`: the config entry with host, username, and password redacted.
- `snapshot`: the library's health report.
  - `available`, `auth_failure`: whether the broker connection is up, and the rejection reason when the connection loop stopped for auth.
  - `server_info`: the M-SERV self-report, with the LAN address and the hardware id redacted. Its `user_id` is the account class the server assigned your login: `-1` is the reserved administrator login, and any other value is an app-created standard account. Read it first, because the M-SERV serves several of the blocks below to the administrator login alone.
  - `connection`: liveness counters - `started_at`, `reconnect_count`, `last_message_at`, `last_error`, `subscribe_failures` (a topic the broker rejected, mapped to its reason code, usually a grant problem), and `protocol_violations` (a topic whose reply came in a shape the library would not read, mapped to the reason - the connection stays up regardless). Each topic in both maps carries a placeholder where the account name sat, not your real username. `last_error` carries the same placeholder in any topic it names, and it reads `**REDACTED**` where the broker host stood.
  - `mac_collisions`: override MACs shared by more than one module row, each with the Designer device ids that carry it. Present on the administrator login alone.
  - `params_gap`: objects the params table carries no row for, so every Designer config flag on them reads as unset. A non-empty list marks a server fault, since that table is the one source for those flags on both account tiers and is meant to cover the whole catalogue.
  - `not_configured`: the Designer rows the integration left out, each as `[id, **REDACTED**]`. These are the objects that point at no module output, and they have no entities at all. The second field is the row's Designer name, masked before the file is written, the id is the Designer object number, and the repair on the Settings page names the same ids and the fix for them.
  - `modules`: one row per module in the catalogue, sorted by id, with the module id, MAC, type, model, `last_seen`, `supply_voltage`, and `temperature`. The user-given module name stays out. The last-seen time is in epoch seconds. It is the local time of the last live message from the module. It stays empty after a connect until a real push arrives, because the replay of stored values does not count. The two health values come from the module's own broadcast, and not every module type sends that broadcast, see [faq.md](faq.md#a-module-shows-no-last-seen-time-in-the-diagnostics). Present on the administrator login alone.
  - `last_payloads`: one entry per server endpoint. Each entry holds the shape of the endpoint's last reply rather than the reply itself. A table reply reads as a row count, and a reply whose JSON or table envelope is malformed reads as `**REDACTED**`. The row count is taken before the library parses the rows, so a table can show a row count while the library refused the rows themselves. The `info` entry reads as `**REDACTED**` too. That value is one JSON string, and key-based redaction cannot reach inside a string. The integration therefore masks the whole value, and the parsed `server_info` above already carries the debugging value. A table reply is summarized because it carries device names, room names, and the account's street address.
- `designer_config`: the Designer's stored configuration behind several entities, not their live state. The stored values come from the module catalogue and the description-record sweep, which the M-SERV serves to the administrator login alone, but which objects are covers comes from the object catalogue both account tiers receive. So a standard account gets an empty `modules` list and a full `covers` list reading `null` throughout:
  - `modules`: one entry per module in the catalogue, sorted by id, with its `id`, its override `mac` in hex, its `capabilities` map, and its `panel_settings`. The MAC is what the module's device in Home Assistant is keyed on, so it is what joins an entry here to a device page, and it is the form the repair notices use as well. A capability id reads as a name where the library names it (`BUZZER`, `ROLLER`, and the rest of `ModuleFunction`) and as a number where it does not, and the paired count is a channel count, not a flag - this is what answers "why does this module have no buzzer" or "why does this panel refuse field 7". `panel_settings` is what the Backlight and Status light entities start from, and it reads `null` on a module whose panel layout the library has not proven. Its `light_signal` field is named the same way a capability id is, as `PanelLightSignal` values where the library knows them, so do not expect the raw numbers the library model holds.
  - `covers`: one entry per cover object the integration sees, sorted by id, with its `id` and its stored `cover_parameters` or `null`. The list of ids is what answers "why is my roller a switch", because an object missing from it was not classified as a cover. A hidden, a system, or a leafless row never reaches the integration, so it is absent from the list rather than listed with `null`. `cover_parameters` is what the Designer holds for a roller's open and close time, calibration, and slat timing. It reads `null` throughout on a standard account, because the sweep that fills it is administrator-only, and on an administrator account it reads `null` for a cover on a board whose roller layout the library has not proven.

### Reading the raw catalogue

The diagnostics download no longer carries catalogue rows. To read your own object's row, request the catalogue from the M-SERV with an MQTT client.

On either account tier, publish `devices` to `ampio/control/<user>/data` and read the reply on `ampio/fromDB/<user>/data/devices`. That reply carries every field below except `params`, so also publish `params_devices` to the same control topic and read `ampio/fromDB/<user>/data/params_devices`. Replace `<user>` with your Ampio login in both.

The fields the integration classifies from:

- `typ_komponentu`: the object type (`przekaznik`, `flaga`, `roleta`, ...) - decides the base kind.
- `type`: the Matter device-type tag mirror - `"256"` (0x0100) marks a relay as a light. An empty value on a relay you tagged in Designer is the half-existing-tag case: see [designer-quirks.md](designer-quirks.md).
- `params`: the Designer flag bitfield - bit 4 hides an object, bit 6 marks it read-only.
- `leafId`: the module output the object drives. It carries the MAC of the module that drives the object, and the device tree files the object's device under that MAC. An object that points at no output cannot be read or controlled, so a row with an empty `leafId` has no entities at all and appears in `snapshot.not_configured` instead. Designer clears the field when an object's Matter checkbox is unchecked and saved, see [designer-quirks.md](designer-quirks.md).

Privacy note before sharing a download publicly: credentials, the account name, the broker host, the LAN address, and the server's hardware id are redacted, and server replies are summarized rather than copied. The download itself carries no object, room, or module name. `designer_config` lists ids, MACs, capability names, panel colors and timings, and travel numbers. `snapshot.modules` carries no name field. Each `not_configured` pair keeps its id and reads `**REDACTED**` in place of the Designer name. The repair notices Home Assistant adds outside `data` carry an issue id and a date, not the text those notices show you, which is where the names of stale records appear. Names surface only in the entity names your own screenshots show.

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
- Setup degradation warning: a failed room-map fetch, with its consequence. A failed Designer-description sweep stops setup instead, and Home Assistant retries it, as described under [Setup does not finish, and stays on "Retrying setup"](faq.md#setup-does-not-finish-and-stays-on-retrying-setup).
- From the library at debug level: connection errors and messages it dropped as unparsable.

State questions ("why is this entity missing", "why is this a switch") are catalogue questions - answer them from the diagnostics download, not the log.
