"""Tests for the bluetooth protocol / connection layer."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bleak import BleakError
import pytest

from custom_components.yeelight_bt import yeelightbt
from custom_components.yeelight_bt.yeelightbt import (
    CMD_BRIGHTNESS,
    CMD_DELETE_AUTH,
    CMD_GETSTATE,
    CMD_GETVER,
    CMD_PAIR,
    CMD_POWER,
    CMD_RGB,
    CMD_SETTIME,
    CMD_TEMP,
    COMMAND_STX,
    MODEL_BEDSIDE,
    MODEL_CANDELA,
    MODEL_UNKNOWN,
    PAIR_ALREADY_PAIRED,
    PAIR_LAMP_OFF,
    PAIR_NOT_PAIRED,
    PAIR_REQUESTED,
    PAIR_SUCCESS,
    Lamp,
    YeelightConnectionError,
    YeelightError,
    YeelightPairingError,
    model_from_name,
)

from .conftest import (
    BEDSIDE_ADDRESS,
    BEDSIDE_VERSION_FRAME,
    CANDELA_ADDRESS,
    CANDELA_PAIRING_ID,
    CANDELA_STATE_FRAME,
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
        ("YEELIGHT_MS_1234", MODEL_CANDELA),
        ("yeelight_msdifferent", MODEL_UNKNOWN),
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
    # a later advertisement does not switch the model of a running lamp, the
    # entry is reloaded instead (see test_light)
    lamp.set_ble_device(ble_device(CANDELA_ADDRESS, "yeelight_ms"))
    assert lamp.model == MODEL_UNKNOWN


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
    assert lamp.versions == (2231, 2238, 2330)
    assert lamp.firmware_version == "2231-2238-2330"
    assert client.writes[0] == bytes.fromhex("436702000000000000000000000000000000")
    assert client.writes[-1] == bytes.fromhex("434400000000000000000000000000000000")
    assert calls


async def test_bedside_sets_clock_on_connect(connector) -> None:
    """The lamp clock gets the local time in BCD, Sunday is weekday 0."""
    client = FakeLampClient()
    connector(client)
    # 2026-11-08 is a Sunday
    lamp = Lamp(
        ble_device(BEDSIDE_ADDRESS, "XMCTD_1"),
        clock=lambda: datetime(2026, 11, 8, 23, 5, 9),
    )

    await lamp.update()

    assert client.commands()[:3] == [CMD_PAIR, CMD_SETTIME, CMD_GETVER]
    assert client.writes[1] == bytes.fromhex("436009052308001126000000000000000000")


async def test_candela_clock_is_not_set(connector) -> None:
    """The Candela clock command is not verified, so it is never sent."""
    client = FakeLampClient(model=MODEL_CANDELA)
    connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"),
        pairing_id=CANDELA_PAIRING_ID,
        clock=lambda: datetime(2026, 11, 8, 23, 5, 9),
    )

    await lamp.update()

    assert CMD_SETTIME not in client.commands()


@pytest.mark.parametrize("answer", [PAIR_REQUESTED, PAIR_LAMP_OFF])
async def test_pairing_request_is_reported(connector, answer: int) -> None:
    """A lamp that forgot our identifier is flagged until it is paired again.

    Switched off, a Bedside answers PAIR_LAMP_OFF instead of pulsing.
    """
    asking = FakeLampClient(pair_responses=(answer,))
    paired = FakeLampClient()
    connector(asking, paired)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    seen = []
    lamp.add_callback_on_state_changed(lambda: seen.append(lamp.pairing_requested))

    with pytest.raises(YeelightPairingError):
        await lamp.turn_on()
    assert lamp.pairing_requested

    await lamp.turn_on()
    assert not lamp.pairing_requested
    assert True in seen
    assert seen[-1] is False


async def test_candela_rejection_is_not_a_pairing_request(connector) -> None:
    """0x06 means "switched off" only on the Bedside."""
    client = FakeLampClient(model=MODEL_CANDELA, pair_responses=(PAIR_LAMP_OFF,))
    connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=CANDELA_PAIRING_ID
    )

    with pytest.raises(YeelightPairingError):
        await lamp.turn_on()

    assert not lamp.pairing_requested


async def test_unpair_removes_authorization(connector) -> None:
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))

    await lamp.unpair()

    # the frame the Yeelight app sends when the lamp is deleted there
    assert client.writes[-1] == bytes.fromhex("437202000000000000000000000000000000")
    assert not client.is_connected


async def test_unpair_leaves_lamp_asking_for_pairing(connector) -> None:
    """No button wait and no delete command for a lamp that is not paired."""
    client = FakeLampClient(pair_responses=(PAIR_REQUESTED,))
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))

    with pytest.raises(YeelightPairingError):
        await lamp.unpair()

    assert client.commands() == [CMD_PAIR]
    assert not client.is_connected


async def test_unpair_reports_write_failures(connector) -> None:
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    client.fail_writes = 1

    with pytest.raises(YeelightError):
        await lamp.unpair()

    assert CMD_DELETE_AUTH not in client.commands()


async def test_unpair_without_identifier_does_nothing(connector) -> None:
    fake = connector(FakeLampClient(model=MODEL_CANDELA))
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))

    await lamp.unpair()

    assert not fake.connections


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


async def test_candela_is_command_only(connector) -> None:
    """No authorization or queries when the backend cannot receive notifications."""
    client = FakeLampClient(notify=False, model=MODEL_CANDELA)
    fake = connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=CANDELA_PAIRING_ID
    )
    assert lamp.has_state_feedback is None

    # first contact only checks that the lamp can be reached
    await lamp.update()
    assert lamp.available
    assert lamp.has_state_feedback is False
    assert len(fake.connections) == 1
    assert client.writes == []
    assert client.notify_calls == 1

    # while available nothing is polled
    await lamp.update()
    assert len(fake.connections) == 1

    await lamp.turn_on()
    await lamp.set_brightness(40)
    assert client.commands() == [CMD_POWER, CMD_BRIGHTNESS]
    assert client.writes[-2] == bytes.fromhex(
        "43 40 01 01 01 f4 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert client.writes[-1] == bytes.fromhex(
        "43 42 28 01 01 f4 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert lamp.is_on
    assert lamp.brightness == 40


async def test_candela_without_notifications_never_pairs(connector) -> None:
    """An unsupported backend cannot finish authorization, even after reconnects."""
    client = FakeLampClient(notify=False, model=MODEL_CANDELA)
    connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=CANDELA_PAIRING_ID
    )
    await lamp.turn_on()
    client.disconnected_callback(client)
    await lamp.turn_off()
    assert CMD_PAIR not in client.commands()
    assert client.commands() == [CMD_POWER, CMD_POWER]
    assert client.writes[-1] == bytes.fromhex(
        "43 40 02 01 01 f4 00 00 00 00 00 00 00 00 00 00 00 00"
    )


async def test_unreachable_candela_is_probed(connector) -> None:
    client = FakeLampClient(notify=False, model=MODEL_CANDELA)
    connector(client)
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))
    lamp._set_available(False, "test")
    await lamp.update()
    assert lamp.available
    assert client.writes == []


async def test_candela_authorization_state_and_version(connector, caplog) -> None:
    """Follow APK authorization and its model-specific state/ASCII version format."""
    client = FakeLampClient(
        model=MODEL_CANDELA, pair_responses=(PAIR_REQUESTED, PAIR_SUCCESS)
    )
    connector(client)
    pairing_id = bytes.fromhex("0123456789ab")
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=pairing_id)
    await lamp.update()

    assert client.writes[0] == bytes.fromhex("43670123456789ab00000000000000000000")
    assert client.commands() == [CMD_PAIR, CMD_GETVER, CMD_GETSTATE]
    assert "turn the Candela's cylinder" in caplog.text
    assert lamp.available
    assert lamp.has_state_feedback is True
    assert lamp.is_on
    assert lamp.brightness == 80
    assert lamp.mode == Lamp.MODE_WHITE
    assert lamp.temperature == 1800
    assert lamp.firmware_version == "2.2.0"

    # A later physical adjustment must replace the locally assumed state.
    lamp.handle_notification(bytes.fromhex("434500250207080000000000000000000000"))
    assert not lamp.is_on
    assert lamp.brightness == 37

    await lamp.disconnect()
    client.pair_responses = (PAIR_SUCCESS,)
    await lamp.update()
    pairs = [packet for packet in client.writes if packet[1] == CMD_PAIR]
    assert pairs == [pairs[0], pairs[0]]


async def test_candela_rejects_bedside_already_paired_code(connector) -> None:
    client = FakeLampClient(model=MODEL_CANDELA, pair_responses=(PAIR_ALREADY_PAIRED,))
    connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=CANDELA_PAIRING_ID
    )
    with pytest.raises(YeelightPairingError):
        await lamp.turn_on()
    assert not lamp.available
    assert client.commands() == [CMD_PAIR]


async def test_subscription_alone_does_not_prove_state_feedback(connector) -> None:
    client = FakeLampClient(model=MODEL_CANDELA, state=bytearray(b"\x43\x45"))
    connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=CANDELA_PAIRING_ID
    )
    await lamp.update()
    assert lamp.available
    assert lamp.firmware_version == "2.2.0"
    assert lamp.has_state_feedback is None
    lamp.handle_notification(CANDELA_STATE_FRAME)
    assert lamp.has_state_feedback is True


async def test_backend_change_to_no_notifications(connector) -> None:
    client = FakeLampClient(model=MODEL_CANDELA)
    connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=CANDELA_PAIRING_ID
    )
    await lamp.update()
    assert lamp.has_state_feedback is True
    await lamp.disconnect()
    client.notify = False
    client.writes.clear()
    await lamp.update()
    assert lamp.has_state_feedback is False
    assert client.writes == []

    # A later capable backend must resume polling after unsupported notifications.
    await lamp.disconnect()
    client.notify = True
    await lamp.turn_on()
    assert lamp.has_state_feedback is None
    await lamp.update()
    assert lamp.has_state_feedback is True


async def test_color_and_temperature_preserve_brightness(connector) -> None:
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    await lamp.set_color(255, 0, 0)
    assert client.writes[-1] == bytes.fromhex("4341ff000000000000000000000000000000")
    assert lamp.brightness == 80
    await lamp.set_temperature(4000)
    assert client.writes[-1] == bytes.fromhex(
        "43 43 0f a0 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert lamp.brightness == 80
    # The real Bedside accepts explicit 5%, even though the app clamps to 10%.
    await lamp.set_color(255, 0, 0, brightness=5)
    assert client.writes[-1][:7] == bytes((COMMAND_STX, CMD_RGB, 255, 0, 0, 0, 5))
    await lamp.set_temperature(4000, brightness=5)
    assert client.writes[-1][:5] == bytes((COMMAND_STX, CMD_TEMP, 0x0F, 0xA0, 5))
    assert lamp.brightness == 5


def test_bedside_bcd_version_and_invalid_frames() -> None:
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    lamp.handle_notification(BEDSIDE_VERSION_FRAME)
    assert lamp.versions == (2231, 2238, 2330)
    assert lamp.firmware_version == "2231-2238-2330"
    for packet in (
        b"\x43\x5d",
        BEDSIDE_VERSION_FRAME[:10],
        b"\x99" + BEDSIDE_VERSION_FRAME[1:],
        bytes.fromhex("435d022001ab3122382330"),
    ):
        lamp.handle_notification(packet)
        assert lamp.firmware_version == "2231-2238-2330"
    lamp.handle_notification(bytes.fromhex("435d022001ffff22382330"))
    assert lamp.firmware_version == "0-2238-2330"


def test_candela_payload_offsets_and_truncated_notifications() -> None:
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))
    lamp.handle_notification(CANDELA_STATE_FRAME)
    assert lamp.brightness == 80
    assert lamp.temperature == 1800
    for packet in (
        b"\x43\x45",
        CANDELA_STATE_FRAME[:6],
        b"\x43\x45\x01\x50\x03\x00\x00\x00",
    ):
        lamp.handle_notification(packet)
        assert lamp.brightness == 80
        assert lamp.mode == Lamp.MODE_WHITE
    lamp.handle_notification(bytes.fromhex("4345010a0300000025"))
    assert lamp.mode == Lamp.MODE_FLOW
    assert lamp.brightness == 37
    lamp.handle_notification(b"\x43\x5d2.2.0\x00")
    for packet in (b"\x43\x5d", b"\x43\x5d\xff\x00", b"\x43\x5d\x01\x00"):
        lamp.handle_notification(packet)
        assert lamp.firmware_version == "2.2.0"


def test_candela_unknown_mode_keeps_power_and_brightness() -> None:
    """The APK still consumes power/brightness when the mode is unrecognized."""
    lamp = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))
    lamp.handle_notification(CANDELA_STATE_FRAME)
    lamp.handle_notification(bytes.fromhex("434502250007080000000000000000000000"))
    assert not lamp.is_on
    assert lamp.brightness == 37
    assert lamp.mode == Lamp.MODE_WHITE
    assert lamp.temperature == 1800


async def test_works_right_after_system_start(connector) -> None:
    """time.monotonic() counts from boot: a lamp must be usable 5 s after it."""
    connector(FakeLampClient())
    # replace the clock of yeelightbt only, asyncio keeps its own
    with patch.object(yeelightbt, "time", SimpleNamespace(monotonic=lambda: 5.0)):
        lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
        await lamp.turn_on()
    assert lamp.available
    assert lamp.is_on


async def test_disconnect_right_after_pairing_is_retried(connector) -> None:
    """A link dropped during connect/pair goes through the normal retry."""
    dropped = FakeLampClient()
    good = FakeLampClient()
    fake = connector(dropped, good)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    original_pair = lamp._pair

    async def pair_then_drop(client, *args):
        await original_pair(client, *args)
        if client is dropped:
            client.is_connected = False
            client.disconnected_callback(client)

    lamp._pair = pair_then_drop  # type: ignore[method-assign]
    await lamp.turn_on()

    assert fake.connections == [dropped, good]
    assert good.commands()[-1] == CMD_POWER
    assert lamp.available


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


async def test_set_commands_do_not_power_on(connector) -> None:
    """A lamp that is off ignores color/brightness/temperature (hardware test)."""
    connector(FakeLampClient())
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.turn_off()

    await lamp.set_color(255, 0, 0)
    await lamp.set_brightness(30)
    await lamp.set_temperature(4000)
    assert not lamp.is_on


async def test_os_error_is_retried(connector) -> None:
    """WinRT raises a plain OSError when the link drops (hardware test)."""
    broken = FakeLampClient()
    good = FakeLampClient()
    fake = connector(broken, good)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()

    async def closed(*args, **kwargs):
        raise OSError("[WinError -2147483629] The object has been closed")

    broken.write_gatt_char = closed  # type: ignore[method-assign]
    await lamp.turn_on()

    assert fake.connections == [broken, good]
    assert good.commands()[-1] == CMD_POWER
    assert lamp.available


async def test_failed_connection_not_retried_again() -> None:
    """establish_connection already retries, an unreachable lamp fails fast (HA test)."""
    connect = AsyncMock(side_effect=BleakError("Device not found"))
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    with patch.object(yeelightbt, "establish_connection", connect):
        with pytest.raises(YeelightConnectionError):
            await lamp.turn_on()
    assert connect.await_count == 1
    assert not lamp.available

    # a command queued right behind the failed one does not try again
    with patch.object(yeelightbt, "establish_connection", connect):
        with pytest.raises(YeelightConnectionError):
            await lamp.turn_off()
    assert connect.await_count == 1

    lamp._last_connect_failure -= yeelightbt.CONNECT_FAILURE_HOLDOFF
    with patch.object(yeelightbt, "establish_connection", connect):
        with pytest.raises(YeelightConnectionError):
            await lamp.turn_off()
    assert connect.await_count == 2


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


async def test_logs_once_when_unavailable_and_back(caplog, connector) -> None:
    """log-when-unavailable: one info line each way, not one per failed poll."""
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()

    client.fail_writes = 100
    client.is_connected = False
    for _ in range(3):
        lamp._last_connect_failure = None
        with pytest.raises(YeelightError):
            await lamp.update()
    client.fail_writes = 0
    await lamp.update()

    messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert sum("is unavailable" in m for m in messages) == 1
    assert sum("is available again" in m for m in messages) == 1
