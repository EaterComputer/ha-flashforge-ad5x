"""Sensor platform for FlashForge AD5X telemetry, IFS/Spoolman slots, Timelapse, and Updates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfMass, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CAMERA_MODE,
    CONF_ENABLE_CAMERA,
    CONF_ENABLE_FILES,
    CONF_ENABLE_FIRMWARE_CHECK,
    CONF_ENABLE_GCODE_CONSOLE,
    CONF_ENABLE_OBICO,
    CONF_ENABLE_SPOOLMAN,
    CONF_ENABLE_TIMELAPSE,
    CONF_EXTERNAL_CAMERA_ENTITY,
    CONF_EXTERNAL_CAMERA_SWITCH,
    CONF_OBICO_CAMERA_ENTITY,
    CONF_OBICO_CONFIDENCE_SENSOR,
    CONF_OBICO_ERROR_BINARY_SENSOR,
    CONF_OBICO_SWITCH_ENTITY,
    CONF_SMART_PLUG_ENTITY,
    DOMAIN,
    IFS_STATE_ACTIONS,
    IFS_STATE_STEPS,
    PRINTER_STATUS_LABELS,
    normalize_hex_color,
)
from .coordinator import FlashForgeAD5XCoordinator


def _detail(coord: FlashForgeAD5XCoordinator) -> dict[str, Any]:
    return (coord.data or {}).get("detail") or {}


def _fmt_hms(seconds: float | int | None) -> str:
    sec = max(0, int(float(seconds or 0)))
    return f"{sec // 3600:02d}h {(sec % 3600) // 60:02d}m {sec % 60:02d}s"


@dataclass(frozen=True, kw_only=True)
class AD5XSensorDescription(SensorEntityDescription):
    """Describes a FlashForge AD5X sensor entity."""

    value_fn: Callable[[FlashForgeAD5XCoordinator], Any]
    attrs_fn: Callable[[FlashForgeAD5XCoordinator], dict[str, Any]] | None = None
    enabled_fn: Callable[[FlashForgeAD5XCoordinator], bool] = lambda _c: True


def _status_attrs(coord: FlashForgeAD5XCoordinator) -> dict[str, Any]:
    d = _detail(coord)
    matl = d.get("matlStationInfo") or {}
    action_id = int(matl.get("stateAction", 0)) if isinstance(matl, dict) else 0
    step_id = int(matl.get("stateStep", 0)) if isinstance(matl, dict) else 0

    raw_slots = {
        int(s["slotId"]): s
        for s in (matl.get("slotInfos") or [])
        if isinstance(s, dict) and s.get("slotId") in (1, 2, 3, 4)
    }
    slots_summary = []
    for s_num in (1, 2, 3, 4):
        si = raw_slots.get(s_num) or {}
        sp = coord.get_assigned_spool(s_num)
        slots_summary.append(
            {
                "slot": s_num,
                "has_filament": bool(si.get("hasFilament", False)),
                "printer_material": si.get("materialName") or "",
                "printer_color": normalize_hex_color(si.get("materialColor"), "#888888")
                if si.get("materialColor")
                else "",
                "spool": sp,
            }
        )

    return {
        "raw_status": coord.printer_status,
        "online": coord.printer_online,
        "printer_name": coord.printer_name,
        "ip_address": d.get("ipAddr") or coord.api.host,
        "mac_address": d.get("macAddr") or "",
        "firmware_version": d.get("firmwareVersion") or "",
        "nozzle_model": d.get("nozzleModel") or "",
        "ifs_action": IFS_STATE_ACTIONS.get(action_id, f"Action {action_id}") if matl else "Idle",
        "ifs_step": IFS_STATE_STEPS.get(step_id, f"Step {step_id}") if matl else "Idle",
        "current_load_slot": int(matl.get("currentLoadSlot") or 0) if isinstance(matl, dict) else 0,
        "waiting_for_cooldown": coord.power_manager.waiting_for_cooldown,
        "slots_summary": slots_summary,
        "gcode_files": coord.gcode_files if coord.files_enabled else [],
        "gcode_details": coord.gcode_details if coord.files_enabled else [],
        "selected_file": coord.selected_file,
        "spoolman_ui_url": coord.spoolman.ui_base_url if coord.spoolman else "",
        "spoolman_create_url": coord.spoolman.create_spool_url if coord.spoolman else "",
        "module_config": {
            "smart_plug": bool(coord.power_manager.has_smart_plug),
            "smart_plug_entity": coord.get_conf(CONF_SMART_PLUG_ENTITY) or "",
            "spoolman": coord.spoolman_enabled,
            "camera": bool(coord.get_conf(CONF_ENABLE_CAMERA, False)),
            "camera_mode": coord.get_conf(CONF_CAMERA_MODE) or "external",
            "external_camera_entity": coord.get_conf(CONF_EXTERNAL_CAMERA_ENTITY) or "",
            "external_camera_switch": coord.get_conf(CONF_EXTERNAL_CAMERA_SWITCH) or "",
            "timelapse": coord.timelapse_manager.enabled,
            "obico": coord.obico_manager.enabled,
            "obico_camera_entity": coord.get_conf(CONF_OBICO_CAMERA_ENTITY) or "",
            "obico_switch_entity": coord.get_conf(CONF_OBICO_SWITCH_ENTITY) or "",
            "obico_error_binary_sensor": coord.get_conf(CONF_OBICO_ERROR_BINARY_SENSOR) or "",
            "obico_confidence_sensor": coord.get_conf(CONF_OBICO_CONFIDENCE_SENSOR) or "",
            "files": coord.files_enabled,
            "gcode_console": bool(coord.get_conf(CONF_ENABLE_GCODE_CONSOLE, False)),
            "firmware_check": coord.firmware_check_enabled,
        },
    }


SENSORS: tuple[AD5XSensorDescription, ...] = (
    AD5XSensorDescription(
        key="status",
        name="Status",
        icon="mdi:printer-3d",
        value_fn=lambda c: PRINTER_STATUS_LABELS.get(
            c.printer_status, c.printer_status.capitalize()
        ),
        attrs_fn=_status_attrs,
    ),
    AD5XSensorDescription(
        key="print_progress",
        name="Print Progress",
        icon="mdi:progress-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: (
            round(float(_detail(c).get("printProgress") or 0.0) * 100)
            if c.printer_online and _detail(c).get("printProgress") is not None
            else 0
        ),
    ),
    AD5XSensorDescription(
        key="extruder_temp",
        name="Extruder Temperature",
        icon="mdi:printer-3d-nozzle-heat",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: round(c.extruder_temp, 1) if c.printer_online else None,
        attrs_fn=lambda c: {
            "target_temp": round(float(_detail(c).get("rightTargetTemp") or 0.0), 1)
        },
    ),
    AD5XSensorDescription(
        key="bed_temp",
        name="Bed Temperature",
        icon="mdi:radiator",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: round(c.bed_temp, 1) if c.printer_online else None,
        attrs_fn=lambda c: {
            "target_temp": round(float(_detail(c).get("platTargetTemp") or 0.0), 1)
        },
    ),
    AD5XSensorDescription(
        key="current_file",
        name="Current File",
        icon="mdi:file-cad",
        value_fn=lambda c: (_detail(c).get("printFileName") or "Idle") if c.printer_online else "Offline",
    ),
    AD5XSensorDescription(
        key="layer_progress",
        name="Layer Progress",
        icon="mdi:layers-triple",
        value_fn=lambda c: (
            f"{int(_detail(c).get('printLayer') or 0)} / {int(_detail(c).get('targetPrintLayer') or 0)}"
            if int(_detail(c).get("targetPrintLayer") or 0) > 0
            else "-- / --"
        ),
        attrs_fn=lambda c: {
            "current_layer": int(_detail(c).get("printLayer") or 0),
            "total_layers": int(_detail(c).get("targetPrintLayer") or 0),
        },
    ),
    AD5XSensorDescription(
        key="print_time",
        name="Print Time",
        icon="mdi:timer-outline",
        value_fn=lambda c: (
            f"{_fmt_hms(_detail(c).get('printDuration'))} / {_fmt_hms(_detail(c).get('estimatedTime'))}"
            if (
                int(float(_detail(c).get("printDuration") or 0)) > 0
                or int(float(_detail(c).get("estimatedTime") or 0)) > 0
            )
            else "-- / --"
        ),
        attrs_fn=lambda c: {
            "elapsed_seconds": int(float(_detail(c).get("printDuration") or 0)),
            "estimated_seconds": int(float(_detail(c).get("estimatedTime") or 0)),
        },
    ),
    AD5XSensorDescription(
        key="print_speed",
        name="Current Print Speed",
        icon="mdi:speedometer",
        native_unit_of_measurement="mm/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: int(float(_detail(c).get("currentPrintSpeed") or 0)) if c.printer_online else 0,
        attrs_fn=lambda c: {
            "speed_adjust_pct": int(float(_detail(c).get("printSpeedAdjust") or 100)),
            "cooling_fan_pct": int(float(_detail(c).get("coolingFanSpeed") or 0)),
        },
    ),
    AD5XSensorDescription(
        key="infill",
        name="Current Infill",
        icon="mdi:texture-box",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda c: int(float(_detail(c).get("fillAmount") or 0)) if c.printer_online else 0,
    ),
    AD5XSensorDescription(
        key="coordinates",
        name="XYZ Coordinates",
        icon="mdi:axis-arrow",
        value_fn=lambda c: (
            f"X:{round(float(_detail(c)['coordinate'][0]), 1)} "
            f"Y:{round(float(_detail(c)['coordinate'][1]), 1)} "
            f"Z:{round(float(_detail(c)['coordinate'][2]), 2)}"
            if isinstance(_detail(c).get("coordinate"), list) and len(_detail(c)["coordinate"]) >= 3
            else "Unknown"
        ),
        attrs_fn=lambda c: (
            {
                "x": round(float(_detail(c)["coordinate"][0]), 2),
                "y": round(float(_detail(c)["coordinate"][1]), 2),
                "z": round(float(_detail(c)["coordinate"][2]), 2),
            }
            if isinstance(_detail(c).get("coordinate"), list) and len(_detail(c)["coordinate"]) >= 3
            else {"x": 0, "y": 0, "z": 0}
        ),
    ),
    AD5XSensorDescription(
        key="job_filament_estimate",
        name="Job Estimated Filament",
        icon="mdi:spool",
        native_unit_of_measurement=UnitOfMass.GRAMS,
        value_fn=lambda c: round(float(_detail(c).get("estimatedRightWeight") or 0.0), 1),
        attrs_fn=lambda c: {
            "estimated_meters": round(float(_detail(c).get("estimatedRightLen") or 0.0) / 1000.0, 2)
        },
    ),
    AD5XSensorDescription(
        key="total_print_time",
        name="Lifetime Print Time",
        icon="mdi:clock-check-outline",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: (
            round(float(_detail(c).get("cumulativePrintTime") or 0.0) / 60.0, 1)
            if c.printer_online
            else None
        ),
    ),
    AD5XSensorDescription(
        key="total_filament",
        name="Lifetime Filament Used",
        icon="mdi:printer-3d-nozzle",
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: (
            round(float(_detail(c).get("cumulativeFilament") or 0.0), 2)
            if c.printer_online
            else None
        ),
    ),
    AD5XSensorDescription(
        key="free_disk_space",
        name="Free Disk Space",
        icon="mdi:database-check",
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: (
            round(float(_detail(c).get("remainingDiskSpace") or 0.0), 2)
            if c.printer_online
            else None
        ),
        attrs_fn=lambda c: {
            "used_pct": round(
                max(0.0, (1.0 - (float(_detail(c).get("remainingDiskSpace") or 0.0) / 5.66)) * 100.0),
                1,
            )
            if c.printer_online
            else None
        },
    ),
    AD5XSensorDescription(
        key="nozzle_model",
        name="Nozzle",
        icon="mdi:printer-3d-nozzle-outline",
        value_fn=lambda c: str(_detail(c).get("nozzleModel") or "0.4mm") if c.printer_online else None,
    ),
    AD5XSensorDescription(
        key="gcode_response",
        name="Console Response",
        icon="mdi:console-line",
        value_fn=lambda c: (c.last_gcode_response[:250] if c.last_gcode_response else "Idle"),
        attrs_fn=lambda c: {"full_response": c.last_gcode_response},
        enabled_fn=lambda c: bool(c.get_conf(CONF_ENABLE_GCODE_CONSOLE, False)),
    ),
    AD5XSensorDescription(
        key="firmware_status",
        name="Firmware Update Status",
        icon="mdi:package-up",
        value_fn=lambda c: (
            "Up to date"
            if c.firmware_info.get("up_to_date", True)
            else f"{len(c.firmware_info.get('packages', {}))} update(s) available"
        ),
        attrs_fn=lambda c: {
            "installed_version": _detail(c).get("firmwareVersion") or "",
            "packages": c.firmware_info.get("packages") or {},
            "release_notes": c.firmware_info.get("release_notes") or "",
        },
        enabled_fn=lambda c: c.firmware_check_enabled,
    ),
)


class AD5XBaseSensor(CoordinatorEntity[FlashForgeAD5XCoordinator], SensorEntity):
    """Standard AD5X telemetry sensor."""

    entity_description: AD5XSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FlashForgeAD5XCoordinator, description: AD5XSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        base = {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": self.entity_description.key,
        }
        if self.entity_description.attrs_fn:
            base.update(self.entity_description.attrs_fn(self.coordinator))
        return base


class AD5XIfsSlotSensor(CoordinatorEntity[FlashForgeAD5XCoordinator], SensorEntity):
    """Unified IFS Slot sensor combining live AD5X slot state + assigned Spoolman metadata."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:printer-3d-nozzle-outline"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator, slot_id: int) -> None:
        super().__init__(coordinator)
        self.slot_id = slot_id
        self._attr_name = f"IFS Slot {slot_id}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_ifs_slot_{slot_id}"
        self._attr_device_info = coordinator.device_info

    def _raw_slot(self) -> dict[str, Any]:
        matl = _detail(self.coordinator).get("matlStationInfo") or {}
        for item in matl.get("slotInfos") or []:
            if isinstance(item, dict) and int(item.get("slotId") or 0) == self.slot_id:
                return item
        return {}

    @property
    def native_value(self) -> str:
        raw = self._raw_slot()
        spool = self.coordinator.get_assigned_spool(self.slot_id)
        has_fil = bool(raw.get("hasFilament", False))
        if spool:
            return f"{spool['vendor']} {spool['material']} ({spool['remaining_weight']}g)"
        if has_fil:
            return str(raw.get("materialName") or "Loaded")
        return "Empty"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self._raw_slot()
        spool = self.coordinator.get_assigned_spool(self.slot_id)
        matl = _detail(self.coordinator).get("matlStationInfo") or {}
        is_loaded_in_nozzle = int(matl.get("currentLoadSlot") or 0) == self.slot_id
        printer_col = raw.get("materialColor") or ""
        display_col = (
            spool["color_hex"]
            if spool
            else (normalize_hex_color(printer_col) if printer_col else "#555555")
        )
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": f"ifs_slot_{self.slot_id}",
            "slot": self.slot_id,
            "has_filament": bool(raw.get("hasFilament", False)),
            "active_in_extruder": is_loaded_in_nozzle,
            "printer_material": raw.get("materialName") or "",
            "printer_color": printer_col,
            "display_color": display_col,
            "spool_id": spool["id"] if spool else None,
            "vendor": spool["vendor"] if spool else None,
            "material": spool["material"] if spool else (raw.get("materialName") or None),
            "filament_name": spool["name"] if spool else None,
            "remaining_weight_g": spool["remaining_weight"] if spool else None,
            "remaining_length_m": spool["remaining_length_m"] if spool else None,
            "location": spool["location"] if spool else None,
            "tags": spool["tags"] if spool else [],
        }


