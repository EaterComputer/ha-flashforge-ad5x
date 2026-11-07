"""Select platform for FlashForge AD5X Spoolman slot assignment, G-code files, jog step, and timelapse modes."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TIMELAPSE_DISABLED, TIMELAPSE_MODES
from .coordinator import FlashForgeAD5XCoordinator

_SPOOL_ID_RE = re.compile(r"^#(\d+)\b")
_UNASSIGNED = "Unassigned"
_NO_FILES = "No files"
_JOG_OPTIONS = ["1 mm", "10 mm", "50 mm", "100 mm"]


def _format_spool_option(spool: dict[str, Any]) -> str:
    """Format a Spoolman spool summary into a human-friendly dropdown option."""
    sid = spool.get("id")
    vendor = spool.get("vendor") or "Generic"
    material = spool.get("material") or "PLA"
    name = spool.get("name") or ""
    rem_w = spool.get("remaining_weight")
    weight_str = f" [{int(round(float(rem_w)))}g]" if rem_w is not None else ""
    name_str = f" ({name})" if name and name.lower() != material.lower() else ""
    return f"#{sid} — {vendor} {material}{name_str}{weight_str}"


class AD5XSlotSpoolSelect(CoordinatorEntity[FlashForgeAD5XCoordinator], SelectEntity):
    """Dropdown selector mapping an IFS slot (1-4) to a Spoolman spool."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:spool"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator, slot_id: int) -> None:
        super().__init__(coordinator)
        self.slot_id = slot_id
        self._key = f"ifs_slot_{slot_id}_spool"
        self._attr_name = f"IFS Slot {slot_id} Spoolman Spool"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{self._key}"
        self._attr_device_info = coordinator.device_info

    @property
    def options(self) -> list[str]:
        sorted_spools = sorted(
            self.coordinator.spools_cache.values(),
            key=lambda s: (
                str(s.get("vendor") or "").lower(),
                str(s.get("material") or "").lower(),
                int(s.get("id") or 0),
            ),
        )
        opts = [_UNASSIGNED] + [_format_spool_option(sp) for sp in sorted_spools]
        assigned_id = self.coordinator.get_assigned_spool_id(self.slot_id)
        if assigned_id is not None and assigned_id not in self.coordinator.spools_cache:
            fallback = f"#{assigned_id} — Assigned Spool"
            if fallback not in opts:
                opts.append(fallback)
        return opts

    @property
    def current_option(self) -> str:
        assigned_id = self.coordinator.get_assigned_spool_id(self.slot_id)
        if assigned_id is None:
            return _UNASSIGNED
        spool = self.coordinator.spools_cache.get(assigned_id)
        if spool:
            return _format_spool_option(spool)
        return f"#{assigned_id} — Assigned Spool"

    async def async_select_option(self, option: str) -> None:
        if option == _UNASSIGNED:
            await self.coordinator.async_unassign_spool(self.slot_id)
            return
        match = _SPOOL_ID_RE.match(option.strip())
        if match:
            spool_id = int(match.group(1))
            await self.coordinator.async_assign_spool_to_slot(self.slot_id, spool_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": self._key,
            "slot": self.slot_id,
            "assigned_spool_id": self.coordinator.get_assigned_spool_id(self.slot_id),
        }


class AD5XGcodeFileSelect(CoordinatorEntity[FlashForgeAD5XCoordinator], SelectEntity):
    """Dropdown selector for stored G-code / 3MF files on the printer."""

    _attr_has_entity_name = True
    _attr_name = "Selected Print File"
    _attr_icon = "mdi:file-cad-box"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_selected_gcode_file"
        self._attr_device_info = coordinator.device_info

    @property
    def options(self) -> list[str]:
        files = list(self.coordinator.gcode_files)
        if self.coordinator.selected_file and self.coordinator.selected_file not in files:
            files.insert(0, self.coordinator.selected_file)
        return files if files else [_NO_FILES]

    @property
    def current_option(self) -> str:
        opts = self.options
        if self.coordinator.selected_file in opts:
            return self.coordinator.selected_file
        return opts[0]

    async def async_select_option(self, option: str) -> None:
        if option == _NO_FILES:
            return
        self.coordinator.selected_file = option
        await self.coordinator.async_refresh_thumbnail(option)
        self.coordinator.async_update_listeners()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": "selected_gcode_file",
            "file_count": len(self.coordinator.gcode_files),
        }


class AD5XJogStepSelect(CoordinatorEntity[FlashForgeAD5XCoordinator], SelectEntity):
    """Selects step distance (1, 10, 50, 100 mm) for manual XYZ jogging."""

    _attr_has_entity_name = True
    _attr_name = "Jog Step Distance"
    _attr_icon = "mdi:ruler"
    _attr_options = _JOG_OPTIONS

    def __init__(self, coordinator: FlashForgeAD5XCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_jog_step"
        self._attr_device_info = coordinator.device_info

    @property
    def current_option(self) -> str:
        label = f"{int(self.coordinator.jog_step_mm)} mm"
        return label if label in _JOG_OPTIONS else "10 mm"

    async def async_select_option(self, option: str) -> None:
        try:
            self.coordinator.jog_step_mm = float(option.split()[0])
        except (ValueError, IndexError):
            self.coordinator.jog_step_mm = 10.0
        self.coordinator.async_update_listeners()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": "jog_step",
            "step_mm": self.coordinator.jog_step_mm,
        }


class AD5XTimelapseModeSelect(
    CoordinatorEntity[FlashForgeAD5XCoordinator], SelectEntity
):
    """Selects capture mode (Disabled, By Layer, 15s, 30s, 60s) for a timelapse channel."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:camera-timer"
    _attr_options = TIMELAPSE_MODES

    def __init__(self, coordinator: FlashForgeAD5XCoordinator, channel_key: str) -> None:
        super().__init__(coordinator)
        self.channel_key = channel_key
        ch = coordinator.timelapse_manager.channels[channel_key]
        suffix = f" ({ch.label})" if len(coordinator.timelapse_manager.channels) > 1 else ""
        self._key = f"timelapse_mode_{channel_key}"
        self._attr_name = f"Timelapse Capture Mode{suffix}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{self._key}"
        self._attr_device_info = coordinator.device_info

    @property
    def current_option(self) -> str:
        ch = self.coordinator.timelapse_manager.channels.get(self.channel_key)
        return ch.mode if ch and ch.mode in TIMELAPSE_MODES else TIMELAPSE_DISABLED

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.timelapse_manager.async_set_mode(self.channel_key, option)

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
    """Set up AD5X select entities based on enabled modules."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = [AD5XJogStepSelect(coordinator)]

    if coordinator.spoolman_enabled:
        for slot_id in (1, 2, 3, 4):
            entities.append(AD5XSlotSpoolSelect(coordinator, slot_id))

    if coordinator.files_enabled:
        entities.append(AD5XGcodeFileSelect(coordinator))

    if coordinator.timelapse_manager.enabled:
        for ch_key in coordinator.timelapse_manager.channels:
            entities.append(AD5XTimelapseModeSelect(coordinator, ch_key))

    async_add_entities(entities)
