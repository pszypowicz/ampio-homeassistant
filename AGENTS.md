# Working instructions for the Ampio integration

This repo is the Ampio integration for Home Assistant, distributed through HACS. It is the authoritative source. `home-assistant/core` PR #179548 is a possible future destination, not a constraint on today's design.

If you are about to make a change, read this whole file first.

## Mental model

- `custom_components/ampio/` is canonical. Nothing mirrors anything.
- The integration talks to the M-SERV only through `ampio-mqtt`. Protocol knowledge belongs in the library, not here.
- Design decisions answer to the users running this on real hardware. The quality rules below stand on their own merit; none of them is "because core would reject it".
- Upstreaming later means a rewrite of the shape, not a copy. That cost was accepted deliberately on 2026-08-28, when `main` had already diverged from the core branch on every shared file.

## Quality rules

These are not negotiable, and no HACS-only shortcut earns an exception:

- No synchronous I/O in setup. Everything on the event loop is async.
- Every platform ships tests at the sensor suite's depth, snapshots included.
- Fully typed. `mypy --strict` passes with no ignores.
- All MQTT goes through `ampio-mqtt`. No hand-rolled clients, no raw publishes.
- Dependencies stay at `ampio-mqtt` plus what Home Assistant already pulls in.
- `quality_scale.yaml` changes carry a reason in the commit message.
- Entities use `has_entity_name`, entity translations, and the `AmpioBaseEntity` base: `AmpioEntity` for an entity backed by one object, `AmpioModuleEntity` for one attached to a module device.
- This integration does not migrate. Every version is prepared as though it were a fresh install on a new system.

The last rule needs its reasoning spelled out, because the code it forbids is the code a careful contributor reaches for first. The integration ships as a beta, and no release carries an upgrade path. That rules out a one-time sweep over the registries, a compatibility read of a key an older release wrote, a carry-forward of a record's disabled state or its area, and a version stamp in the entry data to branch on. When a release changes something in a backward-incompatible way, that is said in the GitHub release notes and nowhere else, and the code stays as simple as it would be for an install that never saw the old shape.

**No code path accommodates a state that exists only between two versions.** A state an install can reach only by having run an earlier release is not a state this code knows about. There is no branch for it, no tolerance for it, and no helper that smooths it over. The rule holds until the first major release. Such a path is written once and read forever, so every later contributor has to keep it true, and the shape it was written for stops existing long before the code does.

When a release leaves an install in a state it cannot recover from on its own, the fix is a written instruction to the person running it. Sometimes that is removing the integration entry and adding it again. Sometimes it is a cleanup through the Home Assistant command line or the entity registry. The steps go in `docs/faq.md`, under a symptom a person would search for, and the GitHub release notes link to them. The release page is the only place a version transition is ever discussed, and the FAQ is where the steps live, so the same instruction is not maintained twice.

What an updating install meets short of that is what the repairs already answer for. A key no shape this release mints accounts for is reported as it stands, because the report names a cause rather than recognizing an old form. An object's child device that hangs under a parent the object no longer resolves to is held back, with its entities withheld and a warning naming the object, until that device goes. The stale-record repair on the Settings page clears both in one submit, and the reload behind it builds each object's child back under the parent it resolves to now, with its id, its name, and its area restored from the deleted record. A device registered under a new identifier is a new device, so it carries nothing over from the old one, and the release notes name what has to be set again.

That distinction is worth keeping straight. A repair that reports what no catalogue accounts for is general behavior, and it happens to catch a record an older release left. A branch that recognizes the older release's key is the forbidden thing, however similar the two look from the outside.

## Quality gates

The gate is `.pre-commit-config.yaml`: betterleaks, ruff check, ruff format, `mypy --strict`, `pytest`, codespell, and the file hygiene hooks. It runs twice. The pre-commit hook runs it on every commit, and the `pre-commit` job of `.github/workflows/ci.yaml` runs it on every PR. Protected `main` requires `pre-commit`, `hassfest`, and `hacs` before merge. Fix the CI, do not skip the check.

