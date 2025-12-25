"""Select platform for the AirTouch 2+ integration."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from airtouch2.at2plus.At2PlusClient import At2PlusClient

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AirTouch 2+ select entities."""
    client: At2PlusClient = hass.data[DOMAIN][config_entry.entry_id]

    # We only need one select entity for the system
    async_add_entities([AirtouchFavouriteSelect(client)])


class AirtouchFavouriteSelect(SelectEntity):
    """A select entity to represent the AirTouch 2+ favourite scenes."""

    _attr_should_poll = False

    def __init__(self, client: At2PlusClient) -> None:
        """Initialize the favourite select entity."""
        self._client = client
        self._attr_name = "Favourite"
        # Get the ID of the first discovered air conditioner to link this entity to it.
        # The ID is the key in the aircons_by_id dictionary.
        self._ac_id = next(iter(self._client.aircons_by_id))
        self._attr_unique_id = f"{self._ac_id}-favourites"
        self._update_attributes()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to link this entity to the main AC device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._ac_id)},
            # Other info like name, manufacturer will be inherited from the main device
        )

    @callback
    def _handle_update(self) -> None:
        """Handle updates from the client."""
        self._update_attributes()
        self.async_write_ha_state()

    def _update_attributes(self) -> None:
        """Update the entity's state attributes."""
        self._attr_options = [fav.name for fav in self._client.favourites]

        active_id = self._client.active_favourite_id
        if active_id is not None:
            active_fav = next(
                (fav for fav in self._client.favourites if fav.id == active_id), None
            )
            self._attr_current_option = active_fav.name if active_fav else None
        else:
            self._attr_current_option = None

    async def async_select_option(self, option: str) -> None:
        """Change the selected favourite."""
        selected_fav = next(
            (fav for fav in self._client.favourites if fav.name == option), None
        )
        if selected_fav:
            _LOGGER.debug(f"User selected favourite: {option} (ID: {selected_fav.id})")
            # The next step will be to implement this method in the client
            # await self._client.set_favourite(selected_fav.id)
        else:
            _LOGGER.warning(f"Could not find favourite with name: {option}")

    async def async_added_to_hass(self) -> None:
        """Register callbacks when the entity is added."""
        self.async_on_remove(self._client.add_favourite_callback(self._handle_update))

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return len(self._client.favourites) > 0
