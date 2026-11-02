"""
Creator : hcoohb
License : MIT
Source  : https://github.com/hcoohb/hass-yeelightbt

Bluetooth protocol and connection handling for the Yeelight Bedside and Candela.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import enum
import logging
import re
import struct
import time
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)

NOTIFY_UUID = "8f65073d-9f57-4aaa-afea-397d19d5bbeb"
CONTROL_UUID = "aa7d3f34-2d4f-41e0-807f-52fbf8cf7443"

COMMAND_STX = 0x43
CMD_PAIR = 0x67
# Android's unavailable adapter address, used by the Bedside app as its ID.
BEDSIDE_PAIRING_ID = bytes.fromhex("020000000000")
RES_PAIR = 0x63
CMD_POWER = 0x40
CMD_POWER_ON = 0x01
CMD_POWER_OFF = 0x02
CMD_COLOR = 0x41
CMD_BRIGHTNESS = 0x42
CMD_TEMP = 0x43
CMD_RGB = 0x41
CMD_GETSTATE = 0x44
CMD_GETSTATE_SEC = 0x00
RES_GETSTATE = 0x45
CMD_GETNAME = 0x52
RES_GETNAME = 0x53
CMD_GETVER = 0x5C
RES_GETVER = 0x5D
CMD_GETSERIAL = 0x5E
RES_GETSERIAL = 0x5F
RES_GETTIME = 0x62

PAIR_REQUESTED = 0x01
PAIR_SUCCESS = 0x02
PAIR_NOT_PAIRED = 0x03
PAIR_ALREADY_PAIRED = 0x04
PAIR_ERRORS = (0x06, 0x07)

MODEL_BEDSIDE = "Bedside"
MODEL_CANDELA = "Candela"
MODEL_UNKNOWN = "Unknown"
MODELS = (MODEL_BEDSIDE, MODEL_CANDELA)

TEMP_MIN = 1700
TEMP_MAX = 6500

# Time to wait for the pairing answer of a lamp that sends notifications.
PAIR_RESPONSE_TIMEOUT = 5.0
# Time given to confirm pairing on the lamp (button or Candela cylinder).
PAIR_BUTTON_TIMEOUT = 30.0
# A Bedside without notifications gets a short grace period after pairing.
PAIR_GRACE_PERIOD = 1.0
# Upper bound for one connect + pair + write cycle, so that a stuck backend
# can never hold the lock (and make the lamp unavailable) forever.
OPERATION_TIMEOUT = PAIR_BUTTON_TIMEOUT + 60.0
# "yeelight_ms" or "yeelight_ms" with a suffix, like the Homey Candela app
CANDELA_NAME = re.compile(r"^yeelight_ms(?:$|[_ -])", re.IGNORECASE)
RETRY_DELAY = 0.5
# Each attempt may take up to 20 s (bleak-retry-connector), so an unreachable
# lamp fails within about a minute.
CONNECT_ATTEMPTS = 3
# A command waiting for the lock while another one just failed to connect
# fails right away instead of trying again.
CONNECT_FAILURE_HOLDOFF = 10.0
# Some backends (e.g. WinRT) raise a plain OSError when the link drops.
RETRY_EXCEPTIONS = (*BLEAK_RETRY_EXCEPTIONS, OSError)


class Conn(enum.Enum):
    DISCONNECTED = 1
    UNPAIRED = 2
    PAIRING = 3
    PAIRED = 4


class YeelightError(Exception):
    """Raised when a command could not be delivered to the lamp."""


class YeelightPairingError(YeelightError):
    """Raised when the lamp refused or did not answer the pairing request."""


class YeelightPairingTimeoutError(YeelightPairingError):
    """Raised when pairing gets no answer or no confirmation before its deadline."""


class YeelightConnectionError(YeelightError):
    """Raised when no connection could be established (after retries)."""


_LOGGER = logging.getLogger(__name__)


def model_from_name(ble_name: str | None) -> str:
    """Return the lamp model from its advertised name.

    Since bleak 1.0 the name of a device without a local name is None.
    """
    name = (ble_name or "").strip()
    if name.startswith("XMCTD_"):
        return MODEL_BEDSIDE
    if CANDELA_NAME.match(name):
        return MODEL_CANDELA
    return MODEL_UNKNOWN


class Lamp:
    """The class that represents a Yeelight lamp
    A Lamp object describe a real world Yeelight lamp.
    """

    MODE_COLOR = 0x01
    MODE_WHITE = 0x02
    MODE_FLOW = 0x03

    def __init__(
        self,
        ble_device: BLEDevice,
        model: str | None = None,
        ble_device_callback: Callable[[], BLEDevice | None] | None = None,
        pairing_id: bytes | None = None,
    ) -> None:
        self._client: BleakClient | None = None
        self._ble_device = ble_device
        self._ble_device_callback = ble_device_callback
        self._mac = ble_device.address
        self._model = model if model in MODELS else model_from_name(ble_device.name)
        self._pairing_id = pairing_id
        if self._pairing_id is None and self._model != MODEL_CANDELA:
            self._pairing_id = BEDSIDE_PAIRING_ID
        if self._pairing_id is not None and len(self._pairing_id) != 6:
            raise ValueError("The pairing identifier must contain six bytes")
        _LOGGER.debug(
            "Initializing Yeelight Lamp %s (%s), model %s",
            ble_device.name,
            self._mac,
            self._model,
        )
        self._is_on = False
        self._rgb: tuple[int, int, int] = (0, 0, 0)
        self._brightness = 0
        self._temperature = 0
        self._mode: int | None = (
            self.MODE_WHITE if self._model == MODEL_CANDELA else None
        )
        self.versions: tuple[int, ...] | None = None
        self.firmware_version: str | None = None
        self.serial: int | None = None

        # store func to call on state received:
        self._state_callbacks: list[Callable[[], None]] = []
        self._conn = Conn.DISCONNECTED
        self._lock = asyncio.Lock()
        self._pair_resp_event = asyncio.Event()
        self._pair_result: int | None = None
        self._notify_active = False
        # None until a valid state arrives; False for command-only operation.
        self._has_state_feedback: bool | None = (
            False if self._model == MODEL_CANDELA and self._pairing_id is None else None
        )
        self._available = False
        self._contacted = False
        # monotonic time of the last failed connection, None: none yet. Not 0.0:
        # time.monotonic() starts at boot, right after it 0.0 looks recent.
        self._last_connect_failure: float | None = None

    def __str__(self) -> str:
        """The string representation"""
        mode_str = {
            self.MODE_COLOR: "Color",
            self.MODE_WHITE: "White",
            self.MODE_FLOW: "Flow",
        }
        str_rgb = (
            f"rgb_{self._rgb} "
            if self._mode in (self.MODE_COLOR, self.MODE_FLOW)
            else ""
        )
        str_temp = f"temp_{self._temperature}" if self._mode == self.MODE_WHITE else ""
        str_mode = mode_str.get(self._mode, self._mode)  # type: ignore[arg-type]
        str_bri = f"bri_{self._brightness} " if self._mode else ""
        return (
            f"<Lamp {self._mac} "
            f"{'ON' if self._is_on else 'OFF'} "
            f"mode_{str_mode} "
            f"{str_bri}{str_rgb}{str_temp}"
            f">"
        )

    # ---- callbacks ----
    def add_callback_on_state_changed(
        self, func: Callable[[], None]
    ) -> Callable[[], None]:
        """
        Register callbacks to be called when lamp state is received or bt disconnected
        Returns a function removing the callback again.
        """
        self._state_callbacks.append(func)

        def _remove() -> None:
            if func in self._state_callbacks:
                self._state_callbacks.remove(func)

        return _remove

    def run_state_changed_cb(self) -> None:
        """Execute all registered callbacks for a state change"""
        for func in list(self._state_callbacks):
            try:
                func()
            except Exception:  # a callback must never break the BLE handling
                _LOGGER.exception("Error in Yeelight state callback")

    def _set_available(self, available: bool, reason: str | None = None) -> None:
        changed = available != self._available or not self._contacted
        # Log once when the lamp becomes unreachable and once when it is back
        if available and not self._available and self._contacted:
            _LOGGER.info("Yeelight %s is available again", self._mac)
        elif not available and (self._available or not self._contacted):
            _LOGGER.info("Yeelight %s is unavailable: %s", self._mac, reason)
        self._contacted = True
        self._available = available
        if changed:
            self.run_state_changed_cb()

    def _disconnected_cb(self, client: BleakClient) -> None:
        # Ignore our own disconnects and late callbacks of an old client.
        if client is not self._client:
            _LOGGER.debug("%s: ignoring disconnect of a stale client", self._mac)
            return
        _LOGGER.debug("%s: disconnected", self._mac)
        self._client = None
        self._conn = Conn.DISCONNECTED
        self._notify_active = False
        # The lamp stays available: the next command or poll reconnects and
        # only a failure there marks it unavailable.
        self.run_state_changed_cb()

    # ---- BLE device handling ----
    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Use the BLEDevice that HA currently considers best (adapter/proxy)."""
        self._ble_device = ble_device

    def _current_ble_device(self) -> BLEDevice:
        """The newest BLEDevice for reconnect attempts of establish_connection."""
        self._refresh_ble_device()
        return self._ble_device

    def _pair_answer(self) -> int | None:
        # Set by the notification handler while _pair() waits
        return self._pair_result

    def _refresh_ble_device(self) -> None:
        if self._ble_device_callback is None:
            return
        if (ble_device := self._ble_device_callback()) is not None:
            self.set_ble_device(ble_device)

    # ---- connection / pairing (must be called with the lock held) ----
    async def _ensure_connected(self) -> BleakClient:
        if (
            self._client is not None
            and self._client.is_connected
            and self._conn == Conn.PAIRED
        ):
            return self._client
        if self._client is not None:
            await self._disconnect_client()

        self._refresh_ble_device()
        _LOGGER.debug("%s: connecting through %s", self._mac, self._ble_device)
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self._mac,
                disconnected_callback=self._disconnected_cb,
                max_attempts=CONNECT_ATTEMPTS,
                ble_device_callback=self._current_ble_device,
            )
        except RETRY_EXCEPTIONS as err:
            # establish_connection already retried, do not start over
            self._last_connect_failure = time.monotonic()
            raise YeelightConnectionError(
                f"Could not connect to {self._mac}: {err!r}"
            ) from err
        self._client = client
        self._conn = Conn.UNPAIRED
        _LOGGER.debug("%s: connected", self._mac)

        if self._model == MODEL_CANDELA and self._pairing_id is None:
            # Existing entries were command-only. New authorization requires
            # someone to turn the cylinder, so only the config flow initiates it.
            self._conn = Conn.PAIRED
            self._set_available(True)
            return client

        await self._start_notify(client)
        if self._model == MODEL_CANDELA and not self._notify_active:
            # Android can receive Gingko notifications without writing a CCCD.
            # Backends that require that missing descriptor need command-only
            # operation; requesting authorization here cannot be completed.
            self._conn = Conn.PAIRED
            self._set_available(True)
            return client

        await self._pair(client)
        self._set_available(True)

        if self._notify_active and self.firmware_version is None:
            await self._write(client, struct.pack("BB16x", COMMAND_STX, CMD_GETVER))
            if self._model != MODEL_CANDELA:
                await self._write(
                    client, struct.pack("BB16x", COMMAND_STX, CMD_GETSERIAL)
                )
        return client

    async def _start_notify(self, client: BleakClient) -> None:
        previous_feedback = self._has_state_feedback
        try:
            await client.start_notify(NOTIFY_UUID, self._notification_handler)
        except (BleakError, ValueError) as err:
            # ESPHome proxies report Candela's notify characteristic without a
            # CCCD descriptor. Commands still work, we just get no feedback.
            _LOGGER.debug("%s: notifications not available: %s", self._mac, err)
            self._notify_active = False
            self._has_state_feedback = False
        else:
            self._notify_active = True
            if self._has_state_feedback is False:
                self._has_state_feedback = None
        if self._has_state_feedback != previous_feedback:
            self.run_state_changed_cb()

    async def _pair(self, client: BleakClient) -> None:
        """Send the pairing request and wait for its outcome when possible."""
        self._pair_resp_event.clear()
        self._pair_result = None
        _LOGGER.debug("%s: request pairing", self._mac)
        assert self._pairing_id is not None
        await self._write(
            client, struct.pack("BB6s10x", COMMAND_STX, CMD_PAIR, self._pairing_id)
        )

        if not self._notify_active:
            await asyncio.sleep(PAIR_GRACE_PERIOD)
            self._conn = Conn.PAIRED
            return

        timeout = PAIR_RESPONSE_TIMEOUT
        while True:
            try:
                async with asyncio.timeout(timeout):
                    await self._pair_resp_event.wait()
            except TimeoutError:
                raise YeelightPairingTimeoutError(
                    "The lamp did not answer the pairing request"
                    if self._pair_answer() is None
                    else "The pairing request was not confirmed on the lamp"
                ) from None
            self._pair_resp_event.clear()
            if (answer := self._pair_answer()) != PAIR_REQUESTED:
                break
            timeout = PAIR_BUTTON_TIMEOUT

        if answer == PAIR_SUCCESS or (
            answer == PAIR_ALREADY_PAIRED and self._model != MODEL_CANDELA
        ):
            return
        if answer == PAIR_NOT_PAIRED:
            raise YeelightPairingError("The lamp refused the pairing request")
        raise YeelightPairingError(f"The lamp rejected pairing (response {answer!r})")

    async def _disconnect_client(self) -> None:
        client = self._client
        self._client = None
        self._conn = Conn.DISCONNECTED
        self._notify_active = False
        if client is None:
            return
        try:
            await client.disconnect()
        except RETRY_EXCEPTIONS as err:
            _LOGGER.debug("%s: error while disconnecting: %s", self._mac, err)

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect_client()

    @staticmethod
    async def _write(client: BleakClient, bits: bytes) -> None:
        await client.write_gatt_char(CONTROL_UUID, bytearray(bits))

    async def send_cmd(self, bits: bytes | None, wait_notif: float = 0.0) -> None:
        """Connect and pair if needed, then send one command (None: connect only).

        A failing write is retried once on a fresh connection, as proxies and
        adapters regularly drop connections ("Connection reset by peer",
        "EOF received", GATT error 133, ...). A failed connection is not
        retried again, establish_connection already did.
        """
        async with self._lock:
            if (
                not self.is_connected
                and self._last_connect_failure is not None
                and time.monotonic() - self._last_connect_failure
                < CONNECT_FAILURE_HOLDOFF
            ):
                raise YeelightConnectionError(f"{self._mac} is not reachable")
            for attempt in (1, 2):
                try:
                    async with asyncio.timeout(OPERATION_TIMEOUT):
                        client = await self._ensure_connected()
                        if bits is not None:
                            await self._write(client, bits)
                except (YeelightPairingError, YeelightConnectionError) as err:
                    await self._disconnect_client()
                    self._set_available(False, str(err))
                    raise
                except RETRY_EXCEPTIONS as err:
                    await self._disconnect_client()
                    if attempt == 1:
                        _LOGGER.debug(
                            "%s: command failed (%s: %s), retrying",
                            self._mac,
                            type(err).__name__,
                            err,
                        )
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                    self._set_available(False, repr(err))
                    raise YeelightError(
                        f"Communication with {self._mac} failed: {err!r}"
                    ) from err
                except asyncio.CancelledError:
                    await self._disconnect_client()
                    raise
                break
            self._set_available(True)
            if wait_notif:
                await asyncio.sleep(wait_notif)

    # ---- properties ----
    @property
    def mac(self) -> str:
        return self._mac

    @property
    def ble_device(self) -> BLEDevice:
        """The BLEDevice (adapter or proxy) used for the next connection."""
        return self._ble_device

    @property
    def available(self) -> bool:
        return self._available

    @property
    def contacted(self) -> bool:
        """Whether a connection attempt has completed (successful or not)."""
        return self._contacted

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._conn == Conn.PAIRED

    @property
    def has_state_feedback(self) -> bool | None:
        """Whether the lamp reports its state (None: not known yet)."""
        return self._has_state_feedback

    @property
    def confirmed_pairing_id(self) -> bytes | None:
        """Return the identifier explicitly accepted during this connection test."""
        return self._pairing_id if self._pair_result == PAIR_SUCCESS else None

    @property
    def model(self) -> str:
        return self._model

    @property
    def mode(self) -> int | None:
        return self._mode

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def temperature(self) -> int:
        return self._temperature

    @property
    def brightness(self) -> int:
        return self._brightness

    @property
    def color(self) -> tuple[int, int, int]:
        return self._rgb

    def restore_state(self, is_on: bool, brightness: int | None) -> None:
        """Restore the last known state for lamps without state feedback."""
        self._is_on = is_on
        if brightness is not None:
            self._brightness = min(100, max(0, brightness))

    # ---- commands ----
    def _power_brightness_command(self, command: int, value: int) -> bytes:
        payload = bytes((COMMAND_STX, command, value))
        if self._model == MODEL_CANDELA:
            # Gingko builders d4.c.x/v append this transition block.
            payload += b"\x01\x01\xf4"
        return payload.ljust(18, b"\x00")

    async def update(self) -> None:
        """Request the state of the lamp (sent back through a notification).

        Lamps without state feedback are only contacted when they
        are unavailable, to find out if they can be reached again.
        """
        if self._has_state_feedback is False and self._available:
            return
        # Connect first: a new backend may not support Candela notifications.
        await self.send_cmd(None)
        if self._has_state_feedback is False:
            return
        bits = struct.pack("BBB15x", COMMAND_STX, CMD_GETSTATE, CMD_GETSTATE_SEC)
        _LOGGER.debug("Send Cmd: Get_state")
        await self.send_cmd(bits, wait_notif=0.5)

    async def turn_on(self) -> None:
        """Turn the lamp on. (send back state through notif)"""
        bits = self._power_brightness_command(CMD_POWER, CMD_POWER_ON)
        _LOGGER.debug("Send Cmd: Turn On")
        await self.send_cmd(bits)
        self._is_on = True
        if self._model == MODEL_CANDELA and self._brightness <= 0:
            self._brightness = 100

    async def turn_off(self) -> None:
        """Turn the lamp off. (send back state through notif)"""
        bits = self._power_brightness_command(CMD_POWER, CMD_POWER_OFF)
        _LOGGER.debug("Send Cmd: Turn Off")
        await self.send_cmd(bits)
        self._is_on = False

    # set_brightness/temperature/color do NOT send a notification back and are
    # ignored by a lamp that is off (or still powering on, about 1 s).
    # However, the lamp takes time to transition to new state
    # and if another command (including get_state) is sent during that time,
    # it stops the transition where it is...
    async def set_brightness(self, brightness: int) -> None:
        """Set the brightness [1-100] (no notif)"""
        brightness = min(100, max(1, int(brightness)))
        _LOGGER.debug("Send Cmd: Brightness %s", brightness)
        bits = self._power_brightness_command(CMD_BRIGHTNESS, brightness)
        await self.send_cmd(bits)
        self._brightness = brightness

    async def set_temperature(self, kelvin: int, brightness: int | None = None) -> None:
        """Set the temperature (White mode) [1700 - 6500 K] (no notif)"""
        if brightness is not None:
            brightness = min(100, max(1, int(brightness)))
        kelvin = min(TEMP_MAX, max(TEMP_MIN, int(kelvin)))
        _LOGGER.debug("Send Cmd: Temperature %s, %s", kelvin, brightness)
        # Zero preserves brightness, as in the app's colour/CT-only calls.
        bits = struct.pack(">BBHB13x", COMMAND_STX, CMD_TEMP, kelvin, brightness or 0)
        await self.send_cmd(bits)
        self._temperature = kelvin
        if brightness is not None:
            self._brightness = brightness
        self._mode = self.MODE_WHITE

    async def set_color(
        self, red: int, green: int, blue: int, brightness: int | None = None
    ) -> None:
        """Set the color of the lamp [0-255] (no notif)"""
        if brightness is not None:
            brightness = min(100, max(1, int(brightness)))
        red, green, blue = (min(255, max(0, int(c))) for c in (red, green, blue))
        _LOGGER.debug("Send Cmd: Color %s, %s", (red, green, blue), brightness)
        bits = struct.pack(
            "BBBBBBB11x", COMMAND_STX, CMD_RGB, red, green, blue, 0x00, brightness or 0
        )
        await self.send_cmd(bits)
        self._rgb = (red, green, blue)
        if brightness is not None:
            self._brightness = brightness
        self._mode = self.MODE_COLOR

    # ---- notifications ----
    def _notification_handler(self, _sender: Any, data: bytearray) -> None:
        """Method called when a notification is sent from the lamp"""
        _LOGGER.debug("%s: received 0x%s", self._mac, data.hex())
        self.handle_notification(bytes(data))

    def handle_notification(self, data: bytes) -> None:
        """Process a Legacy notification, validating the fields it contains."""
        if len(data) < 2 or data[0] != COMMAND_STX:
            _LOGGER.debug(
                "%s: ignoring invalid notification 0x%s", self._mac, data.hex()
            )
            return
        res_type = data[1]

        if res_type == RES_GETSTATE:
            if self._model == MODEL_CANDELA:
                if len(data) < 7:
                    return
                mode = data[4]
                if mode == self.MODE_FLOW:
                    if len(data) < 9:
                        return
                    brightness = data[8]
                    temperature = 0
                elif mode == self.MODE_WHITE:
                    brightness = data[3]
                    temperature = int.from_bytes(data[5:7], "big")
                else:
                    # Gingko still reports power/brightness for unknown modes.
                    brightness = data[3]
                    temperature = self._temperature
                if mode in (self.MODE_WHITE, self.MODE_FLOW):
                    self._mode = mode
                self._brightness = brightness
                self._temperature = temperature
            else:
                if len(data) < 11:
                    return
                self._mode = data[3]
                self._rgb = (data[4], data[5], data[6])
                self._brightness = data[8]
                self._temperature = int.from_bytes(data[9:11], "big")
            self._is_on = data[2] == CMD_POWER_ON
            self._has_state_feedback = True
            _LOGGER.debug("%s", self)
            self.run_state_changed_cb()

        elif res_type == RES_PAIR:
            if len(data) < 3:
                return
            pair_mode = data[2]
            self._pair_result = pair_mode
            if pair_mode == PAIR_REQUESTED:
                _LOGGER.warning(
                    "Yeelight %s pairing request: %s"
                    " (All commands are ignored until the lamp is paired)",
                    self._mac,
                    "turn the Candela's cylinder now"
                    if self._model == MODEL_CANDELA
                    else "push the little button of the lamp now",
                )
                self._conn = Conn.PAIRING
            elif pair_mode == PAIR_SUCCESS or (
                pair_mode == PAIR_ALREADY_PAIRED and self._model != MODEL_CANDELA
            ):
                _LOGGER.debug("%s: paired (0x%02x)", self._mac, pair_mode)
                self._conn = Conn.PAIRED
            elif pair_mode == PAIR_NOT_PAIRED:
                _LOGGER.error(
                    "Yeelight %s is not paired! The next connection will attempt a"
                    " new pairing request",
                    self._mac,
                )
                self._conn = Conn.UNPAIRED
            else:
                # 0x06 / 0x07: lamp disconnect imminent
                _LOGGER.error(
                    "The pairing request of %s was rejected (0x%02x)",
                    self._mac,
                    pair_mode,
                )
                self._conn = Conn.UNPAIRED
            self._pair_resp_event.set()

        elif res_type == RES_GETVER:
            try:
                if self._model == MODEL_CANDELA:
                    version = data[2:].split(b"\x00", 1)[0].decode("ascii")
                    if not version or not version.isprintable():
                        return
                else:
                    if len(data) < 11:
                        return
                    # App utils.a.l: BCD MCU A/B and BLE versions. The first
                    # two slots use ffff for an absent MCU image.
                    versions = tuple(
                        0 if raw == b"\xff\xff" and index < 2 else int(raw.hex())
                        for index, raw in enumerate((data[5:7], data[7:9], data[9:11]))
                    )
                    version = "-".join(map(str, versions))
                    self.versions = versions
            except ValueError:
                _LOGGER.debug(
                    "%s: invalid firmware response 0x%s", self._mac, data.hex()
                )
                return
            self.firmware_version = version
            _LOGGER.debug("%s: firmware %s", self._mac, version)
            self.run_state_changed_cb()

        elif res_type == RES_GETSERIAL:
            if len(data) < 3:
                return
            self.serial = data[2]
            _LOGGER.debug("%s: serial %s", self._mac, self.serial)