## Tier-independent identity rule

Ampio accounts upgrade and downgrade between the administrator login and app-created users, and a tier change must never change an entity's platform, a device's parent, or an entity id. Anything that decides an entity's platform or the device topology derives ONLY from data the restricted tier receives: the object catalogue's kinds, its `type` column, its `params` bits, its object names, and the bus address the door parses from every admitted row, plus the app room tables (`groups`, `group_devices`) for the one-shot area seed. A data source that could flip a platform or move a device between parents on a tier change, or on a degraded boot, is forbidden. The description sweep never mutates the shared catalogue fields, so the partition reads `AmpioObject.matter_device_type` directly and `record.matter_device_type` must never feed it; see `docs/designer-quirks.md` for the user-facing statement.

**The library's admission door owns eligibility, and this repo has no filter of its own.** The door runs in one order on both tiers. A hidden row drops first, because nothing drives it. The two rows the M-SERV creates for itself drop on their type. Every remaining row must carry a Designer leaf, and a row whose leaf is empty is recorded by id and left out. So `client.objects` already holds exactly the rows the platforms build for. Do not add a predicate over it, and do not test a row for the hidden bit or the system type, because the library answered both before it handed the catalogue over and there is no `visible` or `is_system` field left to read.

**A refused row is an Ampio Designer fault, not a deletion.** `AmpioNotConfigured` reports one at connect, and the `NotConfigured` event reports every later change of the refused set. Setup runs on what was served, so one unchecked Matter box cannot take the install offline, and the `not_configured` repair names the row ids and the Designer fix. That repair deletes nothing, because the registry records are what the entities come back under when the installer checks the box again, and `find_stale_records` keeps them out of all three deletion lists while the repair stands. The same exception carries the second fault, an override mac more than one module row claims. Ids alone survive the catch: `RefusedRows.from_error` drops the Designer names the exception's message embeds, and `docs/debugging.md` promises that no object, room, or module name leaves the install.

**The module device keys on the module's override mac, written in hex.** `const.MODULE_KEY_STEM` builds both strings and `ampio_mqtt.format_mac()` writes the address in every one of them, so the device identifier `module_mac:0xCB8F` and the module entity unique id `module_mac_0xCB8F_<suffix>` cannot drift apart, and a record an older key minted cannot read as a match. One function owns the base, so a repair, a device name, a log line, the identities and the `designer_config` block of the diagnostics all read alike, and nobody converts by hand between what Designer shows and what Home Assistant stores. The mac rides `AmpioObject.address` on both tiers, parsed from the leaf by the door, so the tree never reads the module catalogue to decide shape. An object's parent is the module device on its `address.mac`, and the M-SERV's own objects reach the hub through `obj.is_server_owned`. `AmpioData.module_row_for()` tolerates a mac the catalogue has no row for, because Ampio Designer deletes a device at once and only unassigns its objects into a collapsed UNGROUPED section, and the device then takes `Ampio module 0xCB8F` as its name.

**The Designer row id is command addressing, never identity.** It is reassigned when a module is replaced, while Designer re-stamps the override mac onto the replacement, so a device keyed on the row id is orphaned by a swap, and every entity on it with it. `AmpioModuleEntity._require_module_id()` resolves the row id before each administrator command and never caches it. A read does not use it at all: a surface that reports module data asks `module_row_for()` with the entity's mac and reports None on None, which reads as `unknown` while the broker is connected. `AmpioModuleEntity.available` follows the connection and nothing else, so a missing catalogue row never makes an entity unavailable.

A child device cannot be re-parented, so an object moved to another module in Designer is followed by a delete of its device, which `async_remove_config_entry_device` permits whenever the child's parent differs from the one `AmpioData.parent_for()` in `data.py` gives now, which the hook reads through the `expected_parent` map `live_identifiers()` builds. Until that delete lands, `_expected_entities()` holds the object's entities back and logs a warning naming the object.

