# Changelog

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
- Candela, also behind ESPHome bluetooth proxies: command-only (see below), keeps an assumed state that is restored after a restart ([#76](https://github.com/hcoohb/hass-yeelightbt/issues/76), [#59](https://github.com/hcoohb/hass-yeelightbt/issues/59), [#29](https://github.com/hcoohb/hass-yeelightbt/issues/29), [#79](https://github.com/hcoohb/hass-yeelightbt/pull/79), [#81](https://github.com/hcoohb/hass-yeelightbt/issues/81)).
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
