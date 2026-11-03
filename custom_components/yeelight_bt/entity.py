"""Shared entity base for the Yeelight bluetooth night-light entities."""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import YeelightBTConfigEntry
from .const import DOMAIN
from .yeelightbt import Lamp, NightLight, YeelightError

# Unique-ID suffixes of the night-light entities (Bedside only)
NIGHT_LIGHT_PLATFORMS = (Platform.SWITCH, Platform.TIME)
NIGHT_LIGHT_UNIQUE_IDS = (
    "night_light",
    "night_light_start",
    "night_light_end",
)


@callback
def async_remove_night_light_entities(hass: HomeAssistant, lamp: Lamp) -> None:
    """Remove night-light entities left over by a change to the Candela model.

    The entities exist only for the Bedside. Without this they would stay in the
    registry as unavailable leftovers after a reconfigure or a model found later.
    """
    registry = er.async_get(hass)
    for suffix in NIGHT_LIGHT_UNIQUE_IDS:
        unique_id = f"{lamp.mac}_{suffix}"
        for platform in NIGHT_LIGHT_PLATFORMS:
            if entity_id := registry.async_get_entity_id(platform, DOMAIN, unique_id):
                registry.async_remove(entity_id)


class YeelightBTNightLightEntity(Entity):
    """Base for entities that change the lamp's night-light window.

    The night-light state is read from the lamp when it connects. Without
    state feedback the entities stay unavailable: every write sends all fields,
    so unknown times would be overwritten.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: YeelightBTConfigEntry, unique_id_suffix: str) -> None:
        """Initialize the entity."""
        lamp = entry.runtime_data
        self._entry = entry
        self._lamp: Lamp = lamp
        self._attr_unique_id = f"{lamp.mac}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, lamp.mac)},
            connections={(CONNECTION_BLUETOOTH, lamp.mac)},
            name=entry.title,
            manufacturer="Yeelight",
            model=lamp.model,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the lamp state."""
        await super().async_added_to_hass()
        self.async_on_remove(self._lamp.add_callback_on_state_changed(self._status_cb))

    @callback
    def _status_cb(self) -> None:
        self.async_write_ha_state()

    @property
    def night_light(self) -> NightLight | None:
        """The night-light state read from the lamp, None before it arrives."""
        return self._lamp.night_light

    @property
    def available(self) -> bool:
        """Available only when the lamp reports state and its window is known."""
        return (
            self._lamp.available
            and self._lamp.has_state_feedback is not False
            and self._lamp.night_light is not None
        )

    def _command_failed(self) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"name": self._entry.title},
        )

    async def _async_set_night_light(
        self,
        enabled: bool | None = None,
        start: dt_time | None = None,
        end: dt_time | None = None,
    ) -> None:
        """Change the window, reporting lamp errors as Home Assistant errors."""
        try:
            await self._lamp.set_night_light(enabled=enabled, start=start, end=end)
        except YeelightError as err:
            raise self._command_failed() from err
        finally:
            self.async_write_ha_state()
