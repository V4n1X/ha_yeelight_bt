"""Tests for the bluetooth protocol / connection layer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time as dt_time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bleak import BleakError
import pytest

from custom_components.yeelight_bt import yeelightbt
from custom_components.yeelight_bt.yeelightbt import (
    CMD_BRIGHTNESS,
    CMD_DELETE_AUTH,
    CMD_GETNIGHTLIGHT,
    CMD_GETSTATE,
    CMD_GETVER,
    CMD_PAIR,
    CMD_POWER,
    CMD_RGB,
    CMD_SETNIGHTLIGHT,
    CMD_SETTIME,
    CMD_TEMP,
    COMMAND_STX,
    MODEL_BEDSIDE,
    MODEL_CANDELA,
    MODEL_UNKNOWN,
    NIGHT_LIGHT_COLOR,
    PAIR_ALREADY_PAIRED,
    PAIR_LAMP_OFF,
    PAIR_NOT_PAIRED,
    PAIR_REQUESTED,
    PAIR_SUCCESS,
    Lamp,
    LightState,
    NightLight,
    YeelightConnectionError,
    YeelightError,
    YeelightPairingError,
    model_from_name,
)

from .conftest import (
    AFTER_NIGHT_LIGHT_FRAME,
    BEDSIDE_ADDRESS,
    BEDSIDE_VERSION_FRAME,
    CANDELA_ADDRESS,
    CANDELA_PAIRING_ID,
    CANDELA_STATE_FRAME,
    GREEN_100,
    NIGHT_LIGHT_FRAME,
    NIGHT_LIGHT_OFF_FRAME,
    NIGHT_LIGHT_ON_FRAME,
    NIGHT_LIGHT_WINDOW_FRAME,
    WHITE_60,
    WHITE_60_OFF_FRAME,
    FakeLampClient,
    ble_device,
    frame,
    state_frame,
)

# The night light switched off while the lamp is off (43 71 of the window)
NIGHT_LIGHT_DISABLED_FRAME = bytes.fromhex("437100012200090000000000000000000000")


async def _night_light_lamp(
    connector, now: list[datetime], before: bytes = WHITE_60_OFF_FRAME
) -> tuple[Lamp, FakeLampClient]:
    """A connected Bedside, off, night light on 22:00-09:00, clock from now[0]."""
    client = FakeLampClient()
    client.night_light = bytearray(NIGHT_LIGHT_WINDOW_FRAME)
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"), clock=lambda: now[0])
    await lamp.send_cmd(None)
    # the night-light answer
    await asyncio.sleep(0)
    lamp.handle_notification(before)
    assert lamp.night_light == NightLight(True, dt_time(22, 0), dt_time(9, 0))
    return lamp, client


async def _back_from_night_light(
    connector,
    before: bytes = WHITE_60_OFF_FRAME,
    after: bytes = AFTER_NIGHT_LIGHT_FRAME,
) -> tuple[Lamp, FakeLampClient]:
    """Night light at 01:18, off, switched on again at 09:30 (after the window)."""
    now = [datetime(2026, 11, 5, 1, 18)]
    lamp, client = await _night_light_lamp(connector, now, before)
    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)
    lamp.handle_notification(NIGHT_LIGHT_OFF_FRAME)
    now[0] = datetime(2026, 11, 5, 9, 30)
    lamp.handle_notification(after)
    return lamp, client


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

    assert client.commands()[:4] == [
        CMD_PAIR,
        CMD_SETTIME,
        CMD_GETVER,
        CMD_GETNIGHTLIGHT,
    ]
    assert client.writes[1] == bytes.fromhex("436009052308001126000000000000000000")
    # night light: read after the clock and firmware, the lamp answers 43 71
    assert client.writes[3] == bytes.fromhex("437000000000000000000000000000000000")
    assert lamp.night_light == NightLight(False, dt_time(0, 0), dt_time(5, 0))


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


async def test_candela_does_not_read_night_light(connector) -> None:
    """The legacy Candela path has no night-light window."""
    client = FakeLampClient(model=MODEL_CANDELA)
    connector(client)
    lamp = Lamp(
        ble_device(CANDELA_ADDRESS, "yeelight_ms"), pairing_id=CANDELA_PAIRING_ID
    )

    await lamp.update()

    assert CMD_GETNIGHTLIGHT not in client.commands()
    assert lamp.night_light is None


async def test_night_light_not_read_without_notifications(connector) -> None:
    """Without notifications the lamp answer would never arrive."""
    client = FakeLampClient(notify=False)
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))

    await lamp.update()

    assert CMD_GETNIGHTLIGHT not in client.commands()
    assert lamp.night_light is None


async def test_night_light_is_read_again_on_reconnect(connector) -> None:
    """The lamp may have been changed with the Yeelight app meanwhile."""
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    client.night_light = bytearray(
        bytes((COMMAND_STX, 0x71, 0x01, 0x01, 0x21, 0x00, 0x07, 0x00))
    )

    client.disconnected_callback(client)
    await lamp.update()

    assert client.commands().count(CMD_GETNIGHTLIGHT) == 2
    assert lamp.night_light == NightLight(True, dt_time(21, 0), dt_time(7, 0))


def test_night_light_notification_parsing() -> None:
    """43 71 has the same layout as the 43 6F command, times are BCD."""
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    lamp.handle_notification(NIGHT_LIGHT_FRAME)
    assert lamp.night_light == NightLight(False, dt_time(0, 0), dt_time(5, 0))

    lamp.handle_notification(
        bytes((COMMAND_STX, 0x71, 0x01, 0x05, 0x21, 0x30, 0x07, 0x00))
    )
    # the brightness byte (1 %) is not part of the state
    assert lamp.night_light == NightLight(True, dt_time(21, 30), dt_time(7, 0))


def test_invalid_night_light_notifications_are_ignored(caplog) -> None:
    """Truncated frames and times outside BCD must not overwrite the state."""
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    lamp.handle_notification(NIGHT_LIGHT_FRAME)
    for packet in (
        bytes.fromhex("4371000100000500")[:7],
        bytes.fromhex("43710001"),
        # 0x24 is not a BCD digit, 0x61 is not a valid hour
        bytes((COMMAND_STX, 0x71, 0x00, 0x01, 0x24, 0x00, 0x05, 0x00)),
        bytes((COMMAND_STX, 0x71, 0x00, 0x01, 0x00, 0x00, 0x61, 0x00)),
    ):
        lamp.handle_notification(packet)
        assert lamp.night_light == NightLight(False, dt_time(0, 0), dt_time(5, 0))
    assert "invalid night light response" in caplog.text


async def test_set_night_light_sends_all_fields(connector) -> None:
    """The lamp only acknowledges (43 82), the state is updated locally."""
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    calls = []
    lamp.add_callback_on_state_changed(lambda: calls.append(lamp.night_light))

    await lamp.set_night_light(True, dt_time(21, 0), dt_time(7, 0))

    assert client.writes[-1] == bytes.fromhex(
        "43 6f 01 01 21 00 07 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert len(client.writes[-1]) == 18
    assert lamp.night_light == NightLight(True, dt_time(21, 0), dt_time(7, 0))
    assert calls[-1] == lamp.night_light


async def test_set_night_light_keeps_unchanged_fields(connector) -> None:
    """A single field change must not overwrite the times read from the lamp."""
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    lamp.handle_notification(
        bytes((COMMAND_STX, 0x71, 0x01, 0x01, 0x22, 0x15, 0x06, 0x45))
    )

    await lamp.set_night_light(start=dt_time(23, 59))

    assert client.writes[-1][:8] == bytes.fromhex("43 6f 01 01 23 59 06 45")
    assert lamp.night_light == NightLight(True, dt_time(23, 59), dt_time(6, 45))

    client.writes.clear()
    await lamp.set_night_light(enabled=False)
    assert client.writes[-1][:8] == bytes.fromhex("43 6f 00 01 23 59 06 45")
    assert lamp.night_light == NightLight(False, dt_time(23, 59), dt_time(6, 45))


async def test_set_night_light_requires_read_state(connector) -> None:
    """Unknown times must never be sent, they would overwrite the lamp's."""
    fake = connector(FakeLampClient(notify=False))
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()

    with pytest.raises(YeelightError):
        await lamp.set_night_light(True, dt_time(21, 0), dt_time(7, 0))
    # only the connect and pairing, the window is never written
    assert CMD_SETNIGHTLIGHT not in [
        write[1] for client in fake.connections for write in client.writes
    ]


