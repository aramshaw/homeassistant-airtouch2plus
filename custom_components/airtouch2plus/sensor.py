"""Sensor platform for the AirTouch 2+ integration (console temperatures)."""
from __future__ import annotations

import logging

from airtouch2.at2plus import At2PlusClient

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AirTouch 2+ console-temperature sensors.

    Console temperatures arrive in the (unsolicited) 0x2B status, which may not
    have been received yet at setup, so sensors are added as consoles appear.
    """
    client: At2PlusClient = hass.data[DOMAIN][config_entry.entry_id]
    added: set[int] = set()

    @callback
    def discover_consoles() -> None:
        new = [i for i in client.console_temperatures if i not in added]
        if not new:
            return
        added.update(new)
        async_add_entities(
            AirTouch2PlusConsoleTemperature(client, index) for index in new
        )

    discover_consoles()
    config_entry.async_on_unload(
        client.add_console_temperature_callback(discover_consoles)
    )


class AirTouch2PlusConsoleTemperature(SensorEntity):
    """The temperature reported by an AirTouch 2+ wall console."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, client: At2PlusClient, console_index: int) -> None:
        self._client = client
        self._index = console_index
        # Attach to the (first) AC device, matching the climate entity's device id.
        self._ac_id = next(iter(client.aircons_by_id))
        self._attr_unique_id = f"at2plus_console_{console_index}_temperature"
        self._attr_name = f"Console {console_index + 1} Temperature"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, f"at2plus_ac_{self._ac_id}")})

    @property
    def native_value(self) -> float | None:
        return self._client.console_temperatures.get(self._index)

    @property
    def available(self) -> bool:
        return self._client.connected and self._index in self._client.console_temperatures

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._client.add_console_temperature_callback(self.async_write_ha_state)
        )
        self.async_on_remove(
            self._client.add_connection_callback(self.async_write_ha_state)
        )
