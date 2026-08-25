# Ampio for Home Assistant (HACS)

A Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system, talking to the local M-SERV controller over MQTT via the [`ampio-mqtt`](https://pypi.org/project/ampio-mqtt/) library. Local push, no cloud.

## Status

This repository is a staging ground for an integration that has been submitted upstream to `home-assistant/core`. The Python code under `custom_components/ampio/` is kept byte-identical to the open core PR ([PR #179548](https://github.com/home-assistant/core/pull/179548), branch `ampio-sensor`); the only divergences are the two HACS-mandated fields in `manifest.json` (`version`, `documentation`). Tests under `tests/` mirror core's, with a deterministic path transform so they run in this repo's CI.

The integration currently ships a single platform: `sensor`. Additional platforms follow here in this order: `binary_sensor`, `light`, `cover`, `switch`, `climate`, `scene`. After the parent PR merges, each becomes a follow-up core PR. See `CLAUDE.md` for the full discipline.

## Install via HACS

1. Open HACS in Home Assistant.
2. Add this repository as a Custom Repository (category: Integration).
3. Install "Ampio".
4. Restart Home Assistant.
5. Add it from Settings -> Devices & Services -> Add Integration -> Ampio. Enter the M-SERV host and credentials.

Requires Home Assistant 2026.8.0 or newer and `ampio-mqtt==0.24.0` (installed automatically).

## Roadmap

The first HACS release ships after the full platform surface lands:

- [x] `sensor`
- [ ] `binary_sensor`
- [ ] `light`
- [ ] `cover`
- [ ] `switch`
- [ ] `climate`
- [ ] `scene`

## Relationship to home-assistant/core

The long-term home for this integration is `home-assistant/core`, not HACS. This repo exists to:

- let real users exercise the integration ahead of the upstream PR's merge,
- gather field validation that strengthens the upstream review,
- stage additional platforms whose author-here-first / cut-to-core-later workflow keeps the open PR scoped.

When the parent core PR merges, each platform added here gets a follow-up PR against `home-assistant/core`, and the corresponding files in this repo revert to strict-mirror mode with respect to the merged code. The repo never accumulates HACS-only shortcuts; if a pattern would not pass core review, it does not live here.

## Quality

Every change in this repo aims to clear the same gates a core PR would: `ruff`, `mypy`, `hassfest`, and a `pytest` suite mirrored from core. CI runs all four. The parent PR ships at bronze on the integration quality scale. New platforms match the parent PR's shape and test depth. Quality-scale upgrades come later through follow-up core PRs.
