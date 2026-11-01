# Changelog

## v2.0.0

First release of [V4n1X/ha_yeelight_bt](https://github.com/V4n1X/ha_yeelight_bt), the continuation of [hcoohb/hass-yeelightbt](https://github.com/hcoohb/hass-yeelightbt). The domain stays `yeelight_bt`, existing lamps keep working (see the migration notes in the README).

Requires Home Assistant 2026.10 or newer, tested with 2026.10 and 2026.11.

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
- Candela behind ESPHome bluetooth proxies: works without notifications, keeps an assumed state that is restored after a restart and ignores stale state echoes ([#76](https://github.com/hcoohb/hass-yeelightbt/issues/76), [#59](https://github.com/hcoohb/hass-yeelightbt/issues/59), [#29](https://github.com/hcoohb/hass-yeelightbt/issues/29), [#79](https://github.com/hcoohb/hass-yeelightbt/pull/79), [#81](https://github.com/hcoohb/hass-yeelightbt/issues/81)).
- All bluetooth operations of a lamp are serialized, a failing command is retried once on a new connection ([#82](https://github.com/hcoohb/hass-yeelightbt/issues/82), [#56](https://github.com/hcoohb/hass-yeelightbt/issues/56)).
- Pairing can no longer wait forever, which left lamps unavailable until HA was restarted ([#65](https://github.com/hcoohb/hass-yeelightbt/issues/65)).
- Commands that could not be delivered raise an error instead of failing silently ([#49](https://github.com/hcoohb/hass-yeelightbt/issues/49)).
- The model is stored in the config entry, lamps whose advertisement carries no name keep working.
- The lamp follows the best adapter/proxy chosen by HA bluetooth.
- Color mode switches between `color_temp` and `hs` according to the lamp state (was always `hs`).
- Brightness is kept in the HA scale after a color or temperature change.
- Polling runs in the background, no more "Update of light.x is taking over 10 seconds".
- Transition and effect are no longer advertised, they were never implemented.

### Development

- Tests with pytest (works with `pytest-homeassistant-custom-component` and HA's new `pytest-home-assistant`).
- ruff replaces black, isort and flake8; GitHub actions updated.
