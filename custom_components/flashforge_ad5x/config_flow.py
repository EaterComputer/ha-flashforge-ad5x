"""Multi-step UI Config Flow and Options Flow for FlashForge AD5X."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    FlashForgeAD5XApiClient,
    FlashForgeAuthError,
    FlashForgeConnectionError,
)
from .const import (
    CAMERA_MODE_BOTH,
    CAMERA_MODE_EXTERNAL,
    CAMERA_MODE_NATIVE,
    CONF_CAMERA_MODE,
    CONF_CHECK_CODE,
    CONF_COOLDOWN_TEMP,
    CONF_DISCOVERED_PRINTER,
    CONF_ENABLE_CAMERA,
    CONF_ENABLE_FILES,
    CONF_ENABLE_FIRMWARE_CHECK,
    CONF_ENABLE_GCODE_CONSOLE,
    CONF_ENABLE_OBICO,
    CONF_ENABLE_SPOOLMAN,
    CONF_ENABLE_TIMELAPSE,
    CONF_EXTERNAL_CAMERA_ENTITY,
    CONF_EXTERNAL_CAMERA_SWITCH,
    CONF_EXTERNAL_STREAM_URL,
    CONF_HOST,
    CONF_NAME,
    CONF_NATIVE_STREAM_OVERRIDE,
    CONF_NOTIFY_TARGETS,
    CONF_OBICO_ADDON_SLUG,
    CONF_OBICO_CAMERA_ENTITY,
    CONF_OBICO_CONFIDENCE_SENSOR,
    CONF_OBICO_ERROR_BINARY_SENSOR,
    CONF_OBICO_STOP_DELAY_SEC,
    CONF_OBICO_SWITCH_ENTITY,
    CONF_PURGE_MM_PER_SWAP,
    CONF_SERIAL_NUMBER,
    CONF_SMART_PLUG_ENTITY,
    CONF_SNAP_PRINTER_COLORS,
    CONF_SPOOLMAN_LOCATION_PREFIX,
    CONF_SPOOLMAN_URL,
    CONF_TIMELAPSE_CROP,
    CONF_TIMELAPSE_FPS,
    CONF_TIMELAPSE_OUTPUT_DIR,
    CONF_VALIDATE_NOW,
    DEFAULT_COOLDOWN_TEMP,
    DEFAULT_NAME,
    DEFAULT_OBICO_STOP_DELAY_SEC,
    DEFAULT_PURGE_MM_PER_SWAP,
    DEFAULT_SPOOLMAN_LOCATION_PREFIX,
    DEFAULT_TIMELAPSE_FPS,
    DEFAULT_TIMELAPSE_OUTPUT_DIR,
    DOMAIN,
)
from .discovery import DiscoveredFlashForgePrinter, async_discover_printers
from .spoolman import SpoolmanClient, SpoolmanError


def _opt_entity_key(
    key: str, current: dict[str, Any]
) -> vol.Optional:
    """Build an optional voluptuous key without passing empty-string defaults to EntitySelector."""
    val = current.get(key)
    if val:
        return vol.Optional(key, default=val)
    return vol.Optional(key)


def _discover_notify_options(hass: HomeAssistant) -> list[selector.SelectOptionDict]:
    """List available `notify.*` services (highlighting mobile_app targets first)."""
    notify_svcs = hass.services.async_services().get("notify", {})
    options: list[selector.SelectOptionDict] = []
    for svc_name in sorted(
        notify_svcs.keys(),
        key=lambda s: (0 if s.startswith("mobile_app_") else 1, s),
    ):
        if svc_name in ("send_message", "persistent_notification"):
            continue
        label = (
            f"📱 {svc_name.replace('mobile_app_', '').replace('_', ' ').title()} (notify.{svc_name})"
            if svc_name.startswith("mobile_app_")
            else f"notify.{svc_name}"
        )
        options.append(selector.SelectOptionDict(value=f"notify.{svc_name}", label=label))
    return options


def _build_modules_schema(
    hass: HomeAssistant, current: dict[str, Any]
) -> vol.Schema:
    notify_opts = _discover_notify_options(hass)
    fields: dict[Any, Any] = {
        _opt_entity_key(CONF_SMART_PLUG_ENTITY, current): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Optional(
            CONF_COOLDOWN_TEMP,
            default=current.get(CONF_COOLDOWN_TEMP, DEFAULT_COOLDOWN_TEMP),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=30, max=80, step=1, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX
            )
        ),
    }

    if notify_opts:
        fields[
            vol.Optional(
                CONF_NOTIFY_TARGETS,
                default=current.get(CONF_NOTIFY_TARGETS, []),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=notify_opts,
                multiple=True,
                custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    else:
        fields[
            vol.Optional(
                CONF_NOTIFY_TARGETS,
                default=current.get(CONF_NOTIFY_TARGETS, []),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[],
                multiple=True,
                custom_value=True,
            )
        )

    fields.update(
        {
            vol.Required(
                CONF_ENABLE_SPOOLMAN,
                default=current.get(CONF_ENABLE_SPOOLMAN, True),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_CAMERA,
                default=current.get(CONF_ENABLE_CAMERA, True),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_TIMELAPSE,
                default=current.get(CONF_ENABLE_TIMELAPSE, False),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_OBICO,
                default=current.get(CONF_ENABLE_OBICO, False),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_FILES,
                default=current.get(CONF_ENABLE_FILES, True),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_GCODE_CONSOLE,
                default=current.get(CONF_ENABLE_GCODE_CONSOLE, True),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ENABLE_FIRMWARE_CHECK,
                default=current.get(CONF_ENABLE_FIRMWARE_CHECK, False),
            ): selector.BooleanSelector(),
        }
    )
    return vol.Schema(fields)


def _build_spoolman_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SPOOLMAN_URL,
                default=current.get(CONF_SPOOLMAN_URL, "http://homeassistant.local:7912"),
            ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Optional(
                CONF_PURGE_MM_PER_SWAP,
                default=current.get(CONF_PURGE_MM_PER_SWAP, DEFAULT_PURGE_MM_PER_SWAP),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1500, step=10, unit_of_measurement="mm", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_SNAP_PRINTER_COLORS,
                default=current.get(CONF_SNAP_PRINTER_COLORS, True),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_SPOOLMAN_LOCATION_PREFIX,
                default=current.get(
                    CONF_SPOOLMAN_LOCATION_PREFIX, DEFAULT_SPOOLMAN_LOCATION_PREFIX
                ),
            ): selector.TextSelector(),
        }
    )


def _build_camera_schema(current: dict[str, Any], include_timelapse: bool) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_CAMERA_MODE,
            default=current.get(CONF_CAMERA_MODE, CAMERA_MODE_EXTERNAL),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=CAMERA_MODE_EXTERNAL,
                        label="External Camera (HA Camera Entity or RTSP/HTTP Stream)",
                    ),
                    selector.SelectOptionDict(
                        value=CAMERA_MODE_NATIVE,
                        label="Native AD5X Built-In Camera",
                    ),
                    selector.SelectOptionDict(
                        value=CAMERA_MODE_BOTH,
                        label="Both (External + Native AD5X Camera)",
                    ),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        _opt_entity_key(CONF_EXTERNAL_CAMERA_ENTITY, current): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="camera")
        ),
        vol.Optional(
            CONF_EXTERNAL_STREAM_URL,
            default=current.get(CONF_EXTERNAL_STREAM_URL, ""),
        ): selector.TextSelector(),
        _opt_entity_key(CONF_EXTERNAL_CAMERA_SWITCH, current): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Optional(
            CONF_NATIVE_STREAM_OVERRIDE,
            default=current.get(CONF_NATIVE_STREAM_OVERRIDE, ""),
        ): selector.TextSelector(),
    }

    if include_timelapse:
        fields.update(
            {
                vol.Optional(
                    CONF_TIMELAPSE_FPS,
                    default=current.get(CONF_TIMELAPSE_FPS, DEFAULT_TIMELAPSE_FPS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=60, step=1, unit_of_measurement="fps", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_TIMELAPSE_OUTPUT_DIR,
                    default=current.get(
                        CONF_TIMELAPSE_OUTPUT_DIR, DEFAULT_TIMELAPSE_OUTPUT_DIR
                    ),
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_TIMELAPSE_CROP,
                    default=current.get(CONF_TIMELAPSE_CROP, ""),
                ): selector.TextSelector(),
            }
        )
    return vol.Schema(fields)


def _build_obico_schema(hass: HomeAssistant, current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if "hassio" in hass.config.components:
        fields[_opt_entity_key(CONF_OBICO_ADDON_SLUG, current)] = selector.AddonSelector()
    else:
        fields[
            vol.Optional(
                CONF_OBICO_ADDON_SLUG,
                default=current.get(CONF_OBICO_ADDON_SLUG, ""),
            )
        ] = selector.TextSelector()

    fields.update(
        {
            _opt_entity_key(CONF_OBICO_CAMERA_ENTITY, current): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera")
            ),
            _opt_entity_key(CONF_OBICO_SWITCH_ENTITY, current): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            _opt_entity_key(
                CONF_OBICO_ERROR_BINARY_SENSOR, current
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            _opt_entity_key(CONF_OBICO_CONFIDENCE_SENSOR, current): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_OBICO_STOP_DELAY_SEC,
                default=current.get(CONF_OBICO_STOP_DELAY_SEC, DEFAULT_OBICO_STOP_DELAY_SEC),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=1800, step=10, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )
    return vol.Schema(fields)


class FlashForgeAD5XConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle multi-step UI configuration flow for FlashForge AD5X."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._config: dict[str, Any] = {}
        self._discovered: dict[str, DiscoveredFlashForgePrinter] = {}
        self._discovery_done: bool = False

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return OptionsFlow handler."""
        return FlashForgeAD5XOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: UDP LAN Auto-Discovery or Manual IP/Serial + Check Code."""
        errors: dict[str, str] = {}

        if not self._discovery_done:
            self._discovery_done = True
            existing_ids = self._async_current_ids()
            for p in await async_discover_printers(self.hass):
                if p.serial_number.upper() not in existing_ids:
                    self._discovered[p.serial_number] = p

        if user_input is not None:
            chosen_sn = str(user_input.get(CONF_DISCOVERED_PRINTER) or "")
            disc = (
                self._discovered.get(chosen_sn)
                if chosen_sn and chosen_sn != "manual"
                else None
            )

            host = (
                disc.ip_address
                if disc
                else str(user_input.get(CONF_HOST) or "").strip()
            )
            serial = (
                disc.serial_number
                if disc
                else str(user_input.get(CONF_SERIAL_NUMBER) or "").strip()
            )
            code = str(user_input.get(CONF_CHECK_CODE) or "").strip()
            raw_name = str(user_input.get(CONF_NAME) or "").strip()
            name = (
                raw_name
                if raw_name and raw_name != DEFAULT_NAME
                else (disc.name if disc and disc.name else (raw_name or DEFAULT_NAME))
            )
            validate_now = bool(user_input.get(CONF_VALIDATE_NOW, True))

            if not host or not serial:
                errors["base"] = "missing_host_or_serial"
            else:
                await self.async_set_unique_id(serial.upper())
                self._abort_if_unique_id_configured()

                if validate_now:
                    client = FlashForgeAD5XApiClient(
                        session=async_get_clientsession(self.hass),
                        host=host,
                        serial_number=serial,
                        check_code=code,
                        http_port=disc.http_port if disc else 8898,
                        tcp_port=disc.tcp_port if disc else 8899,
                    )
                    try:
                        detail = await client.async_get_detail(timeout=5.0)
                        if name == DEFAULT_NAME and detail.get("name"):
                            name = str(detail["name"])
                    except FlashForgeAuthError:
                        errors["base"] = "invalid_auth"
                    except FlashForgeConnectionError:
                        errors["base"] = "cannot_connect"

            if not errors:
                self._config.update(
                    {
                        CONF_HOST: host,
                        CONF_SERIAL_NUMBER: serial,
                        CONF_CHECK_CODE: code,
                        CONF_NAME: name,
                    }
                )
                return await self.async_step_modules()

        fields: dict[Any, Any] = {}
        first_disc = next(iter(self._discovered.values()), None)

        if self._discovered:
            disc_opts = [
                selector.SelectOptionDict(
                    value=p.serial_number,
                    label=f"🖨️ {p.model} — {p.name} ({p.ip_address} • SN: {p.serial_number})",
                )
                for p in self._discovered.values()
            ]
            disc_opts.append(
                selector.SelectOptionDict(
                    value="manual",
                    label="✏️ Enter IP Address & Serial Number manually",
                )
            )
            default_pick = (user_input or {}).get(
                CONF_DISCOVERED_PRINTER,
                first_disc.serial_number if first_disc else "manual",
            )
            fields[
                vol.Required(CONF_DISCOVERED_PRINTER, default=default_pick)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=disc_opts,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            fields[
                vol.Required(
                    CONF_CHECK_CODE,
                    default=(user_input or {}).get(CONF_CHECK_CODE, ""),
                )
            ] = selector.TextSelector()
            fields[
                vol.Optional(
                    CONF_HOST,
                    default=(user_input or {}).get(
                        CONF_HOST, first_disc.ip_address if first_disc else ""
                    ),
                )
            ] = selector.TextSelector()
            fields[
                vol.Optional(
                    CONF_SERIAL_NUMBER,
                    default=(user_input or {}).get(
                        CONF_SERIAL_NUMBER,
                        first_disc.serial_number if first_disc else "",
                    ),
                )
            ] = selector.TextSelector()
            fields[
                vol.Optional(
                    CONF_NAME,
                    default=(user_input or {}).get(
                        CONF_NAME,
                        first_disc.name if first_disc else DEFAULT_NAME,
                    ),
                )
            ] = selector.TextSelector()
            fields[
                vol.Optional(
                    CONF_VALIDATE_NOW,
                    default=(user_input or {}).get(CONF_VALIDATE_NOW, True),
                )
            ] = selector.BooleanSelector()
        else:
            fields.update(
                {
                    vol.Required(
                        CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_SERIAL_NUMBER,
                        default=(user_input or {}).get(CONF_SERIAL_NUMBER, ""),
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_CHECK_CODE,
                        default=(user_input or {}).get(CONF_CHECK_CODE, ""),
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_NAME,
                        default=(user_input or {}).get(CONF_NAME, DEFAULT_NAME),
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_VALIDATE_NOW,
                        default=(user_input or {}).get(CONF_VALIDATE_NOW, True),
                    ): selector.BooleanSelector(),
                }
            )

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_modules(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Optional Smart Plug, Notification Targets, and Optional Module Toggles."""
        if user_input is not None:
            for key in (CONF_SMART_PLUG_ENTITY,):
                self._config[key] = user_input.get(key, "")
            self._config.update(user_input)
            return await self._async_route_next(after="modules")

        return self.async_show_form(
            step_id="modules",
            data_schema=_build_modules_schema(self.hass, self._config),
        )

    async def async_step_spoolman(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Spoolman Server URL & Filament Tracking Options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = str(user_input[CONF_SPOOLMAN_URL]).strip()
            sm = SpoolmanClient(async_get_clientsession(self.hass), url)
            try:
                await sm.async_get_info()
            except SpoolmanError:
                errors["base"] = "spoolman_unreachable"

            if not errors:
                self._config.update(user_input)
                return await self._async_route_next(after="spoolman")

        return self.async_show_form(
            step_id="spoolman",
            data_schema=_build_spoolman_schema(user_input or self._config),
            errors=errors,
        )

    async def async_step_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Camera Source (External / Native / Both) & Timelapse Options."""
        if user_input is not None:
            for key in (CONF_EXTERNAL_CAMERA_ENTITY, CONF_EXTERNAL_CAMERA_SWITCH):
                self._config[key] = user_input.get(key, "")
            self._config.update(user_input)
            return await self._async_route_next(after="camera")

        return self.async_show_form(
            step_id="camera",
            data_schema=_build_camera_schema(
                self._config, bool(self._config.get(CONF_ENABLE_TIMELAPSE))
            ),
        )

    async def async_step_obico(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 5: Obico ML Supervisor App & Entity Selection."""
        if user_input is not None:
            for key in (
                CONF_OBICO_ADDON_SLUG,
                CONF_OBICO_CAMERA_ENTITY,
                CONF_OBICO_SWITCH_ENTITY,
                CONF_OBICO_ERROR_BINARY_SENSOR,
                CONF_OBICO_CONFIDENCE_SENSOR,
            ):
                self._config[key] = user_input.get(key, "")
            self._config.update(user_input)
            return await self._async_route_next(after="obico")

        return self.async_show_form(
            step_id="obico",
            data_schema=_build_obico_schema(self.hass, self._config),
        )

    async def _async_route_next(self, after: str) -> FlowResult:
        """Advance through only the optional wizard steps enabled by the user."""
        order = ["modules", "spoolman", "camera", "obico"]
        idx = order.index(after)
        for step in order[idx + 1 :]:
            if step == "spoolman" and self._config.get(CONF_ENABLE_SPOOLMAN):
                return await self.async_step_spoolman()
            if step == "camera" and self._config.get(CONF_ENABLE_CAMERA):
                return await self.async_step_camera()
            if step == "obico" and self._config.get(CONF_ENABLE_OBICO):
                return await self.async_step_obico()

        return self.async_create_entry(
            title=str(self._config.get(CONF_NAME, DEFAULT_NAME)),
            data=self._config,
        )


class FlashForgeAD5XOptionsFlow(config_entries.OptionsFlow):
    """Allow reconfiguring connection, modules, Spoolman, Camera/Timelapse, and Obico post-setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize OptionsFlow."""
        self._entry = config_entry
        self._options: dict[str, Any] = {
            **config_entry.data,
            **config_entry.options,
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 in Options: Connection + Smart Plug + Module Toggles."""
        if user_input is not None:
            self._options[CONF_SMART_PLUG_ENTITY] = user_input.get(
                CONF_SMART_PLUG_ENTITY, ""
            )
            self._options.update(user_input)
            return await self._async_route_next(after="init")

        base_schema = _build_modules_schema(self.hass, self._options).schema
        conn_fields: dict[Any, Any] = {
            vol.Required(
                CONF_HOST, default=self._options.get(CONF_HOST, "")
            ): selector.TextSelector(),
            vol.Required(
                CONF_CHECK_CODE, default=self._options.get(CONF_CHECK_CODE, "")
            ): selector.TextSelector(),
        }
        conn_fields.update(base_schema)
        return self.async_show_form(step_id="init", data_schema=vol.Schema(conn_fields))

    async def async_step_spoolman(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = str(user_input[CONF_SPOOLMAN_URL]).strip()
            sm = SpoolmanClient(async_get_clientsession(self.hass), url)
            try:
                await sm.async_get_info()
            except SpoolmanError:
                errors["base"] = "spoolman_unreachable"

            if not errors:
                self._options.update(user_input)
                return await self._async_route_next(after="spoolman")

        return self.async_show_form(
            step_id="spoolman",
            data_schema=_build_spoolman_schema(user_input or self._options),
            errors=errors,
        )

    async def async_step_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            for key in (CONF_EXTERNAL_CAMERA_ENTITY, CONF_EXTERNAL_CAMERA_SWITCH):
                self._options[key] = user_input.get(key, "")
            self._options.update(user_input)
            return await self._async_route_next(after="camera")

        return self.async_show_form(
            step_id="camera",
            data_schema=_build_camera_schema(
                self._options, bool(self._options.get(CONF_ENABLE_TIMELAPSE))
            ),
        )

    async def async_step_obico(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            for key in (
                CONF_OBICO_ADDON_SLUG,
                CONF_OBICO_CAMERA_ENTITY,
                CONF_OBICO_SWITCH_ENTITY,
                CONF_OBICO_ERROR_BINARY_SENSOR,
                CONF_OBICO_CONFIDENCE_SENSOR,
            ):
                self._options[key] = user_input.get(key, "")
            self._options.update(user_input)
            return await self._async_route_next(after="obico")

        return self.async_show_form(
            step_id="obico",
            data_schema=_build_obico_schema(self.hass, self._options),
        )

    async def _async_route_next(self, after: str) -> FlowResult:
        order = ["init", "spoolman", "camera", "obico"]
        idx = order.index(after)
        for step in order[idx + 1 :]:
            if step == "spoolman" and self._options.get(CONF_ENABLE_SPOOLMAN):
                return await self.async_step_spoolman()
            if step == "camera" and self._options.get(CONF_ENABLE_CAMERA):
                return await self.async_step_camera()
            if step == "obico" and self._options.get(CONF_ENABLE_OBICO):
                return await self.async_step_obico()

        return self.async_create_entry(title="", data=self._options)
