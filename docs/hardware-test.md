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

After switching to real Kelvin and the quality scale changes (same instance):

| Check | Result |
| --- | --- |
| `color_temp_kelvin: 4000` | lamp and dashboard both neutral white; HA reports 4000 K |
| Reconfigure flow | opens with name and address (closed without change) |
| Diagnostics | lamp state, firmware and the ESPHome proxy used for the connection |
| Repair issues | none (model known) |
| HA restart | lamp available with the right state |

Delete and re-add the entry (after a manual backup):

| Check | Result |
| --- | --- |
| Lamp unplugged, discovery dialog submitted | error "Could not connect to or pair with the lamp …", no entry created, dialog stays open |
| Lamp plugged in again, same dialog submitted | entry created, no pairing needed |
| Restored settings | entity ID, name, icon, voice assistant exposure, device area and firmware restored (unique ID unchanged) |
| Discovery after deleting | **missing before the fix** (HA reports a device once per run); with `async_rediscover_address` on removal the lamp is discovered again immediately |
| Re-add through the flow | 3.4 s incl. the connection test; the first command right after took 16 s once (reconnect through the proxy), then 0.2 s |

Not tested yet: in-place entity ID change (needs HA 2026.11), long run over several hours, migration from HACS.

### 2026-11-02, Legacy protocol comparison with Yeelight APK 3.5.4

One Bedside (`XMCTD_`), connected locally on Windows/WinRT with bleak 3.0.2.
The HA connection was temporarily disabled by the user. Results below are
actual BLE notification readbacks, not simulated responses or optical measurements.

The version response was:

```text
43 5d 02 20 01 22 31 22 38 23 30 04 03 04 04 04 03 00
```

The APK's BCD decoder reads MCU A/B/BLE from frame bytes 5–6 / 7–8 / 9–10:
**2231-2238-2330**. The previous decoder instead displayed **8705-8753-9016**
by reading little-endian integers across field boundaries. The version labels
in the November 1 test runs above are historical output of that old decoder.

Before changing the integration, an independent raw-GATT probe passed **11/11**
checks:

| Check | Readback |
| --- | --- |
| State queries `43 44 00` and `43 44 02` | Same state |
| 4000 K with brightness 30 | White mode, 4000 K, 30% |
| RGB with the APK's `00` extra byte | Requested red, 30% |
| RGB with the old integration's `01` extra byte | Requested green, 30% |
| RGB with brightness sentinel `00` | Requested blue, brightness stays 30% |
| CT with brightness sentinel `00` | 3000 K, brightness stays 30% |
| Brightness 1 | On, 1% |
| CT with explicit brightness 5 | 4000 K, 5% |
| RGB with explicit brightness 5 | Requested red, 5% |
| Power off | Off |
| Power on | On |

Authorization with the existing Android-placeholder identifier returned `04`
(already paired). First-time authorization after a factory reset was not tested.
The original power, mode, colour and brightness were restored and verified.

The updated `yeelightbt.Lamp` was then exercised on the same lamp, using an
isolated Windows Python 3.14.7 environment with bleak 3.0.2 and
bleak-retry-connector 4.7.1. **7/7 checks passed**: correct version, RGB at 5%,
CT preserving 5%, RGB preserving 5%, brightness 1%, power off, and reconnect.
Every state check required a fresh notification, so optimistic local state
could not make it pass. The original state was restored and the PC disconnected.
These runs used the actual updated protocol class; HA entity/config-flow
behaviour was checked separately in the mocked HA test suites.

Initial regression validation: **62 tests passed** on HA 2026.10.4 and **62 passed**
on HA 2026.11.0b1 (Python 3.14.4). Ruff lint/format checks and the repository's
private-data checks passed. The beta environment emitted five deprecation
warnings from HA/backoff dependencies. No updated package was deployed to the
production HA instance during these runs.

Decisions from this run:

- Use the APK's `43 44 00` query and RGB `00` byte; their exact optional-field
  semantics beyond the tested operations remain unknown.
- Use the keep-brightness sentinel for colour/CT-only changes.
- Retain explicit 1–100% brightness support: the app's combined-command
  minimum of 10% is not a limit of this tested firmware.
- Correct BCD version decoding and use the captured version frame in tests.

Local captures are kept outside Git as
`yeelight-bedside-probe-20261102-172215.json` and
`yeelight-updated-driver-20261102-174233.json`. No Bluetooth addresses or
Home Assistant credentials from these runs were added to this report.

## Candela

The maintainer has no Candela. Candela results come from users through the [Candela test report](https://github.com/V4n1X/ha_yeelight_bt/issues/new?template=candela_report.yml) issue template.

The current legacy Gingko implementation follows APK 3.5.4 for power/brightness
payloads, six-byte authorization identifiers, status offsets and ASCII firmware
versions. Tests use explicitly labelled APK-derived examples, not hardware captures.
New-device setup derives the identifier from the persistent HA instance UUID and
stores it only after successful authorization. Runtime uses that stored value;
existing entries without one stay command-only after upgrades. If the backend cannot subscribe to
notifications, command-only operation remains available without an authorization
request that could not be answered. The APK's Android-only notification setup
without a CCCD write does not establish support in every HA Bluetooth backend.
Until a valid state notification arrives, the HA entity continues to report an
assumed state even when subscription and authorization have succeeded.
Successful reconnects preserve already-confirmed feedback. An unknown Candela
mode does not discard the independent power and brightness fields.

### Review regression checks

Four new cases reproduced failures before the compatibility fixes: an existing
Candela entry with only `PAIR_REQUESTED`, one with no pairing response, a Bedside
reconnect initiated by brightness commands, and a Candela off notification with
an unknown mode. The fixes keep existing entries command-only, persist only
confirmed setup identifiers, retain confirmed feedback after successful
reconnects, and consume Candela power/brightness independently of its mode.

After the fixes, **68/68 tests passed** on both HA 2026.10.4 and HA 2026.11.0b1.
Tests also cover failed setup authorization followed by an unsupported backend:
an unconfirmed identifier is not saved. Backend recovery from unsupported to
supported notifications resumes polling. This follow-up used simulated BLE
responses and did not repeat the physical hardware runs above.

### New-device setup fallback checks

The next review exposed the same authorization compatibility issue during fresh
Candela setup. Five cases failed before the fix: no response or only
`PAIR_REQUESTED` through discovery and manual auto-detection, plus a disconnect
before confirmation. Setup now closes the attempted connection and retries once
without a pairing identifier. A successful retry saves the Candela model but no
`pairing_id`; a failed retry does not create an entry. Explicit authorization
refusals and Bedside timeouts remain errors.

The resulting suite passed **74/74 tests** on HA 2026.10.4 and HA 2026.11.0b1.
These tests use simulated BLE responses; no additional hardware run was performed.