async def test_cached_window_is_not_written_after_losing_notifications(
    connector,
) -> None:
    """A reconnect through a backend without notifications cannot refresh it."""
    client = FakeLampClient()
    fake = connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    assert lamp.night_light is not None
    await lamp.disconnect()
    client.notify = False

    with pytest.raises(YeelightError):
        await lamp.set_night_light(True)
    # the fake still answers the pairing request through the old handler
    await asyncio.sleep(0.1)
    assert CMD_SETNIGHTLIGHT not in [
        write[1] for c in fake.connections for write in c.writes
    ]


async def test_concurrent_night_light_updates_are_merged(connector) -> None:
    """Two time entities changed together keep both values."""
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()

    # A write that yields to the loop, otherwise the first task would finish
    # before the second one starts and nothing would be merged.
    original_write = client.write_gatt_char

    async def slow_write(uuid, data, response=None):
        await asyncio.sleep(0)
        await original_write(uuid, data, response)

    client.write_gatt_char = slow_write  # type: ignore[method-assign]

    await asyncio.gather(
        lamp.set_night_light(start=dt_time(22, 15)),
        lamp.set_night_light(end=dt_time(6, 30)),
    )

    assert lamp.night_light == NightLight(False, dt_time(22, 15), dt_time(6, 30))
    assert client.writes[-1][:8] == bytes.fromhex("43 6f 00 01 22 15 06 30")