**One carve-out, and it is narrow: the tier-gated capability entity.** The M-SERV serves some surfaces to the administrator login alone: the CAN write tree, the module catalogue, the description-record sweep, the panel settings, and the module diagnostics broadcast. An entity that exists only to reach one of those is not built at all on a standard account. The alternative is an entity that can never do its job, and on a sensor there is no press to reject and no way to say why. Such an entity attaches to a module device, carries `EntityCategory.DIAGNOSTIC`, and holds no state another entity reads. `AmpioBuzzer` is the one exception to the category, decided on 2026-09-13: the category hides an entity from the device page's Controls row and from area cards, which suits configuration set once, and a siren is a control a person may want on a dashboard. Everything else on that list is configuration or a reading, so the exception ends there. The gate is declared once, by registering through `AmpioData.async_add_admin_module_platform()` rather than `async_add_platform()`. That method narrows the tier in one place and hands the factory an `AmpioAdminClient`, so a factory body needs no tier guard and cannot be reached off-tier at all. The `admin_only_records` repair explains the disappearance and names the tier. The return needs nothing of its own, because the unique id does not move and the registry hands a restored record the entity id it already had, while no other entity holds that id. No object entity is gated this way, and the object partition still derives from tier-shared data alone.

**The account tier is the only axis, and it is a type.** `_build_client()` reads the stored login name and returns an `AmpioAdminClient` or an `AmpioClient`, so what an account receives is decided at construction, before any code asks. `AmpioData.admin` holds the narrowed reference or None, which makes an admin-only read off-tier a type error rather than a convention every call site has to honor. Both tiers receive the object catalogue in full shape: every admitted object carries its `address`, its `leaf_key`, its `params`, its `czas`, its name, and its state, and both tiers receive the app room tables. The administrator alone receives the module catalogue, module capabilities, panel settings, description records, module diagnostics, and the raw and CAN write trees. Code branches on the tier, never on whether a field happens to be populated. One `None` survives that rule and is not a tier in disguise: the module catalogue can carry no row for a mac a live object still names, for the Designer reason above, so every module lookup tolerates it.

**Home Assistant composes every entity id, and this repo writes none.** An id is minted once, at first registration, from the area name, the device name, and the entity name, in that order when the Entity ID format setting (Settings -> System) is unset (`EntityNamePart.AREA`, `DEVICE`, `ENTITY`), and any part that is empty is left out. That is why an object's primary entity, whose entity name is empty, reads as the area and the device name alone. The setting can leave out `AREA` or add `FLOOR`, and it cannot leave out `DEVICE` or `ENTITY`, because the settings websocket requires both. `AmpioBaseEntity` sets no `entity_id` and hands `entity_platform` no `suggested_object_id`, so an Ampio entity follows that setting like any other integration's, and `async_regenerate_entity_id()` rebuilds an id from the names in force when it is called. For an entity an older release pinned, the stored suggestion clears on the next load, because `async_get_or_create()` passes the absent suggestion straight into `_async_update_entity()`. Never write an `entity_id`, and never let a subclass build a unique id the base class did not: pass `key_suffix` instead.

What the names are is what keeps the tier rule true. An object child device takes the object's name, the Designer `opis_menu` column the library serves as `AmpioObject.name`, and its area is seeded from the app room, both of which reach either tier. A module device takes `nazwa_urzadzenia` from the admin-only module catalogue and falls back to `Ampio module 0xCB8F`, so a module's name follows the account tier, and the only entities that compose an id from it are the tier-gated ones, which are built on the administrator login alone. So a tier change moves no id, and no module entity is ever registered under a name the account is not served. A downgraded install can still hold module records whose ids the administrator login composed, until someone submits the `admin_only_records` repair that lists them. The hub stays `M-SERV`. Areas are seeded once, at a child device's first creation, and never moved afterwards. The Designer `location` is not the area map. A module device gets no seeded area at all, and a child device without one of its own inherits its parent's, so an object's effective area follows its module until someone sets the child's own.

Two consequences to keep in view. A pair of objects that share a Designer name land on one id and a `_2` suffix, which is Home Assistant's own collision handling and not something to work around here. An id also moves when the person running the install asks for it through the regenerate control, so no test and no release note may claim an Ampio id can never change.

