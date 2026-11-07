"""Constants and helpers for the FlashForge AD5X integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "flashforge_ad5x"
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_state"

STATIC_URL_BASE = f"/{DOMAIN}_static"
CARD_JS_FILENAME = "flashforge-ad5x-cards.js"
CARD_JS_URL = f"{STATIC_URL_BASE}/{CARD_JS_FILENAME}"
CARD_JS_VERSION = "1.0.0"

PICKER_API_BASE = f"/api/{DOMAIN}"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.CAMERA,
    Platform.IMAGE,
]

# --- Core Printer Config Keys ---
CONF_DISCOVERED_PRINTER = "discovered_printer"
CONF_HOST = "host"
CONF_SERIAL_NUMBER = "serial_number"
CONF_CHECK_CODE = "check_code"
CONF_NAME = "name"
CONF_VALIDATE_NOW = "validate_now"

# --- Power / Smart Plug Config Keys ---
CONF_SMART_PLUG_ENTITY = "smart_plug_entity"
CONF_COOLDOWN_TEMP = "cooldown_temp"

# --- Optional Module Toggles ---
CONF_ENABLE_SPOOLMAN = "enable_spoolman"
CONF_ENABLE_CAMERA = "enable_camera"
CONF_ENABLE_TIMELAPSE = "enable_timelapse"
CONF_ENABLE_OBICO = "enable_obico"
CONF_ENABLE_FILES = "enable_files"
CONF_ENABLE_GCODE_CONSOLE = "enable_gcode_console"
CONF_ENABLE_FIRMWARE_CHECK = "enable_firmware_check"

# --- Spoolman Config Keys ---
CONF_SPOOLMAN_URL = "spoolman_url"
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_PURGE_MM_PER_SWAP = "purge_mm_per_swap"
CONF_SNAP_PRINTER_COLORS = "snap_printer_colors"
CONF_SPOOLMAN_LOCATION_PREFIX = "spoolman_location_prefix"

# --- Camera & Timelapse Config Keys ---
CONF_CAMERA_MODE = "camera_mode"
CAMERA_MODE_EXTERNAL = "external"
CAMERA_MODE_NATIVE = "native"
CAMERA_MODE_BOTH = "both"

CONF_EXTERNAL_CAMERA_ENTITY = "external_camera_entity"
CONF_EXTERNAL_STREAM_URL = "external_stream_url"
CONF_EXTERNAL_CAMERA_SWITCH = "external_camera_switch"
CONF_NATIVE_STREAM_OVERRIDE = "native_stream_override"

CONF_TIMELAPSE_FPS = "timelapse_fps"
CONF_TIMELAPSE_OUTPUT_DIR = "timelapse_output_dir"
CONF_TIMELAPSE_CROP = "timelapse_crop"

# --- Obico ML Config Keys ---
CONF_OBICO_ADDON_SLUG = "obico_addon_slug"
CONF_OBICO_CAMERA_ENTITY = "obico_camera_entity"
CONF_OBICO_SWITCH_ENTITY = "obico_switch_entity"
CONF_OBICO_ERROR_BINARY_SENSOR = "obico_error_binary_sensor"
CONF_OBICO_CONFIDENCE_SENSOR = "obico_confidence_sensor"
CONF_OBICO_STOP_DELAY_SEC = "obico_stop_delay_sec"

# --- Firmware Update Server Defaults ---
CONF_UPDATE_SERVER_MODULAR = "update_server_modular"
CONF_UPDATE_SERVER_GLOBAL = "update_server_global"
CONF_UPDATE_SERVER_DOWNLOAD = "update_server_download"

DEFAULT_NAME = "FlashForge AD5X"
DEFAULT_HTTP_PORT = 8898
DEFAULT_TCP_PORT = 8899
DEFAULT_COOLDOWN_TEMP = 50
DEFAULT_PURGE_MM_PER_SWAP = 200.0
DEFAULT_SPOOLMAN_LOCATION_PREFIX = "AD5X Slot"
DEFAULT_TIMELAPSE_FPS = 24
DEFAULT_TIMELAPSE_OUTPUT_DIR = "/media/ad5x/timelapses"
DEFAULT_OBICO_CONFIDENCE_THRESHOLD = 65.0
DEFAULT_OBICO_COOLDOWN_MIN = 5.0
DEFAULT_OBICO_STOP_DELAY_SEC = 120

DEFAULT_UPDATE_SERVER_MODULAR = "https://cloud.sz3dp.com/api/v2.0/ota/versionCheck"
DEFAULT_UPDATE_SERVER_GLOBAL = (
    "https://api.flashforge.com/officialWebsite/firmwareControl/versionList?printerName=AD5X"
)
DEFAULT_UPDATE_SERVER_DOWNLOAD = (
    "https://api.flashforge.com/officialWebsite/firmwareControl/downloadList?printerName=AD5X"
)

# --- Adaptive Polling Intervals (seconds) ---
POLL_INTERVAL_PRINTING = 3
POLL_INTERVAL_IDLE = 10
POLL_INTERVAL_OFFLINE = 45

# --- Timelapse Modes ---
TIMELAPSE_DISABLED = "Disabled"
TIMELAPSE_BY_LAYER = "By Layer"
TIMELAPSE_15S = "Every 15s"
TIMELAPSE_30S = "Every 30s"
TIMELAPSE_60S = "Every 60s"

TIMELAPSE_MODES: list[str] = [
    TIMELAPSE_DISABLED,
    TIMELAPSE_BY_LAYER,
    TIMELAPSE_15S,
    TIMELAPSE_30S,
    TIMELAPSE_60S,
]

# --- IFS Station Action & Step Maps ---
IFS_STATE_ACTIONS: dict[int, str] = {
    0: "Waiting",
    1: "Loading",
    2: "Unloading",
    3: "Canceling Load/Unload",
    4: "Printing",
    5: "Busy",
    6: "Paused",
}

IFS_STATE_STEPS: dict[int, str] = {
    0: "Idle",
    1: "Heating",
    2: "Pushing Filament",
    3: "Purging Old Filament",
    4: "Cutting Filament",
    5: "Retracting Filament",
    6: "Load/Unload Completed",
}

PRINTER_STATUS_LABELS: dict[str, str] = {
    "ready": "Ready",
    "printing": "Printing",
    "paused": "Paused",
    "pausing": "Pausing",
    "cancel": "Canceled",
    "cancelled": "Canceled",
    "completed": "Completed",
    "busy": "Busy",
    "heating": "Heating",
    "calibrating": "Calibrating",
    "error": "Error",
    "offline": "Offline",
    "disconnected": "Offline",
}

# --- AD5X Official 24-Color Palette ---
AD5X_COLOR_PALETTE: list[str] = [
    "FFFFFF",  # 1. White
    "FEF043",  # 2. Yellow
    "DCF478",  # 3. Light Green
    "0ACC38",  # 4. Green
    "067749",  # 5. Dark Green
    "0C6283",  # 6. Teal
    "0DE2A0",  # 7. Turquoise
    "75D9F3",  # 8. Light Blue
    "45A8F9",  # 9. Blue
    "2750E0",  # 10. Dark Blue
    "46328E",  # 11. Dark Purple
    "A03CF7",  # 12. Purple
    "F330F9",  # 13. Fuchsia
    "D4B0DC",  # 14. Lavender
    "F95D73",  # 15. Red/Orange
    "F72224",  # 16. Red
    "7C4B00",  # 17. Brown
    "F98D33",  # 18. Orange
    "FDEBD5",  # 19. Light Beige
    "D3C4A3",  # 20. Beige
    "AF7836",  # 21. Light Brown
    "898989",  # 22. Silver Grey
    "BCBCBC",  # 23. Grey
    "161616",  # 24. Black
]

# --- Notification Action Constants ---
ACTION_OBICO_PAUSE = f"{DOMAIN.upper()}_OBICO_PAUSE"
ACTION_OBICO_RESUME = f"{DOMAIN.upper()}_OBICO_RESUME"
ACTION_OBICO_CANCEL = f"{DOMAIN.upper()}_OBICO_CANCEL"
ACTION_OBICO_SNOOZE = f"{DOMAIN.upper()}_OBICO_SNOOZE"

# --- Service Names ---
SERVICE_SEND_GCODE = "send_gcode"
SERVICE_JOG = "jog"
SERVICE_ASSIGN_SPOOL = "assign_spool"
SERVICE_UNASSIGN_SPOOL = "unassign_spool"
SERVICE_CONFIGURE_IFS_SLOT = "configure_ifs_slot"
SERVICE_IFS_LOAD_UNLOAD = "ifs_load_unload"
SERVICE_START_PRINT = "start_print"
SERVICE_UPLOAD_FILE = "upload_file"
SERVICE_RENAME_PRINTER = "rename_printer"
SERVICE_CHECK_FIRMWARE = "check_firmware"
SERVICE_DOWNLOAD_FIRMWARE = "download_firmware"


def normalize_hex_color(color: str | None, default: str = "#FFFFFF") -> str:
    """Normalize a hex color string (supports 6 or 8 hex chars from Spoolman) to #RRGGBB."""
    if not color:
        return default
    cleaned = color.strip().lstrip("#").upper()
    if len(cleaned) == 8:
        # Spoolman sometimes stores RRGGBBAA or AARRGGBB; strip alpha suffix
        cleaned = cleaned[:6]
    if len(cleaned) == 3:
        cleaned = "".join(ch * 2 for ch in cleaned)
    if len(cleaned) != 6:
        return default
    try:
        int(cleaned, 16)
    except ValueError:
        return default
    return f"#{cleaned}"


def snap_to_ad5x_palette(color: str | None) -> str:
    """Find the closest RGB match in the AD5X's 24-color firmware palette."""
    norm = normalize_hex_color(color, "#FFFFFF")[1:]
    r1 = int(norm[0:2], 16)
    g1 = int(norm[2:4], 16)
    b1 = int(norm[4:6], 16)

    best_hex = "FFFFFF"
    min_dist = 10**9
    for candidate in AD5X_COLOR_PALETTE:
        r2 = int(candidate[0:2], 16)
        g2 = int(candidate[2:4], 16)
        b2 = int(candidate[4:6], 16)
        dist = (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2
        if dist < min_dist:
            min_dist = dist
            best_hex = candidate
    return f"#{best_hex}"