async def test_late_readback_does_not_undo_a_write(connector) -> None:
    """A 43 71 answer to the query of a new connection arrives after the write.

    It describes the window from before the write. Overwriting the local state
    with it would make the next change send the old window to the lamp.
    """
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()

    # The app changed the window, then the connection dropped before HA read it
    client.night_light = bytearray(
        bytes((COMMAND_STX, 0x71, 0x01, 0x01, 0x17, 0x00, 0x06, 0x45))
    )
    client.night_light_delay = 0.05
    client.disconnected_callback(client)

    await lamp.set_night_light(start=dt_time(21, 0))

    # the write used the window the lamp reports (17:00-06:45), not the cache
    assert client.writes[-1][:8] == bytes.fromhex("43 6f 01 01 21 00 06 45")
    assert lamp.night_light == NightLight(True, dt_time(21, 0), dt_time(6, 45))

    # the delayed answer of the connection's query must not replace that
    await asyncio.sleep(0.2)
    assert lamp.night_light == NightLight(True, dt_time(21, 0), dt_time(6, 45))


async def test_stale_readback_after_a_write_is_ignored(connector, caplog) -> None:
    """An answer to a query sent before a write must not undo that write.

    Reached when the lamp answers slower than NIGHT_LIGHT_READ_TIMEOUT: the
    write goes out first, the late answer still describes the old window.
    """
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()

    # The app changed the window and the lamp answers slowly
    client.night_light = bytearray(
        bytes((COMMAND_STX, 0x71, 0x01, 0x01, 0x17, 0x00, 0x06, 0x45))
    )
    client.night_light_delay = 0.05
    client.disconnected_callback(client)
    with patch.object(yeelightbt, "NIGHT_LIGHT_READ_TIMEOUT", 0):
        await lamp.set_night_light(start=dt_time(21, 0))
    written = lamp.night_light
    # merged from the cache: the connection's answer had not arrived yet
    assert written == NightLight(False, dt_time(21, 0), dt_time(5, 0))

    # The answer of the connection's query arrives afterwards
    await asyncio.sleep(0.2)
    assert lamp.night_light == written
    assert "ignoring stale night light response" in caplog.text


