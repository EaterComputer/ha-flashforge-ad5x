"""Binary sensor platform for FlashForge AD5X connectivity and IFS filament presence."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FlashForgeAD5XCoordinator


class AD5XOnlineBinarySensor(CoordinatorEntity[FlashForgeAD5XCoordinator], BinarySensorEntity):
    """Indicates whether the AD5X is currently reachable on the network."""

    _attr_has_entity_name = True
    _attr_name = "Connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: FlashForgeAD5XCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_online"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.printer_online

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": "online",
        }


class AD5XSlotFilamentBinarySensor(
    CoordinatorEntity[FlashForgeAD5XCoordinator], BinarySensorEntity
):
    """Indicates whether filament is physically detected in IFS Slot 1-4 (fixes YAML slotInfos[0] bug)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:check-circle-outline"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator, slot_id: int) -> None:
        super().__init__(coordinator)
        self.slot_id = slot_id
        self._attr_name = f"IFS Slot {slot_id} Filament Detected"
        self._attr_icon = "mdi:check-circle-outline"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_slot_{slot_id}_has_filament"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        detail = (self.coordinator.data or {}).get("detail") or {}
        matl = detail.get("matlStationInfo") or {}
        for item in matl.get("slotInfos") or []:
            if isinstance(item, dict) and int(item.get("slotId") or 0) == self.slot_id:
                return bool(item.get("hasFilament", False))
        return False

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": f"slot_{self.slot_id}_has_filament",
            "slot": self.slot_id,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AD5X binary sensors."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [AD5XOnlineBinarySensor(coordinator)]
    for slot_id in (1, 2, 3, 4):
        entities.append(AD5XSlotFilamentBinarySensor(coordinator, slot_id))
    async_add_entities(entities)
