"""Tests for the config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.yeelight_bt.const import CONF_MODEL, DOMAIN
from custom_components.yeelight_bt.yeelightbt import CMD_PAIR

from .conftest import (
    BEDSIDE_ADDRESS,
    CANDELA_ADDRESS,
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

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC: CANDELA_ADDRESS, CONF_NAME: "Candela"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result.get("errors")
    assert result["title"] == "Candela"
    assert result["data"] == {
        CONF_NAME: "Candela",
        CONF_MAC: CANDELA_ADDRESS,
        CONF_MODEL: "Candela",
    }
    assert result["result"].unique_id == CANDELA_ADDRESS.lower()
    # the connection was tested before the entry was created; a Candela only
    # gets connected, it is never sent the pairing request
    assert lamp.writes == []
    assert not lamp.is_connected


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
