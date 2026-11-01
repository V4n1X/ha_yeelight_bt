"""Tests for the setup and the light entity."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    ColorMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    CONF_MAC,
    CONF_NAME,
    EVENT_HOMEASSISTANT_STOP,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest

from custom_components.yeelight_bt.const import CONF_MODEL, DOMAIN
from custom_components.yeelight_bt.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.yeelight_bt.yeelightbt import (
    CMD_BRIGHTNESS,
    CMD_GETSTATE,
    CMD_POWER,
    CMD_RGB,
    CMD_TEMP,
    COMMAND_STX,
)

from .conftest import (
    BEDSIDE_ADDRESS,
    CANDELA_ADDRESS,
    FakeLampClient,
    MockConfigEntry,
    async_fire_time_changed,
    ble_device,
    mock_restore_cache,
    service_info,
)


@pytest.fixture(autouse=True)
def bluetooth_ready(enable_bluetooth: None) -> None:
    """Set up the HA bluetooth stack (mocked adapters)."""


@pytest.fixture
def lamp_visible() -> Generator[None]:
    """Make HA bluetooth know both lamps (as if advertising)."""
    devices = {
        BEDSIDE_ADDRESS: ble_device(BEDSIDE_ADDRESS, "XMCTD_1"),
        # bleak >= 1.0 reports None for devices without a local name
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
    hass: HomeAssistant, address: str, model: str | None, **entry_kwargs: Any
) -> MockConfigEntry:
    data = {CONF_NAME: "Lamp", CONF_MAC: address}
    if model:
        data[CONF_MODEL] = model
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


async def test_setup_retry_when_not_found(hass: HomeAssistant) -> None:
    entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_bedside(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    client = FakeLampClient()
    connector(client)
    # entries created by v1.x have no model stored
    entry = await _setup(hass, BEDSIDE_ADDRESS, None)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_MODEL] == "Bedside"

    entity_id = entity_registry.async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, BEDSIDE_ADDRESS
    )
    assert entity_id == "light.lamp"
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.COLOR_TEMP
    # the lamp reports 4080 K, shown as such
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 4080
    assert state.attributes[ATTR_BRIGHTNESS] == 204
    assert ATTR_ASSUMED_STATE not in state.attributes

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, BEDSIDE_ADDRESS), entry.entry_id
    )
    assert device.model == "Bedside"
    assert device.sw_version == "2-3-4"

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id, ATTR_HS_COLOR: (0, 100), ATTR_BRIGHTNESS: 255},
        blocking=True,
    )
    assert client.writes[-1][:7] == bytes((COMMAND_STX, CMD_RGB, 255, 0, 0, 1, 100))
    state = hass.states.get(entity_id)
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.HS
    assert state.attributes[ATTR_HS_COLOR] == (0.0, 100.0)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id, ATTR_COLOR_TEMP_KELVIN: 2740},
        blocking=True,
    )
    # true Kelvin: 2740 K = 0x0AB4 is sent unchanged
    assert client.writes[-1][:5] == bytes((COMMAND_STX, CMD_TEMP, 0x0A, 0xB4, 100))
    state = hass.states.get(entity_id)
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.COLOR_TEMP
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 2740

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert client.writes[-1][:3] == bytes((COMMAND_STX, CMD_POWER, 0x02))
    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )
    assert client.writes[-2][:3] == bytes((COMMAND_STX, CMD_POWER, 0x01))
    assert client.writes[-1][:3] == bytes((COMMAND_STX, CMD_BRIGHTNESS, 51))
    assert hass.states.get(entity_id).state == STATE_ON

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not client.is_connected


async def test_candela_proxy(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """Candela is brightness only (#69) and optimistic without notifications (#76)."""
    mock_restore_cache(hass, [State("light.lamp", STATE_ON, {ATTR_BRIGHTNESS: 128})])
    client = FakeLampClient(notify=False)
    connector(client)
    await _setup(hass, CANDELA_ADDRESS, "Candela")

    state = hass.states.get("light.lamp")
    assert state.state == STATE_ON
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.BRIGHTNESS]
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.BRIGHTNESS
    # 128/255 is stored as 51 % by the lamp
    assert state.attributes[ATTR_BRIGHTNESS] == 130
    assert state.attributes[ATTR_ASSUMED_STATE] is True

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.lamp", ATTR_BRIGHTNESS: 26},
        blocking=True,
    )
    # assumed state: always power on before setting the brightness
    assert client.writes[-2][:3] == bytes((COMMAND_STX, CMD_POWER, 0x01))
    assert client.writes[-1][:3] == bytes((COMMAND_STX, CMD_BRIGHTNESS, 11))
    state = hass.states.get("light.lamp")
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.BRIGHTNESS

    # A color sent to the Candela (e.g. in a light group) is not passed on
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.lamp", ATTR_HS_COLOR: (10, 10)},
        blocking=True,
    )
    assert CMD_RGB not in [write[1] for write in client.writes]
    assert (
        hass.states.get("light.lamp").attributes[ATTR_COLOR_MODE]
        == ColorMode.BRIGHTNESS
    )


async def test_unreachable_at_startup(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """Advertising alone does not keep a lamp available that cannot be reached."""
    connector(FakeLampClient(fail_writes=10))
    await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    assert hass.states.get("light.lamp").state == "unavailable"


async def test_entity_id_change(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    entity_registry: er.EntityRegistry,
) -> None:
    """Renaming the entity keeps one working lamp subscription."""
    client = FakeLampClient()
    connector(client)
    entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    lamp = entry.runtime_data

    entity_registry.async_update_entity("light.lamp", new_entity_id="light.bedroom")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get("light.lamp") is None
    assert len(lamp._state_callbacks) == 1
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "light.bedroom"}, blocking=True
    )
    assert hass.states.get("light.bedroom").state == STATE_OFF