async def test_a_dropped_link_forgets_pending_queries(connector) -> None:
    """A query lost with its link must not shift every later answer.

    Only the lamp or proxy disconnects here, so _disconnect_client never runs.
    A leftover query would make the next fresh answer look stale, keep the old
    window in Home Assistant and make every later write use the stale cache.
    """
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()

    # A query goes out, its answer never arrives, then the lamp drops the link
    client._handler = None  # type: ignore[assignment]
    await lamp._query_night_light(client)  # type: ignore[arg-type]
    client.disconnected_callback(client)
    assert lamp._night_light_pending == []
    client._handler = lamp._notification_handler  # type: ignore[assignment]

    await lamp.set_night_light(enabled=True)

    # The window is changed in the app, the next connection reads it
    client.night_light = bytearray(
        bytes((COMMAND_STX, 0x71, 0x01, 0x01, 0x21, 0x00, 0x07, 0x00))
    )
    client.disconnected_callback(client)
    await lamp.update()
    await asyncio.sleep(0.1)

    assert lamp.night_light == NightLight(True, dt_time(21, 0), dt_time(7, 0))
    assert lamp._night_light_pending == []


async def test_a_readback_after_the_write_is_accepted(connector) -> None:
    """A query answered after the write wins: it is the lamp's current state."""
    client = FakeLampClient()
    connector(client)
    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    await lamp.update()
    await lamp.set_night_light(start=dt_time(21, 0))

    client.night_light = bytearray(
        bytes((COMMAND_STX, 0x71, 0x01, 0x01, 0x17, 0x00, 0x06, 0x45))
    )
    await lamp.send_cmd(lamp._night_light_query())
    await asyncio.sleep(0.05)

    assert lamp.night_light == NightLight(True, dt_time(17, 0), dt_time(6, 45))


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


async def test_power_on_after_night_light_shows_the_lamp_state(connector) -> None:
    """The night light leaves its brightness in the lamp's report (hardware test).

    Switched on inside the window the lamp shows and reports orange at 1 %.
    With the night light switched off it comes up with its state from before
    again, but keeps reporting 1 % until a command sets the brightness.
    """
    now = [datetime(2026, 11, 5, 1, 18)]
    lamp, client = await _night_light_lamp(connector, now)

    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)
    assert (lamp.mode, lamp.color, lamp.brightness) == (
        Lamp.MODE_COLOR,
        NIGHT_LIGHT_COLOR,
        1,
    )
    assert lamp.power_on_state == WHITE_60
    assert not lamp.resync_pending

    lamp.handle_notification(NIGHT_LIGHT_OFF_FRAME)
    lamp.handle_notification(NIGHT_LIGHT_DISABLED_FRAME)
    lamp.handle_notification(AFTER_NIGHT_LIGHT_FRAME)
    assert lamp.is_on
    assert lamp.resync_pending
    assert (lamp.mode, lamp.temperature, lamp.brightness) == (Lamp.MODE_WHITE, 4000, 60)

    # polls get the leftovers as well
    lamp.handle_notification(AFTER_NIGHT_LIGHT_FRAME)
    assert lamp.brightness == 60

    await lamp.async_resync()
    assert client.writes[-1] == bytes.fromhex("43430fa03c").ljust(18, b"\x00")
    assert not lamp.resync_pending
    assert lamp.power_on_state is None
    assert lamp.brightness == 60


