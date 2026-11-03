"""Time platform for the Yeelight bluetooth night-light window."""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
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
    """Set up the night-light start and end times (Bedside only)."""
    if entry.runtime_data.model == MODEL_CANDELA:
        # The Candela has no night-light window in the legacy protocol.
        async_remove_night_light_entities(hass, entry.runtime_data)
        return
    async_add_entities(
        [
            YeelightBTNightLightTime(entry, "night_light_start", start=True),
            YeelightBTNightLightTime(entry, "night_light_end", start=False),
        ]
    )


class YeelightBTNightLightTime(YeelightBTNightLightEntity, TimeEntity):
    """The start or end time of the night-light window.

    The window may cross midnight (21:00-07:00), the lamp compares the clock
    against both times.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, entry: YeelightBTConfigEntry, translation_key: str, *, start: bool
    ) -> None:
        """Initialize the time entity."""
        super().__init__(entry, translation_key)
        self._attr_translation_key = translation_key
        self._is_start = start

    @property
    def native_value(self) -> dt_time | None:
        """The start or end time reported by the lamp."""
        night_light = self._lamp.night_light
        if night_light is None:
            return None
        return night_light.start if self._is_start else night_light.end

    async def async_set_value(self, value: dt_time) -> None:
        """Change the start or end time, keeping the other fields."""
        # The frame carries only hours and minutes; showing the seconds the
        # user picked would not match the lamp.
        value = value.replace(second=0, microsecond=0)
        if self._is_start:
            await self._async_set_night_light(start=value)
        else:
            await self._async_set_night_light(end=value)
