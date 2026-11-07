"""Smart plug monitoring, thermal safety interlock, and auto-cooldown shutdown manager."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import CONF_COOLDOWN_TEMP, CONF_SMART_PLUG_ENTITY, DEFAULT_COOLDOWN_TEMP

if TYPE_CHECKING:
    from .coordinator import FlashForgeAD5XCoordinator

_LOGGER = logging.getLogger(__name__)


class SmartPlugPowerManager:
    """Manages an optional smart plug switch with print & thermal interlocks."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FlashForgeAD5XCoordinator,
    ) -> None:
        """Initialize the power manager."""
        self.hass = hass
        self.coordinator = coordinator
        self._unsub_state: Callable[[], None] | None = None
        self._unsub_boot_polls: list[Callable[[], None]] = []
        self._cooldown_task: asyncio.Task[None] | None = None
        self.waiting_for_cooldown: bool = False

    @property
    def smart_plug_entity(self) -> str | None:
        """Return configured smart plug switch entity_id if any."""
        val = self.coordinator.get_conf(CONF_SMART_PLUG_ENTITY)
        return str(val).strip() if val else None

    @property
    def has_smart_plug(self) -> bool:
        """Return True if a smart plug entity is configured."""
        return bool(self.smart_plug_entity)

    @property
    def cooldown_temp(self) -> float:
        """Return target nozzle cooldown threshold in °C."""
        stored = self.coordinator.persistent_state.get("cooldown_temp")
        if stored is not None:
            return float(stored)
        return float(self.coordinator.get_conf(CONF_COOLDOWN_TEMP, DEFAULT_COOLDOWN_TEMP))

    @property
    def auto_power_off_enabled(self) -> bool:
        """Return True if Auto Power-Off After Print & Cooldown is armed."""
        return bool(self.coordinator.persistent_state.get("auto_power_off", False))

    def is_plug_off(self) -> bool:
        """Return True ONLY if a smart plug is configured and explicitly in 'off' state."""
        entity_id = self.smart_plug_entity
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return False
        return state.state == STATE_OFF

    def is_plug_on(self) -> bool:
        """Return True if plug is on or if no plug is configured."""
        entity_id = self.smart_plug_entity
        if not entity_id:
            return True
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON

    async def async_setup(self) -> None:
        """Subscribe to smart plug state changes if configured."""
        self.async_unload()
        entity_id = self.smart_plug_entity
        if not entity_id:
            return

        @callback
        def _handle_plug_change(event: Event) -> None:
            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")
            if new_state is None:
                return
            old_val = old_state.state if old_state else None
            new_val = new_state.state
            if old_val == new_val:
                return

            if new_val == STATE_OFF:
                self._cancel_boot_polls()
                self._cancel_cooldown_task()
                self.hass.async_create_task(
                    self.coordinator.async_on_plug_power_changed(False)
                )
            elif new_val == STATE_ON:
                self.hass.async_create_task(
                    self.coordinator.async_on_plug_power_changed(True)
                )
                self._schedule_boot_polls()

        self._unsub_state = async_track_state_change_event(
            self.hass, [entity_id], _handle_plug_change
        )

    def _schedule_boot_polls(self) -> None:
        """Schedule staggered refreshes while the AD5X Linux board boots after power-on."""
        self._cancel_boot_polls()
        for delay in (4.0, 12.0, 25.0, 40.0):

            @callback
            def _wake(_now: object) -> None:
                self.hass.async_create_task(self.coordinator.async_request_refresh())

            self._unsub_boot_polls.append(async_call_later(self.hass, delay, _wake))

    def _cancel_boot_polls(self) -> None:
        while self._unsub_boot_polls:
            unsub = self._unsub_boot_polls.pop()
            unsub()

    def _cancel_cooldown_task(self) -> None:
        if self._cooldown_task and not self._cooldown_task.done():
            self._cooldown_task.cancel()
        self._cooldown_task = None
        self.waiting_for_cooldown = False

    async def async_set_auto_power_off(self, enabled: bool) -> None:
        """Enable or disable Auto Power-Off After Print & Cooldown."""
        if not enabled:
            self._cancel_cooldown_task()
        await self.coordinator.async_update_persistent_state({"auto_power_off": bool(enabled)})

    async def async_set_cooldown_temp(self, temp: float) -> None:
        """Update safety cooldown temperature threshold (°C)."""
        await self.coordinator.async_update_persistent_state(
            {"cooldown_temp": round(float(temp), 1)}
        )

    async def async_turn_on(self) -> None:
        """Turn on the smart plug."""
        entity_id = self.smart_plug_entity
        if not entity_id:
            raise HomeAssistantError("No smart plug switch is configured for this printer.")
        await self.hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )

    async def async_safe_turn_off(self, force: bool = False) -> None:
        """Safely turn off the smart plug, enforcing print & hotend cooldown interlocks."""
        entity_id = self.smart_plug_entity
        if not entity_id:
            raise HomeAssistantError("No smart plug switch is configured for this printer.")

        if not force and self.coordinator.printer_online:
            if self.coordinator.is_printing or self.coordinator.printer_status in ("paused", "pausing"):
                raise HomeAssistantError(
                    "Refusing to cut power while a print job is active or paused. Cancel the print first."
                )
            nozzle_temp = self.coordinator.extruder_temp
            threshold = self.cooldown_temp
            if nozzle_temp >= threshold:
                raise HomeAssistantError(
                    f"Nozzle is still hot ({nozzle_temp:.1f}°C >= {threshold:.0f}°C). "
                    "Wait for the hotend fan to cool the nozzle before cutting power."
                )

        self._cancel_cooldown_task()
        await self.hass.services.async_call(
            "switch", "turn_off", {"entity_id": entity_id}, blocking=True
        )

    def on_print_completed(self) -> None:
        """Start cooldown watch when a print finishes if Auto Power-Off is enabled."""
        if not self.has_smart_plug or not self.auto_power_off_enabled:
            return
        self._cancel_cooldown_task()
        self.waiting_for_cooldown = True
        self._cooldown_task = self.hass.async_create_task(self._async_cooldown_and_shutdown())

    def on_print_started(self) -> None:
        """Abort any pending auto-shutdown if a new print starts."""
        if self.waiting_for_cooldown:
            self._cancel_cooldown_task()

    async def _async_cooldown_and_shutdown(self) -> None:
        """Wait for extruder temperature to stay below threshold for 60s, then power off."""
        _LOGGER.info(
            "AD5X print completed with Auto Power-Off armed; waiting for nozzle < %.1f°C",
            self.cooldown_temp,
        )
        below_since: float | None = None
        loop = asyncio.get_running_loop()
        try:
            while self.auto_power_off_enabled and self.has_smart_plug:
                await asyncio.sleep(10)
                if self.coordinator.is_printing:
                    _LOGGER.info("New print started; aborting Auto Power-Off.")
                    return
                if not self.coordinator.printer_online:
                    return

                temp = self.coordinator.extruder_temp
                if temp < self.cooldown_temp:
                    if below_since is None:
                        below_since = loop.time()
                    elif (loop.time() - below_since) >= 60.0:
                        _LOGGER.info(
                            "Nozzle cooled to %.1f°C (< %.1f°C) for 60s; powering off smart plug.",
                            temp,
                            self.cooldown_temp,
                        )
                        await self.async_safe_turn_off(force=False)
                        await self.coordinator.async_send_notification(
                            title="🔌 AD5X Powered Off Safely",
                            message=(
                                f"Print completed and nozzle cooled to {temp:.1f}°C. "
                                "Printer smart plug has been turned off."
                            ),
                            tag="ad5x_power_off",
                        )
                        return
                else:
                    below_since = None
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Auto Power-Off task encountered an error: %s", err)
        finally:
            self.waiting_for_cooldown = False

    def async_unload(self) -> None:
        """Clean up listeners and tasks."""
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        self._cancel_boot_polls()
        self._cancel_cooldown_task()
