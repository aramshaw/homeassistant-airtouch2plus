"""Select platform for the AirTouch 2+ integration (favourite scenes)."""
from __future__ import annotations

import logging

from airtouch2.at2plus import At2PlusClient

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AirTouch 2+ favourite select entity."""
    client: At2PlusClient = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([AirTouch2PlusFavouriteSelect(client)])


class AirTouch2PlusFavouriteSelect(SelectEntity):
    """Selects the active AirTouch 2+ favourite (scene)."""

    _attr_should_poll = False
    _attr_icon = "mdi:star"

    def __init__(self, client: At2PlusClient) -> None:
        self._client = client
        # Attach to the (first) AC device, matching the climate entity's device id.
        self._ac_id = next(iter(client.aircons_by_id))
        self._attr_unique_id = f"at2plus_ac_{self._ac_id}_favourite"
        self._attr_name = "Favourite"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, f"at2plus_ac_{self._ac_id}")})

    @property
    def options(self) -> list[str]:
        return [favourite.name for favourite in self._client.favourites]

    @property
    def current_option(self) -> str | None:
        active_id = self._client.active_favourite_id
        if active_id is None:
            return None
        return next(
            (f.name for f in self._client.favourites if f.id == active_id), None
        )

    @property
    def available(self) -> bool:
        return len(self._client.favourites) > 0

    async def async_select_option(self, option: str) -> None:
        favourite = next(
            (f for f in self._client.favourites if f.name == option), None
        )
        if favourite is None:
            _LOGGER.warning("Unknown favourite selected: %s", option)
            return
        await self._client.activate_favourite(favourite.id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._client.add_favourite_callback(self.async_write_ha_state)
        )
