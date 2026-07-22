# AirTouch 2+ integration — aramshaw fork

**This is a fork of [nathanvdh's AirTouch 2+ integration](https://github.com/nathanvdh/homeassistant-airtouch2plus) with connection-reliability fixes and extra features.** It exists so the fixes can be run and soak-tested while they are contributed back upstream ([PR #18](https://github.com/nathanvdh/airtouch2-python/pull/18)). Once merged upstream, that's the place to be.

> Active development happens on the `feat/favourites-and-console-temp` branch; `master` deliberately mirrors upstream. HACS installs from [releases](https://github.com/aramshaw/homeassistant-airtouch2plus/releases), which are cut from the feature branch.

## What's different from upstream

- **Connection-dropout fixes** (the "becomes unavailable" problem):
  - ACKs the controller's undocumented status broadcasts (with the required `0xC0` address)
  - Keep-alive poll defeats the controller's ~16-minute idle timeout
  - Survives the controller dropping off WiFi mid-connection (auto-reconnects — no more dead-until-restart)
- **Favourites** — view and activate AirTouch favourites from HA
- **Console temperature** — the touchscreen's temperature as a sensor
- Entities go *unavailable* during outages and recover automatically
- The [airtouch2 library (forked)](https://github.com/aramshaw/airtouch2-python) is **bundled** — no separate install needed

**This component will set up the following platforms.**

Platform | Description
-- | --
`climate` | Control temperature, mode, fan speed
`fan` | Zones (groups) as fan entities
`select` | Activate favourites
`sensor` | Console (touchscreen) temperature

## Installation

This integration is not available in Home Assistant by default — install it via HACS (recommended):

1. In HACS, add `https://github.com/aramshaw/homeassistant-airtouch2plus` as a [custom repository](https://www.hacs.xyz/docs/faq/custom_repositories) (type: **Integration**), then download it.
2. In the HA UI go to "Settings" → "Devices & Services" → "+ Add integration" and search for "airtouch2plus".
3. Enter the host address (IP) of your AirTouch 2+ system — and that's it.
