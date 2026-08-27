# Ampio for Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/pszypowicz/ampio-homeassistant)](https://github.com/pszypowicz/ampio-homeassistant/releases)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5)](https://hacs.xyz/)
[![License](https://img.shields.io/github/license/pszypowicz/ampio-homeassistant)](LICENSE)

A Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system. It talks to the local M-SERV controller over MQTT through the [`ampio-mqtt`](https://pypi.org/project/ampio-mqtt/) library. Local push, no cloud.

## Platforms

| Platform        | What you get                                                                 |
| --------------- | ---------------------------------------------------------------------------- |
| `sensor`        | Temperature, humidity, pressure, CO2, air quality, illuminance, loudness     |
| `binary_sensor` | Flags and motion detection                                                   |
| `light`         | Dimmers, RGBW outputs, and relays tagged as lights in Ampio Designer         |
| `cover`         | Shutters and blinds, with position and slat tilt where the hardware has them |
| `switch`        | Remaining relays, with the outlet class for plug-tagged ones                 |
| `climate`       | Heating regulators with temperature readback and operating-mode presets      |
| `scene`         | The Ampio app's scene catalog                                                |

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

Devices appear as a hub, then one device per Ampio module, then one device per Ampio object, named as in the Ampio app. Objects assigned to a room in the Ampio app are suggested into the matching Home Assistant area on first setup; you own the areas afterwards.

### Panel status LEDs

The per-field status LEDs on M-DOT touch panels appear as switch entities. Commands reach them only when the integration signs in with an admin account. To make an LED controllable, create its app object in Ampio Designer and leave the LED out of Designer logic. An LED bound to a Designer condition accepts the command, but the module's own logic re-asserts it within seconds.

## Relationship to home-assistant/core

This integration is submitted to `home-assistant/core` ([PR #179548](https://github.com/home-assistant/core/pull/179548)). This repository tracks that PR and adds the remaining platforms ahead of the merge, so Ampio users can run the integration today and field feedback can strengthen the upstream review. After the PR merges, the platforms staged here follow as core PRs.

Report bugs and ideas in the [issues](https://github.com/pszypowicz/ampio-homeassistant/issues).
