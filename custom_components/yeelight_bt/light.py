"""Light platform for Yeelight bluetooth lamps."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.color import (
    brightness_to_value,
    color_hs_to_RGB,
    color_RGB_to_hs,
    value_to_brightness,
)

from . import YeelightBTConfigEntry
from .const import DOMAIN
from .yeelightbt import MODEL_CANDELA, TEMP_MAX, TEMP_MIN, Lamp, YeelightError

_LOGGER = logging.getLogger(__name__)

# State is pushed by the lamp, polling is done by the entity itself so that a
# slow (re)connection through a proxy never blocks HA's update loop.
PARALLEL_UPDATES = 0
UPDATE_INTERVAL = timedelta(seconds=30)
UPDATE_TIMEOUT = 120
RECONNECT_DELAY = 5
# Pause between powering on and a follow-up command, the lamp ignores
# commands while it is still powering on.
POWER_ON_DELAY = 0.5
# Lamps stop their transition when they receive a command, so give the
# transition some time before HA sends the next one.
TRANSITION_DELAY = 0.7

BRIGHTNESS_SCALE = (1, 100)
# the temp HA wants to set at when clicking on white in UI
HA_WHITE_TEMP = 2740
# the temp that correspond to true white on the lamp
LAMP_WHITE_TEMP = 4080


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YeelightBTConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the light from a config entry."""
    async_add_entities([YeelightBT(entry.runtime_data, entry.title)])


def scale_temp(temp: int) -> int:
    """Scale the temperature so that the white in HA UI correspond to the
    white on the lamp!"""
    a, b = TEMP_MIN, TEMP_MAX
    mid, white = HA_WHITE_TEMP, LAMP_WHITE_TEMP
    temp = min(b, max(a, int(temp)))
    if temp < mid:
        new_temp = (white - a) / (mid - a) * temp + a * (mid - white) / (mid - a)
    else:
        new_temp = (b - white) / (b - mid) * temp + b * (white - mid) / (b - mid)
    return round(new_temp)


def scale_temp_reversed(temp: int) -> int:
    """Reverse the scale to match HA UI"""
    a, b = TEMP_MIN, TEMP_MAX
    mid, white = HA_WHITE_TEMP, LAMP_WHITE_TEMP
    temp = min(b, max(a, int(temp)))
    if temp < white:
        new_temp = (mid - a) / (white - a) * temp - a * (mid - white) / (white - a)
    else:
        new_temp = (b - mid) / (b - white) * temp - b * (white - mid) / (b - white)
    return round(new_temp)


