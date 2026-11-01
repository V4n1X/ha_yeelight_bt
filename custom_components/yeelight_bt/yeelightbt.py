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
import struct
import time
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)

NOTIFY_UUID = "8f65073d-9f57-4aaa-afea-397d19d5bbeb"
CONTROL_UUID = "aa7d3f34-2d4f-41e0-807f-52fbf8cf7443"

COMMAND_STX = 0x43
CMD_PAIR = 0x67
CMD_PAIR_ON = 0x02
RES_PAIR = 0x63
CMD_POWER = 0x40
CMD_POWER_ON = 0x01
CMD_POWER_OFF = 0x02
CMD_COLOR = 0x41
CMD_BRIGHTNESS = 0x42
CMD_TEMP = 0x43
CMD_RGB = 0x41
CMD_GETSTATE = 0x44
CMD_GETSTATE_SEC = 0x02
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
# Time given to the user to push the lamp button when it asks for pairing.
PAIR_BUTTON_TIMEOUT = 30.0
# Without notifications (Candela, some proxies) there is no pairing answer,
# so the lamp only gets a short grace period before we assume it is paired.
PAIR_GRACE_PERIOD = 1.0
# Upper bound for one connect + pair + write cycle, so that a stuck backend
# can never hold the lock (and make the lamp unavailable) forever.
OPERATION_TIMEOUT = PAIR_BUTTON_TIMEOUT + 60.0
# The Candela tends to echo an outdated state right after a command.
STALE_STATE_WINDOW = 3.0
RETRY_DELAY = 0.5
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


_LOGGER = logging.getLogger(__name__)


