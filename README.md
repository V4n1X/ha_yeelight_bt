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
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Migrating from hcoohb/hass-yeelightbt](#migrating-from-hcoohbhass-yeelightbt)
- [Contributing](#contributing)
- [Credits and license](#credits-and-license)

## Supported lamps

| Lamp | BLE name | Features | Status |
| --- | --- | --- | --- |
| Yeelight Bedside | `XMCTD_*` | on/off, brightness, color (HS), color temperature 1700–6500 K | Supported, tested with real hardware by the maintainer |
| Yeelight Candela | `yeelight_ms*` | on/off, brightness | **Experimental**: the maintainer has no Candela, support is based on community reports. [Tell us how it works for you.](https://github.com/V4n1X/ha_yeelight_bt/issues/new?template=candela_report.yml) |

The Candela behind most proxies does not report its state. Home Assistant then shows an *assumed state* (lightning buttons instead of a toggle) and restores the last known state after a restart.

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
3. **Candela**: it usually does not confirm the pairing. If it pulses, push its button and switch it again from Home Assistant. With a local adapter, pairing it once with `bluetoothctl` (`connect <MAC>`, then `pair <MAC>`) is known to help.

Once paired, do not connect the lamp to the Yeelight app anymore, or both will keep fighting for the connection.

## How it works

| Topic | Behaviour |
| --- | --- |
| State | The Bedside pushes its state. It is additionally polled every 30 s in the background, so changes made on the lamp are seen. |
| Connection drops | Proxies and adapters regularly drop connections. A failing command is retried once on a new connection. |
| Availability | A lamp becomes unavailable only when it cannot be reached at all, and is retried every 30 s. |
| Errors | A command that could not be delivered raises an error, visible in the UI and in automation traces. |
| Adapters / proxies | The integration always connects through the adapter or proxy that Home Assistant considers best for the lamp. |

## Troubleshooting

<details>
<summary><b>The lamp is not discovered</b></summary>

- Make sure it is not connected to the Yeelight app (close the app, or turn off Bluetooth on the phone).
- Check that the lamp is in range of an adapter or proxy: **Settings → Devices & services → Bluetooth → Advertisement monitor** should list it.
- Reset the lamp and try again, or add it with its MAC address.
</details>

<details>
<summary><b>The lamp is unavailable or does not react</b></summary>

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

## Contributing

Bug reports, Candela test reports and pull requests are welcome.

```shell
pip install -r requirements-dev.txt
pytest
```

The tests need Linux or WSL, like Home Assistant itself. The checklist for testing with real lamps is in [docs/hardware-test.md](docs/hardware-test.md).

## Credits and license

Based on [hcoohb/hass-yeelightbt](https://github.com/hcoohb/hass-yeelightbt) by [@hcoohb](https://github.com/hcoohb), originally inspired by [python-yeelightbt](https://github.com/rytilahti/python-yeelightbt) by Teemu Rytilahti. Version 2 includes community work from [#79](https://github.com/hcoohb/hass-yeelightbt/pull/79), [#80](https://github.com/hcoohb/hass-yeelightbt/pull/80), [#81](https://github.com/hcoohb/hass-yeelightbt/issues/81) and the [mhdsallal fork](https://github.com/mhdsallal/hass-yeelightbt).

Licensed under the [MIT license](LICENSE). This project is not affiliated with Yeelight or Xiaomi.
