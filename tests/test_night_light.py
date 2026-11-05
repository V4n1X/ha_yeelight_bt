"""Tests for the Bedside night-light switch and times."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.time import DOMAIN as TIME_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_MAC,
    CONF_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest

from custom_components.yeelight_bt.const import CONF_MODEL, CONF_PAIRING_ID, DOMAIN
from custom_components.yeelight_bt.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.yeelight_bt.yeelightbt import (
    CMD_POWER,
    CMD_RGB,
    CMD_SETNIGHTLIGHT,
    COMMAND_STX,
    MODEL_CANDELA,
    LightState,
)

from .conftest import (
    AFTER_NIGHT_LIGHT_FRAME,
    BEDSIDE_ADDRESS,
    CANDELA_ADDRESS,
    CANDELA_PAIRING_ID,
    GREEN_100,
    NIGHT_LIGHT_OFF_FRAME,
    NIGHT_LIGHT_ON_FRAME,
    NIGHT_LIGHT_WINDOW_FRAME,
    WHITE_60,
    FakeLampClient,
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_restore_state_shutdown_restart,
    ble_device,
    mock_restore_cache_with_extra_data,
    state_frame,
)

SWITCH_ENTITY_ID = "switch.lamp_night_light"
START_ENTITY_ID = "time.lamp_night_light_start"
END_ENTITY_ID = "time.lamp_night_light_end"
LIGHT_ENTITY_ID = "light.lamp"


@pytest.fixture(autouse=True)
def bluetooth_ready(enable_bluetooth: None) -> None:
    """Set up the HA bluetooth stack (mocked adapters)."""


@pytest.fixture
def lamp_visible() -> Generator[None]:
    """Make HA bluetooth know both lamps (as if advertising)."""
    devices = {
        BEDSIDE_ADDRESS: ble_device(BEDSIDE_ADDRESS, "XMCTD_1"),
        CANDELA_ADDRESS: ble_device(CANDELA_ADDRESS, None),
    }
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            side_effect=lambda hass, address, connectable=True: devices.get(address),
        ),
        patch(
            "homeassistant.components.bluetooth.async_address_present",
            return_value=True,
        ),
    ):
        yield


async def _setup(
    hass: HomeAssistant,
    address: str = BEDSIDE_ADDRESS,
    model: str = "Bedside",
    pairing_id: str | None = None,
    **entry_kwargs: Any,
) -> MockConfigEntry:
    data = {CONF_NAME: "Lamp", CONF_MAC: address, CONF_MODEL: model}
    if pairing_id is not None:
        data[CONF_PAIRING_ID] = pairing_id
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=address.lower(),
        title="Lamp",
        data=data,
        version=2,
        **entry_kwargs,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _night_light_writes(client: FakeLampClient) -> list[bytes]:
    return [write for write in client.writes if write[1] == CMD_SETNIGHTLIGHT]


async def _setup_with_night_light(
    hass: HomeAssistant, connector, state: LightState
) -> tuple[MockConfigEntry, FakeLampClient, list[datetime]]:
    """The lamp on with the given state, night light on 22:00-09:00.

    The lamp's clock (it decides about the window) starts at 01:18 and reads
    the returned list, a frozen loop clock would stop the fake lamp's answers.
    """
    client = FakeLampClient(state=bytearray(state_frame(True, state)))
    client.night_light = bytearray(NIGHT_LIGHT_WINDOW_FRAME)
    connector(client)
    entry = await _setup(hass)
    now = [datetime(2026, 11, 5, 1, 18)]
    entry.runtime_data._clock = lambda: now[0]
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_ON
    return entry, client, now


async def _light(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(
        LIGHT_DOMAIN,
        service,
        {ATTR_ENTITY_ID: LIGHT_ENTITY_ID, **data},
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_entities_and_metadata(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    entity_registry: er.EntityRegistry,
) -> None:
    """Both times and the switch are configuration entities of the Bedside."""
    connector(FakeLampClient())
    await _setup(hass)

    switch = entity_registry.async_get(SWITCH_ENTITY_ID)
    start = entity_registry.async_get(START_ENTITY_ID)
    end = entity_registry.async_get(END_ENTITY_ID)
    assert switch is not None and start is not None and end is not None
    for registry_entry in (switch, start, end):
        assert registry_entry.entity_category is er.EntityCategory.CONFIG
        assert registry_entry.unique_id.startswith(f"{BEDSIDE_ADDRESS}_night_light")

    # the factory state of the test lamp: off, 00:00-05:00
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_OFF
    assert hass.states.get(START_ENTITY_ID).state == "00:00:00"
    assert hass.states.get(END_ENTITY_ID).state == "05:00:00"


async def test_switch_sets_window(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """Turning the switch writes all fields, the lamp only acknowledges."""
    client = FakeLampClient()
    connector(client)
    await _setup(hass)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: SWITCH_ENTITY_ID},
        blocking=True,
    )
    assert _night_light_writes(client)[-1][:8] == bytes.fromhex(
        "43 6f 01 01 00 00 05 00"
    )
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: SWITCH_ENTITY_ID},
        blocking=True,
    )
    assert _night_light_writes(client)[-1][:8] == bytes.fromhex(
        "43 6f 00 01 00 00 05 00"
    )
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_OFF


@pytest.mark.parametrize(
    ("entity_id", "value", "expected"),
    [
        (START_ENTITY_ID, "21:00:00", "43 6f 00 01 21 00 05 00"),
        (END_ENTITY_ID, "07:30:00", "43 6f 00 01 00 00 07 30"),
    ],
)
async def test_time_sets_one_field(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    entity_id: str,
    value: str,
    expected: str,
) -> None:
    """Changing one time keeps the enabled flag and the other time."""
    client = FakeLampClient()
    connector(client)
    await _setup(hass)

    await hass.services.async_call(
        TIME_DOMAIN,
        "set_value",
        {ATTR_ENTITY_ID: entity_id, "time": value},
        blocking=True,
    )

    assert _night_light_writes(client)[-1][:8] == bytes.fromhex(expected)
    assert hass.states.get(entity_id).state == value


async def test_state_follows_notification(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """A window changed in the Yeelight app is shown after the lamp reports it."""
    connector(FakeLampClient())
    entry = await _setup(hass)
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_OFF

    entry.runtime_data.handle_notification(
        bytes.fromhex("437101052100070000000000000000000000")
    )
    await hass.async_block_till_done()

    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_ON
    assert hass.states.get(START_ENTITY_ID).state == "21:00:00"
    assert hass.states.get(END_ENTITY_ID).state == "07:00:00"


async def test_unavailable_without_read_window(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """Without the 43 71 answer the times are unknown, so nothing is offered."""
    client = FakeLampClient()
    client.night_light = None
    connector(client)
    await _setup(hass)

    for entity_id in (SWITCH_ENTITY_ID, START_ENTITY_ID, END_ENTITY_ID):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert _night_light_writes(client) == []


async def test_unavailable_without_notifications(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """A backend without notifications cannot read or safely write the window."""
    client = FakeLampClient(notify=False)
    connector(client)
    await _setup(hass)

    for entity_id in (SWITCH_ENTITY_ID, START_ENTITY_ID, END_ENTITY_ID):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: SWITCH_ENTITY_ID},
        blocking=True,
    )
    assert _night_light_writes(client) == []


async def test_unavailable_when_lamp_is_unreachable(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """The entities follow the lamp's availability."""
    client = FakeLampClient()
    connector(client)
    entry = await _setup(hass)
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_OFF

    entry.runtime_data._set_available(False, "test")
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_UNAVAILABLE

    entry.runtime_data._set_available(True)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_OFF


