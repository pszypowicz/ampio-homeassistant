# Ampio

Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system over local MQTT. Local push, no cloud.

This repo tracks an integration submitted upstream to `home-assistant/core` ([PR #179548](https://github.com/home-assistant/core/pull/179548)). The code under `custom_components/ampio/` mirrors the open core PR, and HACS lets you try it ahead of merge. The sensor platform is available now. The roadmap adds `binary_sensor`, `light`, `cover`, `switch`, `climate`, and `scene` before the first release.

Requires Home Assistant 2026.8.0+ and `ampio-mqtt==0.24.0` (auto-installed).
