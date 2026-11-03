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

### Night light

- [ ] The switch and both times show the lamp's window instead of `unavailable`.
- [ ] Switching the night light on and off in Home Assistant changes it in the Yeelight app (or after re-adding the lamp there).
- [ ] Changing a time keeps the switch state and the other time.
- [ ] A window that crosses midnight (21:00–07:00) is active in the morning.
- [ ] Turning the lamp on inside the window: orange at 1 %, also from the lamp's own button.
- [ ] Changing the window in the Yeelight app shows up in Home Assistant after the next connect.
- [ ] Through a backend without notifications: the three entities stay `unavailable`, the light keeps working.

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

### 2026-11-02/03, Bedside extended legacy features and app capture

One Bedside (`XMCTD_`), connected from a Windows PC (WinRT, bleak 3.0.2, the
updated `yeelightbt.Lamp` for connect and pairing, raw frames for the rest), the
Home Assistant connection disabled. The protocol results are in
[app-ble-protocol.md](app-ble-protocol.md#55-bedside-extended-commands-verified-on-hardware-and-against-the-app-2026-11-0203).
Every write was read back and restored, except the factory byte of the stored
flow steps (see "Restore"); the captures stay outside Git.

| Check | Result |
| --- | --- |
| Read-only queries (state, version, name, night-light, delay-off, alarms, wake-up, lamp time, colour flow entries 1–3) | All answered except the wake-up read, which returns the command response `43 82 43 89 02` when nothing is set. Slot 0 of the alarms returns nothing. |
| Lamp time | `43 60` accepted, readback matches the PC clock; the lamp clock was about 14 min off before. The app sets the time on every connect. |
| Night-light | Set to 22:30–06:45 with BCD and with binary bytes: the lamp echoes the bytes verbatim, so the readback does not show the encoding (the app sends BCD). In the app capture, a power-on inside the active window 21:00–07:00 came up orange at 1 % instead of the previous colour at 100 %. Restored to off, 00:00–05:00. |
| Delay-off | 20 min set, readback shows the remaining seconds counting down (1198 → 1131 s after 65 s); stop works; the remembered duration was restored to 15 min. |
| Alarm | Slot 0 is not stored. Slot 1 (probe: daily, once) and slot 2 (app: Mon–Fri) were written and read back unchanged; slots 3–5 were not written. `43 48 <slot>` deletes. Two once-alarms set by the probe (today's day, `act = 03`) fired on time (00:42:02, 00:56:01 by the state polled every second). With gradual `5` the lamp was fully bright within a few seconds (seen by eye); the state reported 100 % at once. |
| Stored entry byte at offset 8 | Written as `0x14`, `0x65` and `00`: switching back to the colour entry kept the current 50 % every time, no visible effect. |
| Wake-up | Set, read (`43 8A`), disabled via the enable flag by the app. `43 48 06` and `43 8B 06` each delete it on their own (tested separately). |
| Colour flow | 4 colours stored and read back; live flow `43 7C 06 …` and mode select `43 4A 02 01 01` switch the lamp into flow mode (state mode 3). Seen by eye: red → green → blue → yellow at 2 s, at the brightness sent with `43 42`. |
| Beacon pairing mode | `43 54 01` → status `AE 01`, `43 54 02` → `AE 00`. No remote was added. |
| Factory reset (`43 74 00`) | Acknowledged, then the lamp dropped the connection and needed the button for pairing. Factory state read back (see the protocol notes); the clock kept running. The reset brought the factory flow with `0x65` back. Restored afterwards, including `0x65` of the stored white and colour entries (writable through `43 4A`); rewriting the stored flow with `43 7C 02` during the restore put `00` into the flow steps, which `43 7C` cannot set back. |
| Name | Written by the app ("Probe"), read back, restored to empty with two empty chunks. |
| Official app 3.5.4 (Android 16) | Login with a Xiaomi account, adding the Bedside, setting night-light, timer, alarms (once, Mon–Fri), wake-up, flow, white, colour and name while capturing the HCI traffic. The capture matches the frames from the static analysis. |
| Deleting the lamp in the app | The app sends `43 72 02` and **removes the authorization** the integration uses. Pairing again answered `43 63 01` (pulses), `43 63 02` after the button, `43 63 05` after a 60 s timeout. After the button push the PC connected with the unchanged identifier (`43 63 04` on later connects). Home Assistant was not run; the expected behaviour is in the protocol notes. |
| Restore | After the runs: lamp off, original colour and brightness, night-light and delay-off as before, name empty, no alarms or wake-up, lamp time correct, stored white and colour entries as before. Left over: the stored flow steps carry `00` instead of the factory `0x65` at payload offset 8 (no visible effect found). |

Resolved from the APK instead of hardware: the Sunday weekday value (0), the
next-day rule of the mode 1 repeat byte and the action `04` (switch off); the
repeat byte as today's day and `03` (switch on) fired on hardware. Not tested:
alarm mode 3 by probe (only through the app), alarm slots 3–5, Home Assistant
after re-pairing, adding a remote, OTA (deliberately, no firmware image) and
anything on the Candela. The app was removed from the test phone afterwards and the lamp deleted
from the app.

### 2026-11-03, clock sync and unpair with the updated driver

Same Bedside from the Windows PC with the updated `yeelightbt.Lamp`, Home Assistant
disconnected. The suites (86 tests on HA 2026.10.4 and 2026.11.0b1), ruff, mypy
`--strict` and hassfest passed before this run.

| Check | Result |
| --- | --- |
| Clock sync on connect | The lamp read back 02:15:43, Tuesday (weekday 2), one second before the PC's 02:15:44. |
| `unpair()` | `43 72 02` written; the following connections from the PC were answered `43 63 06` without pulsing, also after a power cycle: the lamp was **off** all the time. |
| Re-adding through Home Assistant (installed release, through the ESPHome proxy) | No pulsing while the lamp was off; after switching it on at the lamp it pulsed and the button paired it. The entry works again. |

Controlled pairing answers from the PC (Home Assistant entry disabled):

| Lamp | Authorized | Answer to `43 67 02` |
| --- | --- | --- |
| off | yes | `04` |
| on | yes | `04` |
| on | no (after `43 72 02` from the same PC) | `01`, the lamp pulses; `05` exactly 60 s later without the button |
| off (switched off at the lamp) | no | `06`, no pulsing |
| switched on again 30 s later | no | `01` at once |

So `06` means "not authorized and switched off". The PC was accepted again after
its own `43 72`; nothing is blocked.

Then the updated integration in the maintainer's Home Assistant (HAOS 2026.10.4,
ESPHome proxy), after a manual backup and an archive of the installed files:

| Check | Result |
| --- | --- |
| Repair issue | Entry enabled with the lamp unauthorized and off: `06` on every attempt, `pairing_required` issue shown, log "not paired and switched off". Switched on: `01`, button, `02`; the issue disappeared and the light was available again. |
| Clock sync | Lamp clock set to 2026-01-01 12:00 from the PC; after Home Assistant connected it read 2026-11-03 03:02:34, Tuesday (Home Assistant's time zone). |
| Deleting the entry | `04`, then "authorization removed" in the debug log. The delete call took 25.5 s (connection through the proxy). |
| Re-adding | The lamp (on) pulsed and was paired with the button; the entity came back with its old entity ID. |

In these two runs the button was pushed 25 s and 4 s after Home Assistant's pairing
request: the lamp already pulsed before that request (apparently from the
connection on), so the pulsing does not tell when Home Assistant's window starts.
A third run timed the push from the logged request instead:

| Check | Result |
| --- | --- |
| 60 s button window (runtime) | Lamp unpaired from the PC, entry enabled: request logged at 03:17:51, the button pushed at a given time 40 s later, paired at 03:18:36 (45.5 s, beyond the former 30 s). Light available and on, no issue left. |
| Device page while connecting | With the assumed-state change, the page showed the normal switch while Home Assistant connected (before: two "assumed state" buttons until the first state). |

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
