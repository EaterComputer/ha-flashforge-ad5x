"""Obico ML Supervisor App RAM lifecycle, print monitoring, and actionable alert manager."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING, Callable

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    ACTION_OBICO_CANCEL,
    ACTION_OBICO_PAUSE,
    ACTION_OBICO_RESUME,
    ACTION_OBICO_SNOOZE,
    CONF_ENABLE_OBICO,
    CONF_OBICO_ADDON_SLUG,
    CONF_OBICO_CAMERA_ENTITY,
    CONF_OBICO_CONFIDENCE_SENSOR,
    CONF_OBICO_ERROR_BINARY_SENSOR,
    CONF_OBICO_STOP_DELAY_SEC,
    CONF_OBICO_SWITCH_ENTITY,
    DEFAULT_OBICO_CONFIDENCE_THRESHOLD,
    DEFAULT_OBICO_COOLDOWN_MIN,
    DEFAULT_OBICO_STOP_DELAY_SEC,
)

if TYPE_CHECKING:
    from .coordinator import FlashForgeAD5XCoordinator

_LOGGER = logging.getLogger(__name__)


class ObicoManager:
    """Manages optional Obico ML add-on start/stop, detection switch, and failure alerts."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FlashForgeAD5XCoordinator,
    ) -> None:
        """Initialize Obico manager."""
        self.hass = hass
        self.coordinator = coordinator
        self._unsub_entities: Callable[[], None] | None = None
        self._unsub_actions: Callable[[], None] | None = None
        self._unsub_stop_timer: Callable[[], None] | None = None
        self._switch_sync_task: asyncio.Task[None] | None = None
        self._last_alert_monotonic: float = 0.0
        self._snoozed_until_monotonic: float = 0.0
        self._addon_requested_running: bool | None = None

    @property
    def enabled(self) -> bool:
        """Return True if Obico ML integration module is enabled."""
        return bool(self.coordinator.get_conf(CONF_ENABLE_OBICO, False))

    @property
    def addon_slug(self) -> str | None:
        val = self.coordinator.get_conf(CONF_OBICO_ADDON_SLUG)
        return str(val).strip() if val else None

    @property
    def camera_entity(self) -> str | None:
        val = self.coordinator.get_conf(CONF_OBICO_CAMERA_ENTITY)
        return str(val).strip() if val else None

    @property
    def switch_entity(self) -> str | None:
        val = self.coordinator.get_conf(CONF_OBICO_SWITCH_ENTITY)
        return str(val).strip() if val else None

    @property
    def error_binary_sensor(self) -> str | None:
        val = self.coordinator.get_conf(CONF_OBICO_ERROR_BINARY_SENSOR)
        return str(val).strip() if val else None

    @property
    def confidence_sensor(self) -> str | None:
        val = self.coordinator.get_conf(CONF_OBICO_CONFIDENCE_SENSOR)
        return str(val).strip() if val else None

    @property
    def notify_on_alert(self) -> bool:
        return bool(self.coordinator.persistent_state.get("obico_notify", True))

    @property
    def auto_pause_on_alert(self) -> bool:
        return bool(self.coordinator.persistent_state.get("obico_auto_pause", False))

    @property
    def confidence_threshold(self) -> float:
        return float(
            self.coordinator.persistent_state.get(
                "obico_confidence_threshold", DEFAULT_OBICO_CONFIDENCE_THRESHOLD
            )
        )

    @property
    def cooldown_minutes(self) -> float:
        return float(
            self.coordinator.persistent_state.get(
                "obico_cooldown_min", DEFAULT_OBICO_COOLDOWN_MIN
            )
        )

    async def async_setup(self) -> None:
        """Register state and mobile action listeners for Obico ML."""
        self.async_unload()
        if not self.enabled:
            return

        watched = [
            e
            for e in (self.error_binary_sensor, self.confidence_sensor)
            if e
        ]
        if watched:
            self._unsub_entities = async_track_state_change_event(
                self.hass, watched, self._handle_detection_state_event
            )

        self._unsub_actions = self.hass.bus.async_listen(
            "mobile_app_notification_action", self._handle_notification_action
        )

        # Align initial state with current printer status
        if self.coordinator.printer_online or (
            self.coordinator.power_manager.has_smart_plug
            and self.coordinator.power_manager.is_plug_on()
        ):
            await self.async_on_printer_online()

    @callback
    def _handle_detection_state_event(self, _event: Event) -> None:
        """Evaluate detection thresholds whenever error binary_sensor or confidence updates."""
        self.hass.async_create_task(self.async_evaluate_detection())

    @callback
    def _handle_notification_action(self, event: Event) -> None:
        """Respond to actionable mobile push buttons (Pause, Resume, Cancel, Snooze)."""
        action = str(event.data.get("action") or "")
        entry_suffix = f"_{self.coordinator.entry.entry_id}"
        if action.endswith(entry_suffix):
            base_action = action[: -len(entry_suffix)]
        else:
            base_action = action

        if base_action == ACTION_OBICO_PAUSE:
            self.hass.async_create_task(self.coordinator.api.async_job_control("pause"))
        elif base_action == ACTION_OBICO_RESUME:
            self.hass.async_create_task(self.coordinator.api.async_job_control("continue"))
        elif base_action == ACTION_OBICO_CANCEL:
            self.hass.async_create_task(self.coordinator.api.async_job_control("cancel"))
        elif base_action == ACTION_OBICO_SNOOZE:
            self._snoozed_until_monotonic = time.monotonic() + 600.0
            _LOGGER.info("Obico ML alerts snoozed for 10 minutes by user action.")

    async def _async_set_supervisor_addon(self, start: bool) -> None:
        """Start or stop the Obico ML Supervisor App (Add-on) to manage RAM usage."""
        slug = self.addon_slug
        if not slug or self._addon_requested_running is start:
            return

        service_candidates = (
            ("app_start", "addon_start") if start else ("app_stop", "addon_stop")
        )
        called = False
        for svc in service_candidates:
            if self.hass.services.has_service("hassio", svc):
                try:
                    await self.hass.services.async_call(
                        "hassio", svc, {"addon": slug}, blocking=False
                    )
                    self._addon_requested_running = start
                    called = True
                    _LOGGER.info(
                        "Requested Supervisor hassio.%s for Obico ML (%s)", svc, slug
                    )
                    break
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Failed calling hassio.%s(%s): %s", svc, slug, err)
        if not called:
            _LOGGER.debug(
                "Supervisor hassio start/stop service not available in this HA installation."
            )

    def _cancel_stop_timer(self) -> None:
        if self._unsub_stop_timer:
            self._unsub_stop_timer()
            self._unsub_stop_timer = None

    async def async_on_printer_online(self) -> None:
        """Ensure Obico ML App is started when printer turns on / comes online."""
        if not self.enabled:
            return
        self._cancel_stop_timer()
        await self._async_set_supervisor_addon(True)

    async def async_on_printer_offline(self, immediate: bool = False) -> None:
        """Turn off Obico communication switch and stop Supervisor App to free RAM."""
        if not self.enabled:
            return
        await self.async_set_communication_switch(False)
        if not self.addon_slug:
            return

        self._cancel_stop_timer()
        delay = 5 if immediate else int(
            self.coordinator.get_conf(CONF_OBICO_STOP_DELAY_SEC, DEFAULT_OBICO_STOP_DELAY_SEC)
        )

        @callback
        def _stop_later(_now: object) -> None:
            self._unsub_stop_timer = None
            if not self.coordinator.printer_online:
                self.hass.async_create_task(self._async_set_supervisor_addon(False))

        self._unsub_stop_timer = async_call_later(self.hass, float(delay), _stop_later)

    async def async_on_print_state_changed(self, is_printing: bool) -> None:
        """Toggle Obico ML detection API switch on when printing and off when idle."""
        if not self.enabled:
            return
        if is_printing:
            await self.async_on_printer_online()
            await self.async_set_communication_switch(True, retry_until_ready=True)
        else:
            await self.async_set_communication_switch(False)

    async def async_set_communication_switch(
        self, turn_on: bool, retry_until_ready: bool = False
    ) -> None:
        """Toggle Obico ML communication switch, waiting for add-on boot if needed."""
        sw = self.switch_entity
        if not sw:
            return

        if self._switch_sync_task and not self._switch_sync_task.done():
            self._switch_sync_task.cancel()

        async def _sync() -> None:
            attempts = 10 if (turn_on and retry_until_ready) else 1
            for attempt in range(attempts):
                state = self.hass.states.get(sw)
                if state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    desired = "on" if turn_on else "off"
                    if state.state != desired:
                        await self.hass.services.async_call(
                            "switch",
                            "turn_on" if turn_on else "turn_off",
                            {"entity_id": sw},
                            blocking=False,
                        )
                    return
                if attempt < attempts - 1:
                    await asyncio.sleep(5)

        self._switch_sync_task = self.hass.async_create_task(_sync())

    async def async_evaluate_detection(self) -> None:
        """Check Obico binary sensor / confidence score and trigger pause & alert if needed."""
        if not self.enabled or not self.coordinator.is_printing:
            return

        now = time.monotonic()
        if now < self._snoozed_until_monotonic:
            return
        cooldown_sec = max(30.0, self.cooldown_minutes * 60.0)
        if self._last_alert_monotonic and (now - self._last_alert_monotonic) < cooldown_sec:
            return

        err_detected = False
        if self.error_binary_sensor:
            bs = self.hass.states.get(self.error_binary_sensor)
            if bs and bs.state == STATE_ON:
                err_detected = True

        confidence_val: float | None = None
        if self.confidence_sensor:
            cs = self.hass.states.get(self.confidence_sensor)
            if cs and cs.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
                try:
                    raw_conf = float(cs.state)
                    # Normalize 0.0-1.0 vs 0-100 scales
                    confidence_val = raw_conf * 100.0 if 0.0 < raw_conf <= 1.0 and self.confidence_threshold > 1.0 else raw_conf
                except ValueError:
                    pass

        threshold_hit = (
            confidence_val is not None
            and self.confidence_threshold > 0
            and confidence_val >= self.confidence_threshold
        )

        if not (err_detected or threshold_hit):
            return

        self._last_alert_monotonic = now
        paused_automatically = False

        if self.auto_pause_on_alert:
            try:
                await self.coordinator.api.async_job_control("pause")
                paused_automatically = True
                _LOGGER.warning(
                    "Obico ML detected print failure (confidence=%s); print auto-paused.",
                    confidence_val,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to auto-pause AD5X on Obico alert: %s", err)

        if not self.notify_on_alert:
            return

        image_url: str | None = None
        if self.camera_entity:
            try:
                from homeassistant.components.camera import async_get_image

                img = await async_get_image(self.hass, self.camera_entity)
                if img and img.content:
                    www_dir = Path(self.hass.config.path("www"))
                    out_file = www_dir / f"ad5x_obico_{self.coordinator.entry.entry_id}.jpg"
                    await asyncio.to_thread(www_dir.mkdir, parents=True, exist_ok=True)
                    await asyncio.to_thread(out_file.write_bytes, img.content)
                    image_url = f"/local/{out_file.name}?t={int(time.time())}"
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Could not snapshot Obico annotated camera: %s", err)

        conf_str = f" ({confidence_val:.0f}% confidence)" if confidence_val is not None else ""
        entry_suffix = f"_{self.coordinator.entry.entry_id}"
        actions = []
        if paused_automatically:
            actions.append({"action": f"{ACTION_OBICO_RESUME}{entry_suffix}", "title": "Resume Print"})
        else:
            actions.append({"action": f"{ACTION_OBICO_PAUSE}{entry_suffix}", "title": "Pause Print"})
        actions.append({"action": f"{ACTION_OBICO_CANCEL}{entry_suffix}", "title": "Cancel Print"})
        actions.append({"action": f"{ACTION_OBICO_SNOOZE}{entry_suffix}", "title": "Snooze 10m"})

        title = (
            f"🍝 Obico Auto-Paused Print{conf_str}"
            if paused_automatically
            else f"⚠️ Obico Print Issue Detected{conf_str}"
        )
        msg = (
            f"Potential print failure detected on {self.coordinator.printer_name}{conf_str}. "
            + ("The print has been paused automatically." if paused_automatically else "Review camera feed.")
        )

        await self.coordinator.async_send_notification(
            title=title,
            message=msg,
            tag=f"ad5x_obico_{self.coordinator.entry.entry_id}",
            image=image_url,
            actions=actions,
        )

    def async_unload(self) -> None:
        """Remove all listeners and background tasks."""
        if self._unsub_entities:
            self._unsub_entities()
            self._unsub_entities = None
        if self._unsub_actions:
            self._unsub_actions()
            self._unsub_actions = None
        self._cancel_stop_timer()
        if self._switch_sync_task and not self._switch_sync_task.done():
            self._switch_sync_task.cancel()
        self._switch_sync_task = None
