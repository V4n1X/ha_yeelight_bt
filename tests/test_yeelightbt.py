"""Tests for the bluetooth protocol / connection layer."""

from __future__ import annotations

import time

import pytest

from custom_components.yeelight_bt.yeelightbt import (
    CMD_BRIGHTNESS,
    CMD_GETSTATE,
    CMD_GETVER,
    CMD_PAIR,
    CMD_POWER,
    COMMAND_STX,
    MODEL_BEDSIDE,
    MODEL_CANDELA,
    MODEL_UNKNOWN,
    PAIR_NOT_PAIRED,
    PAIR_REQUESTED,
    PAIR_SUCCESS,
    RES_GETSTATE,
    Lamp,
    YeelightError,
    YeelightPairingError,
    model_from_name,
)

from .conftest import (
    BEDSIDE_ADDRESS,
    CANDELA_ADDRESS,
    FakeLampClient,
    ble_device,
    frame,
)


@pytest.mark.parametrize(
    ("name", "model"),
    [
        (None, MODEL_UNKNOWN),
        ("", MODEL_UNKNOWN),
        ("Some other device", MODEL_UNKNOWN),
        ("XMCTD_123456", MODEL_BEDSIDE),
        ("yeelight_ms", MODEL_CANDELA),
        (" yeelight_ms ", MODEL_CANDELA),
    ],
)
def test_model_from_name(name: str | None, model: str) -> None:
    """Unnamed devices must not crash the model detection (#83)."""
    assert model_from_name(name) == model


def test_model_override_for_unnamed_device() -> None:
    lamp = Lamp(ble_device(CANDELA_ADDRESS, None), model=MODEL_CANDELA)
    assert lamp.model == MODEL_CANDELA
    lamp = Lamp(ble_device(CANDELA_ADDRESS, None))
    assert lamp.model == MODEL_UNKNOWN
    lamp.set_ble_device(ble_device(CANDELA_ADDRESS, "yeelight_ms"))
    assert lamp.model == MODEL_CANDELA


async def test_bedside_connect_pair_and_state(connector) -> None:
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    calls = []
    lamp.add_callback_on_state_changed(lambda: calls.append(1))

    await lamp.update()

    assert lamp.available
    assert lamp.has_state_feedback is True
    assert client.commands()[:2] == [CMD_PAIR, CMD_GETVER]
    assert client.commands()[-1] == CMD_GETSTATE
    assert lamp.is_on
    assert lamp.mode == Lamp.MODE_WHITE
    assert lamp.brightness == 80
    assert lamp.temperature == 4080
    assert lamp.versions == (1, 2, 3, 4, 5)
    assert calls


async def test_pairing_waits_for_button(connector) -> None:
    client = FakeLampClient(pair_responses=(PAIR_REQUESTED, PAIR_SUCCESS))
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))

    await lamp.turn_on()

    assert lamp.available
    assert client.writes[-1][:3] == bytes((COMMAND_STX, CMD_POWER, 0x01))


@pytest.mark.parametrize(
    "responses", [(), (PAIR_NOT_PAIRED,), (PAIR_REQUESTED,), (0x07,)]
)
async def test_pairing_failures(connector, responses: tuple[int, ...]) -> None:
    """A lamp that does not pair is unavailable instead of hanging forever."""
    client = FakeLampClient(pair_responses=responses)
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))

    with pytest.raises(YeelightPairingError):
        await lamp.turn_on()

    assert not lamp.available
    assert client.commands() == [CMD_PAIR]


async def test_candela_without_notifications(connector) -> None:
    """Candela behind an ESPHome proxy has no CCCD: assume paired, no polling."""
    client = FakeLampClient(notify=False)
    connector(client)
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))

    await lamp.update()
    assert lamp.available
    assert lamp.has_state_feedback is False
    assert client.commands() == [CMD_PAIR, CMD_GETSTATE]

    await lamp.update()
    assert client.commands() == [CMD_PAIR, CMD_GETSTATE]

    await lamp.set_brightness(40)
    assert lamp.is_on
    assert lamp.brightness == 40
    assert client.writes[-1][:3] == bytes((COMMAND_STX, CMD_BRIGHTNESS, 40))


async def test_candela_without_pair_answer(connector) -> None:
    """Candela with notifications but without pairing answer is assumed paired."""
    client = FakeLampClient(pair_responses=())
    connector(client)
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))

    await lamp.turn_off()

    assert lamp.available
    assert not lamp.is_on


async def test_candela_ignores_stale_state(connector) -> None:
    client = FakeLampClient(notify=False)
    connector(client)
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))
    await lamp.turn_on()

    lamp.handle_notification(bytes(frame(COMMAND_STX, RES_GETSTATE, 0x02, 50)))
    assert lamp.is_on

    lamp._last_command = time.monotonic() - 10
    lamp.handle_notification(bytes(frame(COMMAND_STX, RES_GETSTATE, 0x02, 50)))
    assert not lamp.is_on
    assert lamp.brightness == 50


async def test_write_retried_on_new_connection(connector) -> None:
    """Transient proxy errors (#82, #56) are retried once on a new connection."""
    broken = FakeLampClient(fail_writes=1)
    good = FakeLampClient()
    fake = connector(broken, good)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))

    await lamp.turn_off()

    assert fake.connections == [broken, good]
    assert not broken.is_connected
    assert good.commands()[-1] == CMD_POWER
    assert lamp.available


async def test_write_failing_twice_raises(connector) -> None:
    client = FakeLampClient(fail_writes=10)
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))

    with pytest.raises(YeelightError):
        await lamp.turn_on()
    assert not lamp.available


async def test_stale_disconnect_callback_ignored(connector) -> None:
    old = FakeLampClient()
    new = FakeLampClient()
    connector(old, new)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    await lamp.disconnect()
    await lamp.update()
    assert lamp.is_connected

    lamp._disconnected_cb(old)  # type: ignore[arg-type]
    assert lamp.is_connected

    lamp._disconnected_cb(new)  # type: ignore[arg-type]
    assert not lamp.is_connected
    # a dropped connection alone does not make the lamp unavailable
    assert lamp.available


def test_short_or_unknown_notifications_ignored() -> None:
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    lamp.handle_notification(b"\x43\x45")
    lamp.handle_notification(bytes(frame(COMMAND_STX, 0x99)))
    assert not lamp.is_on
