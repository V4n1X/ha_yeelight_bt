"""Config flow for yeelight_bt"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.helpers import device_registry as dr, instance_id
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import probatio

from .const import CONF_MODEL, CONF_PAIRING_ID, DOMAIN, MODEL_AUTO
from .yeelightbt import (
    MODEL_BEDSIDE,
    MODEL_CANDELA,
    MODEL_UNKNOWN,
    MODELS,
    PAIR_BUTTON_TIMEOUT,
    Lamp,
    YeelightError,
    YeelightPairingTimeoutError,
    model_from_name,
)

_LOGGER = logging.getLogger(__name__)

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
MODEL_OPTIONS = {"bedside": MODEL_BEDSIDE, "candela": MODEL_CANDELA}
# connect + pairing (incl. the time to push the button) + one command
CONNECTION_TEST_TIMEOUT = PAIR_BUTTON_TIMEOUT + 60


def _model_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[MODEL_AUTO, *MODEL_OPTIONS],
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="model",
        )
    )


class Yeelight_btConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for yeelight_bt."""

    VERSION = 2

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}
        self._candela_data: dict[str, str] = {}

    async def _async_test_connection(
        self, address: str, model: str | None
    ) -> str | None:
        """Test pairing, retry an unconfirmed Candela once in command-only mode."""
        self._candela_data = {}
        ble_device = async_ble_device_from_address(
            self.hass, address.upper(), connectable=True
        )
        if ble_device is None:
            return "not_found"
        if model not in MODELS:
            model = model_from_name(ble_device.name)
        pairing_ids = (
            (
                bytes.fromhex(
                    (await instance_id.async_get(self.hass)).replace("-", "")[:12]
                ),
                None,
            )
            if model == MODEL_CANDELA
            else (None,)
        )
        for pairing_id in pairing_ids:
            lamp = Lamp(ble_device, model=model, pairing_id=pairing_id)
            try:
                async with asyncio.timeout(CONNECTION_TEST_TIMEOUT):
                    await lamp.update()
            except YeelightPairingTimeoutError as err:
                _LOGGER.debug("Pairing with %s was not confirmed: %s", address, err)
            except (YeelightError, TimeoutError) as err:
                _LOGGER.debug("Connection test with %s failed: %s", address, err)
                return "cannot_connect"
            else:
                if model == MODEL_CANDELA:
                    self._candela_data[CONF_MODEL] = MODEL_CANDELA
                    if lamp.confirmed_pairing_id is not None:
                        self._candela_data[CONF_PAIRING_ID] = (
                            lamp.confirmed_pairing_id.hex()
                        )
                return None
            finally:
                # Release the attempted authorization connection before retrying.
                await lamp.disconnect()
        return "cannot_connect"

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
        model = model_from_name(info.name)
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (error := await self._async_test_connection(info.address, model)):
                return self._create_entry(user_input[CONF_NAME], info.address, model)
            errors["base"] = error
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Required(CONF_NAME): str}),
                user_input or {CONF_NAME: self._default_name(info)},
            ),
            description_placeholders={"model": model, "address": info.address},
            errors=errors,
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
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_MAC]
            await self.async_set_unique_id(
                dr.format_mac(address), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            model = self._discovered[address]
            if not (error := await self._async_test_connection(address, model)):
                return self._create_entry(user_input[CONF_NAME], address, model)
            errors["base"] = error
        else:
            current_ids = self._async_current_ids(include_ignore=False)
            self._discovered = {}
            for info in async_discovered_service_info(self.hass, connectable=True):
                # Devices without a name are reported with name=None by bleak (#83)
                model = model_from_name(info.name)
                if model == MODEL_UNKNOWN or dr.format_mac(info.address) in current_ids:
                    continue
                self._discovered[info.address] = model
            if not self._discovered:
                return self.async_abort(reason="no_devices_found")

        names = {
            address: f"Yeelight {model} ({address})"
            for address, model in self._discovered.items()
        }
        return self.async_show_form(
            step_id="pick_device",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_MAC): probatio.In(names),
                        probatio.Required(CONF_NAME): str,
                    }
                ),
                user_input,
            ),
            errors=errors,
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
                model = MODEL_OPTIONS.get(user_input.get(CONF_MODEL, MODEL_AUTO))
                if not (error := await self._async_test_connection(address, model)):
                    return self._create_entry(user_input[CONF_NAME], address, model)
                errors["base"] = error

        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_NAME): str,
                        probatio.Required(CONF_MAC): str,
                        probatio.Required(
                            CONF_MODEL, default=MODEL_AUTO
                        ): _model_selector(),
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the model of a configured lamp."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            data = {k: v for k, v in entry.data.items() if k != CONF_MODEL}
            if model := MODEL_OPTIONS.get(user_input[CONF_MODEL]):
                data[CONF_MODEL] = model
            return self.async_update_reload_and_abort(entry, data=data)

        current = next(
            (k for k, v in MODEL_OPTIONS.items() if v == entry.data.get(CONF_MODEL)),
            MODEL_AUTO,
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Required(CONF_MODEL): _model_selector()}),
                {CONF_MODEL: current},
            ),
            description_placeholders={
                "name": entry.title,
                "address": entry.data[CONF_MAC],
            },
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
        data.update(self._candela_data)
        return self.async_create_entry(title=name, data=data)
