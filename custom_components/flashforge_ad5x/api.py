"""Async API client for FlashForge AD5X (HTTP :8898 + TCP G-code :8899 + Firmware OTA)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

from .const import (
    DEFAULT_HTTP_PORT,
    DEFAULT_TCP_PORT,
    DEFAULT_UPDATE_SERVER_DOWNLOAD,
    DEFAULT_UPDATE_SERVER_GLOBAL,
    DEFAULT_UPDATE_SERVER_MODULAR,
    normalize_hex_color,
)

_LOGGER = logging.getLogger(__name__)


class FlashForgeError(Exception):
    """Base exception for FlashForge AD5X errors."""


class FlashForgeConnectionError(FlashForgeError):
    """Raised when the printer cannot be reached."""


class FlashForgeAuthError(FlashForgeError):
    """Raised when serialNumber or checkCode is rejected by the printer."""


class FlashForgeAD5XApiClient:
    """Async client for the FlashForge AD5X local HTTP and TCP interfaces."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        serial_number: str,
        check_code: str,
        http_port: int = DEFAULT_HTTP_PORT,
        tcp_port: int = DEFAULT_TCP_PORT,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self.host = host.strip()
        self.serial_number = serial_number.strip()
        self.check_code = check_code.strip()
        self.http_port = http_port
        self.tcp_port = tcp_port

    @property
    def base_url(self) -> str:
        """Return base HTTP URL for port 8898."""
        return f"http://{self.host}:{self.http_port}"

    def _auth_body(self) -> dict[str, Any]:
        """Return base authentication dictionary."""
        return {
            "serialNumber": self.serial_number,
            "checkCode": self.check_code,
        }

    async def _post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Perform an authenticated JSON POST to the AD5X HTTP API."""
        url = f"{self.base_url}{endpoint}"
        try:
            async with self._session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise FlashForgeConnectionError(
                        f"HTTP {resp.status} from {url}: {text[:200]}"
                    )
                if not text.strip():
                    return {}
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as err:
                    raise FlashForgeConnectionError(
                        f"Invalid JSON from {url}: {err}"
                    ) from err

                code = data.get("code")
                if code is not None and code not in (0, "0"):
                    msg = data.get("message") or data.get("msg") or f"API error code {code}"
                    if code in (1, 401, 403) or "check" in str(msg).lower() or "serial" in str(msg).lower():
                        raise FlashForgeAuthError(str(msg))
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise FlashForgeConnectionError(f"Cannot connect to {url}: {err}") from err

    async def async_get_detail(self, timeout: float = 2.5) -> dict[str, Any]:
        """Query `/detail` for full printer and IFS telemetry."""
        data = await self._post_json("/detail", self._auth_body(), timeout=timeout)
        detail = data.get("detail")
        if not isinstance(detail, dict):
            if data.get("code") not in (None, 0, "0"):
                raise FlashForgeAuthError(
                    data.get("message", f"Rejected by printer (code={data.get('code')})")
                )
            raise FlashForgeConnectionError("Missing 'detail' object in printer response")
        return detail

    async def async_get_gcode_list(self, timeout: float = 8.0) -> dict[str, Any]:
        """Query `/gcodeList` for stored G-code/3MF files and tool metadata."""
        data = await self._post_json("/gcodeList", self._auth_body(), timeout=timeout)
        return {
            "files": data.get("gcodeList") or [],
            "details": data.get("gcodeListDetail") or [],
        }

    async def async_get_gcode_thumb(
        self, filename: str, timeout: float = 6.0
    ) -> bytes | None:
        """Fetch embedded preview thumbnail for a specific file via `/gcodeThumb`."""
        if not filename:
            return None
        payload = {**self._auth_body(), "fileName": filename}
        try:
            data = await self._post_json("/gcodeThumb", payload, timeout=timeout)
        except FlashForgeError:
            return None
        b64_str = data.get("imageData")
        if not b64_str or not isinstance(b64_str, str):
            return None
        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        try:
            return base64.b64decode(b64_str)
        except (ValueError, TypeError):
            return None

    async def async_get_active_thumb(self, timeout: float = 5.0) -> bytes | None:
        """Fetch active print job thumbnail via `/getThum` fallback."""
        url = f"{self.base_url}/getThum"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == 200:
                    body = await resp.read()
                    if body and len(body) > 64:
                        return body
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        return None

    async def async_send_control(
        self, cmd: str, args: dict[str, Any] | None = None, timeout: float = 6.0
    ) -> dict[str, Any]:
        """Send a `/control` command payload to the printer."""
        cmd_payload: dict[str, Any] = {"cmd": cmd}
        if args is not None:
            cmd_payload["args"] = args
        payload = {**self._auth_body(), "payload": cmd_payload}
        return await self._post_json("/control", payload, timeout=timeout)

    async def async_set_light(self, enabled: bool) -> dict[str, Any]:
        """Turn the printer chamber LED light on or off."""
        return await self.async_send_control(
            "lightControl_cmd", {"status": "open" if enabled else "close"}
        )

    async def async_job_control(self, action: str) -> dict[str, Any]:
        """Control active print job: 'pause', 'continue', or 'cancel'."""
        return await self.async_send_control(
            "jobCtl_cmd", {"jobID": "", "action": action}
        )

    async def async_clear_platform(self) -> dict[str, Any]:
        """Clear build plate state (`setClearPlatform`) after a completed print."""
        return await self.async_send_control(
            "stateCtrl_cmd", {"action": "setClearPlatform"}
        )

    async def async_home(self) -> dict[str, Any]:
        """Home all printer axes (`homingCtrl_cmd`)."""
        return await self.async_send_control("homingCtrl_cmd", None)

    async def async_move(self, axis: str, delta: float) -> dict[str, Any]:
        """Jog axis ('x', 'y', or 'z') by delta millimeters."""
        return await self.async_send_control(
            "moveCtrl_cmd", {"axis": axis.lower(), "delta": float(delta)}
        )

    async def async_calibrate(
        self, leveling: bool = True, vibration: bool = False
    ) -> dict[str, Any]:
        """Start bed leveling and/or vibration compensation calibration."""
        return await self.async_send_control(
            "calibration_cmd",
            {
                "levelingDetection": bool(leveling),
                "vibrationCompensation": bool(vibration),
            },
        )

    async def async_set_bed_temp(self, temperature: int) -> dict[str, Any]:
        """Set target build plate temperature (°C)."""
        return await self.async_send_control(
            "temperatureCtl_cmd", {"platform": max(0, min(130, int(temperature)))}
        )

    async def async_set_extruder_temp(self, temperature: int) -> dict[str, Any]:
        """Set target nozzle temperature (°C)."""
        return await self.async_send_control(
            "temperatureCtl_cmd", {"rightNozzle": max(0, min(330, int(temperature)))}
        )

    async def async_set_print_params(
        self,
        z_offset: float,
        speed_pct: int,
        cooling_fan_pct: int,
    ) -> dict[str, Any]:
        """Adjust live print parameters (`printerCtl_cmd`)."""
        return await self.async_send_control(
            "printerCtl_cmd",
            {
                "zAxisCompensation": round(float(z_offset), 3),
                "speed": int(speed_pct),
                "coolingFan": int(cooling_fan_pct),
                "chamberFan": 0,
                "coolingLeftFan": 0,
            },
        )

    async def async_rename_printer(self, new_name: str) -> dict[str, Any]:
        """Rename the printer on the local network."""
        return await self.async_send_control("reName_cmd", {"name": new_name.strip()})

    async def async_configure_ifs_slot(
        self, slot: int, material: str, hex_color: str
    ) -> dict[str, Any]:
        """Configure material type and RGB color for an IFS slot (1-4)."""
        return await self.async_send_control(
            "msConfig_cmd",
            {
                "slot": int(slot),
                "mt": material.strip() or "PLA",
                "rgb": normalize_hex_color(hex_color),
            },
        )

    async def async_ifs_load_unload(
        self, slot: int, unload: bool = False
    ) -> dict[str, Any]:
        """Trigger IFS filament load (action=0) or unload (action=1) for a slot."""
        return await self.async_send_control(
            "ms_cmd",
            {
                "slot": int(slot),
                "action": 1 if unload else 0,
            },
        )

    async def async_start_print(
        self,
        filename: str,
        material_mappings: list[dict[str, Any]],
        leveling_before_print: bool = False,
        flow_calibration: bool = False,
        first_layer_inspection: bool = False,
        use_matl_station: bool = True,
        timelapse_video: bool = False,
    ) -> dict[str, Any]:
        """Start printing an internal file via `/printGcode` with multi-tool IFS mappings."""
        payload = {
            **self._auth_body(),
            "fileName": filename,
            "levelingBeforePrint": bool(leveling_before_print),
            "flowCalibration": bool(flow_calibration),
            "firstLayerInspection": bool(first_layer_inspection),
            "timeLapseVideo": bool(timelapse_video),
            "useMatlStation": bool(use_matl_station),
            "gcodeToolCnt": max(1, len(material_mappings)),
            "materialMappings": material_mappings,
        }
        return await self._post_json("/printGcode", payload, timeout=12.0)

    async def async_upload_gcode(
        self,
        file_path: Path,
        material_mappings: list[dict[str, Any]],
        print_now: bool = False,
        leveling_before_print: bool = False,
        flow_calibration: bool = False,
        first_layer_inspection: bool = False,
        use_matl_station: bool = True,
    ) -> dict[str, Any]:
        """Upload a local `.gcode` or `.3mf` file from disk to the printer via `/uploadGcode`."""
        if not file_path.is_file():
            raise FlashForgeError(f"File not found: {file_path}")
        file_bytes = await asyncio.to_thread(file_path.read_bytes)
        return await self.async_upload_gcode_bytes(
            filename=file_path.name,
            file_bytes=file_bytes,
            material_mappings=material_mappings,
            print_now=print_now,
            leveling_before_print=leveling_before_print,
            flow_calibration=flow_calibration,
            first_layer_inspection=first_layer_inspection,
            use_matl_station=use_matl_station,
        )

    async def async_upload_gcode_bytes(
        self,
        filename: str,
        file_bytes: bytes,
        material_mappings: list[dict[str, Any]],
        print_now: bool = False,
        leveling_before_print: bool = False,
        flow_calibration: bool = False,
        first_layer_inspection: bool = False,
        use_matl_station: bool = True,
    ) -> dict[str, Any]:
        """Upload raw `.gcode` or `.3mf` bytes to the printer via `/uploadGcode`."""
        filesize = len(file_bytes)
        mapping_b64 = base64.b64encode(
            json.dumps(material_mappings).encode("utf-8")
        ).decode("ascii")

        headers = {
            "Expect": "",
            "serialNumber": self.serial_number,
            "checkCode": self.check_code,
            "fileSize": str(filesize),
            "printNow": "true" if print_now else "false",
            "levelingBeforePrint": "true" if leveling_before_print else "false",
            "flowCalibration": "true" if flow_calibration else "false",
            "firstLayerInspection": "true" if first_layer_inspection else "false",
            "timeLapseVideo": "false",
            "useMatlStation": "true" if use_matl_station else "false",
            "gcodeToolCnt": str(max(1, len(material_mappings))),
            "materialMappings": mapping_b64,
        }

        form = aiohttp.FormData()
        form.add_field(
            "gcodeFile",
            file_bytes,
            filename=filename,
            content_type="application/octet-stream",
        )

        url = f"{self.base_url}/uploadGcode"
        try:
            async with self._session.post(
                url,
                data=form,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120.0),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise FlashForgeConnectionError(
                        f"Upload failed (HTTP {resp.status}): {text[:200]}"
                    )
                try:
                    return json.loads(text) if text.strip() else {"code": 0}
                except json.JSONDecodeError:
                    return {"raw": text}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise FlashForgeConnectionError(f"Upload failed: {err}") from err

    async def async_send_raw_gcode(
        self, command: str, timeout: float = 4.0
    ) -> str:
        """Send a raw G-code/M-code command over TCP port 8899 and return the response."""
        clean = command.strip()
        if not clean:
            return ""
        if not clean.startswith("~"):
            clean = f"~{clean}"

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.tcp_port),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, OSError) as err:
            raise FlashForgeConnectionError(
                f"TCP {self.host}:{self.tcp_port} unreachable: {err}"
            ) from err

        try:
            writer.write(f"{clean}\r\n".encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=timeout)
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=0.6)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if b"ok" in chunk.lower():
                    break
            return b"".join(chunks).decode("utf-8", errors="replace").strip()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def async_check_firmware_updates(
        self,
        current_software: str,
        current_control: str = "1.1.1",
        current_kernel: str = "1.1.4",
        current_library: str = "2.0.1",
        modular_url: str = DEFAULT_UPDATE_SERVER_MODULAR,
        global_url: str = DEFAULT_UPDATE_SERVER_GLOBAL,
        download_url: str = DEFAULT_UPDATE_SERVER_DOWNLOAD,
    ) -> dict[str, Any]:
        """Query FlashForge OTA servers for modular or global firmware updates."""
        result: dict[str, Any] = {
            "up_to_date": True,
            "packages": {},
            "release_notes": "",
        }
        payload = {
            "ControlVersion": {"Version": current_control.strip()},
            "KernelVersion": {"Version": current_kernel.strip()},
            "LibraryVersion": {"Version": current_library.strip()},
            "SoftwareVersion": {"Version": current_software.strip()},
            "PID": "0026",
            "VID": "2B71",
        }
        try:
            async with self._session.post(
                modular_url,
                json=payload,
                headers={"Content-Type": "application/json", "Cookie": "SessionID=666"},
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                if resp.status == 200:
                    repo = await resp.json(content_type=None)
                    if isinstance(repo, dict):
                        notes: list[str] = []
                        for key, label in (
                            ("SoftwarePackage", "Software"),
                            ("ControlPackage", "Control"),
                            ("KernelPackage", "Kernel"),
                            ("LibraryPackage", "Library"),
                        ):
                            pkg = repo.get(key)
                            if isinstance(pkg, dict) and pkg.get("Version"):
                                ver = str(pkg["Version"])
                                url = str(pkg.get("URL", ""))
                                desc = str(pkg.get("DescriptionEN") or pkg.get("Description") or "")
                                result["packages"][label.lower()] = {
                                    "name": label,
                                    "version": ver,
                                    "url": url,
                                    "filename": url.rsplit("/", 1)[-1] if url else "",
                                    "description": desc,
                                }
                                if desc:
                                    notes.append(f"{label} v{ver}: {desc}")
                        if result["packages"]:
                            result["up_to_date"] = False
                            result["release_notes"] = "\n\n".join(notes)
                            return result
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("Modular firmware check skipped/failed: %s", err)

        # Fallback to global firmware list endpoint
        try:
            sep = "&" if "?" in global_url else "?"
            full_global_url = f"{global_url}{sep}version={current_software.strip()}"
            async with self._session.get(
                full_global_url,
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                if resp.status == 200:
                    global_res = await resp.json(content_type=None)
                    items = (
                        global_res.get("data", {}).get("list", [])
                        if isinstance(global_res, dict) and global_res.get("code") == 0
                        else []
                    )
                    if items:
                        first_item = items[0]
                        new_ver = str(first_item.get("version", "")).strip()
                        details = first_item.get("detail") or []
                        en_details = [
                            d for d in details if isinstance(d, dict) and d.get("language") == "en"
                        ]
                        changelog = (
                            en_details[0].get("changelist", "")
                            if en_details
                            else (details[0].get("changelist", "") if details else "")
                        )
                        dl_file_url = ""
                        if new_ver:
                            sep_dl = "&" if "?" in download_url else "?"
                            async with self._session.get(
                                f"{download_url}{sep_dl}version={new_ver}",
                                ssl=False,
                                timeout=aiohttp.ClientTimeout(total=10.0),
                            ) as dl_resp:
                                if dl_resp.status == 200:
                                    dl_json = await dl_resp.json(content_type=None)
                                    dl_list = (
                                        dl_json.get("data", {}).get("list", [])
                                        if isinstance(dl_json, dict) and dl_json.get("code") == 0
                                        else []
                                    )
                                    if dl_list:
                                        dl_file_url = str(dl_list[0].get("url", ""))
                        if new_ver and new_ver != current_software.strip():
                            result["up_to_date"] = False
                            result["packages"]["software"] = {
                                "name": "Software",
                                "version": new_ver,
                                "url": dl_file_url,
                                "filename": dl_file_url.rsplit("/", 1)[-1] if dl_file_url else f"AD5X_{new_ver}.tgz",
                                "description": changelog,
                            }
                            result["release_notes"] = changelog
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("Global firmware check failed: %s", err)

        return result

    async def async_download_firmware(self, url: str, dest_path: Path) -> Path:
        """Download a firmware archive from FlashForge servers to disk."""
        await asyncio.to_thread(dest_path.parent.mkdir, parents=True, exist_ok=True)
        async with self._session.get(
            url,
            headers={"Cookie": "SessionID=666"},
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=300.0),
        ) as resp:
            if resp.status >= 400:
                raise FlashForgeConnectionError(f"Download HTTP {resp.status} for {url}")
            data = await resp.read()
            await asyncio.to_thread(dest_path.write_bytes, data)
        return dest_path