@pytest.mark.parametrize(
    ("before", "after", "written"),
    [
        # in color mode the leftovers are the night light's report itself
        (GREEN_100, NIGHT_LIGHT_ON_FRAME, "434100ff000064"),
        # a flow keeps running, it only gets its brightness back
        (
            LightState(Lamp.MODE_FLOW, (2, 0, 0), 0, 50),
            state_frame(True, LightState(Lamp.MODE_FLOW, NIGHT_LIGHT_COLOR, 0, 1)),
            "434232",
        ),
    ],
)
async def test_power_on_after_the_window(
    connector, before: LightState, after: bytes, written: str
) -> None:
    """With the night light still on, the window decides: it ended at 09:00."""
    lamp, client = await _back_from_night_light(
        connector, state_frame(False, before), after
    )

    assert lamp.resync_pending
    assert (lamp.mode, lamp.color, lamp.brightness) == (
        before.mode,
        before.color,
        before.brightness,
    )
    await lamp.async_resync()
    assert client.writes[-1] == bytes.fromhex(written).ljust(18, b"\x00")


async def test_power_on_inside_the_window_again(connector) -> None:
    """A second night-light power-on keeps the state from before the first."""
    now = [datetime(2026, 11, 5, 1, 18)]
    lamp, _ = await _night_light_lamp(connector, now)
    for _ in range(2):
        lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)
        lamp.handle_notification(NIGHT_LIGHT_OFF_FRAME)
        now[0] = datetime(2026, 11, 5, 8, 59)

    assert lamp.power_on_state == WHITE_60
    assert not lamp.resync_pending
    assert lamp.brightness == 1


Command = Callable[[Lamp], Awaitable[None]]


@pytest.mark.parametrize(
    ("command", "written"),
    [
        # keeping the brightness would apply the night light's 1 % (hardware test)
        (lambda lamp: lamp.set_temperature(2700), "43430a8c3c"),
        (lambda lamp: lamp.set_color(255, 0, 0), "4341ff0000003c"),
        # a brightness alone would leave the night-light color in the lamp
        (lambda lamp: lamp.set_brightness(30), "43430fa01e"),
        (lambda lamp: lamp.set_temperature(3000, brightness=20), "43430bb814"),
    ],
)
async def test_commands_after_night_light_set_all_fields(
    connector, command: Command, written: str
) -> None:
    lamp, client = await _back_from_night_light(connector)
    assert lamp.resync_pending

    await command(lamp)

    assert client.writes[-1] == bytes.fromhex(written).ljust(18, b"\x00")
    assert not lamp.resync_pending
    assert lamp.power_on_state is None
    # nothing is left to write back
    writes = len(client.writes)
    await lamp.async_resync()
    assert len(client.writes) == writes


async def test_changes_inside_the_night_light_are_not_kept(connector) -> None:
    """The lamp stores nothing set while the night light is on (hardware test)."""
    now = [datetime(2026, 11, 5, 1, 18)]
    lamp, _ = await _night_light_lamp(connector, now)
    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)

    await lamp.set_brightness(30)
    await lamp.set_color(0, 255, 0, brightness=100)
    assert (lamp.color, lamp.brightness) == ((0, 255, 0), 100)
    assert lamp.power_on_state == WHITE_60

    lamp.handle_notification(state_frame(False, GREEN_100))
    now[0] = datetime(2026, 11, 5, 9, 30)
    # captured: the mode comes back, the brightness set inside the night light stays
    lamp.handle_notification(bytes.fromhex("4345010200000000640fa022000000000000"))
    assert lamp.resync_pending
    assert (lamp.mode, lamp.temperature, lamp.brightness) == (Lamp.MODE_WHITE, 4000, 60)


async def test_unknown_state_before_the_night_light(connector) -> None:
    """Leftovers of an earlier night light are no state to write back."""
    lamp, client = await _back_from_night_light(connector, before=NIGHT_LIGHT_OFF_FRAME)

    assert lamp.power_on_state is None
    assert not lamp.resync_pending
    # the report is all that is known
    assert (lamp.mode, lamp.temperature, lamp.brightness) == (Lamp.MODE_WHITE, 4000, 1)
    writes = len(client.writes)
    await lamp.async_resync()
    assert len(client.writes) == writes


