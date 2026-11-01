"""Config flow for yeelight_bt"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .const import CONF_MODEL, DOMAIN, MODEL_AUTO
from .yeelightbt import MODEL_BEDSIDE, MODEL_CANDELA, MODEL_UNKNOWN, model_from_name

_LOGGER = logging.getLogger(__name__)

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
MODEL_OPTIONS = {"bedside": MODEL_BEDSIDE, "candela": MODEL_CANDELA}


class Yeelight_btConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for yeelight_bt."""

    VERSION = 2

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered bluetooth device: %s", discovery_info)
        if model_from_name(discovery_info.name) == MODEL_UNKNOWN:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(dr.format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": self._default_name(discovery_info)
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered lamp and give it a name."""
        assert self._discovery_info is not None
        info = self._discovery_info
        if user_input is not None:
            return self._create_entry(
                user_input[CONF_NAME], info.address, model_from_name(info.name)
            )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME, default=self._default_name(info)): str}
            ),
            description_placeholders={
                "model": model_from_name(info.name),
                "address": info.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        return self.async_show_menu(
            step_id="user", menu_options=["pick_device", "manual"]
        )

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one of the lamps seen by HA bluetooth (adapters and proxies)."""
        if user_input is not None:
            address = user_input[CONF_MAC]
            await self.async_set_unique_id(
                dr.format_mac(address), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            return self._create_entry(
                user_input[CONF_NAME], address, self._discovered[address]
            )

        current_ids = self._async_current_ids(include_ignore=False)
        names: dict[str, str] = {}
        for info in async_discovered_service_info(self.hass, connectable=True):
            # Devices without a name are reported with name=None by bleak (#83)
            model = model_from_name(info.name)
            if model == MODEL_UNKNOWN or dr.format_mac(info.address) in current_ids:
                continue
            self._discovered[info.address] = model
            names[info.address] = f"{info.name} - {model} ({info.address})"

        if not names:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC): vol.In(names),
                    vol.Required(CONF_NAME): str,
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the MAC address manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_MAC].strip()
            if not MAC_REGEX.match(address):
                errors[CONF_MAC] = "invalid_mac"
            else:
                address = address.replace("-", ":").upper()
                await self.async_set_unique_id(dr.format_mac(address))
                self._abort_if_unique_id_configured()
                return self._create_entry(
                    user_input[CONF_NAME],
                    address,
                    MODEL_OPTIONS.get(user_input.get(CONF_MODEL, MODEL_AUTO)),
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_NAME): str,
                        vol.Required(CONF_MAC): str,
                        vol.Required(CONF_MODEL, default=MODEL_AUTO): SelectSelector(
                            SelectSelectorConfig(
                                options=[MODEL_AUTO, *MODEL_OPTIONS],
                                mode=SelectSelectorMode.DROPDOWN,
                                translation_key="model",
                            )
                        ),
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    @staticmethod
    def _default_name(info: BluetoothServiceInfoBleak) -> str:
        return f"Yeelight {model_from_name(info.name)}"

    def _create_entry(
        self, name: str, address: str, model: str | None
    ) -> ConfigFlowResult:
        data = {CONF_NAME: name, CONF_MAC: address.upper()}
        if model and model != MODEL_UNKNOWN:
            data[CONF_MODEL] = model
        return self.async_create_entry(title=name, data=data)
