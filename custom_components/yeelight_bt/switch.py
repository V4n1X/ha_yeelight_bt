"""Switch platform for the Yeelight bluetooth night light."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YeelightBTConfigEntry
from .entity import YeelightBTNightLightEntity, async_remove_night_light_entities
from .yeelightbt import MODEL_CANDELA

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YeelightBTConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the night-light switch (Bedside only)."""
    if entry.runtime_data.model == MODEL_CANDELA:
        # The Candela has no night-light window in the legacy protocol.
        async_remove_night_light_entities(hass, entry.runtime_data)
        return
    async_add_entities([YeelightBTNightLightSwitch(entry)])


class YeelightBTNightLightSwitch(YeelightBTNightLightEntity, SwitchEntity):
    """Switch the lamp's night-light window on or off."""

    _attr_translation_key = "night_light"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: YeelightBTConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(entry, "night_light")

    @property
    def is_on(self) -> bool:
        """Whether the night light is enabled."""
        night_light = self.night_light
        return night_light is not None and night_light.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the night light."""
        await self._async_set_night_light(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the night light."""
        await self._async_set_night_light(enabled=False)
