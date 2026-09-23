# Ampio for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/pszypowicz/ampio-homeassistant)](https://github.com/pszypowicz/ampio-homeassistant/releases)
[![CI](https://github.com/pszypowicz/ampio-homeassistant/actions/workflows/ci.yaml/badge.svg)](https://github.com/pszypowicz/ampio-homeassistant/actions/workflows/ci.yaml)
[![License](https://img.shields.io/github/license/pszypowicz/ampio-homeassistant)](LICENSE)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![Maintainer](https://img.shields.io/badge/maintainer-%40pszypowicz-blue.svg)](https://github.com/pszypowicz)

A Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system. It talks to the local M-SERV controller over MQTT through the [`ampio-mqtt`](https://pypi.org/project/ampio-mqtt/) library. Local push, no cloud.

## Platforms

| Platform        | What you get                                                                                                                                                                                                                                                                                                        |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sensor`        | Temperature, humidity, pressure, CO2, air quality, illuminance, loudness, and every integer sensor slot, with the Designer unit where one is set (Modbus meters behind an M-CON-485), plus the supply voltage and temperature each module reports about itself (administrator login)                                |
| `binary_sensor` | Wired button inputs, the armed and triggered halves of an alarm partition behind an M-CON, and an Opening lock and a Closing lock reading for each cover                                                                                                                                                            |
| `light`         | Dimmers, RGBW outputs, warm/cold white (CCT) outputs, and relays tagged as lights in Ampio Designer, plus a Backlight and a Status light on each touch panel that reports the capability, with `ampio.set_backlight_fields` and `ampio.set_status_fields` actions to color named touch fields (administrator login) |
| `cover`         | Shutters and blinds, with position and slat tilt where the hardware has them, and an `ampio.set_roller_lock` action to hold or release the roller lock (administrator login)                                                                                                                                        |
| `switch`        | Remaining relays and Ampio flags, with the outlet class for plug-tagged ones                                                                                                                                                                                                                                        |
| `button`        | Relays and flags marked as bell objects in Ampio Designer (a single press), an Identify button on each module that lights its CAN LED, and an Unlock touch button on each touch panel that releases its touch lock, with an `ampio.lock_touch` action to set one (administrator login)                              |
| `climate`       | Heating regulators with temperature readback and operating-mode presets                                                                                                                                                                                                                                             |
| `scene`         | The Ampio app's scene catalog                                                                                                                                                                                                                                                                                       |
| `siren`         | The buzzer on each Ampio touch panel, with an `ampio.buzz_pattern` action for two-step sequences (administrator login)                                                                                                                                                                                              |
| `number`        | Ampio analog flags, the module's own 8-bit and signed 16-bit variables, bounded by the field width                                                                                                                                                                                                                  |

## Actions

Every entity id below is an example. Home Assistant composes each id when it first registers the entity, by default from the area name, the device name and the entity name, so your install has ids of its own. Look them up under Settings, then Devices and services, then Entities.

`ampio.buzz_pattern` plays a two-step sequence on a panel's buzzer. The siren entity covers a single tone for a single length, and this action reaches the rest of what the hardware frame carries.

Each cycle plays the first tone, then the second. Tone 0 is silence, so a doorbell of three pips is one tone, one silent step, and three cycles:

```yaml
action: ampio.buzz_pattern
target:
  entity_id: siren.m_sens_salon_buzzer
data:
  tone: 6
  seconds: 0.3
  tone2: 0
  seconds2: 0.3
  cycles: 3
```

Tones run from 0 to 31, and tone 6 is the loudest. Each step lasts up to 655.35 seconds, and `cycles` goes up to 254. Set `cycles` to 0 to repeat until you turn the siren off. Add `delay` to hold off before the first cycle starts, up to the same 655.35 second ceiling. It defaults to 0, a start with no wait. The action needs an administrator Ampio account, like the buzzer itself.

`ampio.lock_touch` makes a touch panel ignore every touch for a chosen length. A locked touch field reports nothing at all, not even the press:

```yaml
action: ampio.lock_touch
target:
  entity_id: button.m_sens_salon_unlock_touch
data:
  seconds: 30
```

The lock always expires and caps at 655.35 seconds, with no indefinite form. Nothing on the bus reports whether a panel is locked. To release one early, press the panel's Unlock touch button. A person at the panel can set or clear the lock there too, with its own touch field combination. The action needs an administrator Ampio account, like the button itself.

`ampio.set_backlight_fields` and `ampio.set_status_fields` color individual touch fields on a panel instead of the whole Backlight or Status light entity. Fields are numbered from 1:

```yaml
action: ampio.set_backlight_fields
target:
  entity_id: light.m_sens_salon_backlight
data:
  fields: [1, 2]
  rgbw_color: [255, 100, 100, 50]
```

The backlight's white channel drives the panel's own white LEDs rather than blending into red, green, and blue, so `[0, 0, 0, 255]` is plain white. A field number the panel does not have is refused, naming the panel's real field count rather than doing nothing. Coloring individual fields leaves the Backlight or Status light entity reporting whatever color it already held, because that entity holds one color for the whole surface and a partial change has no honest single color to report. Both actions need an administrator Ampio account, like the entities themselves.

Target these two actions at the entity by name, not at an area or a device, and not at `entity_id: all`. Any of those reaches every Ampio light the target matches and sends the field colors to each one. The Backlight and the Status light carry the diagnostic category, and Home Assistant leaves an entity with a category out of area and device targets, so an area target reaches only object lights, colors nothing at all, and returns exactly one error, since Home Assistant re-raises just the first exception rather than one per light. That error is Home Assistant's own handling of an action not every targeted entity supports, and there is no way to filter it out.

`ampio.set_roller_lock` holds or releases a cover's roller lock. The lock frame rides the raw CAN tree, which only the administrator login reaches:

```yaml
action: ampio.set_roller_lock
target:
  entity_id: cover.sypialnia_roleta_sypialnia
data:
  direction: opening
  blocked: true
```

`direction` is `opening`, `closing`, or `both`. `blocked` on holds that direction, off releases it. A lock the action sets never expires on its own, so call the action again with `blocked: false` to release it, or clear it from wherever it was set. The cover's Opening lock and Closing lock diagnostic binary sensors report which direction, if any, is currently held, on either account tier. On a standard account the action raises instead of doing nothing, naming the account tier as the reason.

`ampio.send_notification` pushes a message to every user of the Ampio mobile app on this installation, on either account tier:

```yaml
action: ampio.send_notification
data:
  message: Brama otwarta
```

The Ampio server answers on no topic, so a successful call means the message was sent, not that it arrived. A message cannot contain a slash: the server reads what follows one as a user name and drops the rest, and the action refuses such a message before it reaches the wire.

## Installation

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pszypowicz&repository=ampio-homeassistant&category=integration)

Click the badge, confirm the repository in HACS, install "Ampio", and restart Home Assistant.

Manual steps:

1. Open HACS in Home Assistant.
2. Add `https://github.com/pszypowicz/ampio-homeassistant` as a custom repository (type: Integration).
3. Install "Ampio" and restart Home Assistant.

Requires Home Assistant 2026.9.0 or newer. `ampio-mqtt` is installed automatically.

## Configuration

1. In the Ampio app, create a dedicated Home Assistant user and grant it the devices you want in Home Assistant.
2. In Home Assistant, go to Settings -> Devices & Services -> Add Integration -> Ampio.
3. Enter the M-SERV host and that user's MQTT credentials.

To change the address, the account, or the password later, open the entry and choose Reconfigure from its menu. Your devices and your entities keep their ids, their areas, and any name you gave them yourself. If the Ampio server rejects the stored password, Home Assistant asks you for a new one on its own.

Devices appear as a hub for the M-SERV, one device per Ampio module, and one device per Ampio object under its module. An object device takes its name and its area from the Ampio app when Home Assistant creates it, and the integration never moves it afterwards. Home Assistant builds each entity id once, when it first registers the entity, by default from the area name, the device name and the entity name in that order, and it leaves out any part that is empty. The Entity ID format setting under Settings, then System, can leave out the area or add the floor, and it cannot leave out the device name or the entity name. An id does not follow a later rename. See [docs/devices.md](docs/devices.md) for the names, the areas, and what a change in Ampio Designer does.

An RGBW output shows a color control and a separate white slider by default. A color change keeps the brightness the light has, and a light that is off turns on at full brightness. To get one color control instead, open the entry, choose Configure, and turn on "Blend the white channel into the color". The integration then sets the white channel from the part of the color that red, green, and blue share, so a pure white drives the white LEDs alone. The entry reloads when you save. With the option on, a scene or an automation that sets `rgbw_color` still works, but Home Assistant converts the value to one color first, so the light shows the nearest blended color and a white level set apart from the color is not kept. A brightness change keeps the channels the light holds, including a mix set from the Ampio app. The option does not change the touch panel Backlight.

One M-SERV per Home Assistant. Object ids are unique per server only, so the integration allows one entry.

## Updating

Every `0.0.x` release is beta. None of them carries a migration, so an update can change device names or the entity set with no upgrade path. Take a backup before you update, and read the release note. It leads with the breaking changes and the upgrade steps.

Home Assistant stores an entity id against the entity's unique id, so your ids survive an update unless the release note says otherwise. A release that changes what a device record is keyed on needs one step from you. Those devices are built again under the new key, and Home Assistant treats each one as a new device, so the name, the area and the labels you gave it start empty. The device of each object on a module keeps hanging under the old module device, and the object's entities keep working there, until you submit the repair on the Settings page titled "Ampio records to clean up", or "Ampio records to check" on a standard account. [docs/faq.md](docs/faq.md#my-module-devices-lost-their-names-areas-and-labels) walks through that repair.

If an update leaves you with missing entities or entities that stay unavailable, look for an Ampio repair on the Settings page first, and follow [docs/faq.md](docs/faq.md#entities-are-missing-or-stay-unavailable-after-an-update) from there. Removing the integration and adding it again comes after that. Home Assistant remembers a removed entity for 30 days, so a re-add restores your entity ids, your renames, and your areas.

If something else looks wrong, see [docs/faq.md](docs/faq.md). Each answer there tells you how to check whether it affects you, and how to fix it.

## Documentation

- [docs/faq.md](docs/faq.md): what to check and what to do when something looks wrong, from missing entities to entity ids.
- [docs/devices.md](docs/devices.md): the device tree, names, areas, entity ids, and what a change in Ampio Designer does.
- [docs/designer-quirks.md](docs/designer-quirks.md): a relay tagged as a light that shows as a switch, the Matter checkbox, an object moved to another module, and integer sensor slots.
- [docs/debugging.md](docs/debugging.md): the diagnostics download and debug logging, for a bug report.
- [docs/development.md](docs/development.md): the gate venv, the pre-commit hooks, and how to run the CI checks locally.

## Known limitations

- Scenes are read once at setup. A scene added in the app needs a reload.
- A module you add in Ampio Designer while the integration runs gets its capability map from another description sweep. When the module has capability data after that sweep, its buzzer, its Unlock touch button, and its Backlight and Status light appear within seconds. When it has none, the module gets its sensors and its Identify button only, and a warning in the log says that the module has no capability data and that a reload sweeps again.
- Two objects that carry the same name in Ampio Designer and share a room, or share having none, cannot share an entity id, so one of the two takes Home Assistant's `_2` suffix. Give such a pair distinct names in Designer if you want to tell them apart by their ids.

## Relationship to home-assistant/core

An earlier form of this integration is submitted to `home-assistant/core` as [PR #179548](https://github.com/home-assistant/core/pull/179548). This repository leads that submission, and reports from real installs are what will make the upstream version worth merging.

## Disclaimer

This integration is an independent, best-effort project and has no affiliation with Ampio. Use it at your own risk. It commands real hardware, and a wrong command moves real devices.

The M-SERV itself guarantees the safety of a standard account. The broker limits such an account to the objects granted in the Ampio app, and it denies the raw CAN surfaces on the wire. A defect in this integration or the underlying [`ampio-mqtt`](https://github.com/pszypowicz/ampio-mqtt) library cannot widen that boundary.

Ampio does not guarantee the stability of the wire surfaces this integration depends on. A server update or a module firmware update can change or remove behavior without notice, and breaking changes by Ampio are a known pattern. The author of an earlier Ampio integration [stopped maintenance for exactly this reason](https://github.com/kstaniek/ampio-hacc/issues/2). If your install works and you are happy with it, stay on your current versions and do not chase the latest ones. If you decide to update anyway, make a full backup first - ideally a full image of the M-SERV's microSD card.

Report bugs and ideas in the [issues](https://github.com/pszypowicz/ampio-homeassistant/issues). The [debugging guide](docs/debugging.md) shows how to capture diagnostics and debug logs for a report.
