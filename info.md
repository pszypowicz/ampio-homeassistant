# Ampio

Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system over local MQTT, with zeroconf and DHCP discovery. Local push, no cloud.

This repo tracks an integration submitted upstream to `home-assistant/core`. The code under `custom_components/ampio/` mirrors the open core PR; HACS lets you try it ahead of merge. Currently sensor-only; more platforms are being added here and will be cut into follow-up core PRs once the parent PR lands.

Requires Home Assistant 2025.2.0+, Python 3.13+, and `ampio-mqtt>=1.5.0` (auto-installed).
