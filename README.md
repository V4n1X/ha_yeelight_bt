<img src="custom_components/yeelight_bt/brand/icon.png" alt="Yeelight Bluetooth" width="96" align="right">

# Yeelight Bluetooth for Home Assistant

[![Release](https://img.shields.io/github/v/release/V4n1X/ha_yeelight_bt)](https://github.com/V4n1X/ha_yeelight_bt/releases)
[![Validate](https://github.com/V4n1X/ha_yeelight_bt/actions/workflows/validate.yml/badge.svg)](https://github.com/V4n1X/ha_yeelight_bt/actions/workflows/validate.yml)
[![Tests](https://github.com/V4n1X/ha_yeelight_bt/actions/workflows/tests.yml/badge.svg)](https://github.com/V4n1X/ha_yeelight_bt/actions/workflows/tests.yml)
[![License](https://img.shields.io/github/license/V4n1X/ha_yeelight_bt)](LICENSE)

Control the **Yeelight Bedside lamp** and the **Yeelight Candela** from Home Assistant. These lamps have no Wi-Fi and are controlled via Bluetooth Low Energy only, through a local Bluetooth adapter or a Bluetooth proxy (ESPHome, Shelly, …).

![Yeelight Bedside](yeelight-bedside.jpg)

- Automatic discovery, set up from the UI
- Works with local adapters and Bluetooth proxies, follows the best one automatically
- On/off, brightness, color and color temperature (Bedside)
- Robust against dropped connections: commands are retried once, failures are reported to automations
- English and German translations

## Contents

- [Supported lamps](#supported-lamps)
- [Requirements](#requirements)
- [Installation](#installation)
- [Setup](#setup)
- [Supported functions](#supported-functions)
- [Data updates](#data-updates)
- [Known limitations](#known-limitations)
- [Examples](#examples)
- [Removing the integration](#removing-the-integration)
- [Troubleshooting](#troubleshooting)
- [Migrating from hcoohb/hass-yeelightbt](#migrating-from-hcoohbhass-yeelightbt)
- [Contributing](#contributing)
- [Credits and license](#credits-and-license)

## Supported lamps

| Lamp | BLE name | Features | Status |
| --- | --- | --- | --- |
| Yeelight Bedside | `XMCTD_*` | on/off, brightness, color (HS), color temperature 1700–6500 K (real Kelvin) | Supported, tested with real hardware by the maintainer |
| Yeelight Candela | `yeelight_ms*` | on/off, brightness | **Experimental**: the maintainer has no Candela. The implementation follows two independent implementations that work with real lamps ([Homey app](https://github.com/KevinvOosterhout/com.kevinvanoosterhout.yeelightcandela), [candelapy](https://github.com/praschak/candelapy)). [Tell us how it works for you.](https://github.com/V4n1X/ha_yeelight_bt/issues/new?template=candela_report.yml) |

Supported is the original Bluetooth Candela **YLFW01YL** (micro USB) with the **switch underneath it in the Bluetooth position**. It does not report its state: Home Assistant shows the last state it sent as an *assumed state* (lightning buttons instead of a toggle) and restores it after a restart. Changes made by twisting the lamp are not seen.

## Requirements

- Home Assistant **2026.10** or newer (tested with 2026.10 and 2026.11)
- The [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/) with a local adapter, or a [Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) with active connections (`bluetooth_proxy: active: true`). Every lamp needs a free connection slot.
- The lamp must **not** be connected to the Yeelight app or any other device.

## Installation

### Marketplace (Home Assistant 2026.11+)

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=V4n1X&repository=ha_yeelight_bt&category=integration)

1. Click the button above, or go to **Settings → Marketplace**, search for **Yeelight Bluetooth** (or add `https://github.com/V4n1X/ha_yeelight_bt` as a custom repository of type *Integration*).
2. Install it. Home Assistant asks for a restart when needed.

### HACS (Home Assistant 2026.10)

The same button opens the repository in HACS. Otherwise add `https://github.com/V4n1X/ha_yeelight_bt` as a custom repository of type *Integration*, install **Yeelight Bluetooth** and restart Home Assistant.

### Manual

1. Download `yeelight_bt.zip` from the [latest release](https://github.com/V4n1X/ha_yeelight_bt/releases/latest).
2. Extract it to `<config>/custom_components/yeelight_bt/`.
3. Restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up Yeelight Bluetooth.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=yeelight_bt)

Lamps in range are **discovered automatically** and show up under **Settings → Devices & services**. You can also add them manually with **Add integration → Yeelight Bluetooth**:

- **Select a discovered lamp**: the lamps currently seen by your adapters and proxies.
- **Enter the MAC address manually**: for lamps that do not advertise their name. Pick the model if it cannot be detected.

### Pairing

1. If the lamp was paired with another device before, [reset it](https://www.youtube.com/watch?v=PnjcOSgnbAM).
2. On the first connection the lamp **pulses**. Push the small button on the lamp within 30 seconds. From then on it is paired with Home Assistant.
3. **Candela**: no pairing is needed. Put the switch underneath the lamp in the Bluetooth position and charge it; Home Assistant can then control it right away.

Once paired, do not connect the lamp to the Yeelight app anymore, or both will keep fighting for the connection.

### Setup parameters

| Parameter | Description |
| --- | --- |
| Lamp name | Name of the device in Home Assistant. |
| Lamp / MAC address | The lamp to connect to. Manual entry accepts `AA:BB:CC:DD:EE:FF` or `AA-BB-…`. |
| Model | Manual entry only: *Detect automatically*, *Bedside* or *Candela*. |

Home Assistant connects to the lamp before the setup finishes, so a lamp that cannot be reached or paired is reported right away instead of ending up as an unavailable device.

### Options

- **Reconfigure** (⋮ menu of the lamp under **Settings → Devices & services → Yeelight Bluetooth**): change the model, e.g. when a Candela was detected as Bedside. A repair issue tells you when the model could not be detected.
- **System options → Enable polling**: when turned off, the integration no longer requests the state every 30 s. The lamp still pushes changes while it is connected, and `homeassistant.update_entity` refreshes it on demand.

## Supported functions

One light entity per lamp:

| | Bedside | Candela |
| --- | --- | --- |
| On / off | ✓ | ✓ |
| Brightness | 1–100 % | 1–100 % |
| Color temperature | 1700–6500 K (real Kelvin, as reported by the lamp) | – |
| Color (hue / saturation) | ✓ | – |
| State changes made on the lamp | pushed to Home Assistant | usually not reported (assumed state) |

The device page shows the model and the firmware version. **Download diagnostics** on the device page collects the lamp state, model, connection and the adapter/proxy with its signal strength, for bug reports.

## Data updates

- While connected, the Bedside **pushes** every state change, including changes made with its touch controls.
- In addition the state is **requested every 30 s** in the background; a dropped connection is re-established within a few seconds.
- A lamp is marked **unavailable** only when it cannot be reached (about a minute for a lamp that is unplugged) and is retried every 30 s. It is logged once when it becomes unavailable and once when it is back.
- The integration always connects through the adapter or proxy Home Assistant considers best for the lamp.

## Known limitations

- **Transitions and effects** are not supported, the lamp fades with its own fixed speed.
- **Commands right after switching on**: the lamp ignores commands for about 1 s after powering on. Turning on with a color, brightness or temperature therefore takes about 2.5 s.
- **One connection at a time**: while Home Assistant is connected, the Yeelight app cannot connect to the lamp, and the other way round.
- **Proxy slots**: every lamp occupies one connection slot of a Bluetooth proxy (ESPHome proxies have 3 by default). Shelly devices only forward advertisements and cannot connect.
- **Candela**: it does not report its state (also not after twisting it), so the last state sent by Home Assistant is shown and restored after a restart. Its built-in flame mode cannot be selected, no Bluetooth command for it is known.
- **An unreachable lamp** fails a command after about a minute (3 connection attempts of up to 20 s).

## Examples

Wake-up light: warm and dim first, bright neutral white ten minutes later.

```yaml
alias: Wake-up light
mode: single
triggers:
  - trigger: time
    at: "06:30:00"
actions:
  - action: light.turn_on
    target:
      entity_id: light.bedside
    data:
      color_temp_kelvin: 2700
      brightness_pct: 10
  - delay: "00:10:00"
  - action: light.turn_on
    target:
      entity_id: light.bedside
    data:
      color_temp_kelvin: 4000
      brightness_pct: 80
```

Night light: very warm and dim while there is motion in the bedroom, off two minutes after the motion has cleared.

```yaml
alias: Bedroom night light
mode: restart
triggers:
  - trigger: motion.detected
    target:
      area_id: bedroom
actions:
  - action: light.turn_on
    target:
      entity_id: light.bedside
    data:
      color_temp_kelvin: 1700
      brightness_pct: 5
  - wait_for_trigger:
      - trigger: motion.cleared
        target:
          area_id: bedroom
        options:
          behavior: all
          for: "00:02:00"
  - action: light.turn_off
    target:
      entity_id: light.bedside
```

Other use cases: a reading light with a fixed color temperature, a colored notification (e.g. red when the washing machine is done), or the lamp in scenes and light groups.

## Removing the integration

1. Go to **Settings → Devices & services → Yeelight Bluetooth**, open the ⋮ menu of the lamp and select **Delete**.
2. If you installed it from the Marketplace/HACS and no lamp is left, remove the repository there as well and restart Home Assistant.

The lamp keeps its pairing. To use it with the Yeelight app again, [reset it](https://www.youtube.com/watch?v=PnjcOSgnbAM).

## Troubleshooting

<details>
<summary><b>The lamp is not discovered</b></summary>

- Make sure it is not connected to the Yeelight app (close the app, or turn off Bluetooth on the phone).
- Check that the lamp is in range of an adapter or proxy: **Settings → Devices & services → Bluetooth → Advertisement monitor** should list it.
- Reset the lamp and try again, or add it with its MAC address.
</details>

<details>
<summary><b>The lamp is unavailable or does not react</b></summary>

- **Candela**: the switch underneath the lamp must be in the Bluetooth position, and the lamp must be charged.
- Proxies need active connections and a free connection slot per lamp.
- Watch for the pulsing lamp after a reset: it waits for its button to be pushed.
- Move the proxy closer, Bluetooth range through walls is short.
</details>

<details>
<summary><b>Home Assistant Container / Core: Bluetooth is not available</b></summary>

The host needs BlueZ (`bluetoothctl`). For Docker, share D-Bus and grant the network capabilities:

```yaml
volumes:
  - /var/run/dbus:/var/run/dbus
cap_add:
  - NET_ADMIN
  - NET_RAW
network_mode: host
```

For Home Assistant Core, the user running Home Assistant must be in the `bluetooth` group.
</details>

<details>
<summary><b>Collect a debug log</b></summary>

Go to **Settings → Devices & services → Yeelight Bluetooth → ⋮ → Enable debug logging**, reproduce the problem, then disable debug logging again to download the log. To log from startup, add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.yeelight_bt: debug
    bleak_retry_connector: debug
```

Attach the log to a [bug report](https://github.com/V4n1X/ha_yeelight_bt/issues/new?template=bug_report.yml).
</details>

## Migrating from hcoohb/hass-yeelightbt

This project continues [hcoohb/hass-yeelightbt](https://github.com/hcoohb/hass-yeelightbt) with the same domain (`yeelight_bt`), so your configured lamps, entities and automations keep working.

1. Remove the old repository in the Marketplace/HACS (**⋮ → Remove**), but keep the integration configured. Two repositories with the same domain cannot be installed at the same time.
2. Install this repository as described above and restart Home Assistant.

Lamps whose model was never stored are detected again on the first start. The changes are listed in the [changelog](CHANGELOG.md).

> **Color temperature is real Kelvin now.** Version 1.x shifted the values so that the middle of the slider looked white: 2740 K in Home Assistant became about 4080 K on the lamp. Version 2 sends and shows the Kelvin value the lamp really uses. Automations and scenes with a color temperature look different after the update: use about 4000 K where you want neutral white.

## Contributing

Bug reports, Candela test reports and pull requests are welcome.

```shell
pip install -r requirements-dev.txt
pytest
```

The tests need Linux or WSL, like Home Assistant itself. The checklist for testing with real lamps is in [docs/hardware-test.md](docs/hardware-test.md).

### Keep personal data out of the repository

Bug reports and test results often contain Bluetooth addresses, host names, IP addresses or tokens. Please replace them with placeholders (e.g. `<lamp address>`); the lamps in the examples use public example addresses.

Enable the checks once after cloning:

```shell
git config core.hooksPath .githooks
```

The `pre-commit` and `pre-push` hooks run `scripts/check_private_data.py` and refuse commits and pushes that contain credentials, private IP addresses, local host names, user paths or real MAC addresses, including commit messages. Add your own names, addresses and host names, one regular expression per line, to `.git/info/private-patterns`: that file is checked as well but never committed. The same checks and [Gitleaks](https://github.com/gitleaks/gitleaks) run in CI for every push and pull request.

## Credits and license

Based on [hcoohb/hass-yeelightbt](https://github.com/hcoohb/hass-yeelightbt) by [@hcoohb](https://github.com/hcoohb), originally inspired by [python-yeelightbt](https://github.com/rytilahti/python-yeelightbt) by Teemu Rytilahti. Version 2 includes community work from [#79](https://github.com/hcoohb/hass-yeelightbt/pull/79), [#80](https://github.com/hcoohb/hass-yeelightbt/pull/80), [#81](https://github.com/hcoohb/hass-yeelightbt/issues/81) and the [mhdsallal fork](https://github.com/mhdsallal/hass-yeelightbt).

Licensed under the [MIT license](LICENSE). This project is not affiliated with Yeelight or Xiaomi.
