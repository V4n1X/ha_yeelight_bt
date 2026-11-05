"""
Creator : hcoohb
License : MIT
Source  : https://github.com/hcoohb/hass-yeelightbt

Bluetooth protocol and connection handling for the Yeelight Bedside and Candela.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, time as dt_time
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
CMD_SETTIME = 0x60
RES_GETTIME = 0x62
CMD_SETNIGHTLIGHT = 0x6F
CMD_GETNIGHTLIGHT = 0x70
RES_NIGHTLIGHT = 0x71
CMD_DELETE_AUTH = 0x72

PAIR_REQUESTED = 0x01
PAIR_SUCCESS = 0x02
PAIR_NOT_PAIRED = 0x03
PAIR_ALREADY_PAIRED = 0x04
# A Bedside that does not know the identifier answers this while it is off;
# switched on, it pulses (PAIR_REQUESTED) instead.
PAIR_LAMP_OFF = 0x06

MODEL_BEDSIDE = "Bedside"
MODEL_CANDELA = "Candela"
MODEL_UNKNOWN = "Unknown"
MODELS = (MODEL_BEDSIDE, MODEL_CANDELA)

TEMP_MIN = 1700
TEMP_MAX = 6500

# Time to wait for the pairing answer of a lamp that sends notifications.
PAIR_RESPONSE_TIMEOUT = 5.0
# Time given to confirm pairing on the lamp (button or Candela cylinder). A
# Bedside waits 60 s for its button before it answers 0x05.
PAIR_BUTTON_TIMEOUT = 60.0
# A Bedside without notifications gets a short grace period after pairing.
PAIR_GRACE_PERIOD = 1.0
# Upper bound for one connect + pair + write cycle, so that a stuck backend
# can never hold the lock (and make the lamp unavailable) forever.
OPERATION_TIMEOUT = PAIR_BUTTON_TIMEOUT + 60.0
# "yeelight_ms" or "yeelight_ms" with a suffix, like the Homey Candela app
CANDELA_NAME = re.compile(r"^yeelight_ms(?:$|[_ -])", re.IGNORECASE)
RETRY_DELAY = 0.5
# Wait for the 43 71 answer to a night-light query (43 70) before using the value
NIGHT_LIGHT_READ_TIMEOUT = 2.0
# A Bedside switched on inside its night-light window comes up in color mode
# with this color, at the window's brightness (always 1 % from the app).
NIGHT_LIGHT_COLOR = (0xFF, 0x98, 0x00)
NIGHT_LIGHT_BRIGHTNESS = 0x01
# Wait for the state a Bedside pushes after a power-on command
POWER_ON_REPORT_TIMEOUT = 2.0
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


@dataclass(frozen=True)
class NightLight:
    """The night-light window of a Bedside lamp.

    The lamp also stores a brightness for the window; the Yeelight app always
    sends 1 % and offers no setting, so it is not part of this state.
    """

    enabled: bool
    start: dt_time
    end: dt_time


@dataclass(frozen=True)
class LightState:
    """Mode, color, temperature and brightness of a Bedside, as in 43 45.

    The lamp reports no temperature in color mode and no color in white mode.
    """

    mode: int
    color: tuple[int, int, int]
    temperature: int
    brightness: int

    def as_dict(self) -> dict[str, Any]:
        """The state as JSON data."""
        return {
            "mode": self.mode,
            "color": list(self.color),
            "temperature": self.temperature,
            "brightness": self.brightness,
        }

    @classmethod
    def from_dict(cls, data: Any) -> LightState | None:
        """Read stored data, None when it is missing or malformed."""
        try:
            red, green, blue = (int(value) for value in data["color"])
            return cls(
                mode=int(data["mode"]),
                color=(red, green, blue),
                temperature=int(data["temperature"]),
                brightness=int(data["brightness"]),
            )
        except KeyError, TypeError, ValueError:
            return None


_LOGGER = logging.getLogger(__name__)


def _to_bcd(value: int) -> int:
    """Encode a two-digit decimal number as one BCD byte (21 -> 0x21)."""
    return ((value // 10) << 4) | (value % 10)


def _from_bcd(value: int) -> int:
    """Decode a BCD byte, raising ValueError for digits outside 0-9."""
    tens, ones = value >> 4, value & 0x0F
    if tens > 9 or ones > 9:
        raise ValueError(f"not a BCD byte: 0x{value:02x}")
    return tens * 10 + ones


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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client: BleakClient | None = None
        self._ble_device = ble_device
        self._ble_device_callback = ble_device_callback
        # Local time for the Bedside clock, set on every connection; None: not set
        self._clock = clock
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
        # The night-light window, None until the lamp reported it (43 71)
        self._night_light: NightLight | None = None
        # Bedside after a power-on inside the night-light window: the state it
        # shows again at its next normal power-on. It stores neither the night
        # light nor changes made during it, but afterwards it keeps reporting
        # the night-light color and brightness, and uses them for commands that
        # keep the brightness, until a command sets them again. None: no night
        # light since, or the state before it is not known.
        self._power_on_state: LightState | None = None
        # The lamp came up with _power_on_state but still reports the night light
        self._resync_pending = False
        # Its report at that power-on, to tell later changes on the lamp apart
        self._stale_report: LightState | None = None
        # Power of the last state report; None after connecting and while our
        # own power-on is on its way, so that only a change counts as power-on
        self._reported_on: bool | None = None
        self._power_on_expected = False
        self._power_on_report = asyncio.Event()

        # store func to call on state received:
        self._state_callbacks: list[Callable[[], None]] = []
        self._conn = Conn.DISCONNECTED
        self._lock = asyncio.Lock()
        self._pair_resp_event = asyncio.Event()
        self._pair_result: int | None = None
        # Set when an accepted 43 71 answer arrived
        self._night_light_event = asyncio.Event()
        # Incremented by every successful write. Each 43 70 query is queued with
        # the value it was sent under; its answer then describes the window
        # before a write that happened meanwhile and must not undo that write.
        self._night_light_generation = 0
        self._night_light_pending: list[int] = []
        # The lamp no longer knows our identifier and waits for its button
        self._pairing_requested = False
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
        self._reported_on = None
        # Answers of queries sent on this link can no longer arrive
        self._forget_night_light_queries()
        # The lamp stays available: the next command or poll reconnects and
        # only a failure there marks it unavailable.
        self.run_state_changed_cb()

    def _forget_night_light_queries(self) -> None:
        """Drop pending 43 70 queries whose link is gone."""
        self._night_light_pending.clear()
        self._night_light_event.clear()

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
    async def _ensure_connected(self, wait_for_button: bool = True) -> BleakClient:
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

        await self._pair(client, wait_for_button)
        self._set_available(True)

        if self._clock is not None and self._model != MODEL_CANDELA:
            # Like the app on every connect; the lamp's own timers use this clock.
            await self._write(client, self._time_command(self._clock()))
        if self._notify_active and self.firmware_version is None:
            await self._write(client, struct.pack("BB16x", COMMAND_STX, CMD_GETVER))
        if self._notify_active and self._model != MODEL_CANDELA:
            # The state cannot be assumed without notifications: every write
            # sends all fields, so unknown times would be overwritten.
            await self._query_night_light(client)
        return client

    async def _query_night_light(self, client: BleakClient) -> None:
        """Send 43 70 00 and queue the generation the query belongs to."""
        self._night_light_event.clear()
        self._night_light_pending.append(self._night_light_generation)
        await self._write(client, self._night_light_query())

    async def _async_await_night_light(self) -> None:
        """Wait (best effort) for the answers to all pending queries."""
        with suppress(TimeoutError):
            async with asyncio.timeout(NIGHT_LIGHT_READ_TIMEOUT):
                while self._night_light_pending:
                    await self._night_light_event.wait()
                    self._night_light_event.clear()

    @staticmethod
    def _time_command(now: datetime) -> bytes:
        """43 60 with BCD second, minute, hour, day, weekday (Sunday 0), month, year."""
        fields = (
            now.second,
            now.minute,
            now.hour,
            now.day,
            now.isoweekday() % 7,
            now.month,
            min(99, max(0, now.year - 2000)),
        )
        return bytes((COMMAND_STX, CMD_SETTIME, *map(_to_bcd, fields))).ljust(
            18, b"\x00"
        )

    @staticmethod
    def _night_light_command(night_light: NightLight) -> bytes:
        """43 6F with enabled, the app's fixed brightness 1 %, start and end (BCD).

        Times may cross midnight (21:00-07:00).
        """
        return bytes(
            (
                COMMAND_STX,
                CMD_SETNIGHTLIGHT,
                0x01 if night_light.enabled else 0x00,
                NIGHT_LIGHT_BRIGHTNESS,
                _to_bcd(night_light.start.hour),
                _to_bcd(night_light.start.minute),
                _to_bcd(night_light.end.hour),
                _to_bcd(night_light.end.minute),
            )
        ).ljust(18, b"\x00")

    @staticmethod
    def _night_light_query() -> bytes:
        """43 70 00, the lamp answers with 43 71."""
        return struct.pack("BBB15x", COMMAND_STX, CMD_GETNIGHTLIGHT, 0x00)

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

    async def _pair(self, client: BleakClient, wait_for_button: bool = True) -> None:
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
            if not wait_for_button:
                raise YeelightPairingError("The lamp asks to be paired again")
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
        self._reported_on = None
        # Answers of queries sent on the old link can no longer arrive
        self._forget_night_light_queries()
        if client is None:
            return
        try:
            await client.disconnect()
        except RETRY_EXCEPTIONS as err:
            _LOGGER.debug("%s: error while disconnecting: %s", self._mac, err)

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect_client()

    async def unpair(self) -> None:
        """Remove the authorization of our identifier from the lamp (43 72).

        Only a lamp that still accepts the identifier is contacted; one that asks
        for pairing again is left alone instead of waiting for its button.
        """
        if self._pairing_id is None:
            return
        async with self._lock:
            try:
                client = await self._ensure_connected(wait_for_button=False)
                await self._write(
                    client,
                    struct.pack(
                        "BB6s10x", COMMAND_STX, CMD_DELETE_AUTH, self._pairing_id
                    ),
                )
                _LOGGER.debug("%s: authorization removed", self._mac)
            except RETRY_EXCEPTIONS as err:
                raise YeelightError(
                    f"Communication with {self._mac} failed: {err!r}"
                ) from err
            finally:
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
            await self._send_cmd(bits, wait_notif)

    async def _send_cmd(self, bits: bytes | None, wait_notif: float = 0.0) -> None:
        """Send one command (must be called with the lock held)."""
        if (
            not self.is_connected
            and self._last_connect_failure is not None
            and time.monotonic() - self._last_connect_failure < CONNECT_FAILURE_HOLDOFF
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
    def pairing_requested(self) -> bool:
        """Whether the lamp asked for pairing again and waits for confirmation."""
        return self._pairing_requested

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

    @property
    def night_light(self) -> NightLight | None:
        """The night-light window, None until the lamp reported it (43 71)."""
        return self._night_light

    @property
    def power_on_state(self) -> LightState | None:
        """The state a Bedside shows again after the night light, if known."""
        return self._power_on_state

    @property
    def resync_pending(self) -> bool:
        """Whether the lamp came up from the night light and reports its leftovers."""
        return self._resync_pending

    def restore_state(self, is_on: bool, brightness: int | None) -> None:
        """Restore the last known state for lamps without state feedback."""
        self._is_on = is_on
        if brightness is not None:
            self._brightness = min(100, max(0, brightness))

    def restore_power_on_state(self, state: LightState | None) -> None:
        """Take the state kept over a restart, unless a newer one is known."""
        if (
            self._model != MODEL_CANDELA
            and self._power_on_state is None
            and state is not None
            and self._restorable(state)
        ):
            self._power_on_state = state

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
        # Its report tells whether the lamp came up with the night light; one
        # that was due before belongs to the old power state.
        self._reported_on = None
        self._power_on_expected = True
        self._power_on_report.clear()
        try:
            await self.send_cmd(bits)
        except BaseException:
            self._power_on_expected = False
            raise
        self._is_on = True
        if self._model == MODEL_CANDELA and self._brightness <= 0:
            self._brightness = 100

    async def async_wait_power_on(self) -> None:
        """Wait (best effort) for the report of the last power-on command.

        It decides how the next command is sent after the night light. A report
        arriving later no longer counts as that power-on: it would describe the
        lamp before the commands that follow now.
        """
        if (
            self._power_on_expected
            and self._notify_active
            and self._model != MODEL_CANDELA
        ):
            with suppress(TimeoutError):
                async with asyncio.timeout(POWER_ON_REPORT_TIMEOUT):
                    await self._power_on_report.wait()
        self._power_on_expected = False

    async def turn_off(self) -> None:
        """Turn the lamp off. (send back state through notif)"""
        bits = self._power_brightness_command(CMD_POWER, CMD_POWER_OFF)
        _LOGGER.debug("Send Cmd: Turn Off")
        await self.send_cmd(bits)
        self._is_on = False
        # The lamp keeps the leftovers, its next normal power-on writes back
        self._resync_pending = False

    async def set_night_light(
        self,
        enabled: bool | None = None,
        start: dt_time | None = None,
        end: dt_time | None = None,
    ) -> None:
        """Set the night-light window; None keeps the value read from the lamp.

        The lamp answers only with a generic 43 82 acknowledgement, so the state
        is updated locally. Merging and writing happen under the lock: a second
        change running right behind keeps the fields of the first one.
        """
        async with self._lock:
            current = await self._async_current_night_light()
            if current is None:
                raise YeelightError("The lamp has not reported its night light yet")
            night_light = NightLight(
                enabled=current.enabled if enabled is None else enabled,
                start=current.start if start is None else start,
                end=current.end if end is None else end,
            )
            _LOGGER.debug(
                "Send Cmd: Night light %s %s-%s",
                "on" if night_light.enabled else "off",
                night_light.start.strftime("%H:%M"),
                night_light.end.strftime("%H:%M"),
            )
            await self._send_cmd(self._night_light_command(night_light))
            # Later answers describe the window from before this write
            self._night_light_generation += 1
            self._night_light = night_light
        self.run_state_changed_cb()

    async def _async_current_night_light(self) -> NightLight | None:
        """The window to merge a change into (must be called with the lock held).

        A connection established now queries the lamp; its answer describes the
        window as it is, while the cached one may be outdated by changes in the
        Yeelight app. Waiting for it keeps a stale answer from undoing the write.
        """
        if not self.is_connected:
            await self._send_cmd(None)
        elif self._night_light_pending:
            # An earlier query was never answered: ask again, its answer would
            # otherwise arrive after the write and describe the old window.
            client = self._client
            assert client is not None
            await self._query_night_light(client)
        if not self._notify_active:
            # A backend without notifications cannot confirm the window: the
            # cached one may be outdated and must not be written back.
            return None
        if self._night_light_pending:
            await self._async_await_night_light()
        return self._night_light

    # set_brightness/temperature/color do NOT send a notification back and are
    # ignored by a lamp that is off (or still powering on, about 1 s).
    # However, the lamp takes time to transition to new state
    # and if another command (including get_state) is sent during that time,
    # it stops the transition where it is...
    async def set_brightness(self, brightness: int) -> None:
        """Set the brightness [1-100] (no notif)"""
        brightness = min(100, max(1, int(brightness)))
        if self._resync_pending and (state := self._power_on_state) is not None:
            # The lamp still holds the night-light color: send the shown one
            await self._write_state(replace(state, brightness=brightness))
            return
        _LOGGER.debug("Send Cmd: Brightness %s", brightness)
        bits = self._power_brightness_command(CMD_BRIGHTNESS, brightness)
        await self.send_cmd(bits)
        self._brightness = brightness

    async def set_temperature(self, kelvin: int, brightness: int | None = None) -> None:
        """Set the temperature (White mode) [1700 - 6500 K] (no notif)"""
        brightness = self._command_brightness(brightness)
        kelvin = min(TEMP_MAX, max(TEMP_MIN, int(kelvin)))
        _LOGGER.debug("Send Cmd: Temperature %s, %s", kelvin, brightness)
        await self.send_cmd(self._temperature_command(kelvin, brightness or 0))
        self._temperature = kelvin
        if brightness is not None:
            self._brightness = brightness
            self._command_set_all_fields()
        self._mode = self.MODE_WHITE

    async def set_color(
        self, red: int, green: int, blue: int, brightness: int | None = None
    ) -> None:
        """Set the color of the lamp [0-255] (no notif)"""
        brightness = self._command_brightness(brightness)
        red, green, blue = (min(255, max(0, int(c))) for c in (red, green, blue))
        _LOGGER.debug("Send Cmd: Color %s, %s", (red, green, blue), brightness)
        await self.send_cmd(self._color_command(red, green, blue, brightness or 0))
        self._rgb = (red, green, blue)
        if brightness is not None:
            self._brightness = brightness
            self._command_set_all_fields()
        self._mode = self.MODE_COLOR

    def _command_brightness(self, brightness: int | None) -> int | None:
        """The brightness for a color or temperature command, None: keep it."""
        if brightness is not None:
            return min(100, max(1, int(brightness)))
        if self._resync_pending and self._power_on_state is not None:
            # Keeping it would apply the night light's brightness
            return self._power_on_state.brightness
        return None

    def _command_set_all_fields(self) -> None:
        """A command set color or temperature and brightness.

        Inside the night light the lamp stores nothing, the kept state stays.
        After it, the lamp now reports and stores what it shows.
        """
        if self._resync_pending:
            self._forget_power_on_state()

    @staticmethod
    def _temperature_command(kelvin: int, brightness: int) -> bytes:
        # Zero preserves brightness, as in the app's colour/CT-only calls.
        return struct.pack(">BBHB13x", COMMAND_STX, CMD_TEMP, kelvin, brightness)

    @staticmethod
    def _color_command(red: int, green: int, blue: int, brightness: int) -> bytes:
        return struct.pack(
            "BBBBBBB11x", COMMAND_STX, CMD_RGB, red, green, blue, 0x00, brightness
        )

    def _state_command(self, state: LightState) -> bytes:
        """Set all fields of a state; a flow only gets its brightness and keeps on."""
        if state.mode == self.MODE_WHITE:
            return self._temperature_command(state.temperature, state.brightness)
        if state.mode == self.MODE_COLOR:
            return self._color_command(*state.color, state.brightness)
        return self._power_brightness_command(CMD_BRIGHTNESS, state.brightness)

    async def _write_state(self, state: LightState) -> None:
        """Send all fields of a state to a lamp that came up from the night light."""
        _LOGGER.debug("Send Cmd: State %s", state)
        await self.send_cmd(self._state_command(state))
        self._show(state)
        self._forget_power_on_state()

    async def async_resync(self) -> None:
        """Write the shown state back to a lamp that came up from the night light.

        The lamp shows that state already, the write only replaces the
        night-light values it still reports. Skipped when a command or a
        power-off came first.
        """
        async with self._lock:
            state = self._power_on_state
            if not self._resync_pending or not self._is_on or state is None:
                return
            _LOGGER.debug("Send Cmd: Restore %s", state)
            await self._send_cmd(self._state_command(state))
            self._forget_power_on_state()
        self.run_state_changed_cb()

    # ---- state after the night light (Bedside) ----
    def _forget_power_on_state(self) -> None:
        self._power_on_state = None
        self._resync_pending = False
        self._stale_report = None

    def _show(self, state: LightState) -> None:
        self._mode = state.mode
        self._rgb = state.color
        self._temperature = state.temperature
        self._brightness = state.brightness

    @classmethod
    def _restorable(cls, state: LightState) -> bool:
        """Whether a state can be written back to the lamp as it is.

        The night light's own color and brightness are refused: after the
        night light, they are what the lamp reports, not what it shows.
        """
        if not NIGHT_LIGHT_BRIGHTNESS < state.brightness <= 100:
            return False
        if state.mode == cls.MODE_WHITE:
            return TEMP_MIN <= state.temperature <= TEMP_MAX
        if state.mode == cls.MODE_COLOR:
            return state.color not in ((0, 0, 0), NIGHT_LIGHT_COLOR)
        return state.mode == cls.MODE_FLOW

    def _night_light_power_on(self, reported: LightState) -> bool:
        """Whether the lamp came up with its night light.

        The report alone cannot tell: a lamp in color mode keeps reporting the
        night-light color after a normal power-on as well. The window decides,
        the lamp clock is set to Home Assistant's time on every connection.
        When in doubt it is the night light, a write-back would override it.
        """
        if reported.mode != self.MODE_COLOR or reported.color != NIGHT_LIGHT_COLOR:
            return False
        night_light = self._night_light
        if night_light is None or self._clock is None:
            return True
        if not night_light.enabled:
            return False
        now = self._clock().time()
        if night_light.start < night_light.end:
            return night_light.start <= now < night_light.end
        # Crossing midnight; equal times count as the whole day
        return now >= night_light.start or now < night_light.end

    def _handle_bedside_state(self, is_on: bool, reported: LightState) -> None:
        """Show a state report, or the state it hides after the night light."""
        powered_on = is_on and (self._power_on_expected or self._reported_on is False)
        self._reported_on = is_on
        if is_on:
            self._power_on_expected = False
            self._power_on_report.set()
        if powered_on:
            self._resync_pending = False
            if self._night_light_power_on(reported):
                self._keep_power_on_state()
            elif (state := self._power_on_state) is not None:
                _LOGGER.debug(
                    "%s: back from the night light, shows %s, reports %s",
                    self._mac,
                    state,
                    reported,
                )
                self._resync_pending = True
                self._stale_report = reported
                self._show(state)
                return
        elif self._resync_pending:
            if not is_on:
                # Off before the write-back: the next power-on tries again
                self._resync_pending = False
                return
            if self._take_change_on_lamp(reported):
                return
            _LOGGER.debug("%s: changed on the lamp, state %s", self._mac, reported)
            self._forget_power_on_state()
        self._show(reported)

    def _keep_power_on_state(self) -> None:
        """Keep the state from before the night light, the lamp shows it later."""
        if self._power_on_state is not None:
            # From an earlier night-light power-on, the lamp stored nothing since
            return
        state = LightState(
            self._mode or 0, self._rgb, self._temperature, self._brightness
        )
        if self._restorable(state):
            self._power_on_state = state
            _LOGGER.debug("%s: night light, keeping %s", self._mac, state)
        else:
            _LOGGER.debug("%s: night light, state before it unknown", self._mac)

    def _take_change_on_lamp(self, reported: LightState) -> bool:
        """Take a change made on the lamp before the write-back, if it can be.

        Its slider only changes the brightness of the report; any other change
        means the lamp no longer shows the kept state.
        """
        stale, state = self._stale_report, self._power_on_state
        if (
            stale is None
            or state is None
            or replace(reported, brightness=stale.brightness) != stale
        ):
            return False
        if reported.brightness != stale.brightness:
            state = replace(state, brightness=reported.brightness)
            self._power_on_state = state
            self._stale_report = reported
        self._show(state)
        return True

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
                self._handle_bedside_state(
                    data[2] == CMD_POWER_ON,
                    LightState(
                        mode=data[3],
                        color=(data[4], data[5], data[6]),
                        temperature=int.from_bytes(data[9:11], "big"),
                        brightness=data[8],
                    ),
                )
            self._is_on = data[2] == CMD_POWER_ON
            self._has_state_feedback = True
            _LOGGER.debug("%s", self)
            self.run_state_changed_cb()

        elif res_type == RES_PAIR:
            if len(data) < 3:
                return
            pair_mode = data[2]
            self._pair_result = pair_mode
            paired = pair_mode == PAIR_SUCCESS or (
                pair_mode == PAIR_ALREADY_PAIRED and self._model != MODEL_CANDELA
            )
            lamp_off = pair_mode == PAIR_LAMP_OFF and self._model != MODEL_CANDELA
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
            elif paired:
                _LOGGER.debug("%s: paired (0x%02x)", self._mac, pair_mode)
                self._conn = Conn.PAIRED
            elif pair_mode == PAIR_NOT_PAIRED:
                _LOGGER.error(
                    "Yeelight %s is not paired! The next connection will attempt a"
                    " new pairing request",
                    self._mac,
                )
                self._conn = Conn.UNPAIRED
            elif lamp_off:
                _LOGGER.warning(
                    "Yeelight %s is not paired and switched off: switch it on at"
                    " the lamp, then push its little button while it pulses",
                    self._mac,
                )
                self._conn = Conn.UNPAIRED
            else:
                # 0x07 (and 0x06 on a Candela): lamp disconnect imminent
                _LOGGER.error(
                    "The pairing request of %s was rejected (0x%02x)",
                    self._mac,
                    pair_mode,
                )
                self._conn = Conn.UNPAIRED
            self._pair_resp_event.set()
            needs_pairing = pair_mode == PAIR_REQUESTED or lamp_off
            if needs_pairing and not self._pairing_requested:
                self._pairing_requested = True
                self.run_state_changed_cb()
            elif paired and self._pairing_requested:
                self._pairing_requested = False
                self.run_state_changed_cb()

        elif res_type == RES_NIGHTLIGHT:
            if len(data) < 8:
                _LOGGER.debug(
                    "%s: invalid night light response 0x%s", self._mac, data.hex()
                )
                return
            try:
                start = dt_time(_from_bcd(data[4]), _from_bcd(data[5]))
                end = dt_time(_from_bcd(data[6]), _from_bcd(data[7]))
            except ValueError:
                _LOGGER.debug(
                    "%s: invalid night light response 0x%s", self._mac, data.hex()
                )
                return
            # Each response answers the oldest pending query. A query from
            # before the last write describes the window before it.
            query_generation = (
                self._night_light_pending.pop(0)
                if self._night_light_pending
                else self._night_light_generation
            )
            self._night_light_event.set()
            if query_generation < self._night_light_generation:
                _LOGGER.debug(
                    "%s: ignoring stale night light response 0x%s",
                    self._mac,
                    data.hex(),
                )
                return
            self._night_light = NightLight(
                enabled=data[2] == 0x01, start=start, end=end
            )
            _LOGGER.debug(
                "%s: night light %s, %s-%s",
                self._mac,
                "on" if self._night_light.enabled else "off",
                start.strftime("%H:%M"),
                end.strftime("%H:%M"),
            )
            self.run_state_changed_cb()

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