async def test_command_failure(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """A failing command is reported instead of silently ignored (#49)."""
    client = FakeLampClient()
    connector(client)
    await _setup(hass, BEDSIDE_ADDRESS, "Bedside")

    client.fail_writes = 10
    client.is_connected = False
    with pytest.raises(HomeAssistantError, match="Could not send the command"):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "light.lamp"},
            blocking=True,
        )
    assert hass.states.get("light.lamp").state == "unavailable"


async def test_diagnostics(hass: HomeAssistant, connector, lamp_visible: None) -> None:
    connector(FakeLampClient())
    entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_MAC] == BEDSIDE_ADDRESS
    assert diag["lamp"]["model"] == "Bedside"
    assert diag["lamp"]["available"] is True
    assert diag["lamp"]["state_feedback"] is True
    assert diag["lamp"]["versions"] == (1, 2, 3, 4, 5)
    assert diag["lamp"]["temperature"] == 4080
    assert set(diag["connection_via"]) == {"source", "name"}


async def test_polling_and_update_entity(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """The state is polled every 30 s, unless polling is disabled for the entry."""
    client = FakeLampClient()
    connector(client)
    entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside", pref_disable_polling=True)

    def polls() -> int:
        return client.commands().count(CMD_GETSTATE)

    start = polls()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert polls() == start

    # homeassistant.update_entity still refreshes on request
    await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant", "update_entity", {ATTR_ENTITY_ID: "light.lamp"}, blocking=True
    )
    assert polls() == start + 1

    hass.config_entries.async_update_entry(entry, pref_disable_polling=False)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert polls() == start + 2


async def test_unknown_model_repair(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An undetected model raises a repair issue, fixed with the reconfigure flow."""
    connector(FakeLampClient())
    # the Candela address advertises no name in this test
    entry = await _setup(hass, CANDELA_ADDRESS, None)
    issue_id = f"unknown_model_{entry.entry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "unknown_model"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODEL: "candela"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.data[CONF_MODEL] == "Candela"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    state = hass.states.get("light.lamp")
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.BRIGHTNESS]


async def test_no_bluetooth_scanner(hass: HomeAssistant) -> None:
    with patch(
        "homeassistant.components.bluetooth.async_scanner_count", return_value=0
    ):
        entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_follows_adapter_and_disconnects_on_stop(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """The lamp uses the BLEDevice of the best adapter/proxy, and HA stop disconnects."""
    client = FakeLampClient()
    connector(client)
    callbacks = []

    def _register(hass, callback, matcher, mode):
        callbacks.append(callback)
        return lambda: None

    with patch(
        "homeassistant.components.bluetooth.async_register_callback",
        side_effect=_register,
    ):
        entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    lamp = entry.runtime_data

    via_proxy = ble_device(BEDSIDE_ADDRESS, "XMCTD_1")
    callbacks[0](
        service_info(BEDSIDE_ADDRESS, "XMCTD_1"), BluetoothChange.ADVERTISEMENT
    )
    assert lamp._ble_device is not via_proxy
    assert lamp._ble_device.address == BEDSIDE_ADDRESS

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert not client.is_connected


async def test_remove_entry_deletes_repair_issue(
    hass: HomeAssistant,
    connector,
    lamp_visible: None,
    issue_registry: ir.IssueRegistry,
) -> None:
    connector(FakeLampClient())
    entry = await _setup(hass, CANDELA_ADDRESS, None)
    assert issue_registry.async_get_issue(DOMAIN, f"unknown_model_{entry.entry_id}")

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not issue_registry.async_get_issue(DOMAIN, f"unknown_model_{entry.entry_id}")


async def test_turn_on_edge_cases(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    client = FakeLampClient()
    connector(client)
    entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    lamp = entry.runtime_data

    # already on and nothing to change: no command
    writes = len(client.writes)
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "light.lamp"}, blocking=True
    )
    assert len(client.writes) == writes

    # brightness 0 while on turns the lamp off
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.lamp", ATTR_BRIGHTNESS: 0},
        blocking=True,
    )
    assert client.writes[-1][:3] == bytes((COMMAND_STX, CMD_POWER, 0x02))

    # a lamp reporting brightness 0 has no brightness attribute
    lamp._brightness = 0
    lamp._is_on = True
    lamp.run_state_changed_cb()
    assert hass.states.get("light.lamp").attributes[ATTR_BRIGHTNESS] is None

    client.fail_writes = 10
    client.is_connected = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "light.lamp", ATTR_HS_COLOR: (120, 100)},
            blocking=True,
        )


async def test_reconnects_after_dropped_connection(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    """A dropped connection is re-established soon to keep receiving the state."""
    client = FakeLampClient()
    connector(client)
    entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    polls = client.commands().count(CMD_GETSTATE)

    client.disconnected_callback(client)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert client.commands().count(CMD_GETSTATE) == polls + 1

    # a pending reconnect is cancelled when the entry is unloaded
    client.disconnected_callback(client)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_update_timeout(
    hass: HomeAssistant, connector, lamp_visible: None
) -> None:
    connector(FakeLampClient())
    entry = await _setup(hass, BEDSIDE_ADDRESS, "Bedside")
    await async_setup_component(hass, "homeassistant", {})
    with patch.object(entry.runtime_data, "update", side_effect=TimeoutError):
        await hass.services.async_call(
            "homeassistant",
            "update_entity",
            {ATTR_ENTITY_ID: "light.lamp"},
            blocking=True,
        )
    assert hass.states.get("light.lamp").state == STATE_ON
