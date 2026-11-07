"""Button platform for FlashForge AD5X job control, homing, calibration, IFS load/unload, and timelapses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FlashForgeAD5XCoordinator


@dataclass(frozen=True, kw_only=True)
class AD5XButtonDescription(ButtonEntityDescription):
    """Describes an AD5X action button."""

    press_fn: Callable[[FlashForgeAD5XCoordinator], Awaitable[Any]]
    require_online: bool = True
    enabled_fn: Callable[[FlashForgeAD5XCoordinator], bool] = lambda _c: True


async def _start_selected_print(c: FlashForgeAD5XCoordinator) -> None:
    opts = c.persistent_state.get("print_options") or {}
    mappings = c.build_material_mappings(filename=c.selected_file)
    await c.api.async_start_print(
        filename=c.selected_file,
        material_mappings=mappings,
        leveling_before_print=bool(opts.get("leveling", True)),
        flow_calibration=bool(opts.get("flow", False)),
        first_layer_inspection=bool(opts.get("inspection", False)),
    )
    await c.async_request_refresh()


async def _check_firmware_btn(c: FlashForgeAD5XCoordinator) -> None:
    detail = (c.data or {}).get("detail") or {}
    cur_sw = str(detail.get("firmwareVersion") or "3.1.5")
    c.firmware_info = await c.api.async_check_firmware_updates(current_software=cur_sw)
    c.async_update_listeners()


BUTTONS: tuple[AD5XButtonDescription, ...] = (
    AD5XButtonDescription(
        key="pause_print",
        name="Pause Print",
        icon="mdi:pause-circle-outline",
        press_fn=lambda c: c.api.async_job_control("pause"),
    ),
    AD5XButtonDescription(
        key="resume_print",
        name="Resume Print",
        icon="mdi:play-circle-outline",
        press_fn=lambda c: c.api.async_job_control("continue"),
    ),
    AD5XButtonDescription(
        key="cancel_print",
        name="Cancel Print",
        icon="mdi:stop-circle-outline",
        press_fn=lambda c: c.api.async_job_control("cancel"),
    ),
    AD5XButtonDescription(
        key="clear_bed",
        name="Clear Build Plate Status",
        icon="mdi:tray-remove",
        press_fn=lambda c: c.api.async_clear_platform(),
    ),
    AD5XButtonDescription(
        key="home_axes",
        name="Home All Axes",
        icon="mdi:home-import-outline",
        press_fn=lambda c: c.api.async_home(),
    ),
    AD5XButtonDescription(
        key="calibrate_bed",
        name="Run Bed Leveling Calibration",
        icon="mdi:tray-arrow-down",
        press_fn=lambda c: c.api.async_calibrate(leveling=True, vibration=False),
    ),
    AD5XButtonDescription(
        key="calibrate_vibration",
        name="Run Vibration Compensation",
        icon="mdi:vibrate",
        press_fn=lambda c: c.api.async_calibrate(leveling=False, vibration=True),
    ),
    AD5XButtonDescription(
        key="start_selected_file",
        name="Print Selected File",
        icon="mdi:printer-3d-nozzle-alert",
        press_fn=_start_selected_print,
        enabled_fn=lambda c: c.files_enabled,
    ),
    AD5XButtonDescription(
        key="refresh_spoolman",
        name="Refresh Spoolman Inventory",
        icon="mdi:database-sync-outline",
        press_fn=lambda c: c.async_refresh_spoolman_cache(),
        require_online=False,
        enabled_fn=lambda c: c.spoolman_enabled,
    ),
    AD5XButtonDescription(
        key="check_firmware",
        name="Check for Firmware Updates",
        icon="mdi:cloud-Sync-outline",
        press_fn=_check_firmware_btn,
        require_online=False,
        enabled_fn=lambda c: c.firmware_check_enabled,
    ),
)


class AD5XActionButton(CoordinatorEntity[FlashForgeAD5XCoordinator], ButtonEntity):
    """Standard AD5X button entity."""

    entity_description: AD5XButtonDescription
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FlashForgeAD5XCoordinator, description: AD5XButtonDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info
        if description.key == "check_firmware":
            self._attr_icon = "mdi:cloud-sync-outline"

    @property
    def available(self) -> bool:
        if self.entity_description.require_online:
            return self.coordinator.printer_online
        return True

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
        if self.coordinator.printer_online:
            await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": self.entity_description.key,
        }


class AD5XIfsSlotLoadUnloadButton(
    CoordinatorEntity[FlashForgeAD5XCoordinator], ButtonEntity
):
    """Per-slot IFS Load or Unload button."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FlashForgeAD5XCoordinator, slot_id: int, unload: bool
    ) -> None:
        super().__init__(coordinator)
        self.slot_id = slot_id
        self.unload = unload
        op = "unload" if unload else "load"
        self._key = f"ifs_slot_{slot_id}_{op}"
        self._attr_name = f"IFS Slot {slot_id} {'Unload' if unload else 'Load'}"
        self._attr_icon = "mdi:tray-arrow-up" if unload else "mdi:tray-arrow-down"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{self._key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return self.coordinator.printer_online

    async def async_press(self) -> None:
        await self.coordinator.api.async_ifs_load_unload(self.slot_id, unload=self.unload)
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": self._key,
            "slot": self.slot_id,
        }


class AD5XTimelapseActionButton(
    CoordinatorEntity[FlashForgeAD5XCoordinator], ButtonEntity
):
    """Snapshot, Compile, and Clear buttons for Timelapse channels."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FlashForgeAD5XCoordinator, channel_key: str, action: str
    ) -> None:
        super().__init__(coordinator)
        self.channel_key = channel_key
        self.tl_action = action
        ch = coordinator.timelapse_manager.channels[channel_key]
        suffix = f" ({ch.label})" if len(coordinator.timelapse_manager.channels) > 1 else ""
        labels = {
            "snapshot": ("Timelapse Capture Snapshot", "mdi:camera-plus-outline"),
            "compile": ("Timelapse Compile MP4", "mdi:movie-Roll"),
            "clear": ("Timelapse Clear Snapshots", "mdi:delete-sweep-outline"),
        }
        title, icon = labels[action]
        if icon == "mdi:movie-Roll":
            icon = "mdi:movie-roll"
        self._key = f"timelapse_{action}_{channel_key}"
        self._attr_name = f"{title}{suffix}"
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{self._key}"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        tm = self.coordinator.timelapse_manager
        if self.tl_action == "snapshot":
            await tm.async_capture_snapshot(self.channel_key)
        elif self.tl_action == "compile":
            await tm.async_compile_video(self.channel_key, clear_after=True)
        elif self.tl_action == "clear":
            await tm.async_clear_snapshots(self.channel_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": self._key,
            "channel": self.channel_key,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AD5X buttons."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = [
        AD5XActionButton(coordinator, desc)
        for desc in BUTTONS
        if desc.enabled_fn(coordinator)
    ]

    for slot_id in (1, 2, 3, 4):
        entities.append(AD5XIfsSlotLoadUnloadButton(coordinator, slot_id, unload=False))
        entities.append(AD5XIfsSlotLoadUnloadButton(coordinator, slot_id, unload=True))

    if coordinator.timelapse_manager.enabled:
        for ch_key in coordinator.timelapse_manager.channels:
            for act in ("snapshot", "compile", "clear"):
                entities.append(AD5XTimelapseActionButton(coordinator, ch_key, act))

    async_add_entities(entities)