**A remove and re-add inside 30 days keeps an entity id.** `async_get_or_create()` pops the matching `deleted_entities` record and restores its `entity_id` while no other entity holds it, along with its `name`, `area_id`, and `aliases`. A record deleted while the entry stands carries no orphan time and is kept as long as the entry is. Removing the entry stamps the orphan time, and `ORPHANED_ENTITY_KEEP_SECONDS` purges the record 30 days after that. So an install that ran an earlier release keeps the ids that release minted, and no release note may promise that an update replaces them. The only wholesale reset is deleting the registry records with core stopped, which is a support instruction, never an upgrade step.

## Object identity

Several Designer objects can drive one physical output, and every such view repeats the same `leafId`. So `AmpioObject.leaf_key` identifies the output, not the row. Entities key on `AmpioObject.object_key` (`obj_<id>`) with no server scope: object ids live in the Designer database, which moves to new hardware with the project, and the server mac does not. `single_config_entry` keeps them unique. Never key an entity on `leaf_key`, and never put the server mac into an identity.

The device tree is three deep: the hub (`hub`), one full device per module override mac (`module_mac:0xCB8F`), and one child device per object the door admitted (`obj_<id>`), registered through the entity's `ChildDeviceInfo`. The parent is the module device on the object's `address.mac`, or the hub for the M-SERV's own objects, chosen once at first creation. Some entities sit outside this scheme. `AmpioSceneEntity` is built from the scene catalogue rather than an object, sits on the hub, and carries the app's scene name. The rest are built per module device, key on the override mac in hex, and exist on the administrator login alone: `AmpioIdentifyButton` (`module_mac_0xCB8F_identify`), `AmpioTouchUnlockButton` (`module_mac_0xCB8F_unlock_touch`), `AmpioModuleSensor` (`module_mac_0xCB8F_voltage` and `module_mac_0xCB8F_temperature`), `AmpioBuzzer` (`module_mac_0xCB8F_buzzer`), `AmpioPanelBacklight` (`module_mac_0xCB8F_backlight`), and `AmpioPanelStatusLight` (`module_mac_0xCB8F_status_light`). They share `AmpioModuleEntity`, which owns the key, the unique id, the module device attachment, the row-id resolution the module commands take, the availability subscription, and `available`. A subclass passes `key_suffix` and nothing else about its identity, and one that adds a subscription of its own awaits the base's `async_added_to_hass` first.

## Nomenclature

The library names its fields after the surfaces it reads them from, so the public API speaks the Designer's own column names wherever a column has one: `nazwa_urzadzenia`, `typ_urzadzenia`, `wersja_softu`, `wersja_pcb`, `czas`, `lammel`, `state`, `measure_temp`. Two things the Designer names are reached another way. The object's `opis_menu` column arrives as `AmpioObject.name`, and an object carries its own Designer row id as `id` and no module row id. Its module is `address.mac`, and its identity is `object_key`, which is built from `id`. The command methods carry the wire verbs: `connect`, `disconnect`, `switch`, `set_value`, `set_colors`, `open`, `close`, `stop`, `set_roller_pos`, `set_roller_lamella`, `block_opening`, `block_closing`, `buzz`, `identify`.

**This repo translates that vocabulary to Home Assistant's at the entity boundary, and nowhere else.** Each Ampio name is read once, in one file, and assigned straight to its Home Assistant counterpart: `AmpioObject.name` to the child device's `name`, `nazwa_urzadzenia` to the module device's, `lammel` to `current_cover_tilt_position`, `wersja_softu` to `sw_version`, `measure_temp` to `current_temperature`, and so on. Do not add an adapter layer, a wrapper dataclass, or helper accessors. The boundary is a short list of assignments, and indirection over a handful of reads makes the integration harder to follow, not simpler. A Home Assistant method named `async_open_cover` calling a library method named `open` is the translation working, not a naming inconsistency.

