# Changelog

## v2.2.0

Requires Home Assistant 2026.10 or newer, tested with 2026.10 and 2026.11.

### Bedside

All changes below were tested with a real Bedside in Home Assistant.

- **New: the lamp clock is set** to Home Assistant's local time on every connection, like the Yeelight app does (it was 14 minutes off on the test lamp). The lamp's own night-light and timers use this clock.
- **New: repair issue when the lamp has to be paired again**, for example after it was deleted in the Yeelight app or reset. It asks you to switch the lamp on and push its button, and disappears once the lamp is paired. A switched-off lamp that is not paired no longer shows up as an unknown rejection (`0x06`) in the log.
- **New: deleting the lamp in Home Assistant removes its pairing on the lamp**, as deleting it in the Yeelight app does. Adding it again needs the button. The Bedside shares this pairing with the Yeelight app, so it is signed out there as well.
- The pairing button may now be pushed within 60 seconds instead of 30, the time the lamp itself waits.
- The serial number is no longer requested: the Bedside never answers it.
- A newly added or restarted Bedside shows its normal switch while Home Assistant connects, instead of the two "assumed state" buttons. Only a Bedside on a Bluetooth backend without notifications keeps the assumed state.

### Candela (experimental)

- Deleting an authorized Candela removes its Home Assistant authorization on the lamp (frame from the app, not tested on hardware). Command-only entries are not contacted.

### Development

- Protocol notes and hardware test report extended with the lamp clock, night-light, delay-off timer, alarms, wake-up light, color flows, name, beacons, factory reset and pairing behaviour.

## v2.1.1

- README: the logo, the lamp pictures and the version and license badges now also show in the Marketplace/HACS (absolute links instead of relative ones).

## v2.1.0

Requires Home Assistant 2026.10 or newer, tested with 2026.10 and 2026.11.

### Bedside

- The state query and the color command now use the same bytes as the Yeelight app 3.5.4 (tested on a real lamp).
- Changing the color or color temperature without a brightness keeps the lamp's current brightness.
- The firmware version is now shown correctly (e.g. `2231-2238-2330` instead of `8705-8753-9016`).
- The lamp state stays confirmed after a reconnect: no short "assumed state" in the UI and no extra power-on command.

### Candela (experimental)