async def test_command_failure(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """A failing write is reported to the caller, like on the light."""
    client = FakeLampClient()
    connector(client)
    await _setup(hass)

    client.fail_writes = 10
    client.is_connected = False
    with pytest.raises(HomeAssistantError, match="Could not send the command"):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: SWITCH_ENTITY_ID},
            blocking=True,
        )
    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_UNAVAILABLE


async def test_candela_has_no_night_light_entities(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    entity_registry: er.EntityRegistry,
) -> None:
    """The Candela has no night-light window in the legacy protocol."""
    client = FakeLampClient(model=MODEL_CANDELA)
    connector(client)
    entry = await _setup(
        hass,
        CANDELA_ADDRESS,
        MODEL_CANDELA,
        pairing_id=CANDELA_PAIRING_ID.hex(),
    )
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("light.lamp") is not None
    for entity_id in (SWITCH_ENTITY_ID, START_ENTITY_ID, END_ENTITY_ID):
        assert hass.states.get(entity_id) is None
    assert not [
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        )
        if registry_entry.domain in (SWITCH_DOMAIN, TIME_DOMAIN)
    ]
    # and nothing to keep over a restart
    data = await async_mock_restore_state_shutdown_restart(hass)
    stored = {item.state.entity_id: item for item in data.async_get_stored_states()}
    assert stored[LIGHT_ENTITY_ID].extra_data is None