async def test_brightness_changed_on_the_lamp_before_the_write_back(connector) -> None:
    """The lamp's slider changes only the brightness of the report."""
    lamp, client = await _back_from_night_light(connector)

    lamp.handle_notification(
        state_frame(True, LightState(Lamp.MODE_WHITE, (0, 0, 0), 4000, 80))
    )

    assert lamp.resync_pending
    assert lamp.brightness == 80
    await lamp.async_resync()
    assert client.writes[-1] == bytes.fromhex("43430fa050").ljust(18, b"\x00")


async def test_other_change_on_the_lamp_before_the_write_back(connector) -> None:
    """Any other change: the lamp no longer shows the kept state."""
    lamp, client = await _back_from_night_light(connector)
    red = LightState(Lamp.MODE_COLOR, (255, 0, 0), 0, 1)

    lamp.handle_notification(state_frame(True, red))

    assert not lamp.resync_pending
    assert lamp.power_on_state is None
    assert (lamp.mode, lamp.color, lamp.brightness) == (Lamp.MODE_COLOR, (255, 0, 0), 1)
    writes = len(client.writes)
    await lamp.async_resync()
    assert len(client.writes) == writes


async def test_switched_off_before_the_write_back(connector) -> None:
    """The lamp keeps its leftovers, the next power-on writes back instead."""
    lamp, client = await _back_from_night_light(connector)

    lamp.handle_notification(bytes.fromhex("4345020200000000010fa022000000000000"))
    assert not lamp.resync_pending
    assert lamp.power_on_state == WHITE_60
    writes = len(client.writes)
    await lamp.async_resync()
    assert len(client.writes) == writes

    lamp.handle_notification(AFTER_NIGHT_LIGHT_FRAME)
    assert lamp.resync_pending
    assert lamp.brightness == 60


async def test_power_on_command_and_its_report(connector) -> None:
    """Home Assistant's own power-on: its report decides, then commands follow."""
    now = [datetime(2026, 11, 5, 1, 18)]
    lamp, client = await _night_light_lamp(connector, now)

    client.power_on_state = NIGHT_LIGHT_ON_FRAME
    await lamp.turn_on()
    await lamp.async_wait_power_on()
    assert lamp.power_on_state == WHITE_60
    assert lamp.brightness == 1

    await lamp.turn_off()
    now[0] = datetime(2026, 11, 5, 9, 30)
    client.power_on_state = AFTER_NIGHT_LIGHT_FRAME
    await lamp.turn_on()
    await lamp.async_wait_power_on()
    assert lamp.resync_pending
    assert lamp.brightness == 60

    await lamp.turn_off()
    assert not lamp.resync_pending
    assert lamp.power_on_state == WHITE_60


async def test_late_power_on_report_is_not_a_power_on(connector) -> None:
    """A report arriving after the following commands no longer decides."""
    now = [datetime(2026, 11, 5, 1, 18)]
    lamp, client = await _night_light_lamp(connector, now)
    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)
    lamp.handle_notification(NIGHT_LIGHT_OFF_FRAME)
    now[0] = datetime(2026, 11, 5, 9, 30)

    handler = client._handler
    client._handler = None
    with patch.object(yeelightbt, "POWER_ON_REPORT_TIMEOUT", 0.01):
        await lamp.turn_on()
        await lamp.async_wait_power_on()
    assert handler is not None
    handler(None, bytearray(AFTER_NIGHT_LIGHT_FRAME))

    assert not lamp.resync_pending
    assert lamp.brightness == 1
    assert lamp.power_on_state == WHITE_60


async def test_failed_power_on_command_expects_no_report(connector) -> None:
    lamp, client = await _night_light_lamp(connector, [datetime(2026, 11, 5, 9, 30)])
    client.fail_writes = 10

    with pytest.raises(YeelightError):
        await lamp.turn_on()

    assert not lamp._power_on_expected