- Power and brightness commands use the format of the Yeelight app 3.5.4.
- **New: state feedback.** When a Candela is added through a Bluetooth backend that supports notifications, Home Assistant asks you to turn the cylinder to authorize it. After that, changes made on the lamp and its firmware version are shown.
- If the authorization is not confirmed or not answered, the Candela is still added, in command-only mode as before.
- **Existing Candela entries are unchanged:** they stay command-only and get no pairing request. To get state feedback, remove the lamp and add it again.
- An "off" message from the lamp is no longer dropped when it reports an unknown mode.
- The maintainer has no Candela: these changes follow the app and are not tested with real hardware yet. Please use the [Candela test report](https://github.com/V4n1X/ha_yeelight_bt/issues/new?template=candela_report.yml).

### Development

- Protocol reference from the Yeelight app 3.5.4: [docs/app-ble-protocol.md](docs/app-ble-protocol.md).
- Tests with a real Bedside version answer and with Candela frames taken from the app; hardware test report updated.

## v2.0.0

First release of [V4n1X/ha_yeelight_bt](https://github.com/V4n1X/ha_yeelight_bt), the continuation of [hcoohb/hass-yeelightbt](https://github.com/hcoohb/hass-yeelightbt). The domain stays `yeelight_bt`, existing lamps keep working (see the migration notes in the README).

Requires Home Assistant 2026.10 or newer, tested with 2026.10 and 2026.11.

### ⚠️ Breaking change: real Kelvin

The color temperature is no longer shifted: 1.x turned 2740 K in Home Assistant into about 4080 K on the lamp so that the middle of the slider looked white. 2.0 sends and reports the Kelvin value the lamp really uses (1700–6500 K, measured on hardware: 4080 K is neutral white). **Automations and scenes with a color temperature look different after the update**, use about 4000 K for neutral white.

### Quality scale

The integration follows the Home Assistant integration quality scale up to Platinum (see `quality_scale.yaml`):

- The config flow connects to and pairs with the lamp before creating the entry (`test-before-configure`).
- Reconfigure flow to change the model, repair issue when the model could not be detected.
- Diagnostics download (lamp state, connection, adapter/proxy, signal strength).
- Logs once when a lamp becomes unavailable and once when it is back.
- Icon translations (lamp / candle icon per model).
- Respects the "Enable polling" system option; `homeassistant.update_entity` refreshes on demand.
- Schemas use Probatio, Home Assistant's validation engine since 2026.9, instead of voluptuous.
- Fully typed (`mypy --strict`), test coverage 98 %.
- Unused protocol helpers removed.
- A deleted lamp is discovered again right away, without restarting Home Assistant (found in the hardware test).
- Commands no longer fail during the first 10 s after the host booted (the "connection just failed" hold-off started at monotonic time 0).

### Candela

Reworked after comparing with two independent implementations that work with real lamps, the [Homey Candela app](https://github.com/KevinvOosterhout/com.kevinvanoosterhout.yeelightcandela) and [candelapy](https://github.com/praschak/candelapy) (protocol facts only, no code taken):

- The pairing request is no longer sent. Neither implementation uses it, and it puts the Candela back into pairing mode and makes it drop the connection after a few seconds ([#81](https://github.com/hcoohb/hass-yeelightbt/issues/81)).
- No notifications and no state, version or serial requests: their content was guessed and could overwrite the state with wrong values. The Candela always uses an assumed state.
- Brightness is sent right after powering on; the 1.5 s wait is only needed by the Bedside.
- Checking whether an unavailable Candela is back only connects, it sends nothing.
- Only names `yeelight_ms` or `yeelight_ms` with a `_`, `-` or space suffix are detected as Candela.
- README: supported model YLFW01YL (micro USB), switch underneath in the Bluetooth position.

### Marketplace

- Ready for the Home Assistant Marketplace (formerly HACS): brand images in the integration, `categories` in the manifest, HACS and hassfest validation in one workflow, release workflow that checks the version and attaches `yeelight_bt.zip`.
- Issue templates for bug reports and Candela test reports, MIT license file.

### Home Assistant compatibility

- Light entity uses the Kelvin API only (`color_temp_kelvin`), the mired properties and helpers removed in HA 2026.1 are gone.
- Entity naming through the device (`has_entity_name`), no more `entity_id` set by the integration.
- Opts in to in-place entity ID changes (`async_entity_id_changed`, HA 2026.11).
- `ConfigEntry.runtime_data`, `ConfigFlowResult`, `async_unload_platforms`, translated exceptions.
- The config flow lists the lamps seen by HA bluetooth (adapters and proxies) instead of running its own scan with HA internals.
- Depends on `bluetooth_adapters`, so proxies are set up before the lamps.
- Compatible with bleak 3.x / bleak-retry-connector 4.x (no access to private bleak attributes, names may be `None`).
- Software version updated through the entity's device entry (`device_registry.async_get_device` is deprecated in HA 2026.10).

### Fixes

- Discovery no longer crashes on bluetooth devices without a name ([#83](https://github.com/hcoohb/hass-yeelightbt/issues/83)).
- Candela reports the `brightness` color mode it supports ([#69](https://github.com/hcoohb/hass-yeelightbt/issues/69), [#80](https://github.com/hcoohb/hass-yeelightbt/pull/80)).
- Candela, also behind ESPHome bluetooth proxies: command-only (see above), keeps an assumed state that is restored after a restart ([#76](https://github.com/hcoohb/hass-yeelightbt/issues/76), [#59](https://github.com/hcoohb/hass-yeelightbt/issues/59), [#29](https://github.com/hcoohb/hass-yeelightbt/issues/29), [#79](https://github.com/hcoohb/hass-yeelightbt/pull/79), [#81](https://github.com/hcoohb/hass-yeelightbt/issues/81)).
- All bluetooth operations of a lamp are serialized, a failing command is retried once on a new connection ([#82](https://github.com/hcoohb/hass-yeelightbt/issues/82), [#56](https://github.com/hcoohb/hass-yeelightbt/issues/56)).
- Pairing can no longer wait forever, which left lamps unavailable until HA was restarted ([#65](https://github.com/hcoohb/hass-yeelightbt/issues/65)).
- Commands that could not be delivered raise an error instead of failing silently ([#49](https://github.com/hcoohb/hass-yeelightbt/issues/49)).
- Turning on with a color, brightness or temperature works: the Bedside ignores commands for about 1 s after powering on, the integration now waits 1.5 s (measured on hardware, [#37](https://github.com/hcoohb/hass-yeelightbt/issues/37)).
- Color, brightness or temperature commands no longer mark a lamp as on, a lamp that is off ignores them.
- A plain `OSError` from the bluetooth backend (e.g. WinRT, broken pipe) is retried like any other connection error.
- An unreachable lamp fails within about a minute instead of 100 s: a failed connection is not started over (bleak-retry-connector already retried, 3 attempts), and a command queued right behind a failed connection fails right away.
- The model is stored in the config entry, lamps whose advertisement carries no name keep working.
- The lamp follows the best adapter/proxy chosen by HA bluetooth.
- Color mode switches between `color_temp` and `hs` according to the lamp state (was always `hs`).
- Brightness is kept in the HA scale after a color or temperature change.
- Polling runs in the background, no more "Update of light.x is taking over 10 seconds".
- Transition and effect are no longer advertised, they were never implemented.

### Development

- Tests with pytest (works with `pytest-homeassistant-custom-component` and HA's new `pytest-home-assistant`).
- ruff replaces black, isort and flake8; GitHub actions updated.
