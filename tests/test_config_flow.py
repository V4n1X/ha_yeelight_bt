"""Tests for the config flow."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from bleak import BleakError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import instance_id
import pytest

from custom_components.yeelight_bt import yeelightbt
from custom_components.yeelight_bt.const import CONF_MODEL, CONF_PAIRING_ID, DOMAIN
from custom_components.yeelight_bt.yeelightbt import (
    CMD_PAIR,
    PAIR_NOT_PAIRED,
    PAIR_REQUESTED,
)

from .conftest import (
    BEDSIDE_ADDRESS,
    CANDELA_ADDRESS,
    CANDELA_STATE_FRAME,
    CANDELA_VERSION_FRAME,
    FakeLampClient,
    MockConfigEntry,
    ble_device,
    service_info,
)

UNNAMED_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _patch_discovered(*infos):
    return patch(
        "custom_components.yeelight_bt.config_flow.async_discovered_service_info",
        return_value=list(infos),
    )


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[None]:
    """Do not set up the created entries."""

    async def _setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        entry.runtime_data = AsyncMock()
        return True

    with patch(
        "custom_components.yeelight_bt.async_setup_entry", side_effect=_setup_entry
    ):
        yield


@pytest.fixture(autouse=True)
def lamp(connector) -> Generator[FakeLampClient]:
    """A reachable lamp for the connection test of the flow."""
    client = FakeLampClient()
    connector(client)
    devices = {
        BEDSIDE_ADDRESS: ble_device(BEDSIDE_ADDRESS, "XMCTD_1"),
        CANDELA_ADDRESS: ble_device(CANDELA_ADDRESS, "yeelight_ms"),
    }
    with patch(
        "custom_components.yeelight_bt.config_flow.async_ble_device_from_address",
        side_effect=lambda hass, address, connectable=True: devices.get(address),
    ):
        yield client


async def test_pick_discovered_device(
    hass: HomeAssistant, lamp: FakeLampClient
) -> None:
    """Unnamed devices in the scan are skipped instead of crashing (#83)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU

    with _patch_discovered(
        service_info(UNNAMED_ADDRESS, None),
        service_info(BEDSIDE_ADDRESS, "XMCTD_1"),
        service_info(CANDELA_ADDRESS, "yeelight_ms"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "pick_device"}
        )
    assert result["type"] is FlowResultType.FORM
    options = result["data_schema"].schema[CONF_MAC].container
    assert set(options) == {BEDSIDE_ADDRESS, CANDELA_ADDRESS}

    lamp.state = bytearray(CANDELA_STATE_FRAME)
    lamp.version_frame = CANDELA_VERSION_FRAME
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC: CANDELA_ADDRESS, CONF_NAME: "Candela"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result.get("errors")
    assert result["title"] == "Candela"
    identifier = bytes.fromhex((await instance_id.async_get(hass))[:12])
    assert result["data"] == {
        CONF_NAME: "Candela",
        CONF_MAC: CANDELA_ADDRESS,
        CONF_MODEL: "Candela",
        CONF_PAIRING_ID: identifier.hex(),
    }
    assert result["result"].unique_id == CANDELA_ADDRESS.lower()
    # Authorization uses a stable app identifier, as in the APK.
    assert lamp.writes[0][1] == CMD_PAIR
    assert lamp.writes[0][2:8] == identifier
    assert not lamp.is_connected


async def test_manual_candela_auto_uses_instance_identifier(
    hass: HomeAssistant, lamp: FakeLampClient
) -> None:
    """Auto detection must select the same identifier as the eventual setup."""
    lamp.state = bytearray(CANDELA_STATE_FRAME)
    lamp.version_frame = CANDELA_VERSION_FRAME
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with patch(
        "homeassistant.helpers.instance_id.async_get",
        return_value="00112233-4455-6677-8899-aabbccddeeff",
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_NAME: "Candela", CONF_MAC: CANDELA_ADDRESS, CONF_MODEL: "auto"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert lamp.writes[0][1:8] == bytes.fromhex("67001122334455")
    assert result["data"][CONF_PAIRING_ID] == "001122334455"
    assert result["data"][CONF_MODEL] == "Candela"


@pytest.mark.parametrize("responses", [(PAIR_REQUESTED,), ()])
@pytest.mark.parametrize("manual", [False, True])
async def test_candela_config_flow_falls_back_without_confirmation(
    hass: HomeAssistant,
    lamp: FakeLampClient,
    responses: tuple[int, ...],
    manual: bool,
) -> None:
    lamp.pair_responses = responses
    if manual:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manual"}
        )
        user_input = {
            CONF_NAME: "Candela",
            CONF_MAC: CANDELA_ADDRESS,
            CONF_MODEL: "auto",
        }
    else:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=service_info(CANDELA_ADDRESS, "yeelight_ms"),
        )
        user_input = {CONF_NAME: "Candela"}

    original_connect = yeelightbt.establish_connection
    connect = AsyncMock(side_effect=original_connect.__call__)
    with patch.object(yeelightbt, "establish_connection", connect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "Candela"
    assert CONF_PAIRING_ID not in result["data"]
    assert connect.await_count == 2
    assert lamp.notify_calls == 1
    assert lamp.commands() == [CMD_PAIR]
    assert not lamp.is_connected


async def test_candela_fallback_reconnects_after_pairing_disconnect(
    hass: HomeAssistant, lamp: FakeLampClient
) -> None:
    lamp.pair_responses = (PAIR_REQUESTED,)
    original_write = lamp.write_gatt_char
    dropped = False

    def disconnect() -> None:
        nonlocal dropped
        dropped = True
        lamp.is_connected = False
        lamp.disconnected_callback(lamp)

    async def write_then_disconnect(uuid, data, response=None):
        await original_write(uuid, data, response)
        if data[1] == CMD_PAIR:
            asyncio.get_running_loop().call_later(0.03, disconnect)

    lamp.write_gatt_char = write_then_disconnect
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(CANDELA_ADDRESS, "yeelight_ms"),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Candela"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_PAIRING_ID not in result["data"]
    assert dropped
    assert lamp.commands() == [CMD_PAIR]
    assert not lamp.is_connected


async def test_failed_candela_fallback_does_not_create_entry(
    hass: HomeAssistant, lamp: FakeLampClient
) -> None:
    lamp.pair_responses = ()
    original_connect = yeelightbt.establish_connection
    attempts = 0

    async def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise BleakError("Device no longer reachable")
        return await original_connect(*args, **kwargs)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(CANDELA_ADDRESS, "yeelight_ms"),
    )
    with patch.object(yeelightbt, "establish_connection", connect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: "Candela"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert attempts == 2


@pytest.mark.parametrize(
    ("address", "name", "responses"),
    [
        (BEDSIDE_ADDRESS, "XMCTD_1", ()),
        (CANDELA_ADDRESS, "yeelight_ms", (PAIR_NOT_PAIRED,)),
    ],
)
async def test_pairing_errors_without_fallback(
    hass: HomeAssistant,
    lamp: FakeLampClient,
    address: str,
    name: str,
    responses: tuple[int, ...],
) -> None:
    lamp.pair_responses = responses
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(address, name),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Lamp"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert lamp.commands() == [CMD_PAIR]


async def test_pick_device_cannot_connect(
    hass: HomeAssistant, lamp: FakeLampClient
) -> None:
    """A lamp that cannot be reached is not configured (test-before-configure)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_discovered(service_info(BEDSIDE_ADDRESS, "XMCTD_1")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "pick_device"}
        )
    lamp.fail_writes = 10
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC: BEDSIDE_ADDRESS, CONF_NAME: "Bed"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    lamp.fail_writes = 0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC: BEDSIDE_ADDRESS, CONF_NAME: "Bed"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pick_device_none_found(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=BEDSIDE_ADDRESS.lower()).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with _patch_discovered(
        service_info(UNNAMED_ADDRESS, None), service_info(BEDSIDE_ADDRESS, "XMCTD_1")
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "pick_device"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_manual_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Bed", CONF_MAC: "not-a-mac", CONF_MODEL: "auto"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MAC: "invalid_mac"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Bed", CONF_MAC: UNNAMED_ADDRESS, CONF_MODEL: "auto"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_found"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Bed", CONF_MAC: "f8-24-41-e6-3e-39", CONF_MODEL: "bedside"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_NAME: "Bed",
        CONF_MAC: BEDSIDE_ADDRESS,
        CONF_MODEL: "Bedside",
    }


async def test_manual_entry_auto_model(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Bed", CONF_MAC: BEDSIDE_ADDRESS, CONF_MODEL: "auto"},
    )
    assert result["data"] == {CONF_NAME: "Bed", CONF_MAC: BEDSIDE_ADDRESS}


async def test_bluetooth_discovery(hass: HomeAssistant, lamp: FakeLampClient) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(BEDSIDE_ADDRESS, "XMCTD_1"),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    lamp.fail_writes = 10
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Bedroom"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    lamp.fail_writes = 0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Bedroom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "Bedside"
    # the Bedside is paired during the connection test
    assert CMD_PAIR in lamp.commands()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(BEDSIDE_ADDRESS, "XMCTD_1"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_discovery_unsupported(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(UNNAMED_ADDRESS, None),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("candela", {CONF_MODEL: "Candela"}), ("auto", {})],
)
async def test_reconfigure(hass: HomeAssistant, choice: str, expected: dict) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BEDSIDE_ADDRESS.lower(),
        title="Lamp",
        data={CONF_NAME: "Lamp", CONF_MAC: BEDSIDE_ADDRESS, CONF_MODEL: "Bedside"},
        version=2,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODEL: choice}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == {
        CONF_NAME: "Lamp",
        CONF_MAC: BEDSIDE_ADDRESS,
        **expected,
    }
