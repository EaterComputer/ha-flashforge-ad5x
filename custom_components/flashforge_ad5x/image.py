"""Image platform exposing the active or selected 3D model thumbnail from the FlashForge AD5X."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import FlashForgeAD5XCoordinator

# Minimal 1x1 transparent PNG placeholder when no file thumbnail has been loaded yet
_EMPTY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _detect_mime_type(data: bytes | None) -> str:
    if not data:
        return "image/png"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return "image/png"


class AD5XPrintThumbnailImage(
    CoordinatorEntity[FlashForgeAD5XCoordinator], ImageEntity
):
    """Serves the G-code / 3MF preview thumbnail fetched via `/gcodeThumb` or `/getThum`."""

    _attr_has_entity_name = True
    _attr_name = "Model Preview Thumbnail"
    _attr_icon = "mdi:cube-scan"

    def __init__(self, coordinator: FlashForgeAD5XCoordinator) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_thumbnail"
        self._attr_device_info = coordinator.device_info
        self._last_digest: str = ""
        self._attr_image_last_updated = dt_util.utcnow()
        self._sync_digest()

    def _sync_digest(self) -> bool:
        raw = self.coordinator.thumbnail_bytes
        digest = hashlib.md5(raw).hexdigest() if raw else ""
        self._attr_content_type = _detect_mime_type(raw)
        if digest != self._last_digest:
            self._last_digest = digest
            self._attr_image_last_updated = dt_util.utcnow()
            return True
        return False

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_digest()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        self._sync_digest()
        return self.coordinator.thumbnail_bytes or _EMPTY_PNG

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": "thumbnail",
            "filename": self.coordinator.thumbnail_filename,
            "has_thumbnail": bool(self.coordinator.thumbnail_bytes),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AD5X model preview thumbnail image entity."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AD5XPrintThumbnailImage(coordinator)])