One trap the rename introduced: `stop` moved from the client lifecycle to the cover command, and `disconnect` took over shutdown. `entry.async_on_unload(client.disconnect)` passes a bare callback, so a careless rename there registers the cover command as the shutdown hook. `test_setup_and_unload` asserts `disconnect` for exactly this reason.

## Adding a new platform

1. Branch off `main`. It is the protected default branch: PRs only, with the three CI checks (`pre-commit`, `hassfest`, `hacs`) required.
2. Add `custom_components/ampio/<platform>.py` and `tests/test_<platform>.py`, following `sensor.py` patterns.
3. Add `Platform.<NEW>` to `PLATFORMS` in `custom_components/ampio/const.py`.
4. Bump `version` in `manifest.json`, then release. Users install through HACS and report back.

## Release

`manifest.json` carries `version`, which HACS requires. Bump it in the feature PR, or in a small release PR when a feature PR forgets. Then tag and publish:

```sh
git tag vX.Y.Z <merge-sha> && git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <file>
```

The title must equal the tag: HACS prints the tag as the heading of the update dialog and appends the title when the two differ. Tag first. `gh release create --target` rejects a short SHA and reuses an existing tag silently, so check `git ls-remote origin "refs/tags/vX.Y.Z"` by output before tagging.

Release notes are user-facing prose for the person about to press update, so they read as an explanation rather than a list of commits. Lead with what breaks and what the reader has to do about it.

A release that leaves an install unable to recover on its own carries manual steps, and those steps go in `docs/faq.md` under a symptom someone would search for. The release notes link to that section rather than repeating it. This keeps one copy of an instruction that gets refined after real people follow it, and it means a reader who meets the symptom six months later finds the answer without knowing which release caused it. The release page is the only place a version transition is discussed at all, because the code carries nothing for it.

## Generated translation

`custom_components/ampio/translations/en.json` mirrors `strings.json` and is hand-maintained. There is no generator script, whatever the word "generated" suggests. Edit it in place on every `strings.json` change, keep the four-space indentation, and expand each `[%key:...%]` reference to its English text. `tests/test_translations.py` checks the pair for matching keys, for literal values copied through, and for references left unexpanded. It cannot tell whether an expanded reference carries the right English text.

## Issue routing

- Bugs in this integration: file and fix here.
- Bugs in the protocol layer or the models: file against `ampio-mqtt` and fix there. Working around a library bug in this repo does not count as fixing it.
- HACS packaging (manifest, CI, README, install): here.

## Tools and commands

- Gate venv: `./.venv` (uv-managed, gitignored). The mypy and pytest hooks start through `uv run --no-project`, which finds `.venv` in the checkout or in a parent directory, so a worktree inside the checkout needs nothing; a worktree elsewhere sets `VIRTUAL_ENV`. Install the hook with `pre-commit install` (a pre-commit from outside the venv); ruff and betterleaks come from their own pre-commit hooks, not the venv.
- Recreate the venv from the same two sources CI reads: `uv venv .venv --python 3.14 && uv pip install -p .venv -r requirements_test.txt "$(jq -r '.requirements[0]' custom_components/ampio/manifest.json)"`. mypy is pinned in `requirements_test.txt`.
- **Two pins, two files, both explicit. Nothing resolves a version at run time.** `requirements_test.txt` holds the Home Assistant release and the `pytest-homeassistant-custom-component` release that pins it; the pair moves together, because the plugin pins one exact core release. `custom_components/ampio/manifest.json` holds `ampio-mqtt`, so the gates test the version HACS installs. Never leave the plugin unpinned: its newest release tracks the next core beta, and an exact pre-release pin inside a dependency overrides pip's refusal to take pre-releases. Python 3.14.2 or newer is required, because the pinned Home Assistant release requires it, and the exact pin makes an install on an older Python fail.
- Keep local filesystem paths out of tracked Markdown and Python files. The `no-local-paths` hook in `.pre-commit-config.yaml` rejects them.

## When in doubt

Ask what serves the people running this on real hardware. If a change cannot be tested, cannot be typed, or cannot be explained in the commit message, do less, not more.
