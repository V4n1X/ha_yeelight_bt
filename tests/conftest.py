"""Fixtures for the yeelight_bt tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
import struct
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
        mock_restore_cache,
    )
except ImportError:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_time_changed,
        mock_restore_cache,
    )

from custom_components.yeelight_bt import yeelightbt
from custom_components.yeelight_bt.yeelightbt import (
    CMD_GETSERIAL,
    CMD_GETSTATE,
    CMD_GETVER,
    CMD_PAIR,
    COMMAND_STX,
    PAIR_SUCCESS,
    RES_GETSERIAL,
    RES_GETSTATE,
    RES_GETVER,
    RES_PAIR,
)

BEDSIDE_ADDRESS = "F8:24:41:E6:3E:39"
CANDELA_ADDRESS = "F8:24:41:C0:0E:D2"

__all__ = ["MockConfigEntry", "async_fire_time_changed", "mock_restore_cache"]


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


class FakeLampClient:
    """Minimal stand-in for a connected BleakClient talking to a lamp."""

    def __init__(
        self,
        *,
        notify: bool = True,
        pair_responses: tuple[int, ...] = (PAIR_SUCCESS,),
        state: bytearray | None = None,
        fail_writes: int = 0,
    ) -> None:
        self.notify = notify
        self.pair_responses = pair_responses
        self.state = state or frame(
            COMMAND_STX, RES_GETSTATE, 0x01, 0x02, 0, 0, 0, 0, 80, 0x0F, 0xF0
        )
        self.fail_writes = fail_writes
        self.writes: list[bytes] = []
        self.notify_calls = 0
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
        elif data[1] == CMD_GETSERIAL:
            loop.call_soon(self._handler, None, frame(COMMAND_STX, RES_GETSERIAL, 7))
        elif data[1] == CMD_GETVER:
            loop.call_soon(
                self._handler,
                None,
                bytearray(
                    struct.pack("<BBBxHHHH6x", COMMAND_STX, RES_GETVER, 1, 2, 3, 4, 5)
                ),
            )

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