async def test_report_after_reconnecting_is_not_a_power_on(connector) -> None:
    """The lamp may have been switched while the link was down."""
    lamp, client = await _back_from_night_light(connector, after=NIGHT_LIGHT_OFF_FRAME)
    assert lamp.power_on_state == WHITE_60

    client.disconnected_callback(client)
    lamp.handle_notification(AFTER_NIGHT_LIGHT_FRAME)

    assert not lamp.resync_pending
    assert lamp.brightness == 1


@pytest.mark.parametrize(
    ("window", "now", "night_light"),
    [
        ("437101012200090000000000000000000000", dt_time(21, 59), False),
        ("437101012200090000000000000000000000", dt_time(22, 0), True),
        ("437101012200090000000000000000000000", dt_time(1, 0), True),
        ("437101012200090000000000000000000000", dt_time(8, 59), True),
        ("437101012200090000000000000000000000", dt_time(9, 0), False),
        ("437101010000050000000000000000000000", dt_time(0, 0), True),
        ("437101010000050000000000000000000000", dt_time(5, 0), False),
        ("437101010000050000000000000000000000", dt_time(23, 0), False),
        # equal times: the whole day, a write-back could override the night light
        ("437101011200120000000000000000000000", dt_time(3, 0), True),
        ("437100012200090000000000000000000000", dt_time(1, 0), False),
        # window not read yet
        (None, dt_time(12, 0), True),
    ],
)
def test_night_light_power_on_follows_the_window(
    window: str | None, now: dt_time, night_light: bool
) -> None:
    lamp = Lamp(
        ble_device(BEDSIDE_ADDRESS, "XMCTD_1"),
        clock=lambda: datetime.combine(date(2026, 11, 5), now),
    )
    if window is not None:
        lamp.handle_notification(bytes.fromhex(window))

    on = LightState(Lamp.MODE_COLOR, NIGHT_LIGHT_COLOR, 0, 1)
    assert lamp._night_light_power_on(on) is night_light
    # any other report is a normal power-on
    assert not lamp._night_light_power_on(WHITE_60)
    assert not lamp._night_light_power_on(GREEN_100)


def test_power_on_state_storage_and_restore() -> None:
    assert LightState.from_dict(GREEN_100.as_dict()) == GREEN_100
    for data in (
        None,
        {},
        {**GREEN_100.as_dict(), "color": [0, 255]},
        {**GREEN_100.as_dict(), "brightness": "high"},
    ):
        assert LightState.from_dict(data) is None

    lamp = Lamp(ble_device(BEDSIDE_ADDRESS, "XMCTD_1"))
    for unusable in (
        None,
        LightState(Lamp.MODE_COLOR, NIGHT_LIGHT_COLOR, 0, 100),
        LightState(Lamp.MODE_COLOR, (0, 0, 0), 0, 100),
        LightState(Lamp.MODE_WHITE, (0, 0, 0), 4000, 1),
        LightState(Lamp.MODE_WHITE, (0, 0, 0), 0, 60),
        LightState(0, (0, 0, 0), 0, 60),
    ):
        lamp.restore_power_on_state(unusable)
        assert lamp.power_on_state is None
    lamp.restore_power_on_state(GREEN_100)
    assert lamp.power_on_state == GREEN_100
    # one known in this run wins
    lamp.restore_power_on_state(WHITE_60)
    assert lamp.power_on_state == GREEN_100
    # without a clock the night-light color is taken for the night light
    lamp.handle_notification(NIGHT_LIGHT_WINDOW_FRAME)
    assert lamp._night_light_power_on(
        LightState(Lamp.MODE_COLOR, NIGHT_LIGHT_COLOR, 0, 1)
    )

    candela = Lamp(ble_device(CANDELA_ADDRESS, "yeelight_ms"))
    candela.restore_power_on_state(GREEN_100)
    assert candela.power_on_state is None
