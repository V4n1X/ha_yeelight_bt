"""Diagnostics support for Yeelight bluetooth."""

from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from . import YeelightBTConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YeelightBTConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (contains no credentials)."""
    lamp = entry.runtime_data
    service_info = bluetooth.async_last_service_info(hass, lamp.mac, connectable=True)
    # A connected lamp stops advertising, so service_info may be missing; the
    # BLEDevice still names the adapter/proxy used for the connection.
    details = lamp.ble_device.details
    source = details.get("source") if isinstance(details, dict) else None
    scanner = bluetooth.async_scanner_by_source(hass, source) if source else None
    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "pref_disable_polling": entry.pref_disable_polling,
        },
        "lamp": {
            "model": lamp.model,
            "available": lamp.available,
            "connected": lamp.is_connected,
            "state_feedback": lamp.has_state_feedback,
            "versions": lamp.versions,
            "serial": lamp.serial,
            "is_on": lamp.is_on,
            "mode": lamp.mode,
            "brightness": lamp.brightness,
            "temperature": lamp.temperature,
            "color": lamp.color,
        },
        "connection_via": {
            "source": source,
            "name": scanner.name if scanner else None,
        },
        "bluetooth": None
        if service_info is None
        else {
            "name": service_info.name,
            "source": service_info.source,
            "rssi": service_info.rssi,
            "connectable": service_info.connectable,
            "time": service_info.time,
            "service_uuids": service_info.service_uuids,
            "manufacturer_data": {
                str(key): value.hex()
                for key, value in service_info.manufacturer_data.items()
            },
        },
        "scanners": bluetooth.async_scanner_count(hass, connectable=True),
    }