async def test_entities_are_removed_when_the_model_becomes_candela(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    entity_registry: er.EntityRegistry,
) -> None:
    """Reconfiguring a Bedside to Candela leaves no orphaned entities behind."""
    connector(FakeLampClient(), FakeLampClient(model=MODEL_CANDELA))
    entry = await _setup(hass)
    assert entity_registry.async_get(SWITCH_ENTITY_ID) is not None

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODEL: "candela"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.data[CONF_MODEL] == MODEL_CANDELA
    for entity_id in (SWITCH_ENTITY_ID, START_ENTITY_ID, END_ENTITY_ID):
        assert entity_registry.async_get(entity_id) is None
        assert hass.states.get(entity_id) is None
    assert hass.states.get("light.lamp") is not None


async def test_seconds_are_dropped(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """The frame carries only hh:mm, so the UI must not show hidden seconds."""
    client = FakeLampClient()
    connector(client)
    await _setup(hass)

    await hass.services.async_call(
        TIME_DOMAIN,
        "set_value",
        {ATTR_ENTITY_ID: START_ENTITY_ID, "time": "21:30:45"},
        blocking=True,
    )

    assert _night_light_writes(client)[-1][:6] == bytes.fromhex("43 6f 00 01 21 30")
    assert hass.states.get(START_ENTITY_ID).state == "21:30:00"


async def test_night_light_is_read_on_reload(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """A reload reads the window again, a value changed in the app is picked up."""
    client = FakeLampClient()
    connector(client)
    entry = await _setup(hass)
    client.night_light = bytearray(
        bytes((COMMAND_STX, 0x71, 0x01, 0x01, 0x22, 0x00, 0x06, 0x30))
    )

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(SWITCH_ENTITY_ID).state == STATE_ON
    assert hass.states.get(START_ENTITY_ID).state == "22:00:00"
    assert hass.states.get(END_ENTITY_ID).state == "06:30:00"
    assert client.commands().count(0x70) == 2


async def test_light_after_night_light_switched_off(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """The reported bug: the night light switched off while the lamp was off.

    Switched on at the lamp, it showed green at 100 % again, while Home
    Assistant showed the orange at 1 % the lamp kept reporting.
    """
    entry, client, _ = await _setup_with_night_light(hass, connector, GREEN_100)
    lamp = entry.runtime_data

    # off and on at the lamp, inside the window
    lamp.handle_notification(state_frame(False, GREEN_100))
    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get(LIGHT_ENTITY_ID)
    assert state.attributes[ATTR_RGB_COLOR] == (255, 152, 0)
    assert state.attributes[ATTR_BRIGHTNESS] == 3
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["lamp"]["power_on_state"] == GREEN_100.as_dict()
    assert diagnostics["lamp"]["resync_pending"] is False

    lamp.handle_notification(NIGHT_LIGHT_OFF_FRAME)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: SWITCH_ENTITY_ID},
        blocking=True,
    )
    writes = len(client.writes)
    # on again at the lamp: in color mode it reports the night light again
    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(LIGHT_ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (0, 255, 0)
    assert state.attributes[ATTR_BRIGHTNESS] == 255
    # written back once, so that the lamp reports it as well
    assert client.writes[writes:] == [
        bytes.fromhex("434100ff000064").ljust(18, b"\x00")
    ]
    assert not lamp.resync_pending
    assert lamp.power_on_state is None


async def test_turn_on_after_the_window(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """In the morning the lamp is back at its state, Home Assistant as well."""
    _, client, now = await _setup_with_night_light(hass, connector, WHITE_60)

    await _light(hass, SERVICE_TURN_OFF)
    client.power_on_state = NIGHT_LIGHT_ON_FRAME
    await _light(hass, SERVICE_TURN_ON)
    assert hass.states.get(LIGHT_ENTITY_ID).attributes[ATTR_BRIGHTNESS] == 3

    await _light(hass, SERVICE_TURN_OFF)
    now[0] = datetime(2026, 11, 5, 9, 30)
    client.power_on_state = AFTER_NIGHT_LIGHT_FRAME
    await _light(hass, SERVICE_TURN_ON)

    state = hass.states.get(LIGHT_ENTITY_ID)
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 4000
    assert state.attributes[ATTR_BRIGHTNESS] == 153
    assert client.writes[-2][:3] == bytes((COMMAND_STX, CMD_POWER, 0x01))
    assert client.writes[-1] == bytes.fromhex("43430fa03c").ljust(18, b"\x00")


async def test_turn_on_with_brightness_after_night_light(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """The kept color goes with the new brightness, nothing is written after."""
    _, client, now = await _setup_with_night_light(hass, connector, GREEN_100)
    await _light(hass, SERVICE_TURN_OFF)
    client.power_on_state = NIGHT_LIGHT_ON_FRAME
    await _light(hass, SERVICE_TURN_ON)
    await _light(hass, SERVICE_TURN_OFF)

    now[0] = datetime(2026, 11, 5, 9, 30)
    client.power_on_state = NIGHT_LIGHT_ON_FRAME
    await _light(hass, SERVICE_TURN_ON, **{ATTR_BRIGHTNESS: 128})

    assert client.commands()[-2:] == [CMD_POWER, CMD_RGB]
    assert client.writes[-1] == bytes.fromhex("434100ff000033").ljust(18, b"\x00")
    state = hass.states.get(LIGHT_ENTITY_ID)
    assert state.attributes[ATTR_RGB_COLOR] == (0, 255, 0)
    assert state.attributes[ATTR_BRIGHTNESS] == 130


async def test_failed_write_back_is_retried(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """A lamp that cannot be reached gets the state when it is back."""
    entry, client, now = await _setup_with_night_light(hass, connector, WHITE_60)
    lamp = entry.runtime_data
    lamp.handle_notification(state_frame(False, WHITE_60))
    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)
    lamp.handle_notification(NIGHT_LIGHT_OFF_FRAME)
    now[0] = datetime(2026, 11, 5, 9, 30)

    client.fail_writes = 10
    client.is_connected = False
    lamp.handle_notification(AFTER_NIGHT_LIGHT_FRAME)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert lamp.resync_pending
    assert hass.states.get(LIGHT_ENTITY_ID).state == STATE_UNAVAILABLE

    # the next update finds the lamp again, still reporting the leftovers
    client.fail_writes = 0
    client.state = bytearray(AFTER_NIGHT_LIGHT_FRAME)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not lamp.resync_pending
    assert bytes.fromhex("43430fa03c").ljust(18, b"\x00") in client.writes
    state = hass.states.get(LIGHT_ENTITY_ID)
    assert state.attributes[ATTR_BRIGHTNESS] == 153


async def test_power_on_state_is_saved(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """The state from before the night light survives a restart."""
    entry, _, _ = await _setup_with_night_light(hass, connector, WHITE_60)
    lamp = entry.runtime_data
    lamp.handle_notification(state_frame(False, WHITE_60))
    lamp.handle_notification(NIGHT_LIGHT_ON_FRAME)

    data = await async_mock_restore_state_shutdown_restart(hass)

    stored = {item.state.entity_id: item for item in data.async_get_stored_states()}
    assert stored[LIGHT_ENTITY_ID].extra_data.as_dict() == {
        "power_on_state": WHITE_60.as_dict()
    }


@pytest.mark.parametrize(
    ("stored", "restored"),
    [
        (WHITE_60.as_dict(), WHITE_60),
        ({"mode": "white"}, None),
        (None, None),
    ],
)
async def test_power_on_state_is_restored(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    stored: dict[str, Any] | None,
    restored: LightState | None,
) -> None:
    mock_restore_cache_with_extra_data(
        hass, [(State(LIGHT_ENTITY_ID, STATE_OFF), {"power_on_state": stored})]
    )
    connector(FakeLampClient())
    entry = await _setup(hass)

    assert entry.runtime_data.power_on_state == restored
