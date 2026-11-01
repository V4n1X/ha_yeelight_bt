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

from .conftest import BEDSIDE_ADDRESS, CANDELA_ADDRESS, MockConfigEntry, service_info

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


async def test_pick_discovered_device(hass: HomeAssistant) -> None:
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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Candela"
    assert result["data"] == {
        CONF_NAME: "Candela",
        CONF_MAC: CANDELA_ADDRESS,
        CONF_MODEL: "Candela",
    }
    assert result["result"].unique_id == CANDELA_ADDRESS.lower()


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


async def test_bluetooth_discovery(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(BEDSIDE_ADDRESS, "XMCTD_1"),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Bedroom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "Bedside"

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