class AD5XTimelapseSummarySensor(CoordinatorEntity[FlashForgeAD5XCoordinator], SensorEntity):
    """Reports captured frame count and estimated video duration for a timelapse channel."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:movie-open-outline"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator, channel_key: str) -> None:
        super().__init__(coordinator)
        self.channel_key = channel_key
        ch = coordinator.timelapse_manager.channels[channel_key]
        suffix = f" ({ch.label})" if len(coordinator.timelapse_manager.channels) > 1 else ""
        self._attr_name = f"Timelapse Summary{suffix}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_timelapse_summary_{channel_key}"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str:
        ch = self.coordinator.timelapse_manager.channels.get(self.channel_key)
        if not ch:
            return "0 frames | 0.0s"
        if ch.is_compiling:
            return "Compiling MP4..."
        secs = round(ch.frame_count / max(1, self.coordinator.timelapse_manager.fps), 1)
        return f"{ch.frame_count} frames | ~{secs}s"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ch = self.coordinator.timelapse_manager.channels.get(self.channel_key)
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": f"timelapse_summary_{self.channel_key}",
            "channel": self.channel_key,
            "frame_count": ch.frame_count if ch else 0,
            "fps": self.coordinator.timelapse_manager.fps,
            "mode": ch.mode if ch else "Disabled",
            "is_compiling": ch.is_compiling if ch else False,
            "last_video_path": ch.last_video_path if ch else None,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AD5X sensors based on enabled modules."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        AD5XBaseSensor(coordinator, desc)
        for desc in SENSORS
        if desc.enabled_fn(coordinator)
    ]

    for slot_id in (1, 2, 3, 4):
        entities.append(AD5XIfsSlotSensor(coordinator, slot_id))

    if coordinator.timelapse_manager.enabled:
        for ch_key in coordinator.timelapse_manager.channels:
            entities.append(AD5XTimelapseSummarySensor(coordinator, ch_key))

    async_add_entities(entities)
