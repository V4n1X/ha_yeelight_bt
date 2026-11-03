# Yeelight Android app 3.5.4 — BLE device and protocol notes

Status: static APK reference, with the Bedside core commands additionally
verified in the [2026-11-02 hardware run](hardware-test.md#2026-11-02-legacy-protocol-comparison-with-yeelight-apk-354)
and the extended Bedside features (clock, night-light, timer, alarms, wake-up,
colour flows, name, authorization) verified in the
[2026-11-02/03 run](hardware-test.md#2026-11-0203-bedside-extended-legacy-features-and-app-capture)
and described in section 5.5.

This document describes the Bluetooth Low Energy (BLE) device families and
protocols found in the Yeelight Android app `com.yeelight.cherry` 3.5.4
(version code 35402). It focuses on the two legacy Yeelight BLE products that
are relevant to this integration — the **Bedside Lamp** and the **Candela /
Ambiance Lamp** — and summarises the newer MIoT BLE and BLE-mesh device models
that ship in the same APK.

The APK inspection did not execute the app or verify its signature. A subsequent
Bedside hardware run and the resulting integration changes are described below.
Candela findings remain static observations.

| Item | Value |
| --- | --- |
| APK | `com.yeelight.cherry_3.5.4-35402_minAPI19(arm64-v8a,armeabi,armeabi-v7a,x86,x86_64)(nodpi).apk` |
| SHA-256 | `abdcffbcf118e5d5c48088a478c8ea49f4c3fa911db1f1bc5013fed1d61fb7f2` |
| Package | `com.yeelight.cherry` |
| Version | 3.5.4 (35402) |
| minSdk / targetSdk | 19 / 33 |
| Analysis | JADX/REA static inspection; APK not executed |

## 1. Summary

The app contains two independent BLE stacks:

1. **Legacy Yeelight BLE protocol** — binary GATT packets constructed from hex
   strings by the app, used by the original Bedside Lamp
   (`r3.r`, logs as `CherryDevice`) and the Candela/Ambiance Lamp (`r3.f0`,
   model `yeelink.light.gingko`). Both use the same 18-byte command frame and
   the `BleMessage` / `d4.c` command tables, but with different GATT services
   and a few different payload variants.
2. **MIoT BLE stack** — the Xiaomi `com.miot.bluetooth` and
   `miot.bluetooth.security` libraries, used for newer Yeelight BLE products
   such as the Bedside Lamp 2/3, the BLE remote controls, the Quick Curtain,
   the mesh gateway and the BLE-mesh lights. These devices are described by
   MIoT XML specifications in `assets/` and use MIoT property/action IDs and
   per-device binding/authentication rather than the legacy ASCII frames.

The GATT UUIDs used by the legacy Bedside path match this repository's
integration exactly:

- control: `aa7d3f34-2d4f-41e0-807f-52fbf8cf7443`
- notify: `8f65073d-9f57-4aaa-afea-397d19d5bbeb`

The app also reveals several payload differences from the baseline integration
(see section 5), most notably the pairing payload, the state-query payload and
the colour payload.

## 2. Legacy Yeelight BLE — Bedside Lamp (“Cherry”)

### 2.1 Device identity

`r3.r` is the legacy BLE device implementation. Evidence:

- its log messages use `CherryDevice` and `Cherry Device`;
- its fallback name uses `R$string.yeelight_device_name_cherry`, which is
  `"Bedside Lamp"`;
- it uses the `BleMessage` hex-string command enum and the `d4.c` command builders;
- it implements the same `aa7d3f34…` / `8f65073d…` GATT pair as this
  integration.

No advertised-name check was found in the legacy path; it is selected from the
stored device record/MAC. The `XMCTD_*` advertised name used by this
integration is therefore an empirical discovery convention, not something this
APK depends on.

### 2.2 GATT layout

Service discovery is implemented in
`com.yeelight.yeelib.device.connections.a.C0107a.c.run()`.

| Service | Characteristic | Role |
| --- | --- | --- |
| `8E2F0CBD-1A66-4B53-ACE6-B494E25F87BD` | `aa7d3f34-2d4f-41e0-807f-52fbf8cf7443` | control write (`CHAR_CHERRY_CTRL`) |
| `8E2F0CBD-1A66-4B53-ACE6-B494E25F87BD` | `8f65073d-9f57-4aaa-afea-397d19d5bbeb` | notifications (`CHAR_CHERRY_NOTIFY`) |
| `8E2F0CBD-1A66-4B53-ACE6-B494E25F87BD` | `f000fff1-0451-4000-b000-000000000000` | OTA control (`CHAR_OTA_CTRL`) |
| `8E2F0CBD-1A66-4B53-ACE6-B494E25F87BD` | `f000fff2-0451-4000-b000-000000000000` | OTA notifications (`CHAR_OTA_NOTIFY`) |
| `f000ffc0-0451-4000-b000-000000000000` | `f000ffc1-0451-4000-b000-000000000000` | OAD notifications (`CHAR_OAD_NOTIFY`) |
| `f000ffc0-0451-4000-b000-000000000000` | `f000ffc2-0451-4000-b000-000000000000` | OAD block writes (`CHAR_OAD_BLOCK`) |

The same `aa7d3f34…` / `8f65073d…` characteristics also appear under the
Xiaomi service `0000fe87-0000-1000-8000-00805f9b34fb` for the Gingko/Candela
path. They are not unique to the `8E2F0CBD…` service.

### 2.3 Frame format

The standard legacy control commands below use an 18-byte frame:

```text
byte 0     0x43 ('C')
byte 1     command / response code
bytes 2-17 binary payload and zero padding
```

The implementation builds the frame as a hex string and then converts it to
bytes:

1. `s3.d.a()` concatenates the two-byte command (`"4340"`, `"4341"`, …) and
   each payload argument.
2. It pads the resulting string with the ASCII character `'0'` until it is
   36 characters long.
3. `com.yeelight.yeelib.utils.a.b()` converts every two hex characters into one
   byte.

Standard control packets are padded to 18 bytes. The builder does not truncate
oversized arguments. Missing payload bytes become `0x00`.
The strings in `BleMessage` (`"4340"`, `"4345"`, …) are the hex representation
of the raw bytes, not ASCII text sent over the air.

Example — power on:

```text
43 40 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

### 2.4 Core command table

The command values come from `com.yeelight.yeelib.device.models.BleMessage`;
the payload builders come from `d4.c`. “Payload” below means the bytes after
the two command bytes.

| Command | Name | Builder / direction | Payload | Notes |
| --- | --- | --- | --- | --- |
| `43 40` | ONOFF | `d4.c.G`, `d4.c.x` | `01` on, `02` off | `x()` adds `01 01 F4` (Gingko variant) |
| `43 41` | COLOR | `d4.c.t` | `RR GG BB 00 BB` | App uses `00` in frame byte 5; trailing `BB` is `00` when brightness is `-1`; baseline integration used `01` |
| `43 42` | BRIGHTNESS | `d4.c.l`, `d4.c.v` | `BB` (1–100) | `v()` adds `01 01 F4` (Gingko variant) |
| `43 43` | COLORTEMPERATURE | `d4.c.J` | `TT TT BB` | Kelvin big-endian; trailing `BB` is `00` when brightness is `-1`, otherwise clamped to ≥10 |
| `43 44` | INQIRY | `d4.c.r`, `d4.c.w` | `00` | State query |
| `43 45` | STATUSNOTIFITATION | notification | see 2.5 | State report |
| `43 46` | SETALARM | `d4.c.n` | 11 bytes | Alarm/timer definition |
| `43 47` | READALARM | `d4.c.A`, `d4.c.B` | `FF` = all, otherwise index | Reads alarm slots |
| `43 48` | DELETEALAARM | `d4.c.b` | slot index | Deletes one alarm |
| `43 49` | ALARMNOTIFICATION | notification | `utils.a.k()` | Alarm list/entry |
| `43 4A` | COLORLFOW | `d4.c.E`, `d4.c.F` | mode + parameters | Colour-flow definition |
| `43 4B` | COLORTIMERANDLIGHTNESS | — | — | Defined, no builder found |
| `43 4C` | READCOLORLFOW | `d4.c.o` | index | Reads one flow entry |
| `43 4D` | COLORFLOWNOTIFICATION | notification | index 1/2/3/`FF` | Flow readback |
| `43 4E` | SCENEMODE | — | — | Defined, no builder/handler found |
| `43 4F` | READSCENEMODE | — | — | Defined, no builder/handler found |
| `43 50` | SCENENOTIFICATION | — | — | Defined, no handler found |
| `43 51` | LIGHTNAME | `d4.c.y`, `d4.c.z` | `01 <part> <len> <data>` | Always two chunks; first chunk 13 bytes, second the remainder |
| `43 52` | READLIGHTNAME | `d4.c.u` | `00` | Reads the name |
| `43 53` | LIGHTNAMENOTIFICATION | notification | `part` at offset 1, `len` at 2, data at 3 | Name readback |
| `43 54` | ENABLEBEACON | `d4.c.m` | `01` enable, `02` disable | Beacon/remote pairing |
| `43 55` | ADDBEACON | `d4.c.p` | 16 bytes | Adds a beacon/remote |
| `43 56` | DELETEBEACON | `r3.r.r2` | 16 bytes | Deletes a beacon/remote |
| `43 57` | READSINGLEBEACON | — | — | Defined, no builder found |
| `43 58` | SINGLEBEACONNOTIFICATION | — | — | Defined, no handler found |
| `43 59` | SETGRADUAL | — | — | Defined, no builder found |
| `43 5A` | READGRADUAL | — | — | Defined, no builder found |
| `43 5B` | GRADUALNOTIFICATION | — | — | Defined, no handler found |
| `43 5C` | READVERSION | `d4.c.q` | `00` | Firmware version query |
| `43 5D` | VERSIONNOTIFICATION | notification | Cherry: BCD payload bytes 3–8; Gingko: ASCII | See 2.6 |
| `43 5E` | READSERIALNUMBER | — | — | Defined, no builder found |
| `43 5F` | SERIALNUMBER | — | — | Defined, no handler found |
| `43 60` | SETLAMPTIME | `d4.c.s` | sec, min, hour, day, weekday, month, year−2000 | BCD decimal |
| `43 61` | READLAMPTIME | — | — | Defined, no builder found |
| `43 62` | LAMPTIMERNOTIFICATION | — | — | Defined, no handler found |
| `43 63` | AUTHORIZATIONNOTIFICATION | notification | auth state | See 2.7 |
| `43 64` | READALLBEACON | `d4.c.K` | `00` | Reads all beacons |
| `43 65` | ALLBEACONNOTIFICATION | notification | MAC + suffix | Beacon list entry |
| `43 66` | SWITCH_OAD | `d4.c.H` | — | Switches to OAD/OTA mode |
| `43 67` | pairing | `d4.c.k` | six identifier bytes | See 2.7 |
| `43 6F` | SETNIGHTLIGHT | `d4.c.V` | enabled, `01`, start h/m, end h/m | Night-light window |
| `43 70` | READNIGHTLIGHT | `d4.c.O` | `00` | Night-light query |
| `43 71` | NIGHTLIGHTNOTIFICATION | notification | enabled, brightness, times | Night-light readback |
| `43 72` | DELETEAUTHORIZE | `d4.c.c` | six identifier bytes | Removes the pairing/authorisation |
| `43 73` | DELETESCENE | — | — | Defined, no builder found |
| `43 74` | RESET | `d4.c.S` | `00` | Factory reset |
| `43 77` | READFIRSTSTARTUP | — | — | Defined, no builder found |
| `43 78` | READFIRSTSTARTUPNOTIFICATION | — | — | Defined, no handler found |
| `43 7C` | SIMPLECOLORFLOW | `d4.c.D`, `d4.c.Q` | 16 bytes | Simple flow definition |
| `43 7F` | SETDELAYTIME | `d4.c.T`, `d4.c.U` | mode + time | Delay-off |
| `43 80` | READDELAYTIME | `d4.c.L` | `00` | Delay-off query |
| `43 81` | DELAYTIMENOTIFICATION | notification | Cherry: bytes 1/3/4; Gingko: bytes 2/3 | Delay readback |
| `43 82` | COMMANDRESPONSE | notification | echoed command + status | Command acknowledgement |
| `43 88` | WAKEUPSET | `d4.c.a` | 11 bytes | Wake-up alarm |
| `43 89` | WAKEUPREAD | `d4.c.P` | `06` | Wake-up query |
| `43 8A` | WAKEUPNOTIFICAION | notification | — | Wake-up readback |
| `43 8B` | WAKEUPDELETE | — | — | Defined, no builder found |
| `43 8C` | READRECORDS | `d4.c.R` | `00` | Reads stored records |
| `43 8D`–`43 91` | RECORDSNOTIFICATION1–5 | notification | raw chunks | Stored-record readback |
| `43 A0` | READGINGKOBATTERY | `d4.c.N` | `00` | Candela battery query |
| `43 A1` | BATTERYNOTIFICATION | notification | — | Candela battery |
| `43 A2` | GINGKOEXITFACTORY | `d4.c.d` | `00` | Candela factory mode |
| `43 A3` | GINGKOQUERYFACTORY | `d4.c.M` | `00` | Candela factory query |
| `43 A4` | FACTORYNOTIFICATION | notification | — | Candela factory state |

Commands marked “defined, no builder/handler found” exist in `BleMessage`, but
no active path was found in the legacy Cherry/Gingko code. They may be used by
other firmware, older app versions or the cloud path.

### 2.5 State notification (`43 45`)

Cherry/Bedside layout (`r3.r.G1`), payload offsets:

| Payload offset | Meaning |
| --- | --- |
| 0 | power: `1` = on |
| 1 | mode: `1` = colour, `2` = white/CT, `3` = flow |
| 2–4 | red, green, blue |
| 5 | unused/unknown |
| 6 | brightness (0–100) |
| 7–8 | colour temperature, big-endian Kelvin |
| 9–15 | unused/padding |

Example — on, colour mode, red, brightness 100, CT 4000 K:

```text
43 45 01 01 FF 00 00 00 64 0F A0 00 00 00 00 00 00 00
```

Gingko/Candela layout (`r3.f0.G1`) is different:

| Payload offset | Meaning |
| --- | --- |
| 0 | power: `1` = on |
| 1 | brightness |
| 2 | mode: `2` = white/CT, `3` = flow |
| 3–4 | colour temperature, big-endian (white mode) |
| 6 | brightness (flow mode) |
| others | unused/unknown |

This difference matters if state feedback is ever added for the Candela path.

### 2.6 Firmware version (`43 5D`)

Cherry/Bedside (`utils.a.l`). Offsets are payload offsets (the bytes after
`43 5D`); add 2 for the frame offset:

- payload byte 0: current image (`1` = image B);
- payload bytes 3–4: MCU A version, BCD/hex-nibble encoded (frame bytes 5–6);
- payload bytes 5–6: MCU B version (frame bytes 7–8);
- payload bytes 7–8: 2541 version (frame bytes 9–10).

Gingko/Candela (`r3.f0.G1`):

- the payload is an ASCII version string terminated by `0x00`; it is parsed
  with `new s3.g(str)` rather than the fixed BCD fields.

### 2.7 Pairing / authorisation

The app's pairing request is built by `d4.c.k()` and sent by `o3.a.A()` (Cherry)
or `o3.c.A()` (Gingko):

```text
43 67 <identifier byte 0> ... <identifier byte 5> 00 ...
```

The payload is built from `ConnectionBase.j()` split on `:`. Cherry
(`connections.a`) overrides `j()` and returns the adapter address directly.
Gingko (`connections.b`) inherits `ConnectionBase.j()`:

- below Android 6 (API 23), it returns the adapter address;
- from API 23, it calls `AppUtils.b()`, which returns a cached `KEY_APP_UID`
  value if one exists and is not `02:00:00:00:00:00` (otherwise a random UUID
  on first use), removes only the time-separator characters (hyphens, not
  colons), and truncates the result to 12 characters.

The caller splits on `:` and `d4.c.k()` copies the fields in order. A UUID-derived
12-hex identifier produces six bytes. A cached MAC with colons such as
`AA:BB:CC:DD:EE:FF` is not colon-stripped: after truncation to 12 characters
it becomes `AA:BB:CC:DD:`, so `split(":")` yields four fields and the payload
is `AA BB CC DD 00 00` after padding — not the original six MAC bytes. On
Android 6+ the Cherry adapter address is `02:00:00:00:00:00`, so Cherry's frame
is exactly `43 67 02` followed by zeros — byte-identical to the current
integration's `43 67 02` frame. The two cannot be distinguished on the wire;
the integration's `02` may be a zeroed-address byte rather than a pairing
subcommand.

The lamp answers with `43 63`:

| Payload byte 0 | Cherry meaning | Gingko meaning |
| --- | --- | --- |
| `1` | authorisation in progress (`AUTH_IP`) | authorisation in progress |
| `2` | success (`AUTH_SUC`) | success |
| `3` | failure (`AUTH_FAIL`) | failure |
| `4` | success (already paired) | failure |
| `5`, `6`, `7` | failure | failure |

The app's `authority.a` maps the Cherry values as follows:

- `1` → `AUTH_IP`
- `2`, `4` → `AUTH_SUC`
- `3`, `5`, `6`, `7` → `AUTH_FAIL`

The current integration sends `43 67 02` and treats frame byte 2 (payload
byte 0) of `43 63` as the pairing state. As described above, that frame is also
what the app produces for a zeroed identifier; it is not evidence of a
different protocol. Whether the lamp treats the six bytes as an address or
ignores them still needs hardware testing.

### 2.8 Name, night-light, delay and alarms

Static description; for the Bedside the hardware results in section 5.5 take
precedence where they differ.

- **Name** (`43 51`): `d4.c.y()` → `d4.c.z()` always emits two chunks, each
  `01 <part> <length> <data>`: part `00` with up to 13 UTF-8 bytes
  (hex-encoded), then part `01` with the remainder, or an empty payload for
  short names. The lamp replies with `43 53`; the handler reads part and length
  at payload offsets 1 and 2 and the data at offset 3, finalising on part `01`
  (payload byte 0 is ignored).
- **Night-light** (`43 6F`): `enabled`, `01`, start hour, start minute, end
  hour, end minute. Times are BCD decimal (`%02d`). `43 71` reports the same
  fields; byte 1 is brightness.

  Home Assistant maps this to a switch and two time entities, all of the
  *configuration* category, and only for the Bedside:

  | Frame | Entity |
  | --- | --- |
  | `43 6F` byte 2 | `switch.<lamp>_night_light` (`01` on, `00` off) |
  | `43 6F` bytes 4–5 | `time.<lamp>_night_light_start` |
  | `43 6F` bytes 6–7 | `time.<lamp>_night_light_end` |
  | `43 6F` byte 3 | not exposed, always sent as `01` (1 %), as the app does |
  | `43 70 00` → `43 71` | read on every connection, updates all three entities |

  Every write sends the complete frame, so a single change keeps the values
  read from the lamp. This is why the entities are unavailable when the backend
  cannot subscribe: without `43 71` the other fields are unknown. The window
  may cross midnight; the lamp's clock (set on every connection) decides when
  it is active.

- **Delay-off** (`43 7F`): Cherry sends mode `3` (enable) or `4` (disable)
  plus minutes. Gingko sends mode `1`/`2`, a literal `02`, then the value.
  `43 81` reports the state; Cherry uses bytes 1/3/4, Gingko uses bytes 2/3.
- **Alarm** (`43 46`): 11 payload bytes: `0` slot/sync ID, `1` hour, `2`
  minute, `3` second, `4` mode (`1` single, `2` day repeat, `3` week repeat),
  `5` repeat encoding, `6–7` gradual duration (the parser reads offset `7`),
  `8` on/off flag, `9` sync-to-alarm flag, `10` enable flag. Hour/minute/second
  are two decimal digits packed as one byte (BCD-style). `43 47` reads one slot
  (`FF` for all), `43 48` deletes one slot, `43 49` reports entries with the
  same layout (`utils.a.k()`).
- **Wake-up** (`43 88`): same general shape as an alarm, with `43 89` as the
  query and `43 8A` as the notification.
- **Colour flow** (`43 4A` / `43 7C`): mode/parameter blocks and up to four
  RGB entries. `43 4C` reads one entry; `43 4D` reports index `1` as colour
  temperature (payload bytes 9–10), index `2` as RGB (payload bytes 4–6) plus
  interval (payload bytes 11–12), index `3` as the current colour (payload
  bytes 4–6), and `FF` terminates the read.

### 2.9 Beacon/remote commands

The legacy path also manages Yeelight remotes/beacons:

- `43 54 01` / `43 54 02` — enable/disable beacon pairing;
- `43 55` — add a beacon (16-byte record);
- `43 56` — delete a beacon (16-byte record);
- `43 64 00` — read all beacons;
- `43 65` — beacon list entry, MAC assembled from bytes 2–7 in reverse order,
  followed by `#byte0#byte1`;
- `43 82` — command response, echoing the original command and a status byte;
- `EF 00` — beacon read complete;
- `AE 00` / `AE 01` — beacon status off/on.

On the Bedside both answers arrive padded to 18 bytes (section 5.5).

`EF 00` and `AE 00`/`AE 01` are two-byte response codes outside the `43`
command space. The app's notification dispatcher takes the first two bytes as
the code and preserves any remaining bytes, so the total wire length of these
notifications is not established; padded forms are accepted. It still wraps
them in `s3.p` and matches them as `BleMessage` values; `EF 00` finalises the
beacon list, and `AE 00`/`AE 01` update the beacon status flag.

### 2.10 OTA

The legacy path supports two OTA mechanisms:

1. **Binary OTA control** on `CHAR_CHERRY_CTRL` / `CHAR_OTA_CTRL`:
   - `43 66` — switch to OAD/OTA mode;
   - `80 01` — request OTA (argument `01` or `02`);
   - `81 01` — request response;
   - `80 02` — sync ID (16-bit);
   - `81 02` — sync response;
   - `80 04` — check CRC;
   - `81 04` — CRC response;
   - `80 05` — write flash (16-bit package index, not a byte address);
   - `81 05` — write response;
   - `80 03` — 18-byte MCU OTA data block: `80 03` followed by 16 firmware
     bytes (no block counter);
   - `80 07` — finish OTA (four-digit version);
   - `81 07` — finish response.
2. **Raw OAD blocks** on `CHAR_OAD_BLOCK`
   (`f000ffc2-0451-4000-b000-000000000000`): 18-byte blocks, first two bytes
   are a 16-bit block counter, followed by 16 bytes of firmware data. This is
   the only path with a counter; the `80 03` blocks above do not carry one.

The OTA code is in `r3.r` (`A2`, `B2`, `C2`, `D2`, `I1`, `Y0`) and is not part
of the normal light-control path.

## 3. Legacy Yeelight BLE — Candela / “Gingko”

`r3.f0` is the Gingko implementation. Its constructor uses the model
`yeelink.light.gingko`; its fallback name is
`R$string.yeelight_device_name_gingko` (`"Ambiance Lamp"`), and the group name
is `R$string.yeelight_device_name_gingko_group` (`"Candela Group"`).

It uses the same `BleMessage` / `d4.c` command set and the same 18-byte frame,
but:

- the GATT service is `0000fe87-0000-1000-8000-00805f9b34fb`;
- the control characteristic is still `aa7d3f34-2d4f-41e0-807f-52fbf8cf7443`;
- the notify characteristic is still `8f65073d-9f57-4aaa-afea-397d19d5bbeb`;
- power and brightness use the extended payloads (`43 40 … 01 01 F4` and
  `43 42 … 01 01 F4`);
- the status, version, delay and authorisation notification layouts differ
  from Cherry (sections 2.5–2.7);
- OTA uses the Telink characteristic `00010203-0405-0607-0809-0a0b0c0d1913`.

The baseline integration at `4d47993` used command-only Candela support. The
app's Gingko path contains authorization, notification setup and state parsing.
The local update in section 5.4 uses those facilities when the backend can
subscribe. The inspected connection methods do not establish the app's complete
advertised-name matching logic.

## 4. MIoT device models (BLE, Wi-Fi+BLE and mesh)

The APK ships MIoT XML specifications in `assets/`. These are device models,
not a raw byte protocol: the `com.miot.bluetooth` / `miot.bluetooth.security`
stack translates properties and actions into the device's GATT frames and
handles per-device binding, login and encryption.

Some of these models are also used over Wi-Fi; the table lists the models
referenced by BLE-capable code paths or by the mesh classes, not a claim that
every model is BLE-only. The BLE-relevant models found in the assets are:

| Asset | Model | Device | Main functions |
| --- | --- | --- | --- |
| `YeelightBsLamp1.xml` | `yeelink.light.bslamp1` | Bedside Lamp 1S | power, brightness, CT, RGB/HSV, flow, scenes, night-light, delay, cron |
| `YeelightBsLamp2.xml` | `yeelink.light.bslamp2` | Bedside Lamp 2 | same as above, without the extra BsLamp1 fields |
| `YeelightBsLamp3.xml` | `yeelink.light.bslamp3` | Bedside Lamp 3 | power, brightness, CT, RGB/HSV, flow, scenes, night-light, delay, cron, music mode, knock |
| `YeelightRemoteControl.xml` | `yeelink.controller.v1` | Yeelight remote | battery status, button event (button 1–5, event type 0–2) |
| `YeelightBleController.xml` | `yeelink.bleremote.v1` | BLE remote/controller | empty spec in this build |
| `YeelightQuickCurtain.xml` | `yeelink.curtain.ctmt2` | Quick Curtain | position, direction, step, calibration, battery/charging, custom positions, open mode, LED, self-propelled, cron |
| `YeelightBHFLight.xml` | `yeelink.bhf_light.v1` | 5-in-1 bathroom heater | power, brightness, CT, night-light, delay, scenes, flow, cron, Mi Band sleep |
| `YeelightMeshGateway.xml` | `yeelink.gateway.v1` | Mesh gateway | LED indicator, timezone, reset |
| `YeelightMeshbulb1.xml` | `yeelink.light.meshbulb1` | Mesh bulb | power, brightness, CT, flow, scenes, delay, cron |
| `YeelightMeshbulb2.xml` | `yeelink.light.meshbulb2` | Mesh candle bulb | same as mesh bulb |
| `YeelightSpot1.xml` | `yeelink.light.spot1` | Mesh spotlight | power, brightness, CT, flow, scenes, delay, cron |
| `YeelightDnlight2.xml` | `yeelink.light.dnlight2` | Mesh downlight | power, brightness, CT, flow, scenes, delay, cron |
| `YeelightPanel1.xml` | `yeelink.light.panel1` | Mesh panel | power, brightness, flow, scenes, delay, cron; read-only temperature (`rt_temp`), no CT control |
| `YeelightPanel3.xml` | `yeelink.light.panel3` | Mesh panel Pro | power, brightness, CT, flow, scenes, delay, cron |
| `YeelightPlate2.xml` | `yeelink.light.plate2` | Magic Light | power, brightness, RGB/HSV, scenes, cron; read-only temperature (`rt_temp`), no CT control |

The MIoT security classes present in the APK include
`BleSecurityConnector`, `BleSecurityLogin`, `BleSecurityRegister`,
`BleStandardAuthConnector`, `BleMeshLogin`, `BleMeshRegister`,
`BleRc4Connector` and `BleSecurityChipEncrypt`. Native helpers include
`libTelinkCrypto.so`, `libblecipher.so`, `libmiio.so` and `liblight_mix.so`.

### 4.1 BLE-mesh GATT

The mesh transport is implemented in
`com.yeelight.yeelib.device.connections.b`.

| Service | Characteristic | Role |
| --- | --- | --- |
| `00010203-0405-0607-0809-0a0b0c0d1910` | `00010203-0405-0607-0809-0a0b0c0d1912` | mesh command write |
| `00010203-0405-0607-0809-0a0b0c0d1910` | `00010203-0405-0607-0809-0a0b0c0d1911` | mesh notifications |
| `00010203-0405-0607-0809-0a0b0c0d1910` | `00010203-0405-0607-0809-0a0b0c0d1914` | pair/write |
| `00010203-0405-0607-0809-0a0b0c0d1910` | `00010203-0405-0607-0809-0a0b0c0d1913` | OTA/notify |
| `0000fe87-0000-1000-8000-00805f9b34fb` | `aa7d3f34-2d4f-41e0-807f-52fbf8cf7443` | Gingko control |
| `0000fe87-0000-1000-8000-00805f9b34fb` | `8f65073d-9f57-4aaa-afea-397d19d5bbeb` | Gingko notify |

The mesh notification handler distinguishes:

- `8f65073d…` → legacy `s3.p` frame, parsed by `G1` (Gingko);
- `00010203-…1913` → OTA frame, parsed by `I1`;
- `00010203-…1911` → mesh payload, passed to `PrivateMeshDeviceBase.b2`.

## 5. Comparison with the integration before this update

This comparison records the baseline at commit `4d47993`. The subsequent local
implementation update is summarised in section 5.4; the table is not a claim
that those differences remain in the updated code.

### 5.1 What matches

- The legacy GATT UUID pair (`aa7d3f34…`, `8f65073d…`).
- The 18-byte frame format and the `0x43` command prefix.
- Power (`43 40 01`/`02`), brightness (`43 42 BB`), colour temperature
  (`43 43 TT TT BB`), state response (`43 45`), version query (`43 5C`) and
  pairing response (`43 63`).

### 5.2 Differences found in the app

| Topic | App 3.5.4 | Baseline integration (`4d47993`) | Comment |
| --- | --- | --- | --- |
| State query | `43 44 00` | `43 44 02` | App uses `00`; `02` may be a firmware-specific variant |
| Pairing | `43 67 <6 identifier bytes>` | `43 67 02` | On Android 6+ Cherry's zeroed adapter address makes the app frame identical to `43 67 02`; Gingko uses `AppUtils.b()` (UUID-derived or cached identifier); see 2.7 |
| Colour payload | `43 41 RR GG BB 00 BB` | `43 41 RR GG BB 01 BB` | Frame byte 5 differs (`00` vs `01`) |
| Colour/CT brightness | `-1` → `00`; explicit values clamped to ≥10 | allowed 1–100 | `o3.a.K/M` use the `-1` sentinel for colour-only/CT-only requests; `d4.c.t/J` clamp explicit values |
| Version response | Cherry: BCD payload bytes 3–8; Gingko: ASCII string | little-endian `H` fields at frame offsets 4/6/8/10 | Verify on hardware |
| Candela | Gingko pairs, notifies, reports state | no pairing, no notifications, no state | The app path and the current Candela path differ |
| Extended power/brightness | Gingko uses `43 40 … 01 01 F4` / `43 42 … 01 01 F4` | simple `43 40 01` / `43 42 BB` | Gingko-specific variant |

The differences do not necessarily mean the current integration is wrong:
different firmware generations and Android versions may produce different
frames. They are useful test cases, especially for the Bedside state query and
colour path.

### 5.3 Functions the app exposes but the integration does not

The legacy protocol contains commands for:

- colour flow (read/write, simple and full);
- scenes (`43 4E`/`4F`/`50`; defined but not wired in the legacy path);
- alarms and wake-up timers;
- night-light windows;
- delay-off;
- device name read/write;
- firmware version and serial number;
- beacon/remote management;
- stored records;
- OTA;
- Candela battery/factory state.

These are candidates for future integration features, but each needs hardware
verification and a clear user-facing model. For the Bedside, the lamp clock,
night-light, delay-off timer, alarms, wake-up light, colour flows, mode select and
name, the beacon pairing mode and the factory reset were verified in the
2026-11-02/03 hardware runs, see section 5.5. Adding remotes, the stored records,
OTA and everything on the Candela are still open.

### 5.4 Local Legacy implementation update (2026-11-02)

The existing light-control functionality now uses:

- the Bedside-tested `43 44 00` state query, RGB `00` extra byte and
  zero-brightness sentinel for colour/CT-only changes;
- BCD Bedside version decoding (`2231-2238-2330` on the tested lamp), alongside
  the separate Gingko ASCII version decoder;
- APK-derived Candela power/brightness extensions (`01 01 F4`), state offsets
  and authorization result handling;
- a six-byte Candela identifier derived from HA's persistent instance UUID during
  new-device setup and stored only after successful authorization. Runtime reuses
  this confirmed identifier; existing entries without one remain command-only;
- Candela authorization, status polling and notifications for confirmed entries
  when the backend can subscribe. State remains assumed until a valid status
  frame arrives, and confirmed feedback survives successful reconnects. Backends
  that cannot subscribe retain command-only operation;
- Candela power/brightness decoding even when the reported mode is unknown,
  preserving the last recognised mode and colour temperature as the APK does.

The Bedside keeps its hardware-tested Android-placeholder identifier. An upgrade
does not silently start a new Candela authorization window in the background.

During new Candela setup, an unanswered pairing request or a confirmation timeout
triggers one fresh connection test without a pairing identifier. The first
connection is closed before retrying. A successful retry creates a command-only
entry with the detected Candela model and no `pairing_id`. Explicit refusals,
Bedside pairing timeouts and an unsuccessful fallback remain setup errors.

The Bedside accepted explicit combined-command brightness below the app's 10%
clamp, so the integration retains its 1–100% range. The raw-GATT comparison
passed 11 checks and the actual updated driver passed 7 checks; both restored
the original lamp state. See [hardware-test.md](hardware-test.md) for the
captured packet, readbacks and remaining hardware coverage limits.

### 5.5 Bedside extended commands, verified on hardware and against the app (2026-11-02/03)

Method: raw GATT probes from a PC (read-only queries first, then write, read
back, restore) and an HCI snoop capture of the Yeelight app 3.5.4 on Android 16
while each feature was set in the app UI. This section is hardware-verified for
the Bedside unless an entry is marked as expected, APK-only or from the app
capture, and it supersedes the static descriptions in 2.8 where they differ. All
frames are padded to 18 bytes with zeros and shown in full; "payload offset"
counts from the first byte after `43 <command>`. Nothing here is verified for
the Candela.

**Connection sequence of the app** (capture): CCCD write `01 00`, `43 67 02`
(pairing), `43 5C 00` (version), `43 44 00` (state), `43 52 00` (name),
`43 60 <time>` (sets the lamp clock), `43 80 00`, `43 47 FF`, `43 70 00`,
`43 89 06`, `43 8C 00`, `43 4C 01`, `43 4C 02`, `43 64 00`.

| Feature | Frames | Verified behaviour |
| --- | --- | --- |
| Lamp time | set `43 60 ss mm hh dd wd MM yy`, read `43 61 00` → `43 62` (same layout) | BCD. Weekday: Sunday = 0, Monday = 1 … Saturday = 6 (`d4.c.s` uses `Calendar.DAY_OF_WEEK − 1`; Monday and Tuesday also seen on the wire). The app sets the time on **every connect**. The lamp clock was 14 min off before the first write. A factory reset keeps the clock. |
| Night-light | set `43 6F en 01 hh mm hh mm`, read `43 70 00` → `43 71` (same layout) | App sends BCD (21:00–07:00 = `01 01 21 00 07 00`). The lamp stores and echoes the bytes verbatim (a binary-encoded variant was echoed too), so the readback cannot tell BCD from binary; the lamp clock is BCD and the app sends BCD. The second byte is the night-light brightness per 2.8; the app always sends `01`. With the window 21:00–07:00 active, the app's power-on `43 40 01` at 00:14 lamp time was answered with `43 45 01 01 FF 98 00 00 01`: orange at 1 % instead of the previous colour at 100 % (capture). Factory state: off, 00:00–05:00. |
| Delay-off timer | start `43 7F 03 <min>`, stop `43 7F 04 <min>`, read `43 80 00` → `43 81 01 <min> <state> <remaining s, 2 bytes BE>` | Minutes are binary (`0x14` = 20). State `01` = running, `02` = off. The remaining time counts down in seconds (1198 s after setting 20 min, 1131 s after 65 s). The app sends the last minutes value on stop; `04 00` also stops it. The lamp remembers the last duration. |
| Alarm | set `43 46 slot hh mm ss mode rep gg gg act 00 en`, read `43 47 FF` → one `43 49` per stored alarm, then `43 49 FF` (the wake-up in slot 6 is not included), `43 47 <slot>` → one `43 49` without terminator, delete `43 48 <slot>` | Slot 1 (probe) and slot 2 (app) were written; the app offers five slots, so 1–5 is expected but 3–5 were not written. Slot 0 is ignored. Time fields BCD. `mode`: 1 once, 2 daily, 3 weekly. `rep`: mode 1: **day of the month (BCD) of the next run**. On hardware, two probe alarms with today's day and `act = 03` fired on time; the next-day rule (the app sends tomorrow's day when the time has passed today, and today's day when it deactivates the entry, hence `03` → `02` in the capture) comes from the app; mode 2: `00`; mode 3: weekday mask, bit 0 = Sunday … bit 6 = Saturday (Monday–Friday = `3E`). `gg gg`: gradual value, the app default `00 01` (the app parses only the low byte). With `00 05` the lamp was fully bright within a few seconds of the alarm time (seen by eye), so the value is not minutes; seconds or no effect cannot be told apart, and the state notification reports the target brightness at once. `act`: `03` switch on (fired on hardware), `04` switch off (APK only; the app also reads `01` as "on", a probe with `01` was stored). Payload offset 9 is `00` for alarms. `en`: `01` active, `00` inactive. The app deactivates by resending the entry with `en = 00` and does not delete. `43 48 <slot>` deletes silently. Source for `rep`/`act`: `LightAlarmDetailActivity.h0`, `LightAlarmActivity.Z`. |
| Wake-up light | set `43 88 06 hh mm ss mode rep gg gg 01 01 en`, read `43 89 06` → `43 8A 06 …` | Fixed slot 6. The app sends the start time = wake time − 30 min and `gg gg` = `00 1E`, the same field as the alarm gradual value (its unit is open, see below). Disabled by `en = 00`. An empty slot answers `43 82 43 89 02`. Delete: `43 48 06` (what the app uses, `o3.a.e`) and `43 8B 06` each delete it on their own. |
| Colour flow (stored) | save `43 7C 02 01 <interval s> 04 <4 × RGB>`, read `43 4C 02` → one `43 4D 02 <n> <02/04> 01 RR GG BB 00 <b> … <interval>` per step, then `43 4D FF` | Up to 4 steps. Factory contents: orange, yellow, green and blue steps at 2 s, each with `0x65` at payload offset 8. `43 7C 02` has no field for that byte; saving through it left `00` there (the app sent only the live form `43 7C 06` in the captures). A factory reset brought `0x65` back. |
| Colour flow (live) | `43 7C 06 01 <interval s> 01 <4 × RGB>` | Starts the flow immediately (state mode `3`), the stored flow stays untouched. Confirmed by eye: red → green → blue → yellow at 2 s. The app sends brightness with `43 42 <b>` afterwards; brightness commands work in flow mode and the state reports them. |
| Mode select | `43 4A <1 white / 2 flow / 3 colour> 01 01` | The lamp switches and pushes a state notification itself. In flow mode the state reports mode `3` with the RGB bytes `02 00 00` (not a colour). When leaving the colour mode, the app saves the current colour into the colour entry right after the switch (`43 4A 02 01 01` → saves `FF 98 00`, `43 4A 01 01 01` → saves `14 14 32`). Saving the stored colour entry: `43 4A 03 01 04 01 RR GG BB 00 <b>`, the stored white entry: `43 4A 01 01 04 02 00 00 00 00 <b> hh ll` (Kelvin BE). The byte at payload offset 8 is writable and read back; the app sends `00`, the factory value is `0x65`. It had no visible effect: with `0x14`, `0x65` and `00` the lamp kept its current brightness (50 %) when switching back to the entry, and the app never reads it (`r3.r.G1` uses only Kelvin, RGB and the flow interval). Most likely a factory placeholder (101 is outside 0–100). `43 4A 02 01 04 01` (the form `d4.c.E` builds for the flow) did not switch the mode. |
| Name | write `43 51 01 00 <len> <UTF-8>` then `43 51 01 01 <len> <rest>`, read `43 52 00` → two `43 53 01 <part> <len> <data>` | "Probe" = `01 00 05 50 72 6F 62 65` followed by `01 01 00`. The factory name is empty (both chunks length 0). |
| Colour / white | `43 41 RR GG BB 00 <b>`, `43 43 <K BE> <b>` | As in 5.4. The app's scenes are client-side presets sent as normal commands (e.g. "Movie" = `43 41 14 14 32 00 32`, attributed by timing in the capture). |
| Beacons | read `43 64 00` → `AE <pairing mode>` then `EF 00`; pairing mode on `43 54 01`, off `43 54 02` | Both answers padded to 18 bytes. No remotes paired. `43 54 01` is acknowledged with `43 82 43 54 01` and the status changes to `AE 01`, `43 54 02` returns it to `AE 00`. Adding a remote was not tested (no remote available). |
| Records | `43 8C 00` → `43 8D` … `43 91` | Five frames of counters, meaning unknown, not useful for Home Assistant. |
| Command response | `43 82 <command> <status>` | Status `01` = done (seen for `43 54`, `43 74`), `02` = nothing stored (wake-up read). |
| Factory reset | `43 74 00` | Acknowledged with `43 82 43 74 01`, then the lamp **drops the connection** and its authorization: the next `43 67 02` needs the button. Afterwards the lamp is on (stored colour `FF00CA`, 100 %), name empty, night-light off 00:00–05:00, delay-off 15 min, no alarms or wake-up, no beacons, stored entries with `0x65`. The clock keeps running. |

**Night light in Home Assistant.** The integration exposes the window as a
configuration switch and two configuration times (start, end): the switch sends
`43 6F` with `en` toggled, each time sends the complete frame with the other
fields taken from the last readback. The brightness byte stays `01`, matching
the app. The window is read with `43 70 00` on every connection and after a
reconnect; frames that are too short or carry non-BCD times are ignored. Because
a write always contains all fields, the entities are unavailable on backends
without notifications, where the readback never arrives; the light entity is
unaffected. The Candela gets no night-light entities.

**Pairing and deleting the lamp in the app.** The app's pairing frame is `43 67 02`,
byte-identical to the integration. The lamp answers `43 63 04` when the identifier
is already authorized. The app's **"Delete device" sends `43 72 02`**
(DELETEAUTHORIZE with the same identifier `02`) and thereby removes the
authorization the integration relies on. Afterwards `43 67 02` is answered with
`43 63 01` (the lamp pulses and waits for its button), `43 63 02` after the button
was pushed (new authorization, later connects answer `04` again) and `43 63 05`
after 60 s without a button push. **While it is switched off**, a lamp without our
authorization answers `43 63 06` instead and does not pulse; switched on at the
lamp, it answers `01` at once and pulses (controlled on/off runs after `43 72 02`,
see the hardware test). An authorized lamp answers `04` also when it is off. A
central that sent `43 72` is not blocked afterwards. The lamp seemed to pulse
already before the pairing request, from the connection on, so the 60 s window is
counted from `01`, not from the first pulse.

Since this update the integration waits 60 s for the button, treats `01` and, on
the Bedside, `06` as "pairing required" and shows a repair issue until the lamp is
paired; every poll (30 s) starts the next pairing request. Bedside entries store no
pairing identifier, so the entry needs no change. Deleting an entry sends `43 72`
with the entry's identifier when the lamp is in reach and still accepts it.

Not established: whether the alarm gradual value is seconds or ignored (it is not
minutes), whether the wake-up value `1E` really ramps over 30 minutes (the app text
says so, not observed), any effect of the factory byte `0x65`, adding a real
remote, and the stored records. OTA was deliberately not exercised (no matching firmware image,
risk of bricking the lamp).

## 6. Unknowns and limitations

- APK findings are static unless explicitly covered by the Bedside hardware
  run. Candela, other firmware versions and backend-specific differences have
  not been hardware-verified here.
- The MIoT BLE transport is summarised from class names and XML specs, not
  traced frame-by-frame. The MIoT stack uses its own binding/login/encryption
  layer, so the raw GATT payloads are not documented here.
- Some `BleMessage` commands have no builder or handler in the legacy
  Cherry/Gingko path. They may be unused, firmware-specific or used by a
  different product family.
- `BlueMessage` defines an older `0000FFF0`–`0000FFE2` UUID map, but no static
  references from the active connection classes were found. It appears to be
  legacy/dead code in this build.
- APK signatures were not verified. Package declarations do not establish
  authenticity.

## 7. Evidence index

| Area | Class / method |
| --- | --- |
| Legacy Cherry device | `r3.r`, `r3.r.G1`, `r3.r.x`, `r3.r.A2`, `r3.r.B2`, `r3.r.C2`, `r3.r.I1` |
| Legacy Gingko device | `r3.f0`, `r3.f0.G1`, `r3.f0.x`, `r3.f0.B1` |
| Command builders | `d4.c` (`G`, `l`, `J`, `t`, `r`, `k`, `c`, `V`, `T`, `U`, `a`, `n`, `j`, `y`, `z`, `Q`, `D`, `E`, `F`, `o`, `m`, `p`, `K`, `L`, `O`, `P`, `R`, `S`, `q`, `u`, `s`, `b`, `B`, `A`, `e`, `C`, `I`, `h`, `g`, `H`) |
| Legacy parsing | `com.yeelight.yeelib.utils.a.l`, `utils.a.k`, `utils.a.i` |
| Frame construction | `s3.d.<init>`, `s3.d.a`, `s3.p.a`, `com.yeelight.yeelib.utils.a.b` |
| Cherry GATT | `com.yeelight.yeelib.device.connections.a.C0107a.c.run`, `...C0107a.d.run` |
| Gingko/mesh GATT | `com.yeelight.yeelib.device.connections.b.a.c.run`, `connections.b.z` |
| Cherry transport | `o3.a` |
| Gingko transport | `o3.c` |
| Pairing state | `com.yeelight.yeelib.authority.a.a`, `authority.a.d` |
| Pairing identifier | `ConnectionBase.j`, `connections.a.j`, `AppUtils.b`, `AppUtils.i`, `d4.v.a/m` |
| Command enum | `com.yeelight.yeelib.device.models.BleMessage.<clinit>` |
| Legacy UUID map | `com.yeelight.yeelib.device.models.BlueMessage.<clinit>` |
| MIoT device specs | `assets/Yeelight*.xml` |

## 8. Frame examples

Power on (Cherry):

```text
43 40 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

Brightness 40:

```text
43 42 28 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

Colour red at brightness 100 (app form):

```text
43 41 FF 00 00 00 64 00 00 00 00 00 00 00 00 00 00 00
```

Colour temperature 4000 K at brightness 50:

```text
43 43 0F A0 32 00 00 00 00 00 00 00 00 00 00 00 00 00
```

State query:

```text
43 44 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

Pairing with six identifier bytes (`AA:BB:CC:DD:EE:FF`):

```text
43 67 AA BB CC DD EE FF 00 00 00 00 00 00 00 00 00 00
```

State response (on, colour mode, red, brightness 100, CT 4000 K):

```text
43 45 01 01 FF 00 00 00 64 0F A0 00 00 00 00 00 00 00
```

## Appendix A — Legacy `BlueMessage` UUID map

`BlueMessage` defines an older 16-bit UUID map. No static references from the
active Cherry/Gingko connection classes were found, so it is listed for
completeness and may be unused in this build.

| Name | UUID |
| --- | --- |
| MAIN | `0000fff0-0000-1000-8000-00805f9b34fb` |
| CONTROL | `0000fff1-0000-1000-8000-00805f9b34fb` |
| DELAY | `0000fff2-0000-1000-8000-00805f9b34fb` |
| DELAY_QUERY | `0000fff3-0000-1000-8000-00805f9b34fb` |
| DELAY_NOTIFICATION | `0000fff4-0000-1000-8000-00805f9b34fb` |
| STATE_QUERY | `0000fff5-0000-1000-8000-00805f9b34fb` |
| STATE_NOTIFICATION | `0000fff6-0000-1000-8000-00805f9b34fb` |
| COLORFLOW | `0000fff7-0000-1000-8000-00805f9b34fb` |
| NAME | `0000fff8-0000-1000-8000-00805f9b34fb` |
| NAME_NOTIFICATION | `0000fff9-0000-1000-8000-00805f9b34fb` |
| COLORFLOW_QUERY | `0000fffa-0000-1000-8000-00805f9b34fb` |
| COLORFLOW_NOTIFICATION | `0000fffb-0000-1000-8000-00805f9b34fb` |
| LIGHT_SETTING | `0000fffc-0000-1000-8000-00805f9b34fb` |
| INFO | `0000fffd-0000-1000-8000-00805f9b34fb` |
| PASSWD | `0000fffe-0000-1000-8000-00805f9b34fb` |
| BEACON | `0000ffe1-0000-1000-8000-00805f9b34fb` |
| BEACON_NOTIFICATION | `0000ffe2-0000-1000-8000-00805f9b34fb` |
