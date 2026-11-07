"""DataUpdateCoordinator and state orchestration for FlashForge AD5X."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
import secrets
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    FlashForgeAD5XApiClient,
    FlashForgeAuthError,
    FlashForgeConnectionError,
    FlashForgeError,
)
from .const import (
    CONF_CHECK_CODE,
    CONF_ENABLE_CAMERA,
    CONF_ENABLE_FILES,
    CONF_ENABLE_FIRMWARE_CHECK,
    CONF_ENABLE_SPOOLMAN,
    CONF_HOST,
    CONF_NAME,
    CONF_NATIVE_STREAM_OVERRIDE,
    CONF_NOTIFY_TARGETS,
    CONF_PURGE_MM_PER_SWAP,
    CONF_SERIAL_NUMBER,
    CONF_SNAP_PRINTER_COLORS,
    CONF_SPOOLMAN_LOCATION_PREFIX,
    CONF_SPOOLMAN_URL,
    DEFAULT_NAME,
    DEFAULT_PURGE_MM_PER_SWAP,
    DEFAULT_SPOOLMAN_LOCATION_PREFIX,
    DOMAIN,
    PICKER_API_BASE,
    POLL_INTERVAL_IDLE,
    POLL_INTERVAL_OFFLINE,
    POLL_INTERVAL_PRINTING,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    normalize_hex_color,
    snap_to_ad5x_palette,
)
from .discovery import async_discover_printers
from .obico import ObicoManager
from .power import SmartPlugPowerManager
from .spoolman import SpoolmanClient, SpoolmanError, normalize_tag_uid
from .timelapse import TimelapseManager

_LOGGER = logging.getLogger(__name__)


class FlashForgeAD5XCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Central coordinator managing AD5X polling, IFS/Spoolman sync, Power, Obico, and Timelapse."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        session = async_get_clientsession(hass)

        self.api = FlashForgeAD5XApiClient(
            session=session,
            host=str(self.get_conf(CONF_HOST, "")),
            serial_number=str(self.get_conf(CONF_SERIAL_NUMBER, "")),
            check_code=str(self.get_conf(CONF_CHECK_CODE, "")),
        )

        self.spoolman: SpoolmanClient | None = None
        if self.spoolman_enabled and self.get_conf(CONF_SPOOLMAN_URL):
            self.spoolman = SpoolmanClient(
                session=session,
                base_url=str(self.get_conf(CONF_SPOOLMAN_URL)),
            )

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry.entry_id}"
        )
        self.persistent_state: dict[str, Any] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=POLL_INTERVAL_IDLE),
        )

        self.power_manager = SmartPlugPowerManager(hass, self)
        self.obico_manager = ObicoManager(hass, self)
        self.timelapse_manager = TimelapseManager(hass, self)

        self.printer_online: bool = False
        self._logged_offline: bool = False
        self._first_online_poll_done: bool = False

        # Cached auxiliary states
        self.spools_cache: dict[int, dict[str, Any]] = {}
        self.gcode_files: list[str] = []
        self.gcode_details: list[dict[str, Any]] = []
        self.selected_file: str = ""
        self.thumbnail_bytes: bytes | None = None
        self.thumbnail_filename: str = ""
        self.last_gcode_response: str = ""
        self.firmware_info: dict[str, Any] = {"up_to_date": True, "packages": {}, "release_notes": ""}
        self.manual_extruder_target: int = 0
        self.manual_bed_target: int = 0
        self.jog_step_mm: float = 10.0

        # IFS & Spoolman tracking state
        self._prev_slot_has_filament: dict[int, bool] = {}
        self.pending_filament_slot: int | None = None
        self.pending_filament_timestamp: float = 0.0
        self.active_picker_slot: int | None = None
        self.last_scanned_tag: dict[str, Any] | None = None
        self._unsub_tag_scanned: Any = None

        # Active print tracking
        self._prev_status: str = "offline"
        self._prev_layer: int = 0
        self._print_active_slot: int = 0
        self._prev_cumulative_m: float | None = None
        self._prev_est_progress_mm: float = 0.0
        self._pending_usage_mm: dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
        self._last_usage_flush_mono: float = 0.0
        self._last_aux_refresh_mono: float = 0.0
        self._last_ip_rescan_mono: float = 0.0

    def get_conf(self, key: str, default: Any = None) -> Any:
        """Read config value from entry.options first, falling back to entry.data."""
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    @property
    def printer_name(self) -> str:
        """Return display name for the printer."""
        detail_name = (self.data or {}).get("detail", {}).get("name")
        if detail_name:
            return str(detail_name)
        return str(self.get_conf(CONF_NAME, DEFAULT_NAME))

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant DeviceInfo grouping all AD5X entities."""
        detail = (self.data or {}).get("detail") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, str(self.get_conf(CONF_SERIAL_NUMBER, self.entry.entry_id)))},
            name=str(self.get_conf(CONF_NAME, DEFAULT_NAME)),
            manufacturer="FlashForge",
            model="Adventurer 5X (AD5X)",
            sw_version=detail.get("firmwareVersion") or None,
            configuration_url=f"http://{self.api.host}:{self.api.http_port}",
        )

    @property
    def spoolman_enabled(self) -> bool:
        return bool(self.get_conf(CONF_ENABLE_SPOOLMAN, False) and self.get_conf(CONF_SPOOLMAN_URL))

    @property
    def files_enabled(self) -> bool:
        return bool(self.get_conf(CONF_ENABLE_FILES, True))

    @property
    def firmware_check_enabled(self) -> bool:
        return bool(self.get_conf(CONF_ENABLE_FIRMWARE_CHECK, False))

    @property
    def snap_printer_colors(self) -> bool:
        if "snap_printer_colors" in self.persistent_state:
            return bool(self.persistent_state["snap_printer_colors"])
        return bool(self.get_conf(CONF_SNAP_PRINTER_COLORS, True))

    @property
    def purge_mm_per_swap(self) -> float:
        if "purge_mm_per_swap" in self.persistent_state:
            return float(self.persistent_state["purge_mm_per_swap"])
        return float(self.get_conf(CONF_PURGE_MM_PER_SWAP, DEFAULT_PURGE_MM_PER_SWAP))

    @property
    def printer_status(self) -> str:
        if not self.printer_online:
            return "offline"
        raw = str((self.data or {}).get("detail", {}).get("status") or "unknown").lower()
        return raw

    @property
    def is_printing(self) -> bool:
        return self.printer_online and self.printer_status in ("printing", "heating", "calibrating")

    @property
    def extruder_temp(self) -> float:
        detail = (self.data or {}).get("detail") or {}
        try:
            return float(detail.get("rightTemp") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def bed_temp(self) -> float:
        detail = (self.data or {}).get("detail") or {}
        try:
            return float(detail.get("platTemp") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def native_camera_stream_url(self) -> str | None:
        """Return native AD5X stream URL (override or auto-detected from `/detail`)."""
        override = str(self.get_conf(CONF_NATIVE_STREAM_OVERRIDE, "") or "").strip()
        if override:
            return override
        detail = (self.data or {}).get("detail") or {}
        url = str(detail.get("cameraStreamUrl") or "").strip()
        if url and url.lower() not in ("none", "null", "unknown"):
            return url
        if detail.get("camera") == 1 and self.api.host:
            return f"http://{self.api.host}:8080/?action=stream"
        return None

    @property
    def picker_token(self) -> str:
        """Return secret token authorizing notification links to the Spool Picker UI."""
        tok = self.persistent_state.get("picker_token")
        if not tok:
            tok = secrets.token_urlsafe(24)
            self.persistent_state["picker_token"] = tok
        return str(tok)

    def build_picker_url(self, slot: int = 1, relative: bool = False) -> str:
        """Construct URL to the built-in Spool Picker Web UI."""
        rel = (
            f"{PICKER_API_BASE}/picker"
            f"?entry_id={self.entry.entry_id}&slot={int(slot)}&token={self.picker_token}"
        )
        if relative:
            return rel
        try:
            base = get_url(self.hass, prefer_external=False)
            return f"{base.rstrip('/')}{rel}"
        except NoURLAvailableError:
            return rel

    async def async_initialize(self) -> None:
        """Load persistent state, initialize sub-managers, and subscribe to NFC tag scans."""
        loaded = await self._store.async_load()
        if isinstance(loaded, dict):
            self.persistent_state = loaded
        else:
            self.persistent_state = {}

        if "picker_token" not in self.persistent_state:
            self.persistent_state["picker_token"] = secrets.token_urlsafe(24)
        self.persistent_state.setdefault("slot_spools", {})
        self.persistent_state.setdefault("previous_locations", {})
        self.persistent_state.setdefault("queued_slot_configs", {})
        self.persistent_state.setdefault(
            "print_options",
            {
                "leveling": True,
                "flow": False,
                "inspection": False,
                "print_now": True,
                "cal_leveling": True,
                "cal_vibration": False,
            },
        )
        await self._store.async_save(self.persistent_state)

        await self.power_manager.async_setup()
        await self.timelapse_manager.async_setup()
        await self.obico_manager.async_setup()

        if self._unsub_tag_scanned:
            self._unsub_tag_scanned()
        self._unsub_tag_scanned = self.hass.bus.async_listen(
            "tag_scanned", self._handle_ha_tag_scanned
        )

        if self.spoolman:
            await self.async_refresh_spoolman_cache()

    async def async_shutdown_managers(self) -> None:
        """Cleanly unload all listeners and background tasks."""
        if self._unsub_tag_scanned:
            self._unsub_tag_scanned()
            self._unsub_tag_scanned = None
        self.power_manager.async_unload()
        self.obico_manager.async_unload()
        self.timelapse_manager.async_unload()

    async def _async_heal_dhcp_ip(self) -> None:
        """Auto-detect if router DHCP reassigned the AD5X's IP address by matching Serial Number."""
        target_sn = self.api.serial_number.strip().upper()
        if not target_sn:
            return
        for p in await async_discover_printers(self.hass, timeout=1.5):
            if p.serial_number.upper() == target_sn and p.ip_address != self.api.host:
                _LOGGER.info(
                    "Detected AD5X (%s) moved to new LAN IP %s (was %s); updating automatically.",
                    target_sn,
                    p.ip_address,
                    self.api.host,
                )
                self.api.host = p.ip_address
                new_data = {**self.entry.data, CONF_HOST: p.ip_address}
                new_opts = (
                    {**self.entry.options, CONF_HOST: p.ip_address}
                    if CONF_HOST in self.entry.options
                    else dict(self.entry.options)
                )
                self.hass.config_entries.async_update_entry(
                    self.entry, data=new_data, options=new_opts
                )
                await self.async_request_refresh()
                return

    async def async_update_persistent_state(self, updates: dict[str, Any]) -> None:
        """Merge updates into persistent storage and notify entities."""
        self.persistent_state.update(updates)
        await self._store.async_save(self.persistent_state)
        self.async_update_listeners()

    async def async_on_plug_power_changed(self, powered_on: bool) -> None:
        """React immediately when the optional smart plug turns on or off."""
        if not powered_on:
            if self._pending_usage_mm and any(v > 0 for v in self._pending_usage_mm.values()):
                await self._async_flush_spoolman_usage()
            self.printer_online = False
            self._prev_status = "offline"
            await self.obico_manager.async_on_printer_offline(immediate=True)
            self.data = {"online": False, "detail": {}}
            self.update_interval = None
            self.async_update_listeners()
        else:
            await self.obico_manager.async_on_printer_online()
            self.update_interval = timedelta(seconds=POLL_INTERVAL_IDLE)
            await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll `/detail` with adaptive intervals and smart-plug zero-packet pause."""
        if self.power_manager.is_plug_off():
            self.printer_online = False
            self.update_interval = None
            return {"online": False, "detail": {}}

        try:
            detail = await self.api.async_get_detail(timeout=2.5)
        except FlashForgeAuthError as err:
            _LOGGER.error("FlashForge AD5X authentication rejected: %s", err)
            self.printer_online = False
            self.update_interval = timedelta(seconds=POLL_INTERVAL_OFFLINE)
            return {"online": False, "detail": {}, "auth_error": str(err)}
        except FlashForgeConnectionError as err:
            if self.printer_online or not self._logged_offline:
                _LOGGER.info(
                    "FlashForge AD5X at %s is offline/unreachable (%s); backing off polling to %ss.",
                    self.api.host,
                    err,
                    POLL_INTERVAL_OFFLINE,
                )
                self._logged_offline = True
            was_online = self.printer_online
            self.printer_online = False
            self.update_interval = timedelta(seconds=POLL_INTERVAL_OFFLINE)
            if was_online:
                await self.obico_manager.async_on_printer_offline(immediate=False)
            # Periodically probe UDP discovery in case DHCP reassigned the printer's LAN IP
            now_m = time.monotonic()
            if (now_m - self._last_ip_rescan_mono) >= 300.0:
                self._last_ip_rescan_mono = now_m
                self.hass.async_create_task(self._async_heal_dhcp_ip())
            return {"online": False, "detail": {}}

        # Printer is online!
        just_came_online = not self.printer_online
        self.printer_online = True
        if just_came_online:
            self._logged_offline = False
            _LOGGER.info("Connected to FlashForge AD5X (%s)", self.api.host)
            await self.obico_manager.async_on_printer_online()

        status = str(detail.get("status") or "ready").lower()
        fast_states = ("printing", "pausing", "heating", "calibrating", "busy")
        self.update_interval = timedelta(
            seconds=POLL_INTERVAL_PRINTING if status in fast_states else POLL_INTERVAL_IDLE
        )

        # Handle status transitions (printing start, completion, cancellation)
        await self._async_process_status_and_layers(detail, status)

        # Handle IFS `hasFilament` transitions and queued `msConfig_cmd` updates
        await self._async_process_ifs_slots(detail, status)

        # Track multi-color filament consumption to Spoolman
        await self._async_track_filament_usage(detail, status)

        # Periodically refresh G-code file list, active thumbnail, and Spoolman cache
        now_mono = time.monotonic()
        if just_came_online or (now_mono - self._last_aux_refresh_mono) >= 60.0:
            self._last_aux_refresh_mono = now_mono
            self.hass.async_create_task(self._async_refresh_auxiliary_data(detail))

        return {"online": True, "detail": detail}

    async def _async_process_status_and_layers(
        self, detail: dict[str, Any], status: str
    ) -> None:
        """Handle print job lifecycle events for Power, Obico, Timelapse, and Thumbnails."""
        prev = self._prev_status
        self._prev_status = status

        layer = int(detail.get("printLayer") or 0)
        if layer != self._prev_layer:
            if layer > self._prev_layer and status == "printing":
                self.timelapse_manager.on_layer_changed(layer)
            self._prev_layer = layer

        if prev != status:
            if status == "printing":
                self.power_manager.on_print_started()
                await self.obico_manager.async_on_print_state_changed(True)
                print_file = str(detail.get("printFileName") or "")
                if print_file:
                    self.hass.async_create_task(self.async_refresh_thumbnail(print_file))
            elif prev in ("printing", "paused", "pausing") and status in (
                "completed",
                "cancel",
                "cancelled",
                "error",
                "ready",
            ):
                await self.obico_manager.async_on_print_state_changed(False)
                if status == "completed":
                    self.timelapse_manager.on_print_completed()
                    self.power_manager.on_print_completed()

    async def _async_process_ifs_slots(
        self, detail: dict[str, Any], status: str
    ) -> None:
        """Detect newly inserted or removed spools in IFS slots 1-4 and flush queued configs."""
        matl = detail.get("matlStationInfo")
        if isinstance(matl, dict) and isinstance(matl.get("slotInfos"), list):
            for item in matl["slotInfos"]:
                if not isinstance(item, dict):
                    continue
                slot_id = int(item.get("slotId") or 0)
                if slot_id not in (1, 2, 3, 4):
                    continue
                has_fil = bool(item.get("hasFilament", False))

                if not self._first_online_poll_done:
                    self._prev_slot_has_filament[slot_id] = has_fil
                    continue

                prev_fil = self._prev_slot_has_filament.get(slot_id, has_fil)
                self._prev_slot_has_filament[slot_id] = has_fil

                if not prev_fil and has_fil:
                    await self._async_on_new_filament_inserted(slot_id, item)
                elif prev_fil and not has_fil and status not in ("printing", "paused"):
                    # Spool physically removed while not printing -> restore location in Spoolman
                    if self.get_assigned_spool_id(slot_id) is not None:
                        await self.async_unassign_spool(slot_id, clear_printer=False)

            self._first_online_poll_done = True

        # Flush any queued msConfig_cmd requests when printer returns to 'ready'
        queued: dict[str, Any] = dict(
            self.persistent_state.get("queued_slot_configs") or {}
        )
        if status == "ready" and queued:
            remaining: dict[str, Any] = {}
            for slot_str, cfg in queued.items():
                try:
                    await self.api.async_configure_ifs_slot(
                        int(slot_str), cfg["material"], cfg["color"]
                    )
                    _LOGGER.info("Synced queued IFS configuration to Slot %s", slot_str)
                except FlashForgeError as err:
                    _LOGGER.debug("Deferred IFS config for Slot %s: %s", slot_str, err)
                    remaining[slot_str] = cfg
            if remaining != queued:
                await self.async_update_persistent_state({"queued_slot_configs": remaining})

    async def _async_on_new_filament_inserted(
        self, slot_id: int, slot_info: dict[str, Any]
    ) -> None:
        """Notify the user to pick/scan a Spoolman spool when filament is inserted into a slot."""
        self.pending_filament_slot = slot_id
        self.pending_filament_timestamp = time.time()

        if not self.spoolman_enabled:
            return

        picker_rel = self.build_picker_url(slot=slot_id, relative=True)
        picker_abs = self.build_picker_url(slot=slot_id, relative=False)
        mat_hint = slot_info.get("materialName") or "Unknown"

        await self.async_send_notification(
            title=f"🧵 Filament Loaded in IFS Slot {slot_id}",
            message=(
                f"Detected filament in Slot {slot_id} ({mat_hint}). "
                "Tap to select from Spoolman, scan a Spoolman QR code, or tap an NFC tag."
            ),
            tag=f"ad5x_filament_slot_{slot_id}",
            click_url=picker_rel,
            persistent_markdown=(
                f"New filament detected in **IFS Slot {slot_id}** (`{mat_hint}`).\n\n"
                f"👉 **[Open Spoolman Picker / QR & NFC Scanner]({picker_rel})**\n\n"
                f"*(Or scan a linked NFC tag now to auto-assign it to Slot {slot_id}.)*"
            ),
            extra_url=picker_abs,
        )

    async def _async_track_filament_usage(
        self, detail: dict[str, Any], status: str
    ) -> None:
        """Track live filament extrusion per active IFS slot and report to Spoolman."""
        if not self.spoolman:
            return

        matl = detail.get("matlStationInfo") or {}
        load_slot = int(matl.get("currentLoadSlot") or 0)
        cum_m_raw = detail.get("cumulativeFilament")
        cum_m = float(cum_m_raw) if cum_m_raw is not None else None

        est_len_mm = float(detail.get("estimatedRightLen") or 0.0)
        progress = float(detail.get("printProgress") or 0.0)
        progress = max(0.0, min(1.0, progress))
        est_sofar_mm = est_len_mm * progress if est_len_mm > 0 else 0.0

        if status == "printing":
            # Detect active IFS slot & multi-color swaps
            if load_slot in (1, 2, 3, 4):
                if (
                    self._print_active_slot in (1, 2, 3, 4)
                    and load_slot != self._print_active_slot
                ):
                    # Flush previous slot usage and add purge waste for filament swap
                    await self._async_flush_spoolman_usage()
                    purge_mm = max(0.0, self.purge_mm_per_swap)
                    if purge_mm > 0:
                        self._pending_usage_mm[load_slot] = (
                            self._pending_usage_mm.get(load_slot, 0.0) + purge_mm
                        )
                        _LOGGER.debug(
                            "IFS swapped Slot %s -> Slot %s; added %.1fmm purge waste.",
                            self._print_active_slot,
                            load_slot,
                            purge_mm,
                        )
                elif self._print_active_slot == 0:
                    # Initial load purge at start of print
                    purge_mm = max(0.0, self.purge_mm_per_swap)
                    if purge_mm > 0 and progress < 0.05:
                        self._pending_usage_mm[load_slot] = (
                            self._pending_usage_mm.get(load_slot, 0.0) + purge_mm
                        )
                self._print_active_slot = load_slot

            target_slot = self._print_active_slot or load_slot
            delta_mm = 0.0

            if cum_m is not None and self._prev_cumulative_m is not None:
                diff_m = cum_m - self._prev_cumulative_m
                if 0.0 < diff_m < 50.0:
                    delta_mm = diff_m * 1000.0

            # Fallback if printer firmware only updates cumulativeFilament coarsely
            if delta_mm == 0.0 and est_sofar_mm > self._prev_est_progress_mm:
                est_diff = est_sofar_mm - self._prev_est_progress_mm
                if 0.0 < est_diff < 50000.0:
                    delta_mm = est_diff

            if delta_mm > 0.0 and target_slot in (1, 2, 3, 4):
                self._pending_usage_mm[target_slot] = (
                    self._pending_usage_mm.get(target_slot, 0.0) + delta_mm
                )

            if cum_m is not None:
                self._prev_cumulative_m = cum_m
            if est_sofar_mm > 0:
                self._prev_est_progress_mm = est_sofar_mm

            # Live incremental push to Spoolman every 60s
            now_mono = time.monotonic()
            if (now_mono - self._last_usage_flush_mono) >= 60.0 and any(
                mm >= 25.0 for mm in self._pending_usage_mm.values()
            ):
                await self._async_flush_spoolman_usage()
        else:
            # Flush any remaining usage when print pauses, completes, or cancels
            if any(mm > 0.5 for mm in self._pending_usage_mm.values()):
                await self._async_flush_spoolman_usage()

            if status not in ("paused", "pausing"):
                self._print_active_slot = 0
                self._prev_est_progress_mm = 0.0
                if cum_m is not None:
                    self._prev_cumulative_m = cum_m

    async def _async_flush_spoolman_usage(self) -> None:
        """Send accumulated per-slot millimeter usage to Spoolman (`PUT /api/v1/spool/{id}/use`)."""
        self._last_usage_flush_mono = time.monotonic()
        if not self.spoolman:
            self._pending_usage_mm = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
            return

        for slot_id in (1, 2, 3, 4):
            mm = self._pending_usage_mm.get(slot_id, 0.0)
            if mm <= 0.5:
                continue
            self._pending_usage_mm[slot_id] = 0.0
            spool_id = self.get_assigned_spool_id(slot_id)
            if spool_id is None:
                continue
            try:
                updated = await self.spoolman.async_use_filament(
                    spool_id=spool_id, use_length_mm=mm
                )
                if updated:
                    self.spools_cache[spool_id] = updated
                    _LOGGER.info(
                        "Reported %.1f mm filament usage from IFS Slot %s to Spoolman Spool #%s",
                        mm,
                        slot_id,
                        spool_id,
                    )
            except SpoolmanError as err:
                _LOGGER.warning(
                    "Could not report %.1f mm usage to Spoolman spool %s: %s",
                    mm,
                    spool_id,
                    err,
                )
                # Re-credit pending mm so it isn't lost on transient network hiccup
                self._pending_usage_mm[slot_id] += mm

    async def _async_refresh_auxiliary_data(self, detail: dict[str, Any]) -> None:
        """Refresh stored G-code list, active print thumbnail, and Spoolman spools."""
        if self.files_enabled and self.printer_online:
            try:
                glist = await self.api.async_get_gcode_list()
                self.gcode_files = [str(f) for f in glist.get("files", [])]
                self.gcode_details = [
                    d for d in glist.get("details", []) if isinstance(d, dict)
                ]
                if not self.selected_file and self.gcode_files:
                    self.selected_file = self.gcode_files[0]
            except FlashForgeError as err:
                _LOGGER.debug("Could not refresh /gcodeList: %s", err)

        active_file = str(detail.get("printFileName") or "") or self.selected_file
        if active_file and active_file != self.thumbnail_filename:
            await self.async_refresh_thumbnail(active_file)

        if self.spoolman:
            await self.async_refresh_spoolman_cache()

        self.async_update_listeners()

    async def async_refresh_thumbnail(self, filename: str | None = None) -> None:
        """Fetch thumbnail for the specified or active G-code file."""
        target = filename or self.selected_file
        img_bytes: bytes | None = None
        if target:
            img_bytes = await self.api.async_get_gcode_thumb(target)
        if not img_bytes and self.is_printing:
            img_bytes = await self.api.async_get_active_thumb()
        if img_bytes:
            self.thumbnail_bytes = img_bytes
            self.thumbnail_filename = target or "active_print"
            self.async_update_listeners()

    async def async_refresh_spoolman_cache(self) -> list[dict[str, Any]]:
        """Fetch latest active spools from Spoolman into `spools_cache`."""
        if not self.spoolman:
            return []
        try:
            spools = await self.spoolman.async_get_spools(allow_archived=False)
            self.spools_cache = {s["id"]: s for s in spools}
            # Ensure any assigned spool IDs remain in cache even if archived
            for slot_id in (1, 2, 3, 4):
                sid = self.get_assigned_spool_id(slot_id)
                if sid and sid not in self.spools_cache:
                    single = await self.spoolman.async_get_spool(sid)
                    if single:
                        self.spools_cache[sid] = single
            self.async_update_listeners()
            return list(self.spools_cache.values())
        except SpoolmanError as err:
            _LOGGER.debug("Spoolman cache refresh failed: %s", err)
            return list(self.spools_cache.values())

    def get_assigned_spool_id(self, slot_id: int) -> int | None:
        """Return Spoolman spool ID currently mapped to IFS slot 1-4."""
        mapping = self.persistent_state.get("slot_spools") or {}
        raw = mapping.get(str(slot_id))
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def get_assigned_spool(self, slot_id: int) -> dict[str, Any] | None:
        """Return cached Spoolman spool summary for IFS slot 1-4."""
        sid = self.get_assigned_spool_id(slot_id)
        return self.spools_cache.get(sid) if sid is not None else None

    async def async_assign_spool_to_slot(
        self,
        slot_id: int,
        spool_id: int,
        bind_nfc_uid: str | None = None,
        push_to_printer: bool = True,
    ) -> dict[str, Any]:
        """Assign a Spoolman spool to IFS Slot 1-4, sync location, optionally bind NFC tag, and push color/material to AD5X."""
        if slot_id not in (1, 2, 3, 4):
            raise HomeAssistantError(f"Invalid IFS slot {slot_id} (expected 1-4).")
        if not self.spoolman:
            raise HomeAssistantError("Spoolman is not enabled in FlashForge AD5X configuration.")

        spool = await self.spoolman.async_get_spool(spool_id)
        if not spool:
            raise HomeAssistantError(f"Spool #{spool_id} was not found in Spoolman.")

        if bind_nfc_uid:
            await self.spoolman.async_bind_tag_to_spool(spool_id, bind_nfc_uid)
            spool = await self.spoolman.async_get_spool(spool_id) or spool

        slot_spools = dict(self.persistent_state.get("slot_spools") or {})
        prev_locs = dict(self.persistent_state.get("previous_locations") or {})

        # If a different spool was previously in this slot, restore its old location
        old_spool_id = slot_spools.get(str(slot_id))
        if old_spool_id is not None and int(old_spool_id) != int(spool_id):
            await self._async_restore_spool_location(int(old_spool_id), prev_locs)

        # If this spool was assigned to another AD5X slot, clear that old slot mapping
        for other_slot in ("1", "2", "3", "4"):
            if other_slot != str(slot_id) and slot_spools.get(other_slot) == int(spool_id):
                slot_spools.pop(other_slot, None)

        prefix = str(
            self.get_conf(CONF_SPOOLMAN_LOCATION_PREFIX, DEFAULT_SPOOLMAN_LOCATION_PREFIX)
        ).strip()
        target_loc = f"{prefix} {slot_id}"
        current_loc = spool.get("location") or ""
        if not current_loc.startswith(prefix):
            prev_locs[str(spool_id)] = current_loc

        updated_spool = await self.spoolman.async_update_spool_location(spool_id, target_loc)
        if updated_spool:
            spool = updated_spool
        self.spools_cache[int(spool_id)] = spool

        slot_spools[str(slot_id)] = int(spool_id)
        if self.pending_filament_slot == slot_id:
            self.pending_filament_slot = None

        # Push material & color to AD5X (or queue if printer is busy/offline)
        queued = dict(self.persistent_state.get("queued_slot_configs") or {})
        if push_to_printer:
            raw_hex = normalize_hex_color(spool.get("color_hex"), "#FFFFFF")
            printer_hex = snap_to_ad5x_palette(raw_hex) if self.snap_printer_colors else raw_hex
            material = str(spool.get("material") or "PLA")

            if self.printer_online and self.printer_status == "ready":
                try:
                    await self.api.async_configure_ifs_slot(slot_id, material, printer_hex)
                    queued.pop(str(slot_id), None)
                except FlashForgeError as err:
                    _LOGGER.warning(
                        "Printer busy/rejected msConfig_cmd (%s); queuing Slot %s update.",
                        err,
                        slot_id,
                    )
                    queued[str(slot_id)] = {"material": material, "color": printer_hex}
            else:
                queued[str(slot_id)] = {"material": material, "color": printer_hex}

        await self.async_update_persistent_state(
            {
                "slot_spools": slot_spools,
                "previous_locations": prev_locs,
                "queued_slot_configs": queued,
            }
        )
        if self.printer_online:
            self.hass.async_create_task(self.async_request_refresh())
        return spool

    async def async_unassign_spool(
        self, slot_id: int, clear_printer: bool = False
    ) -> None:
        """Unassign the Spoolman spool from IFS Slot 1-4 and restore its previous location."""
        slot_spools = dict(self.persistent_state.get("slot_spools") or {})
        prev_locs = dict(self.persistent_state.get("previous_locations") or {})
        queued = dict(self.persistent_state.get("queued_slot_configs") or {})

        old_id = slot_spools.pop(str(slot_id), None)
        queued.pop(str(slot_id), None)

        if old_id is not None and self.spoolman:
            await self._async_restore_spool_location(int(old_id), prev_locs)

        await self.async_update_persistent_state(
            {
                "slot_spools": slot_spools,
                "previous_locations": prev_locs,
                "queued_slot_configs": queued,
            }
        )
        if clear_printer and self.printer_online and self.printer_status == "ready":
            try:
                await self.api.async_ifs_load_unload(slot_id, unload=True)
            except FlashForgeError:
                pass

    async def _async_restore_spool_location(
        self, spool_id: int, prev_locs: dict[str, Any]
    ) -> None:
        if not self.spoolman:
            return
        prior = str(prev_locs.pop(str(spool_id), "") or "")
        updated = await self.spoolman.async_update_spool_location(spool_id, prior)
        if updated:
            self.spools_cache[spool_id] = updated

    @callback
    def _handle_ha_tag_scanned(self, event: Event) -> None:
        """Handle Home Assistant `tag_scanned` events for NFC UID spool lookup & auto-assign."""
        raw_tag = str(event.data.get("tag_id") or "").strip()
        if not raw_tag or not self.spoolman_enabled:
            return
        self.hass.async_create_task(self.async_process_nfc_scan(raw_tag))

    async def async_process_nfc_scan(
        self, raw_uid: str, explicit_slot: int | None = None
    ) -> dict[str, Any]:
        """Process a scanned NFC tag UID against Spoolman v0.27.0+ native tags."""
        norm_uid = normalize_tag_uid(raw_uid)
        matched_spool: dict[str, Any] | None = None
        if self.spoolman:
            try:
                matched_spool = await self.spoolman.async_find_spool_by_tag(norm_uid)
            except SpoolmanError as err:
                _LOGGER.warning("Spoolman tag lookup failed for %s: %s", norm_uid, err)

        target_slot = explicit_slot
        if target_slot is None:
            if (
                self.pending_filament_slot in (1, 2, 3, 4)
                and (time.time() - self.pending_filament_timestamp) <= 600.0
            ):
                target_slot = self.pending_filament_slot
            elif self.active_picker_slot in (1, 2, 3, 4):
                target_slot = self.active_picker_slot

        scan_record: dict[str, Any] = {
            "uid": norm_uid,
            "raw_uid": raw_uid,
            "timestamp": time.time(),
            "matched_spool": matched_spool,
            "auto_assigned_slot": None,
        }

        if matched_spool and target_slot in (1, 2, 3, 4):
            await self.async_assign_spool_to_slot(target_slot, matched_spool["id"])
            scan_record["auto_assigned_slot"] = target_slot
            await self.async_send_notification(
                title=f"✅ Assigned to IFS Slot {target_slot}",
                message=(
                    f"NFC tag {norm_uid} matched Spool #{matched_spool['id']} "
                    f"({matched_spool['vendor']} {matched_spool['material']}) → Slot {target_slot}."
                ),
                tag=f"ad5x_filament_slot_{target_slot}",
            )
        elif matched_spool:
            picker_rel = self.build_picker_url(slot=1, relative=True) + f"&spool_id={matched_spool['id']}"
            await self.async_send_notification(
                title=f"🏷️ Spool #{matched_spool['id']} Scanned",
                message=(
                    f"{matched_spool['vendor']} {matched_spool['material']} ({matched_spool['name']}). "
                    "Tap to choose which AD5X IFS slot to load it into."
                ),
                tag="ad5x_nfc_scan",
                click_url=picker_rel,
            )
        else:
            slot_for_link = target_slot or 1
            picker_rel = (
                self.build_picker_url(slot=slot_for_link, relative=True)
                + f"&bind_uid={norm_uid}"
            )
            await self.async_send_notification(
                title="🆕 Unrecognized NFC Tag Scanned",
                message=(
                    f"Tag UID {norm_uid} is not linked in Spoolman yet. "
                    "Tap to link it to an existing or new Spoolman spool."
                ),
                tag="ad5x_nfc_scan",
                click_url=picker_rel,
            )

        self.last_scanned_tag = scan_record
        self.async_update_listeners()
        return scan_record

    def build_material_mappings(
        self,
        filename: str | None = None,
        explicit_tool_to_slot: dict[int, int] | None = None,
        default_slot: int = 1,
    ) -> list[dict[str, Any]]:
        """Build multi-color `materialMappings` array for `/printGcode` or `/uploadGcode`."""
        detail = (self.data or {}).get("detail") or {}
        matl = detail.get("matlStationInfo") or {}
        slot_infos: dict[int, dict[str, Any]] = {}
        for item in matl.get("slotInfos") or []:
            if isinstance(item, dict) and item.get("slotId") in (1, 2, 3, 4):
                slot_infos[int(item["slotId"])] = item

        def _slot_meta(slot_num: int) -> tuple[str, str]:
            spool = self.get_assigned_spool(slot_num)
            s_info = slot_infos.get(slot_num) or {}
            mat = (
                str(s_info.get("materialName") or "")
                or (str(spool.get("material")) if spool else "")
                or "PLA"
            )
            col = normalize_hex_color(
                s_info.get("materialColor")
                or (spool.get("color_hex") if spool else None),
                "#FFFFFF",
            )
            return mat, col

        tool_datas: list[dict[str, Any]] = []
        if filename:
            for item in self.gcode_details:
                if item.get("gcodeFileName") == filename and isinstance(
                    item.get("gcodeToolDatas"), list
                ):
                    tool_datas = item["gcodeToolDatas"]
                    break

        if not tool_datas:
            slot_num = (
                explicit_tool_to_slot.get(0, default_slot)
                if explicit_tool_to_slot
                else default_slot
            )
            mat, col = _slot_meta(slot_num)
            return [
                {
                    "toolId": 0,
                    "slotId": int(slot_num),
                    "materialName": mat,
                    "toolMaterialColor": col,
                    "slotMaterialColor": col,
                }
            ]

        mappings: list[dict[str, Any]] = []
        for idx, tdata in enumerate(tool_datas):
            tool_id = int(tdata.get("toolId", idx))
            tool_color = normalize_hex_color(tdata.get("materialColor"), "#FFFFFF")
            tool_mat = str(tdata.get("materialName") or "PLA")

            if explicit_tool_to_slot and tool_id in explicit_tool_to_slot:
                chosen_slot = int(explicit_tool_to_slot[tool_id])
            else:
                # Auto-match best loaded slot by material and color distance
                chosen_slot = min(4, max(1, (idx % 4) + 1))
                best_score = 10**9
                for s_num in (1, 2, 3, 4):
                    s_info = slot_infos.get(s_num) or {}
                    if not s_info.get("hasFilament", True):
                        continue
                    s_mat, s_col = _slot_meta(s_num)
                    mat_penalty = 0 if s_mat.upper() == tool_mat.upper() else 50000
                    r1, g1, b1 = int(tool_color[1:3], 16), int(tool_color[3:5], 16), int(tool_color[5:7], 16)
                    r2, g2, b2 = int(s_col[1:3], 16), int(s_col[3:5], 16), int(s_col[5:7], 16)
                    score = mat_penalty + (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2
                    if score < best_score:
                        best_score = score
                        chosen_slot = s_num

            s_mat, s_col = _slot_meta(chosen_slot)
            mappings.append(
                {
                    "toolId": tool_id,
                    "slotId": chosen_slot,
                    "materialName": s_mat,
                    "toolMaterialColor": tool_color,
                    "slotMaterialColor": s_col,
                }
            )
        return mappings

    async def async_send_notification(
        self,
        title: str,
        message: str,
        tag: str | None = None,
        click_url: str | None = None,
        extra_url: str | None = None,
        image: str | None = None,
        video: str | None = None,
        actions: list[dict[str, str]] | None = None,
        persistent_markdown: str | None = None,
    ) -> None:
        """Dispatch mobile push notifications to configured targets plus HA persistent notifications."""
        targets: list[str] = list(self.get_conf(CONF_NOTIFY_TARGETS) or [])

        mobile_data: dict[str, Any] = {}
        if tag:
            mobile_data["tag"] = tag
            mobile_data["priority"] = "high"
            mobile_data["importance"] = "high"
            mobile_data["ttl"] = 0
        if click_url:
            mobile_data["url"] = click_url
            mobile_data["clickAction"] = click_url
        if image:
            mobile_data["image"] = image
        if video:
            mobile_data["video"] = video
        if actions:
            mobile_data["actions"] = actions

        for target in targets:
            svc_name = target.strip()
            if svc_name.startswith("notify."):
                svc_name = svc_name.split(".", 1)[1]
            if not svc_name or not self.hass.services.has_service("notify", svc_name):
                continue
            payload: dict[str, Any] = {"title": title, "message": message}
            if mobile_data:
                payload["data"] = mobile_data
            try:
                await self.hass.services.async_call(
                    "notify", svc_name, payload, blocking=False
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Notification to notify.%s failed: %s", svc_name, err)

        # Always create/update a persistent notification in HA UI for visibility
        if self.hass.services.has_service("persistent_notification", "create"):
            p_msg = persistent_markdown or message
            if click_url and not persistent_markdown:
                p_msg = f"{message}\n\n[Open Action]({extra_url or click_url})"
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": p_msg,
                    "notification_id": tag or f"{DOMAIN}_{self.entry.entry_id}",
                },
                blocking=False,
            )
