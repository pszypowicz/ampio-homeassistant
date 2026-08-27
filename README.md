# Ampio for Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/pszypowicz/ampio-homeassistant)](https://github.com/pszypowicz/ampio-homeassistant/releases)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5)](https://hacs.xyz/)
[![License](https://img.shields.io/github/license/pszypowicz/ampio-homeassistant)](LICENSE)

A Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system. It talks to the local M-SERV controller over MQTT through the [`ampio-mqtt`](https://pypi.org/project/ampio-mqtt/) library. Local push, no cloud.

## Platforms

| Platform        | What you get                                                                 |
| --------------- | ---------------------------------------------------------------------------- |
| `sensor`        | Temperature, humidity, pressure, CO2, air quality, illuminance, loudness     |
| `binary_sensor` | Motion detection and wired button inputs                                     |
| `light`         | Dimmers, RGBW outputs, and relays tagged as lights in Ampio Designer         |
| `cover`         | Shutters and blinds, with position and slat tilt where the hardware has them |
| `switch`        | Remaining relays and Ampio flags, with the outlet class for plug-tagged ones |
| `climate`       | Heating regulators with temperature readback and operating-mode presets      |
| `scene`         | The Ampio app's scene catalog                                                |

## Installation

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pszypowicz&repository=ampio-homeassistant&category=integration)

Click the badge, confirm the repository in HACS, install "Ampio", and restart Home Assistant.

Manual steps:

1. Open HACS in Home Assistant.
2. Add `https://github.com/pszypowicz/ampio-homeassistant` as a custom repository (type: Integration).
3. Install "Ampio" and restart Home Assistant.

Requires Home Assistant 2026.8.0 or newer. `ampio-mqtt` is installed automatically.

## Configuration

1. In the Ampio app, create a dedicated Home Assistant user and grant it the devices you want in Home Assistant.
2. In Home Assistant, go to Settings -> Devices & Services -> Add Integration -> Ampio.
3. Enter the M-SERV host and that user's MQTT credentials.

Devices appear as a hub, then one device per Ampio module, then one device per Ampio object, named as in the Ampio app. Objects assigned to a room in the Ampio app are suggested into the matching Home Assistant area on first setup, and objects without an app room fall back to their Designer location on admin accounts; you own the areas afterwards. Sensor and input objects, flags included, attach to their module device directly.

## Relationship to home-assistant/core

This integration is submitted to `home-assistant/core` ([PR #179548](https://github.com/home-assistant/core/pull/179548)). This repository tracks that PR and adds the remaining platforms ahead of the merge, so Ampio users can run the integration today and field feedback can strengthen the upstream review. After the PR merges, the platforms staged here follow as core PRs.

## Disclaimer

This integration is an independent, best-effort project and has no affiliation with Ampio. Use it at your own risk. It commands real hardware, and a wrong command moves real devices.

The M-SERV itself guarantees the safety of a standard account. The broker limits such an account to the objects granted in the Ampio app, and it denies the raw CAN surfaces on the wire. A defect in this integration or the underlying [`ampio-mqtt`](https://github.com/pszypowicz/ampio-mqtt) library cannot widen that boundary.

Ampio does not guarantee the stability of the wire surfaces this integration depends on. A server update or a module firmware update can change or remove behavior without notice, and breaking changes by Ampio are a known pattern. The author of an earlier Ampio integration [stopped maintenance for exactly this reason](https://github.com/kstaniek/ampio-hacc/issues/2). If your install works and you are happy with it, stay on your current versions and do not chase the latest ones. If you decide to update anyway, make a full backup first - ideally a full image of the M-SERV's microSD card.

Report bugs and ideas in the [issues](https://github.com/pszypowicz/ampio-homeassistant/issues).
