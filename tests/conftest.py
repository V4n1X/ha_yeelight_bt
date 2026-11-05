"""Fixtures for the yeelight_bt tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
import time
from typing import Any
from unittest.mock import patch

from bleak import BleakError
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
import pytest

try:  # HA >= 2026.11 ships its own test package
    from pytest_homeassistant.common import (
        MockConfigEntry,
        async_fire_time_changed,
        async_mock_restore_state_shutdown_restart,
        mock_restore_cache,
        mock_restore_cache_with_extra_data,
    )
except ImportError:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_time_changed,
        async_mock_restore_state_shutdown_restart,
        mock_restore_cache,
        mock_restore_cache_with_extra_data,
    )

from custom_components.yeelight_bt import yeelightbt
from custom_components.yeelight_bt.yeelightbt import (
    CMD_BRIGHTNESS,
    CMD_GETNIGHTLIGHT,
    CMD_GETSTATE,
    CMD_GETVER,
    CMD_PAIR,
    CMD_POWER,
    CMD_POWER_ON,
    CMD_RGB,
    CMD_TEMP,
    COMMAND_STX,
    MODEL_BEDSIDE,
    MODEL_CANDELA,
    PAIR_SUCCESS,
    RES_GETSTATE,
    RES_PAIR,
    Lamp,
    LightState,
)

BEDSIDE_ADDRESS = "F8:24:41:E6:3E:39"
CANDELA_ADDRESS = "F8:24:41:C0:0E:D2"
CANDELA_PAIRING_ID = bytes.fromhex("0123456789ab")

# Literal Bedside notification captured on 2026-11-02, not packed by our decoder.
BEDSIDE_VERSION_FRAME = bytes.fromhex("435d02200122312238233004030404040300")
# Gingko examples follow the APK's r3.f0.G1 layout; no Candela capture is available.
CANDELA_STATE_FRAME = bytes.fromhex("434501500207080000000000000000000000")
CANDELA_VERSION_FRAME = b"\x43\x5d2.2.0\x00".ljust(18, b"\x00")
# Night light off, 00:00-05:00 (the factory state of the test lamp), BCD bytes.
NIGHT_LIGHT_FRAME = bytes.fromhex("437100010000050000000000000000000000")
# Night light on, 22:00-09:00. The state reports below were captured on
# 2026-11-05 at 01:18 lamp time: off at white 4000 K / 60 %, switched on inside
# the window (shows and reports orange at 1 %), off, then with the night light
# switched off on again: the lamp showed white 4000 K at 60 % but reported 1 %.
NIGHT_LIGHT_WINDOW_FRAME = bytes.fromhex("437101012200090000000000000000000000")
WHITE_60_OFF_FRAME = bytes.fromhex("43450202000000003c0fa022000000000000")
NIGHT_LIGHT_ON_FRAME = bytes.fromhex("43450101ff98000001000022000000000000")
NIGHT_LIGHT_OFF_FRAME = bytes.fromhex("43450201ff98000001000022000000000000")
AFTER_NIGHT_LIGHT_FRAME = bytes.fromhex("4345010200000000010fa022000000000000")
WHITE_60 = LightState(Lamp.MODE_WHITE, (0, 0, 0), 4000, 60)
GREEN_100 = LightState(Lamp.MODE_COLOR, (0, 255, 0), 0, 100)

__all__ = [
    "MockConfigEntry",
    "async_fire_time_changed",
    "async_mock_restore_state_shutdown_restart",
    "mock_restore_cache",
    "mock_restore_cache_with_extra_data",
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in all tests."""


@pytest.fixture(autouse=True)
def fast_timings() -> Generator[None]:
    """Remove the waits meant for real lamps."""
    with (
        patch.object(yeelightbt, "PAIR_GRACE_PERIOD", 0),
        patch.object(yeelightbt, "PAIR_RESPONSE_TIMEOUT", 1.0),
        patch.object(yeelightbt, "PAIR_BUTTON_TIMEOUT", 1.0),
        patch.object(yeelightbt, "RETRY_DELAY", 0),
        patch("custom_components.yeelight_bt.light.POWER_ON_DELAY", 0),
        patch("custom_components.yeelight_bt.light.TRANSITION_DELAY", 0),
    ):
        yield


def ble_device(address: str, name: str | None) -> BLEDevice:
    return BLEDevice(address, name, {})


def service_info(address: str, name: str | None) -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name=name or address,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=ble_device(address, name),
        advertisement=AdvertisementData(
            local_name=name,
            manufacturer_data={},
            service_data={},
            service_uuids=[],
            tx_power=None,
            rssi=-60,
            platform_data=(),
        ),
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
    )


def frame(*values: int) -> bytearray:
    return bytearray(bytes(values).ljust(18, b"\x00"))


def state_frame(on: bool, state: LightState) -> bytes:
    """A Bedside state report (43 45)."""
    return bytes(
        frame(
            COMMAND_STX,
            RES_GETSTATE,
            0x01 if on else 0x02,
            state.mode,
            *state.color,
            0x00,
            state.brightness,
            *state.temperature.to_bytes(2, "big"),
        )
    )


