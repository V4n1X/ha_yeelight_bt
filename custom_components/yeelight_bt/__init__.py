"""Control Yeelight bluetooth lamp."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import CONF_MODEL, CONF_PAIRING_ID, DOMAIN
from .yeelightbt import (
    MODEL_CANDELA,
    MODEL_UNKNOWN,
    MODELS,
    Lamp,
    YeelightError,
    model_from_name,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT]
# Removing an entry waits at most this long for the lamp to drop our authorization.
UNPAIR_TIMEOUT = 30

type YeelightBTConfigEntry = ConfigEntry[Lamp]


async def async_setup_entry(hass: HomeAssistant, entry: YeelightBTConfigEntry) -> bool:
    """Set up yeelight_bt from a config entry."""
    _LOGGER.debug("integration async setup entry: %s", entry.as_dict())
    address = entry.data[CONF_MAC].upper()

    # Find ble device here so that we can raise device not found on startup
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        if bluetooth.async_scanner_count(hass, connectable=True) < 1:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="no_scanner"
            )
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"address": address},
        )

    # The advertised name may be missing (bleak >= 1.0 returns None), so the
    # model detected when the entry was created is stored with it.
    model = entry.data.get(CONF_MODEL)
    if model not in MODELS:
        model = model_from_name(ble_device.name)
        if model != MODEL_UNKNOWN:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_MODEL: model}
            )
        else:
            _LOGGER.warning(
                "Could not detect the model of %s, using the Bedside protocol",
                address,
            )
    _async_update_model_issue(hass, entry, model)

    lamp = Lamp(
        ble_device,
        model=model,
        ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
            hass, address, connectable=True
        ),
        pairing_id=_pairing_id(entry, model),
        clock=dt_util.now,
    )

    pairing_issue = f"pairing_required_{entry.entry_id}"
    ir.async_delete_issue(hass, DOMAIN, pairing_issue)
    pairing_requested = False

    @callback
    def _async_update_pairing_issue() -> None:
        """Ask for the lamp's button when it no longer accepts Home Assistant."""
        nonlocal pairing_requested
        if lamp.pairing_requested == pairing_requested:
            return
        pairing_requested = lamp.pairing_requested
        if not pairing_requested:
            ir.async_delete_issue(hass, DOMAIN, pairing_issue)
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            pairing_issue,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="pairing_required",
            translation_placeholders={"name": entry.title, "address": address},
        )

    entry.async_on_unload(
        lamp.add_callback_on_state_changed(_async_update_pairing_issue)
    )

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Follow the lamp when it is better reachable through another adapter/proxy."""
        lamp.set_ble_device(service_info.device)
        # The entity is set up for one model: store a model found later and reload
        if (
            entry.data.get(CONF_MODEL) not in MODELS
            and (found := model_from_name(service_info.name)) in MODELS
        ):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_MODEL: found}
            )
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            bluetooth.BluetoothCallbackMatcher(address=address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    async def _async_stop(event: Event) -> None:
        await lamp.disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    entry.runtime_data = lamp
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _pairing_id(entry: YeelightBTConfigEntry, model: str | None) -> bytes | None:
    """The stored Candela identifier; Bedside lamps use a fixed one."""
    pairing_id = entry.data.get(CONF_PAIRING_ID)
    return bytes.fromhex(pairing_id) if model == MODEL_CANDELA and pairing_id else None


@callback
def _async_update_model_issue(
    hass: HomeAssistant, entry: YeelightBTConfigEntry, model: str | None
) -> None:
    """Ask the user to pick the model when it could not be detected."""
    issue_id = f"unknown_model_{entry.entry_id}"
    if model in MODELS:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unknown_model",
        translation_placeholders={"name": entry.title, "address": entry.data[CONF_MAC]},
    )


async def async_remove_entry(hass: HomeAssistant, entry: YeelightBTConfigEntry) -> None:
    """Clean up when the lamp is removed."""
    ir.async_delete_issue(hass, DOMAIN, f"unknown_model_{entry.entry_id}")
    ir.async_delete_issue(hass, DOMAIN, f"pairing_required_{entry.entry_id}")
    address = entry.data[CONF_MAC].upper()
    model = entry.data.get(CONF_MODEL)
    # Command-only Candela entries never authorized an identifier.
    if model != MODEL_CANDELA or entry.data.get(CONF_PAIRING_ID):
        await _async_unpair(hass, entry, address, model)
    # HA bluetooth reports a device only once per run; let the lamp be
    # discovered again so it can be re-added without a restart.
    bluetooth.async_rediscover_address(hass, address)


async def _async_unpair(
    hass: HomeAssistant, entry: YeelightBTConfigEntry, address: str, model: str | None
) -> None:
    """Remove the lamp's authorization of Home Assistant, if it can be reached.

    The Bedside identifier is the one the Yeelight app uses as well, so this
    also signs the lamp out of the app, like deleting it there does.
    """
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        _LOGGER.debug("%s is not reachable, its authorization stays", address)
        return
    lamp = Lamp(ble_device, model=model, pairing_id=_pairing_id(entry, model))
    try:
        async with asyncio.timeout(UNPAIR_TIMEOUT):
            await lamp.unpair()
    except (YeelightError, TimeoutError) as err:
        _LOGGER.debug("Could not remove the authorization of %s: %s", address, err)


async def async_unload_entry(hass: HomeAssistant, entry: YeelightBTConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.disconnect()
    return unload_ok
