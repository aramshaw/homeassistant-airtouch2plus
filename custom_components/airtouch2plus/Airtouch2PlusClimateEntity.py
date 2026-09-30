from airtouch2.at2plus import At2PlusAircon, AcMode

from .conversions import (
    AT2PLUS_TO_HA_MODE,
    AT2PLUS_SETMODE_TO_HA_MODE,
    AT2PLUS_TO_HA_FAN_SPEED,
    HA_MODE_TO_AT2PLUS_SETMODE,
    HA_FAN_SPEED_TO_AT2PLUS
)
from .const import DOMAIN

import logging
from typing import final

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    UnitOfTemperature,
    ATTR_TEMPERATURE,
    PRECISION_WHOLE
)
from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

# The console normally echoes a set-point change within ~0.2 s and broadcasts
# status every few seconds. It has no application-level ACK, so confirm the
# change by watching for the echo and re-send once before giving up.
_PendingConfirmTimeoutSec = 5.0
_MaxConfirmAttempts = 2


@final
class Airtouch2PlusClimateEntity(ClimateEntity):
    """Representation of an AirTouch 2+ AC."""

    #
    # Entity attributes:
    #
    _attr_should_poll: bool = False

    #
    # ClimateEntity attributes:
    #
    _attr_max_temp: float = 35.0
    _attr_min_temp: float = 10.0
    _attr_precision: float = PRECISION_WHOLE
    _attr_target_temperature_step: float = 1.0
    _attr_temperature_unit: str = UnitOfTemperature.CELSIUS

    def __init__(self, at2plus_aircon: At2PlusAircon) -> None:
        """Initialize the climate device."""
        self._ac = at2plus_aircon
        # Unconfirmed (optimistic) target temperature from the last local
        # set_temperature call. Cleared when the console echoes it, or
        # reverted when confirmation times out.
        self._pending_setpoint: float | None = None
        self._pending_attempts: int = 0
        self._pending_timer: CALLBACK_TYPE | None = None

    #
    # Entity overrides:
    #

    @property
    def unique_id(self) -> str:
        """Return unique ID for this device."""
        return f"at2plus_ac_{self._ac.status.id}"

    @property
    def name(self):
        """Return the name of the climate device."""
        if self._ac.ability is not None:
            return f"AC {self._ac.ability.name}"
        else:
            return self.unique_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer="Polyaire",
            model="Airtouch 2+",
        )

    @property
    def available(self) -> bool:
        """Return False while the controller connection is down."""
        return self._ac._client.connected

    async def async_added_to_hass(self) -> None:
        """Call when entity is added."""
        # Add callback for when aircon receives new data
        # Removes callback on remove
        self.async_on_remove(self._ac.add_callback(self._handle_ac_update))
        self.async_on_remove(
            self._ac._client.add_connection_callback(self.async_write_ha_state)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Call when entity is removed."""
        self._cancel_pending_timer()

    #
    # ClimateEntity overrides:
    #

    @property
    def hvac_mode(self) -> str:
        """Return hvac operation ie. heat, cool mode."""
        if not self._ac.is_on():
            return HVACMode.OFF

        return AT2PLUS_TO_HA_MODE[self._ac.status.mode]

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available hvac operation modes."""
        modes: list[HVACMode] = [HVACMode.OFF]
        if self._ac.ability is not None:
            for mode in self._ac.ability.supported_modes:
                modes.append(AT2PLUS_SETMODE_TO_HA_MODE[mode])
        return modes

    @property
    def current_temperature(self) -> float:
        """Return the current temperature."""
        if self._ac.status.temperature is not None:
            return self._ac.status.temperature
        else:
            return 0

    @property
    def target_temperature(self) -> float:
        """Return the temperature we try to reach."""
        if self._pending_setpoint is not None:
            return self._pending_setpoint
        if self._ac.status.set_point is not None:
            return self._ac.status.set_point
        else:
            return 0

    @property
    def fan_mode(self) -> str:
        """Return fan mode of this AC."""
        return AT2PLUS_TO_HA_FAN_SPEED[self._ac.status.fan_speed]

    @property
    def fan_modes(self) -> list[str]:
        """Return the list of available fan modes."""
        if self._ac.ability is not None:
            return [AT2PLUS_TO_HA_FAN_SPEED[s] for s in self._ac.ability.supported_fan_speeds]
        return []

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        temp = float(kwargs.get(ATTR_TEMPERATURE, 0))
        self._set_pending_setpoint(temp, attempts=1)
        await self._ac.set_setpoint(temp)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        await self._ac.set_fan_speed(HA_FAN_SPEED_TO_AT2PLUS[fan_mode])

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            if self._ac.is_on():
                await self.async_turn_off()
        else:
            if not self._ac.is_on():
                await self.async_turn_on()
            await self._ac.set_mode(HA_MODE_TO_AT2PLUS_SETMODE[hvac_mode])

    async def async_turn_on(self):
        """Turn on."""
        await self._ac.turn_on()

    async def async_turn_off(self):
        """Turn off."""
        await self._ac.turn_off()

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        # TODO: Verify this with someone with the system.
        mode: AcMode = self._ac.status.mode

        if mode == AcMode.DRY:
            # only because there's no ClimateEntityFeature.NONE
            return ClimateEntityFeature.TARGET_TEMPERATURE

        if mode == AcMode.FAN:
            return ClimateEntityFeature.FAN_MODE

        return ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE

    #
    # Pending-setpoint confirmation
    #

    def _handle_ac_update(self) -> None:
        """Handle a console status update; confirm any pending set-point."""
        if (
            self._pending_setpoint is not None
            and self._ac.status.set_point == self._pending_setpoint
        ):
            self._clear_pending_setpoint()
        self.async_write_ha_state()

    def _set_pending_setpoint(self, value: float, attempts: int) -> None:
        """Record an unconfirmed target temperature and start its timer."""
        self._pending_setpoint = value
        self._pending_attempts = attempts
        self._cancel_pending_timer()
        self._pending_timer = async_call_later(
            self.hass, _PendingConfirmTimeoutSec, self._handle_confirm_timeout
        )
        self.async_write_ha_state()

    async def _handle_confirm_timeout(self, _now=None) -> None:
        """Console did not echo the pending set-point in time: retry or revert."""
        value = self._pending_setpoint
        self._pending_timer = None
        if value is None:
            return
        if self._pending_attempts < _MaxConfirmAttempts:
            _LOGGER.debug(
                "Set-point %s not confirmed after %.1fs (attempt %d); re-sending",
                value, _PendingConfirmTimeoutSec, self._pending_attempts
            )
            try:
                await self._ac.set_setpoint(value)
            except Exception:
                _LOGGER.warning("Retrying set-point %s failed", value, exc_info=True)
                self._clear_pending_setpoint()
                self.async_write_ha_state()
                return
            self._set_pending_setpoint(value, self._pending_attempts + 1)
        else:
            _LOGGER.warning(
                "Console never confirmed set-point %s; reverting to controller value %s",
                value, self._ac.status.set_point
            )
            self._clear_pending_setpoint()
            self.async_write_ha_state()

    def _clear_pending_setpoint(self) -> None:
        """Drop the pending set-point and its timer."""
        self._pending_setpoint = None
        self._pending_attempts = 0
        self._cancel_pending_timer()

    def _cancel_pending_timer(self) -> None:
        """Cancel the confirmation timer if running."""
        if self._pending_timer is not None:
            self._pending_timer()
            self._pending_timer = None