class FakeLampClient:
    """Minimal stand-in for a connected BleakClient talking to a lamp."""

    def __init__(
        self,
        *,
        notify: bool = True,
        pair_responses: tuple[int, ...] = (PAIR_SUCCESS,),
        state: bytearray | None = None,
        fail_writes: int = 0,
        model: str = MODEL_BEDSIDE,
    ) -> None:
        self.notify = notify
        self.model = model
        self.pair_responses = pair_responses
        # What a Bedside reports after the next power-on command, like a lamp
        # inside its night-light window; None: its state, switched on
        self.power_on_state: bytes | None = None
        self.state = state or (
            bytearray(CANDELA_STATE_FRAME)
            if model == MODEL_CANDELA
            else frame(
                COMMAND_STX, RES_GETSTATE, 0x01, 0x02, 0, 0, 0, 0, 80, 0x0F, 0xF0
            )
        )
        self.version_frame = (
            CANDELA_VERSION_FRAME if model == MODEL_CANDELA else BEDSIDE_VERSION_FRAME
        )
        self.fail_writes = fail_writes
        self.writes: list[bytes] = []
        self.notify_calls = 0
        self.night_light = bytearray(NIGHT_LIGHT_FRAME)
        # Delay of the 43 71 answer, to model a slow lamp
        self.night_light_delay = 0.0
        self.is_connected = True
        self.disconnected_callback: Callable[[Any], None] | None = None
        self._handler: Callable[[Any, bytearray], None] | None = None

    async def start_notify(
        self, uuid: str, handler: Callable[[Any, bytearray], None]
    ) -> None:
        self.notify_calls += 1
        if not self.notify:
            raise BleakError(
                "Characteristic does not have a characteristic client config descriptor"
            )
        self._handler = handler

    async def write_gatt_char(
        self, uuid: str, data: bytearray, response: bool | None = None
    ) -> None:
        if not self.is_connected:
            raise BleakError("Not connected")
        if self.fail_writes:
            self.fail_writes -= 1
            raise BleakError("[Errno 104] Connection reset by peer")
        self.writes.append(bytes(data))
        if self._handler is None:
            return
        loop = asyncio.get_running_loop()
        if data[1] == CMD_PAIR:
            for delay, code in enumerate(self.pair_responses):
                loop.call_later(
                    0.01 * (delay + 1),
                    self._handler,
                    None,
                    frame(COMMAND_STX, RES_PAIR, code),
                )
        elif data[1] == CMD_GETSTATE:
            loop.call_soon(self._handler, None, self.state)
        elif data[1] == CMD_GETVER:
            loop.call_soon(
                self._handler,
                None,
                bytearray(self.version_frame),
            )
        elif data[1] == CMD_GETNIGHTLIGHT and self.night_light is not None:
            # A copy: the answer must show the window at the time of the query
            answer = bytearray(self.night_light)
            if self.night_light_delay:
                loop.call_later(self.night_light_delay, self._handler, None, answer)
            else:
                loop.call_soon(self._handler, None, answer)
        elif data[1] == CMD_POWER and self.model == MODEL_BEDSIDE:
            # The Bedside pushes its state after switching
            if data[2] == CMD_POWER_ON and self.power_on_state is not None:
                self.state = bytearray(self.power_on_state)
                self.power_on_state = None
            self.state[2] = data[2]
            loop.call_soon(self._handler, None, bytearray(self.state))
        elif data[1] in (CMD_RGB, CMD_TEMP, CMD_BRIGHTNESS):
            if self.model == MODEL_BEDSIDE:
                self._apply(data)

    def _apply(self, data: bytearray) -> None:
        """Report what a command set, like the Bedside; brightness 0 keeps it."""
        if data[1] == CMD_RGB:
            self.state[3:7] = bytes((Lamp.MODE_COLOR, *data[2:5]))
            self.state[9:11] = b"\x00\x00"
            brightness = data[6]
        elif data[1] == CMD_TEMP:
            self.state[3:7] = bytes((Lamp.MODE_WHITE, 0, 0, 0))
            self.state[9:11] = data[2:4]
            brightness = data[4]
        else:
            brightness = data[2]
        if brightness:
            self.state[8] = brightness

    async def disconnect(self) -> None:
        self.is_connected = False

    def commands(self) -> list[int]:
        return [w[1] for w in self.writes]


class FakeConnector:
    """Replaces bleak_retry_connector.establish_connection."""

    def __init__(self, *clients: FakeLampClient) -> None:
        self.clients = list(clients)
        self.connections: list[FakeLampClient] = []

    async def __call__(
        self, client_class: Any, device: BLEDevice, name: str, **kwargs: Any
    ) -> FakeLampClient:
        client = self.clients.pop(0) if len(self.clients) > 1 else self.clients[0]
        if client in self.connections:
            client.is_connected = True
        client.disconnected_callback = kwargs.get("disconnected_callback")
        # like bleak-retry-connector, ask for the newest BLEDevice
        if (ble_device_callback := kwargs.get("ble_device_callback")) is not None:
            ble_device_callback()
        self.connections.append(client)
        return client


@pytest.fixture
def connector() -> Generator[Callable[..., FakeConnector]]:
    """Patch establish_connection with fake lamp clients."""
    patcher: Any = None

    def _install(*clients: FakeLampClient) -> FakeConnector:
        nonlocal patcher
        fake = FakeConnector(*clients)
        patcher = patch.object(yeelightbt, "establish_connection", fake)
        patcher.start()
        return fake

    yield _install
    if patcher is not None:
        patcher.stop()
