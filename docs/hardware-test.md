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

## Test runs

### 2026-11-01, Bedside, protocol level (no HA)

Bedside (`XMCTD_`, firmware 8705-8753-9016) on Windows/WinRT (Qualcomm FastConnect 7800), bleak 3.0.2, using `yeelightbt.Lamp` directly. 17/17 automated checks passed after the fixes below, plus a visual check:

| Check | Result |
| --- | --- |
| Discovery (advertised name `XMCTD_`), connect + pair | OK, about 4 s, no button needed; reconnect answers "already paired" (0x04) |
| On / off, brightness 1 %, 10 %, 100 % | OK; 1 % is very dim but on |
| White 1700 K / 4080 K / 6500 K | OK: very warm / neutral white / cold bluish. 4080 K, where HA's "white" is mapped, looks neutral |
| Red, green, blue, purple | OK |
| Change on the lamp's touch slider | reported by the lamp on the next state request |
| 10 brightness commands in a row | OK, about 90 ms per command |
| Command right after power on | **Ignored within 0.5 s, accepted after 1.0 s** → `POWER_ON_DELAY` raised to 1.5 s |
| Color/brightness/temperature while off | ignored by the lamp → no longer marked as on |
| Fast reconnect on WinRT | raised a plain `OSError` → now retried |

### 2026-11-01, Bedside in Home Assistant

Same lamp, Home Assistant OS 2026.10.4 (the maintainer's production instance, after a manual backup), connected through an ESPHome bluetooth proxy (ESP32, 3 connection slots), 10 Shelly proxies passive only.

| Check | Result |
| --- | --- |
| Discovery after installing | OK, "Yeelight Bedside" discovered, German confirmation dialog, no pairing needed |
| Entity / device | OK; the old entity (`light.nachttischlampe`, name, icon) was restored through the unchanged unique ID; device shows model Bedside and firmware 8705-8753-9016 |
| White (HA 2740 K) 80 %, warm 2000 K 50 % | OK, neutral white / warm white as seen on the lamp |
| Turn on with blue from off (upstream #37) | OK, blue directly (2.5 s incl. the power-on wait) |
| `brightness: 0` | turns the lamp off |
| Touch control on the lamp | reported to HA immediately (pushed by the lamp) |
| HA restart | lamp available with the right state right after the start, setup took 0.002 s |
| Unplug the lamp, send a command | error "Could not send the command …"; **102 s** before the fix below, **57 s** after; entity unavailable |
| Plug it in again | available again after 20–40 s without restart |
| Proxy dropped (ESPHome entry reloaded) | lamp stays available, next command reconnects (2.5 s) |
| Reload of the config entry | OK |
| Log | no entries of `yeelight_bt` during all tests |

Not tested yet: in-place entity ID change (needs HA 2026.11), long run over several hours, delete and re-add of the entry, migration from HACS.

## Candela

The maintainer has no Candela. Candela results come from users through the [Candela test report](https://github.com/V4n1X/ha_yeelight_bt/issues/new?template=candela_report.yml) issue template.