def model_from_name(ble_name: str | None) -> str:
    """Return the lamp model from its advertised name.

    Since bleak 1.0 the name of a device without a local name is None.
    """
    name = (ble_name or "").strip()
    if name.startswith("XMCTD_"):
        return MODEL_BEDSIDE
    if name.startswith("yeelight_ms"):
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
    ) -> None:
        self._client: BleakClient | None = None
        self._ble_device = ble_device
        self._ble_device_callback = ble_device_callback
        self._mac = ble_device.address
        self._model = model if model in MODELS else model_from_name(ble_device.name)
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
        self.serial: int | None = None

        # store func to call on state received:
        self._state_callbacks: list[Callable[[], None]] = []
        self._conn = Conn.DISCONNECTED
        self._lock = asyncio.Lock()
        self._pair_resp_event = asyncio.Event()
        self._pair_result: int | None = None
        self._notify_active = False
        # None until the first connection told us whether notifications work
        self._has_state_feedback: bool | None = None
        self._available = False
        self._contacted = False
        self._last_command = 0.0

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

    def _set_available(self, available: bool) -> None:
        changed = available != self._available or not self._contacted
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
        if self._model == MODEL_UNKNOWN:
            model = model_from_name(ble_device.name)
            if model != MODEL_UNKNOWN:
                _LOGGER.debug("%s: model detected as %s", self._mac, model)
                self._model = model

    def _refresh_ble_device(self) -> None:
        if self._ble_device_callback is None:
            return
        if (ble_device := self._ble_device_callback()) is not None:
            self.set_ble_device(ble_device)

    # ---- connection / pairing (must be called with the lock held) ----
    async def _ensure_connected(self) -> None:
        if (
            self._client is not None
            and self._client.is_connected
            and self._conn == Conn.PAIRED
        ):
            return
        if self._client is not None:
            await self._disconnect_client()

        self._refresh_ble_device()
        _LOGGER.debug("%s: connecting through %s", self._mac, self._ble_device)
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._mac,
            disconnected_callback=self._disconnected_cb,
            max_attempts=4,
            ble_device_callback=self._ble_device_callback,
        )
        self._client = client
        self._conn = Conn.UNPAIRED
        _LOGGER.debug("%s: connected", self._mac)

        await self._start_notify(client)
        await self._pair(client)
        self._set_available(True)

        if self._notify_active and self.versions is None:
            await self._write(client, struct.pack("BB16x", COMMAND_STX, CMD_GETVER))
            await self._write(client, struct.pack("BB16x", COMMAND_STX, CMD_GETSERIAL))

    async def _start_notify(self, client: BleakClient) -> None:
        try:
            await client.start_notify(NOTIFY_UUID, self._notification_handler)
        except (BleakError, ValueError) as err:
            # ESPHome proxies report Candela's notify characteristic without a
            # CCCD descriptor. Commands still work, we just get no feedback.
            _LOGGER.debug("%s: notifications not available: %s", self._mac, err)
            self._notify_active = False
        else:
            self._notify_active = True
        if self._has_state_feedback != self._notify_active:
            self._has_state_feedback = self._notify_active
            self.run_state_changed_cb()

    async def _pair(self, client: BleakClient) -> None:
        """Send the pairing request and wait for its outcome when possible."""
        self._pair_resp_event.clear()
        self._pair_result = None
        _LOGGER.debug("%s: request pairing", self._mac)
        await self._write(
            client, struct.pack("BBB15x", COMMAND_STX, CMD_PAIR, CMD_PAIR_ON)
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
                if self._model == MODEL_CANDELA and self._pair_result is None:
                    # Candela does not always answer, even with notifications
                    self._conn = Conn.PAIRED
                    return
                raise YeelightPairingError(
                    "The lamp did not answer the pairing request"
                    if self._pair_result is None
                    else "The pairing button of the lamp was not pushed"
                ) from None
            self._pair_resp_event.clear()
            if self._pair_result != PAIR_REQUESTED:
                break
            timeout = PAIR_BUTTON_TIMEOUT

        if self._pair_result in (PAIR_SUCCESS, PAIR_ALREADY_PAIRED):
            return
        if self._pair_result == PAIR_NOT_PAIRED:
            raise YeelightPairingError("The lamp refused the pairing request")
        raise YeelightPairingError(
            "The pairing request returned unexpected results, reset the lamp"
            " (https://www.youtube.com/watch?v=PnjcOSgnbAM)"
        )

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

    async def send_cmd(self, bits: bytes, wait_notif: float = 0.0) -> None:
        """Connect and pair if needed, then send one command.

        A failing write is retried once on a fresh connection, as proxies and
        adapters regularly drop connections ("Connection reset by peer",
        "EOF received", GATT error 133, ...).
        """
        async with self._lock:
            for attempt in (1, 2):
                try:
                    async with asyncio.timeout(OPERATION_TIMEOUT):
                        await self._ensure_connected()
                        assert self._client is not None
                        await self._write(self._client, bits)
                except YeelightPairingError:
                    await self._disconnect_client()
                    self._set_available(False)
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
                    self._set_available(False)
                    raise YeelightError(
                        f"Communication with {self._mac} failed: {err!r}"
                    ) from err
                except asyncio.CancelledError:
                    await self._disconnect_client()
                    raise
                break
            self._last_command = time.monotonic()
            self._set_available(True)
            if wait_notif:
                await asyncio.sleep(wait_notif)

    # ---- properties ----
    @property
    def mac(self) -> str:
        return self._mac

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

    def get_prop_min_max(self) -> dict[str, Any]:
        return {
            "brightness": {"min": 0, "max": 100},
            "temperature": {"min": TEMP_MIN, "max": TEMP_MAX},
            "color": {"min": 0, "max": 255},
        }

    def restore_state(self, is_on: bool, brightness: int | None) -> None:
        """Restore the last known state for lamps without state feedback."""
        self._is_on = is_on
        if brightness is not None:
            self._brightness = min(100, max(0, brightness))

    # ---- commands ----
    async def update(self) -> None:
        """Request the state of the lamp (sent back through a notification).

        Lamps without state feedback (Candela behind most proxies) are only
        contacted when they are unavailable, to find out if they are back.
        """
        if self._available and self._has_state_feedback is False:
            return
        bits = struct.pack("BBB15x", COMMAND_STX, CMD_GETSTATE, CMD_GETSTATE_SEC)
        _LOGGER.debug("Send Cmd: Get_state")
        await self.send_cmd(bits, wait_notif=0.5)

    get_state = update

    async def turn_on(self) -> None:
        """Turn the lamp on. (send back state through notif)"""
        bits = struct.pack("BBB15x", COMMAND_STX, CMD_POWER, CMD_POWER_ON)
        _LOGGER.debug("Send Cmd: Turn On")
        await self.send_cmd(bits)
        self._is_on = True
        if self._model == MODEL_CANDELA and self._brightness <= 0:
            self._brightness = 100

    async def turn_off(self) -> None:
        """Turn the lamp off. (send back state through notif)"""
        bits = struct.pack("BBB15x", COMMAND_STX, CMD_POWER, CMD_POWER_OFF)
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
        bits = struct.pack("BBB15x", COMMAND_STX, CMD_BRIGHTNESS, brightness)
        await self.send_cmd(bits)
        self._brightness = brightness

    async def set_temperature(self, kelvin: int, brightness: int | None = None) -> None:
        """Set the temperature (White mode) [1700 - 6500 K] (no notif)"""
        if brightness is None:
            brightness = self._brightness
        brightness = min(100, max(1, int(brightness)))
        kelvin = min(TEMP_MAX, max(TEMP_MIN, int(kelvin)))
        _LOGGER.debug("Send Cmd: Temperature %s, %s", kelvin, brightness)
        bits = struct.pack(">BBhB13x", COMMAND_STX, CMD_TEMP, kelvin, brightness)
        await self.send_cmd(bits)
        self._temperature = kelvin
        self._brightness = brightness
        self._mode = self.MODE_WHITE

    async def set_color(
        self, red: int, green: int, blue: int, brightness: int | None = None
    ) -> None:
        """Set the color of the lamp [0-255] (no notif)"""
        if brightness is None:
            brightness = self._brightness
        brightness = min(100, max(1, int(brightness)))
        red, green, blue = (min(255, max(0, int(c))) for c in (red, green, blue))
        _LOGGER.debug("Send Cmd: Color %s, %s", (red, green, blue), brightness)
        bits = struct.pack(
            "BBBBBBB11x", COMMAND_STX, CMD_RGB, red, green, blue, 0x01, brightness
        )
        await self.send_cmd(bits)
        self._rgb = (red, green, blue)
        self._brightness = brightness
        self._mode = self.MODE_COLOR

    async def get_name(self) -> None:
        """Get the name from the lamp (through notif)"""
        _LOGGER.debug("Send Cmd: Get_Name")
        await self.send_cmd(struct.pack("BB16x", COMMAND_STX, CMD_GETNAME))

    async def get_version(self) -> None:
        """Get the versions from the lamp (through notif)"""
        _LOGGER.debug("Send Cmd: Get_Version")
        await self.send_cmd(struct.pack("BB16x", COMMAND_STX, CMD_GETVER))

    async def get_serial(self) -> None:
        """Get the serial from the lamp (through notif)"""
        _LOGGER.debug("Send Cmd: Get_Serial")
        await self.send_cmd(struct.pack("BB16x", COMMAND_STX, CMD_GETSERIAL))

    # ---- notifications ----
    def _notification_handler(self, _sender: Any, data: bytearray) -> None:
        """Method called when a notification is sent from the lamp"""
        _LOGGER.debug("%s: received 0x%s", self._mac, data.hex())
        self.handle_notification(bytes(data))

    def handle_notification(self, data: bytes) -> None:
        """Process one 18 byte notification frame."""
        if len(data) < 18:
            _LOGGER.debug("%s: ignoring short notification 0x%s", self._mac, data.hex())
            return
        data = data[:18]
        res_type = data[1]

        if res_type == RES_GETSTATE:
            state = struct.unpack(">xxBBBBBBBhx6x", data)
            if (
                self._model == MODEL_CANDELA
                and time.monotonic() - self._last_command < STALE_STATE_WINDOW
            ):
                # Candela over proxies echoes the state from before the command
                _LOGGER.debug("%s: ignoring stale Candela state", self._mac)
                return
            self._is_on = state[0] == CMD_POWER_ON
            if self._model == MODEL_CANDELA:
                self._brightness = state[1]
                self._mode = self.MODE_WHITE
            else:
                self._mode = state[1]
                self._rgb = (state[2], state[3], state[4])
                self._brightness = state[6]
                self._temperature = state[7]
            _LOGGER.debug("%s", self)
            self.run_state_changed_cb()

        elif res_type == RES_PAIR:
            pair_mode = data[2]
            self._pair_result = pair_mode
            if pair_mode == PAIR_REQUESTED:
                _LOGGER.warning(
                    "Yeelight %s pairing request: push the little button of the lamp"
                    " now! (All commands are ignored until the lamp is paired)",
                    self._mac,
                )
                self._conn = Conn.PAIRING
            elif pair_mode in (PAIR_SUCCESS, PAIR_ALREADY_PAIRED):
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
                    "The pairing request of %s returned unexpected results (0x%02x)."
                    " Please reset the lamp (https://www.youtube.com/watch?v=PnjcOSgnbAM)",
                    self._mac,
                    pair_mode,
                )
                self._conn = Conn.UNPAIRED
            self._pair_resp_event.set()

        elif res_type == RES_GETVER:
            self.versions = struct.unpack("<xxBxHHHH6x", data)
            _LOGGER.info("Lamp %s exposes versions:%s", self._mac, self.versions)
            self.run_state_changed_cb()

        elif res_type == RES_GETSERIAL:
            self.serial = data[2]
            _LOGGER.info("Lamp %s exposes serial:%s", self._mac, self.serial)

    async def read_services(self) -> None:
        """Log all services/characteristics (debugging helper, not used by HA)."""
        if self._client is None:
            return
        for service in self._client.services:
            _LOGGER.info("[Service] %s", service)
            for char in service.characteristics:
                value: Any = None
                if "read" in char.properties:
                    try:
                        value = bytes(await self._client.read_gatt_char(char))
                    except RETRY_EXCEPTIONS as err:
                        value = err
                _LOGGER.info(
                    "__[Characteristic] %s (%s), Value: %s",
                    char,
                    ",".join(char.properties),
                    value,
                )
