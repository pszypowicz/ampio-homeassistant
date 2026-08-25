# Ampio for Home Assistant (HACS)

A Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system, talking to the local M-SERV controller over MQTT via the [`ampio-mqtt`](https://pypi.org/project/ampio-mqtt/) library. Local push, no cloud.

## Status

This repository is a staging ground for an integration that has been submitted upstream to `home-assistant/core`. The Python code under `custom_components/ampio/` is kept byte-identical to the open core PR ([PR #179548](https://github.com/home-assistant/core/pull/179548), branch `ampio-sensor`); the only divergences are the two HACS-mandated fields in `manifest.json` (`version`, `documentation`) and the tracked `translations/en.json`, which core generates at build time and does not track. Tests under `tests/` mirror core's, with a deterministic path transform so they run in this repo's CI.

The full platform surface shipped in v0.1.0: sensor, binary_sensor, light, cover, switch, climate, and scene. After the parent PR merges, each platform becomes a follow-up core PR.

## Install

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pszypowicz&repository=ampio-homeassistant&category=integration)

Click the badge, confirm the repository in HACS, install "Ampio", and restart Home Assistant.

Manual steps, if you prefer them:

1. Open HACS in Home Assistant.
2. Add `https://github.com/pszypowicz/ampio-homeassistant` as a custom repository (type: Integration).
3. Install "Ampio" and restart Home Assistant.
4. Add the integration: Settings -> Devices & Services -> Add Integration -> Ampio.
5. Enter the M-SERV host and the MQTT credentials of a Home Assistant user defined in the Ampio app.

Requires Home Assistant 2026.8.0 or newer and `ampio-mqtt==0.26.0` (installed automatically).

## Roadmap

The full platform surface shipped in v0.1.0:

- [x] `sensor`
- [x] `binary_sensor`
- [x] `light`
- [x] `cover`
- [x] `switch`
- [x] `climate`
- [x] `scene`

## Relationship to home-assistant/core

The long-term home for this integration is `home-assistant/core`, not HACS. This repo exists to:

- let real users exercise the integration ahead of the upstream PR's merge,
- gather field validation that strengthens the upstream review,
- stage additional platforms whose author-here-first / cut-to-core-later workflow keeps the open PR scoped.

When the parent core PR merges, each platform added here gets a follow-up PR against `home-assistant/core`, and the corresponding files in this repo revert to strict-mirror mode with respect to the merged code. The repo never accumulates HACS-only shortcuts; if a pattern would not pass core review, it does not live here.

## Quality

Every change in this repo aims to clear the same gates a core PR would: `ruff`, `mypy`, `hassfest`, and a `pytest` suite mirrored from core. CI runs all four. The parent PR ships at bronze on the integration quality scale. New platforms match the parent PR's shape and test depth. Quality-scale upgrades come later through follow-up core PRs.
