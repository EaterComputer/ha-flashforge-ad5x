"""Number platform for FlashForge AD5X temperatures, print overrides, cooldown, Spoolman purge, and Obico thresholds."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FlashForgeAD5XCoordinator


def _detail(coord: FlashForgeAD5XCoordinator) -> dict[str, Any]:
    return (coord.data or {}).get("detail") or {}


def _cur_print_params(coord: FlashForgeAD5XCoordinator) -> tuple[float, int, int]:
    """Return current (z_offset, speed_pct, cooling_fan_pct) from `/detail`."""
    d = _detail(coord)
    z_off = round(float(d.get("zAxisCompensation") or 0.0), 3)
    speed = int(float(d.get("printSpeedAdjust") or 100))
    fan = int(float(d.get("coolingFanSpeed") or 0))
    return z_off, speed, fan


async def _set_extruder_target(c: FlashForgeAD5XCoordinator, val: float) -> None:
    c.manual_extruder_target = int(round(val))
    await c.api.async_set_extruder_temp(int(round(val)))
    await c.async_request_refresh()


async def _set_bed_target(c: FlashForgeAD5XCoordinator, val: float) -> None:
    c.manual_bed_target = int(round(val))
    await c.api.async_set_bed_temp(int(round(val)))
    await c.async_request_refresh()


async def _set_speed_pct(c: FlashForgeAD5XCoordinator, val: float) -> None:
    z_off, _speed, fan = _cur_print_params(c)
    await c.api.async_set_print_params(
        z_offset=z_off, speed_pct=int(round(val)), cooling_fan_pct=fan
    )
    await c.async_request_refresh()


async def _set_fan_pct(c: FlashForgeAD5XCoordinator, val: float) -> None:
    z_off, speed, _fan = _cur_print_params(c)
    await c.api.async_set_print_params(
        z_offset=z_off, speed_pct=speed, cooling_fan_pct=int(round(val))
    )
    await c.async_request_refresh()


async def _set_z_offset(c: FlashForgeAD5XCoordinator, val: float) -> None:
    _z_off, speed, fan = _cur_print_params(c)
    await c.api.async_set_print_params(
        z_offset=round(float(val), 3), speed_pct=speed, cooling_fan_pct=fan
    )
    await c.async_request_refresh()


@dataclass(frozen=True, kw_only=True)
class AD5XNumberDescription(NumberEntityDescription):
    """Describes a FlashForge AD5X number control entity."""

    value_fn: Callable[[FlashForgeAD5XCoordinator], float | None]
    set_fn: Callable[[FlashForgeAD5XCoordinator, float], Awaitable[None]]
    require_online: bool = True
    enabled_fn: Callable[[FlashForgeAD5XCoordinator], bool] = lambda _c: True


NUMBERS: tuple[AD5XNumberDescription, ...] = (
    AD5XNumberDescription(
        key="target_extruder_temp",
        name="Target Extruder Temperature",
        icon="mdi:printer-3d-nozzle-heat",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_min_value=0,
        native_max_value=330,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        value_fn=lambda c: (
            round(float(_detail(c).get("rightTargetTemp") or 0.0), 0)
            if c.printer_online
            else None
        ),
        set_fn=_set_extruder_target,
    ),
    AD5XNumberDescription(
        key="target_bed_temp",
        name="Target Bed Temperature",
        icon="mdi:radiator",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_min_value=0,
        native_max_value=130,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        value_fn=lambda c: (
            round(float(_detail(c).get("platTargetTemp") or 0.0), 0)
            if c.printer_online
            else None
        ),
        set_fn=_set_bed_target,
    ),
    AD5XNumberDescription(
        key="print_speed_pct",
        name="Print Speed Override",
        icon="mdi:speedometer",
        native_min_value=10,
        native_max_value=200,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda c: (
            float(int(float(_detail(c).get("printSpeedAdjust") or 100)))
            if c.printer_online
            else None
        ),
        set_fn=_set_speed_pct,
    ),
    AD5XNumberDescription(
        key="cooling_fan_pct",
        name="Part Cooling Fan",
        icon="mdi:fan",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda c: (
            float(int(float(_detail(c).get("coolingFanSpeed") or 0)))
            if c.printer_online
            else None
        ),
        set_fn=_set_fan_pct,
    ),
    AD5XNumberDescription(
        key="z_offset",
        name="Z-Offset Compensation",
        icon="mdi:arrow-expand-vertical",
        native_min_value=-2.0,
        native_max_value=2.0,
        native_step=0.005,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        mode=NumberMode.BOX,
        value_fn=lambda c: (
            round(float(_detail(c).get("zAxisCompensation") or 0.0), 3)
            if c.printer_online
            else None
        ),
        set_fn=_set_z_offset,
    ),
    AD5XNumberDescription(
        key="cooldown_temp",
        name="Cooldown Power-Off Threshold",
        icon="mdi:thermometer-chevron-down",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_min_value=30,
        native_max_value=80,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.SLIDER,
        require_online=False,
        value_fn=lambda c: round(c.power_manager.cooldown_temp, 0),
        set_fn=lambda c, v: c.power_manager.async_set_cooldown_temp(v),
        enabled_fn=lambda c: c.power_manager.has_smart_plug,
    ),
    AD5XNumberDescription(
        key="purge_mm_per_swap",
        name="IFS Purge Waste per Swap",
        icon="mdi:printer-3d-nozzle-alert-outline",
        native_min_value=0,
        native_max_value=1500,
        native_step=10,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        mode=NumberMode.BOX,
        require_online=False,
        value_fn=lambda c: round(c.purge_mm_per_swap, 0),
        set_fn=lambda c, v: c.async_update_persistent_state(
            {"purge_mm_per_swap": round(float(v), 1)}
        ),
        enabled_fn=lambda c: c.spoolman_enabled,
    ),
    AD5XNumberDescription(
        key="obico_confidence_threshold",
        name="Obico Alert Confidence Threshold",
        icon="mdi:shield-alert-outline",
        native_min_value=10,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        require_online=False,
        value_fn=lambda c: round(c.obico_manager.confidence_threshold, 0),
        set_fn=lambda c, v: c.async_update_persistent_state(
            {"obico_confidence_threshold": float(v)}
        ),
        enabled_fn=lambda c: c.obico_manager.enabled,
    ),
    AD5XNumberDescription(
        key="obico_cooldown_min",
        name="Obico Alert Cooldown",
        icon="mdi:timer-sand",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.SLIDER,
        require_online=False,
        value_fn=lambda c: round(c.obico_manager.cooldown_minutes, 0),
        set_fn=lambda c, v: c.async_update_persistent_state(
            {"obico_cooldown_min": float(v)}
        ),
        enabled_fn=lambda c: c.obico_manager.enabled,
    ),
)


class AD5XNumberEntity(CoordinatorEntity[FlashForgeAD5XCoordinator], NumberEntity):
    """Number entity for FlashForge AD5X live parameters and module thresholds."""

    entity_description: AD5XNumberDescription
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FlashForgeAD5XCoordinator, description: AD5XNumberDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        if self.entity_description.require_online:
            return self.coordinator.printer_online
        return True

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_fn(self.coordinator, value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": self.entity_description.key,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AD5X number entities based on enabled modules."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            AD5XNumberEntity(coordinator, desc)
            for desc in NUMBERS
            if desc.enabled_fn(coordinator)
        ]
    )
