# Ampio

Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system over local MQTT. Local push, no cloud.

This repo tracks an integration submitted upstream to `home-assistant/core` ([PR #179548](https://github.com/home-assistant/core/pull/179548)). The code under `custom_components/ampio/` mirrors the open core PR, and HACS lets you try it ahead of merge. Seven platforms are available: sensor, binary_sensor, light, cover, switch, climate, and scene.

Requires Home Assistant 2026.8.0+ and `ampio-mqtt==0.34.0` (auto-installed).
