"""Switch platform for FlashForge AD5X chamber light, power safety, Spoolman, print options, and Obico ML."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FlashForgeAD5XCoordinator


class AD5XLightSwitch(CoordinatorEntity[FlashForgeAD5XCoordinator], SwitchEntity):
    """Controls the AD5X chamber LED light."""

    _attr_has_entity_name = True
    _attr_name = "Chamber Light"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light"
        self._attr_device_info = coordinator.device_info

    @property
    def icon(self) -> str:
        return "mdi:lightbulb-on" if self.is_on else "mdi:lightbulb-outline"

    @property
    def available(self) -> bool:
        return self.coordinator.printer_online

    @property
    def is_on(self) -> bool:
        detail = (self.coordinator.data or {}).get("detail") or {}
        return str(detail.get("lightStatus") or "").lower() == "open"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.api.async_set_light(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.api.async_set_light(False)
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ad5x_entry_id": self.coordinator.entry.entry_id, "ad5x_key": "light"}


class AD5XSafePowerSwitch(CoordinatorEntity[FlashForgeAD5XCoordinator], SwitchEntity):
    """Controls the optional Smart Plug with print & hotend cooldown safety interlocks."""

    _attr_has_entity_name = True
    _attr_name = "Safe Printer Power"
    _attr_icon = "mdi:power-plug"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_safe_power"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.power_manager.is_plug_on()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.power_manager.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.power_manager.async_safe_turn_off(force=False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": "safe_power",
            "smart_plug_entity": self.coordinator.power_manager.smart_plug_entity,
            "cooldown_threshold_c": self.coordinator.power_manager.cooldown_temp,
            "waiting_for_cooldown": self.coordinator.power_manager.waiting_for_cooldown,
        }


class AD5XPersistentToggleSwitch(CoordinatorEntity[FlashForgeAD5XCoordinator], SwitchEntity):
    """Generic persistent configuration switch backed by coordinator storage."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FlashForgeAD5XCoordinator,
        key: str,
        name: str,
        icon: str,
        state_key: str,
        default: bool = False,
        nested_dict: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._state_key = state_key
        self._default = default
        self._nested_dict = nested_dict
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        if self._nested_dict:
            sub = self.coordinator.persistent_state.get(self._nested_dict) or {}
            return bool(sub.get(self._state_key, self._default))
        return bool(self.coordinator.persistent_state.get(self._state_key, self._default))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, val: bool) -> None:
        if self._key == "auto_power_off":
            await self.coordinator.power_manager.async_set_auto_power_off(val)
            return
        if self._nested_dict:
            sub = dict(self.coordinator.persistent_state.get(self._nested_dict) or {})
            sub[self._state_key] = val
            await self.coordinator.async_update_persistent_state({self._nested_dict: sub})
        else:
            await self.coordinator.async_update_persistent_state({self._state_key: val})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ad5x_entry_id": self.coordinator.entry.entry_id, "ad5x_key": self._key}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AD5X switches based on enabled modules."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [AD5XLightSwitch(coordinator)]

    if coordinator.power_manager.has_smart_plug:
        entities.append(AD5XSafePowerSwitch(coordinator))
        entities.append(
            AD5XPersistentToggleSwitch(
                coordinator,
                key="auto_power_off",
                name="Auto Power-Off After Print & Cooldown",
                icon="mdi:timer-off-outline",
                state_key="auto_power_off",
                default=False,
            )
        )

    if coordinator.spoolman_enabled:
        entities.append(
            AD5XPersistentToggleSwitch(
                coordinator,
                key="snap_printer_colors",
                name="Snap Spool Colors to AD5X Palette",
                icon="mdi:palette",
                state_key="snap_printer_colors",
                default=True,
            )
        )

    if coordinator.files_enabled:
        entities.extend(
            [
                AD5XPersistentToggleSwitch(
                    coordinator,
                    key="print_leveling",
                    name="Level Before Print",
                    icon="mdi:format-align-bottom",
                    state_key="leveling",
                    default=True,
                    nested_dict="print_options",
                ),
                AD5XPersistentToggleSwitch(
                    coordinator,
                    key="print_flow",
                    name="Flow Calibration Before Print",
                    icon="mdi:water-percent",
                    state_key="flow",
                    default=False,
                    nested_dict="print_options",
                ),
                AD5XPersistentToggleSwitch(
                    coordinator,
                    key="print_inspection",
                    name="First Layer Inspection",
                    icon="mdi:magnify-scan",
                    state_key="inspection",
                    default=False,
                    nested_dict="print_options",
                ),
            ]
        )

    if coordinator.obico_manager.enabled:
        entities.extend(
            [
                AD5XPersistentToggleSwitch(
                    coordinator,
                    key="obico_notify",
                    name="Notify on Obico Alert",
                    icon="mdi:bell-alert-outline",
                    state_key="obico_notify",
                    default=True,
                ),
                AD5XPersistentToggleSwitch(
                    coordinator,
                    key="obico_auto_pause",
                    name="Auto-Pause on Obico Alert",
                    icon="mdi:pause-octagon-outline",
                    state_key="obico_auto_pause",
                    default=False,
                ),
            ]
        )

    async_add_entities(entities)
