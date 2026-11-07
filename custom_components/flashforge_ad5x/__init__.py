"""The FlashForge AD5X Custom Integration for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    CARD_JS_URL,
    CARD_JS_VERSION,
    DOMAIN,
    PLATFORMS,
    SERVICE_ASSIGN_SPOOL,
    SERVICE_CHECK_FIRMWARE,
    SERVICE_CONFIGURE_IFS_SLOT,
    SERVICE_DOWNLOAD_FIRMWARE,
    SERVICE_IFS_LOAD_UNLOAD,
    SERVICE_JOG,
    SERVICE_RENAME_PRINTER,
    SERVICE_SEND_GCODE,
    SERVICE_START_PRINT,
    SERVICE_UNASSIGN_SPOOL,
    SERVICE_UPLOAD_FILE,
    STATIC_URL_BASE,
)
from .coordinator import FlashForgeAD5XCoordinator
from .http_views import (
    AD5XFileUploadApiView,
    AD5XSpoolPickerApiView,
    AD5XSpoolPickerPageView,
)

_LOGGER = logging.getLogger(__name__)

_FRONTEND_REGISTERED_KEY = f"{DOMAIN}_frontend_registered"
_SERVICES_REGISTERED_KEY = f"{DOMAIN}_services_registered"


async def _async_register_frontend_and_views(hass: HomeAssistant) -> None:
    """Serve bundled Custom Lovelace Cards and register HTTP Spool Picker views once."""
    if hass.data.get(_FRONTEND_REGISTERED_KEY):
        return

    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                url_path=STATIC_URL_BASE,
                path=str(frontend_dir),
                cache_headers=False,
            )
        ]
    )

    versioned_url = f"{CARD_JS_URL}?v={CARD_JS_VERSION}"
    add_extra_js_url(hass, versioned_url)

    # Also register in Lovelace storage resources when running in storage mode
    lovelace_data = hass.data.get("lovelace")
    resources = getattr(lovelace_data, "resources", None) if lovelace_data else None
    if resources is not None and getattr(resources, "async_loaded", False):
        try:
            existing = [
                item
                for item in resources.async_items()
                if str(item.get("url", "")).startswith(CARD_JS_URL)
            ]
            if not existing and hasattr(resources, "async_create_item"):
                await resources.async_create_item(
                    {"res_type": "module", "url": versioned_url}
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Skipping Lovelace storage resource auto-create: %s", err)

    hass.http.register_view(AD5XSpoolPickerPageView())
    hass.http.register_view(AD5XSpoolPickerApiView())
    hass.http.register_view(AD5XFileUploadApiView())
    hass.data[_FRONTEND_REGISTERED_KEY] = True


def _resolve_service_coordinator(
    hass: HomeAssistant, call: ServiceCall
) -> FlashForgeAD5XCoordinator:
    """Resolve target coordinator from optional `entry_id` or default to first configured printer."""
    entries: dict[str, FlashForgeAD5XCoordinator] = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError("No FlashForge AD5X printers are currently configured.")
    entry_id = call.data.get("entry_id")
    if entry_id and entry_id in entries:
        return entries[entry_id]
    return next(iter(entries.values()))


def _register_domain_services(hass: HomeAssistant) -> None:
    """Register domain-level actions/services for AD5X control, IFS/Spoolman, files, and G-code."""
    if hass.data.get(_SERVICES_REGISTERED_KEY):
        return

    async def _handle_send_gcode(call: ServiceCall) -> ServiceResponse:
        coord = _resolve_service_coordinator(hass, call)
        command = str(call.data["command"]).strip()
        resp = await coord.api.async_send_raw_gcode(command)
        coord.last_gcode_response = resp
        coord.async_update_listeners()
        return {"command": command, "response": resp}

    async def _handle_jog(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        axis = str(call.data["axis"]).lower()
        distance = float(call.data["distance"])
        await coord.api.async_move(axis, distance)
        await coord.async_request_refresh()

    async def _handle_assign_spool(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        slot = int(call.data["slot"])
        spool_id = int(call.data["spool_id"])
        nfc_uid = str(call.data.get("nfc_uid") or "").strip() or None
        await coord.async_assign_spool_to_slot(
            slot_id=slot, spool_id=spool_id, bind_nfc_uid=nfc_uid
        )

    async def _handle_unassign_spool(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        slot = int(call.data["slot"])
        await coord.async_unassign_spool(slot_id=slot)

    async def _handle_configure_ifs_slot(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        slot = int(call.data["slot"])
        material = str(call.data["material"])
        color = str(call.data["color"])
        await coord.api.async_configure_ifs_slot(slot, material, color)
        await coord.async_request_refresh()

    async def _handle_ifs_load_unload(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        slot = int(call.data["slot"])
        unload = str(call.data.get("operation", "load")).lower() in ("unload", "descargar", "1")
        await coord.api.async_ifs_load_unload(slot, unload=unload)
        await coord.async_request_refresh()

    async def _handle_start_print(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        filename = str(call.data.get("filename") or coord.selected_file).strip()
        if not filename:
            raise HomeAssistantError("No G-code / 3MF filename specified to print.")

        raw_map = call.data.get("tool_mappings") or {}
        tool_map: dict[int, int] = {}
        if isinstance(raw_map, dict):
            for k, v in raw_map.items():
                tool_map[int(k)] = int(v)

        default_slot = int(call.data.get("slot", 1))
        opts = coord.persistent_state.get("print_options") or {}
        leveling = bool(call.data.get("leveling_before_print", opts.get("leveling", True)))
        flow = bool(call.data.get("flow_calibration", opts.get("flow", False)))
        inspect = bool(call.data.get("first_layer_inspection", opts.get("inspection", False)))

        mappings = coord.build_material_mappings(
            filename=filename,
            explicit_tool_to_slot=tool_map or None,
            default_slot=default_slot,
        )
        await coord.api.async_start_print(
            filename=filename,
            material_mappings=mappings,
            leveling_before_print=leveling,
            flow_calibration=flow,
            first_layer_inspection=inspect,
        )
        await coord.async_request_refresh()

    async def _handle_upload_file(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        file_path = Path(str(call.data["file_path"]).strip())
        if not file_path.is_absolute():
            file_path = Path(hass.config.path(str(file_path)))

        default_slot = int(call.data.get("slot", 1))
        opts = coord.persistent_state.get("print_options") or {}
        print_now = bool(call.data.get("print_now", opts.get("print_now", False)))
        leveling = bool(call.data.get("leveling_before_print", opts.get("leveling", True)))
        flow = bool(call.data.get("flow_calibration", opts.get("flow", False)))
        inspect = bool(call.data.get("first_layer_inspection", opts.get("inspection", False)))

        mappings = coord.build_material_mappings(default_slot=default_slot)
        await coord.api.async_upload_gcode(
            file_path=file_path,
            material_mappings=mappings,
            print_now=print_now,
            leveling_before_print=leveling,
            flow_calibration=flow,
            first_layer_inspection=inspect,
        )
        await coord.async_request_refresh()

    async def _handle_rename_printer(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        await coord.api.async_rename_printer(str(call.data["name"]))
        await coord.async_request_refresh()

    async def _handle_check_firmware(call: ServiceCall) -> ServiceResponse:
        coord = _resolve_service_coordinator(hass, call)
        detail = (coord.data or {}).get("detail") or {}
        cur_sw = str(call.data.get("current_software") or detail.get("firmwareVersion") or "3.1.5")
        info = await coord.api.async_check_firmware_updates(current_software=cur_sw)
        coord.firmware_info = info
        coord.async_update_listeners()
        return info

    async def _handle_download_firmware(call: ServiceCall) -> None:
        coord = _resolve_service_coordinator(hass, call)
        url = str(call.data["url"]).strip()
        filename = str(call.data.get("filename") or url.rsplit("/", 1)[-1] or "ad5x_update.tgz")
        dest = Path(hass.config.path("www", "ff_updates", filename))
        await coord.api.async_download_firmware(url, dest)
        await coord.async_send_notification(
            title="✅ AD5X Firmware Downloaded",
            message=f"Saved **{filename}** to `/local/ff_updates/{filename}`.",
            tag="ad5x_firmware_download",
            click_url=f"/local/ff_updates/{filename}",
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_GCODE,
        _handle_send_gcode,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("command"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_JOG,
        _handle_jog,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("axis"): vol.In(["x", "y", "z", "X", "Y", "Z"]),
                vol.Required("distance"): vol.Coerce(float),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ASSIGN_SPOOL,
        _handle_assign_spool,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
                vol.Required("spool_id"): vol.Coerce(int),
                vol.Optional("nfc_uid"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNASSIGN_SPOOL,
        _handle_unassign_spool,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIGURE_IFS_SLOT,
        _handle_configure_ifs_slot,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
                vol.Required("material"): cv.string,
                vol.Required("color"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IFS_LOAD_UNLOAD,
        _handle_ifs_load_unload,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
                vol.Optional("operation", default="load"): vol.In(["load", "unload"]),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_PRINT,
        _handle_start_print,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Optional("filename"): cv.string,
                vol.Optional("slot", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
                vol.Optional("tool_mappings"): dict,
                vol.Optional("leveling_before_print"): cv.boolean,
                vol.Optional("flow_calibration"): cv.boolean,
                vol.Optional("first_layer_inspection"): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPLOAD_FILE,
        _handle_upload_file,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("file_path"): cv.string,
                vol.Optional("slot", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
                vol.Optional("print_now"): cv.boolean,
                vol.Optional("leveling_before_print"): cv.boolean,
                vol.Optional("flow_calibration"): cv.boolean,
                vol.Optional("first_layer_inspection"): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_PRINTER,
        _handle_rename_printer,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("name"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK_FIRMWARE,
        _handle_check_firmware,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Optional("current_software"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_FIRMWARE,
        _handle_download_firmware,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("url"): cv.string,
                vol.Optional("filename"): cv.string,
            }
        ),
    )

    hass.data[_SERVICES_REGISTERED_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FlashForge AD5X from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_frontend_and_views(hass)
    _register_domain_services(hass)

    coordinator = FlashForgeAD5XCoordinator(hass, entry)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a FlashForge AD5X ConfigEntry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: FlashForgeAD5XCoordinator | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator:
            await coordinator.async_shutdown_managers()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload ConfigEntry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
