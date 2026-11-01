# Hardware test checklist

Run this before every release with a real lamp. Enable debug logging for the integration first (**Settings → Devices & services → Yeelight Bluetooth → ⋮ → Enable debug logging**) and keep the log of the run.

Note the setup: Home Assistant version, integration version, local adapter or proxy (type and firmware).

## Bedside

### Setup and pairing

- [ ] Reset the lamp, remove an existing config entry.
- [ ] The lamp is discovered (notification under **Settings → Devices & services**).
- [ ] Adding it from the discovery: the lamp pulses, pushing its button finishes pairing, the light entity becomes available.
- [ ] Adding it manually (**Select a discovered lamp** and **Enter the MAC address manually**) works as well.
- [ ] The device page shows model *Bedside* and a firmware version.

### Control

- [ ] Turn on / off from the dashboard.
- [ ] Brightness 1 %, 50 %, 100 %.
- [ ] Color temperature warm, white (middle of the slider), cold: "white" in HA is white on the lamp.
- [ ] Colors red, green, blue; the color picker shows the color that was set.
- [ ] Turning on with a color while the lamp is off (`light.turn_on` with `rgb_color` and `brightness`) sets the color, not the previous one.
- [ ] `light.turn_on` with `brightness: 0` turns the lamp off.
- [ ] Fast slider changes do not get the lamp stuck.

### State and connection

- [ ] Changing the lamp with its own touch controls shows up in HA within 30 s.
- [ ] Restart Home Assistant: the lamp becomes available again on its own and shows the right state.
- [ ] Unplug the lamp: it becomes unavailable within about a minute; a command meanwhile shows an error in the UI.
- [ ] Plug it in again: it becomes available again without a restart.
- [ ] With a proxy: restart the proxy, the lamp recovers on its own.
- [ ] Several hours of normal use: no unavailability, no errors other than an occasional retried command in the debug log.

### Housekeeping

- [ ] Rename the entity ID: the lamp keeps working under the new ID.
- [ ] Reload and delete the config entry without errors in the log.

## Candela

The maintainer has no Candela. Candela results come from users through the [Candela test report](https://github.com/V4n1X/ha_yeelight_bt/issues/new?template=candela_report.yml) issue template.