class YeelightBT(LightEntity, RestoreEntity):
    """Representation of a Yeelight bluetooth lamp."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_min_color_temp_kelvin = TEMP_MIN
    _attr_max_color_temp_kelvin = TEMP_MAX

    def __init__(self, lamp: Lamp, name: str) -> None:
        """Initialize the light."""
        self._dev = lamp
        self._device_name = name
        self._attr_unique_id = lamp.mac
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, lamp.mac)},
            connections={(CONNECTION_BLUETOOTH, lamp.mac)},
            name=name,
            manufacturer="Yeelight",
            model=lamp.model,
        )
        if lamp.model == MODEL_CANDELA:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.HS}
        self._update_task: asyncio.Task[None] | None = None
        self._unsub_reconnect: Any = None
        self._sw_version: str | None = None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(self._dev.add_callback_on_state_changed(self._status_cb))
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_interval_update, UPDATE_INTERVAL
            )
        )
        self.async_on_remove(self._cancel_tasks)

        if self._dev.model == MODEL_CANDELA and (
            last_state := await self.async_get_last_state()
        ):
            # The Candela rarely reports its state, start from the last known one
            brightness = last_state.attributes.get(ATTR_BRIGHTNESS)
            self._dev.restore_state(
                last_state.state == STATE_ON,
                self._to_lamp_brightness(brightness) if brightness else None,
            )
        self._schedule_update()

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Opt in to in-place entity_id changes (HA 2026.11+)."""
        if (parent := getattr(super(), "async_entity_id_changed", None)) is not None:
            parent(old_entity_id)

    @callback
    def _cancel_tasks(self) -> None:
        if self._unsub_reconnect is not None:
            self._unsub_reconnect()
            self._unsub_reconnect = None
        if self._update_task is not None:
            self._update_task.cancel()
            self._update_task = None

    @callback
    def _async_interval_update(self, _now: datetime) -> None:
        self._schedule_update()

    @callback
    def _schedule_update(self, _now: datetime | None = None) -> None:
        """Request the lamp state, unless a request is already running."""
        self._unsub_reconnect = None
        if self._update_task is not None and not self._update_task.done():
            return
        self._update_task = self.hass.async_create_background_task(
            self._async_update_lamp(), f"{DOMAIN} update {self._dev.mac}"
        )

    async def _async_update_lamp(self) -> None:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                await self._dev.update()
        except YeelightError as err:
            _LOGGER.debug("Updating %s failed: %s", self._dev.mac, err)
        except TimeoutError:
            _LOGGER.debug("Updating %s timed out", self._dev.mac)

    @callback
    def _status_cb(self) -> None:
        _LOGGER.debug("Got state notification from the lamp")
        if (
            not self._dev.is_connected
            and self._dev.available
            and self._dev.has_state_feedback
            and self._unsub_reconnect is None
        ):
            # Reconnect soon, so state changes made on the lamp are seen
            self._unsub_reconnect = async_call_later(
                self.hass, RECONNECT_DELAY, self._schedule_update
            )
        self._update_sw_version()
        self.async_write_ha_state()

    @callback
    def _update_sw_version(self) -> None:
        if not self._dev.versions:
            return
        sw_version = "-".join(map(str, self._dev.versions[1:4]))
        if sw_version == self._sw_version:
            return
        self._sw_version = sw_version
        if self.device_entry is not None:
            dr.async_get(self.hass).async_update_device(
                self.device_entry.id, sw_version=sw_version
            )

    @property
    def available(self) -> bool:
        if self._dev.contacted:
            return self._dev.available
        # Not contacted yet, allow control as long as HA can see the lamp
        return bluetooth.async_address_present(
            self.hass, self._dev.mac, connectable=True
        )

    @property
    def assumed_state(self) -> bool:
        """The state is assumed when the lamp sends no notifications."""
        return self._dev.has_state_feedback is False

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._dev.is_on

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        if self._dev.brightness <= 0:
            return None
        return value_to_brightness(BRIGHTNESS_SCALE, self._dev.brightness)

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode of the light."""
        if self._dev.model == MODEL_CANDELA:
            return ColorMode.BRIGHTNESS
        if self._dev.mode == self._dev.MODE_WHITE:
            return ColorMode.COLOR_TEMP
        return ColorMode.HS

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """
        Return the Hue and saturation color value.
        Lamp has rgb => we calculate hs
        """
        if self.color_mode != ColorMode.HS:
            return None
        return color_RGB_to_hs(*self._dev.color)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the CT color temperature in Kelvin."""
        if self.color_mode != ColorMode.COLOR_TEMP or not self._dev.temperature:
            return None
        return min(TEMP_MAX, max(TEMP_MIN, scale_temp_reversed(self._dev.temperature)))

    @staticmethod
    def _to_lamp_brightness(brightness: int) -> int:
        return min(
            100, max(1, math.ceil(brightness_to_value(BRIGHTNESS_SCALE, brightness)))
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        _LOGGER.debug("Trying to turn on. with ATTR:%s", kwargs)

        # First if brightness of dev to 0: turn off
        if ATTR_BRIGHTNESS in kwargs:
            if kwargs[ATTR_BRIGHTNESS] == 0:
                await self.async_turn_off()
                return
            brightness: int | None = self._to_lamp_brightness(kwargs[ATTR_BRIGHTNESS])
        else:
            brightness = self._dev.brightness or None

        try:
            await self._async_turn_on(kwargs, brightness)
        except YeelightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"name": self._device_name},
            ) from err
        finally:
            self.async_write_ha_state()

    async def _async_turn_on(
        self, kwargs: dict[str, Any], brightness: int | None
    ) -> None:
        # ATTR cannot be set while light is off, so turn it on first
        if not self._dev.is_on or self.assumed_state:
            await self._dev.turn_on()
            if not any(
                attr in kwargs
                for attr in (ATTR_HS_COLOR, ATTR_COLOR_TEMP_KELVIN, ATTR_BRIGHTNESS)
            ):
                return
            await asyncio.sleep(POWER_ON_DELAY)

        if ATTR_HS_COLOR in kwargs and ColorMode.HS in self.supported_color_modes:
            red, green, blue = color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
            await self._dev.set_color(red, green, blue, brightness=brightness or 100)
        elif (
            ATTR_COLOR_TEMP_KELVIN in kwargs
            and ColorMode.COLOR_TEMP in self.supported_color_modes
        ):
            await self._dev.set_temperature(
                scale_temp(kwargs[ATTR_COLOR_TEMP_KELVIN]),
                brightness=brightness or 100,
            )
        elif ATTR_BRIGHTNESS in kwargs and brightness is not None:
            await self._dev.set_brightness(brightness)
        else:
            return
        await asyncio.sleep(TRANSITION_DELAY)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        try:
            await self._dev.turn_off()
        except YeelightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"name": self._device_name},
            ) from err
        finally:
            self.async_write_ha_state()
