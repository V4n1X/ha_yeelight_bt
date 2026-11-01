"""Tests for the setup and the light entity."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

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
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.yeelight_bt.const import CONF_MODEL, DOMAIN
from custom_components.yeelight_bt.yeelightbt import (
    CMD_BRIGHTNESS,
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
    ble_device,
    mock_restore_cache,
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
    hass: HomeAssistant, address: str, model: str | None
) -> MockConfigEntry:
    data = {CONF_NAME: "Lamp", CONF_MAC: address}
    if model:
        data[CONF_MODEL] = model
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=address.lower(), title="Lamp", data=data, version=2
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
    assert client.writes[-1][:5] == bytes((COMMAND_STX, CMD_TEMP, 0x0F, 0xF0, 100))
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
