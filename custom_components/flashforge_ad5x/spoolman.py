"""Async client for Spoolman REST API v1 (including v0.27.0+ native NFC/RFID tags)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

from .const import normalize_hex_color

_LOGGER = logging.getLogger(__name__)

_QR_SPOOLMAN_RE = re.compile(r"spoolman:s-(\d+)", re.IGNORECASE)
_URL_SPOOLMAN_RE = re.compile(r"/spool/(?:show/)?(\d+)", re.IGNORECASE)


def parse_spoolman_qr(raw_value: str) -> int | None:
    """Extract a Spoolman Spool ID from `WEB+SPOOLMAN:S-{id}`, URLs, JSON, or plain digits."""
    if not raw_value:
        return None
    val = raw_value.strip()
    if val.isdigit():
        return int(val)
    match = _QR_SPOOLMAN_RE.search(val)
    if match:
        return int(match.group(1))
    match = _URL_SPOOLMAN_RE.search(val)
    if match:
        return int(match.group(1))
    if val.startswith("{"):
        try:
            parsed = json.loads(val)
            for key in ("id", "spool_id", "spoolId"):
                if key in parsed and str(parsed[key]).isdigit():
                    return int(parsed[key])
        except ValueError:
            pass
    return None


def normalize_tag_uid(uid: str) -> str:
    """Normalize NFC hardware UID to uppercase hex without separators."""
    if not uid:
        return ""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", uid)
    return cleaned.upper() if cleaned else uid.strip()


def format_spool_summary(spool: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw Spoolman `/api/v1/spool` object into a clean UI/sensor dictionary."""
    filament = spool.get("filament") or {}
    vendor_obj = filament.get("vendor") or {}
    vendor = vendor_obj.get("name") or filament.get("vendor_name") or "Generic"
    material = filament.get("material") or "PLA"
    name = filament.get("name") or f"{vendor} {material}"
    raw_color = (
        filament.get("color_hex")
        or (filament.get("multi_color_hexes") or "").split(",")[0]
        or "#FFFFFF"
    )
    color_hex = normalize_hex_color(raw_color)
    rem_weight = spool.get("remaining_weight")
    rem_length = spool.get("remaining_length")
    init_weight = spool.get("initial_weight") or filament.get("weight") or 1000.0
    density = filament.get("density") or 1.24
    diameter = filament.get("diameter") or 1.75

    tags: list[str] = []
    if isinstance(spool.get("tags"), list):
        for item in spool["tags"]:
            if isinstance(item, str):
                tags.append(item)
            elif isinstance(item, dict) and item.get("uid"):
                tags.append(str(item["uid"]))

    return {
        "id": int(spool["id"]),
        "name": str(name),
        "label": f"#{spool['id']} — {vendor} {material} ({name}) [{round(float(rem_weight or 0), 1)}g]",
        "vendor": str(vendor),
        "material": str(material),
        "color_hex": color_hex,
        "remaining_weight": round(float(rem_weight), 1) if rem_weight is not None else None,
        "remaining_length_mm": round(float(rem_length), 1) if rem_length is not None else None,
        "remaining_length_m": round(float(rem_length) / 1000.0, 2) if rem_length is not None else None,
        "initial_weight": float(init_weight),
        "used_weight": round(float(spool.get("used_weight") or 0.0), 1),
        "density": float(density),
        "diameter": float(diameter),
        "location": spool.get("location") or "",
        "archived": bool(spool.get("archived", False)),
        "tags": tags,
        "extra": spool.get("extra") or {},
    }


class SpoolmanError(Exception):
    """Raised on Spoolman communication or validation errors."""


class SpoolmanClient:
    """Async REST client for Spoolman v1 (with v0.27.0+ native tag support)."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        """Initialize the Spoolman client."""
        self._session = session
        cleaned = base_url.strip().rstrip("/")
        if cleaned.endswith("/api/v1"):
            cleaned = cleaned[: -len("/api/v1")]
        self.ui_base_url = cleaned
        self.api_base_url = f"{cleaned}/api/v1"

    @property
    def create_spool_url(self) -> str:
        """Return browser URL for creating a new spool in Spoolman."""
        return f"{self.ui_base_url}/spool/create"

    def spool_detail_url(self, spool_id: int) -> str:
        """Return browser URL for viewing a spool in Spoolman."""
        return f"{self.ui_base_url}/spool/show/{spool_id}"

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 8.0,
    ) -> tuple[int, Any]:
        """Execute an HTTP request against `/api/v1`."""
        url = f"{self.api_base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                data = None
                if text.strip():
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        data = text
                return resp.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise SpoolmanError(f"Spoolman unreachable at {url}: {err}") from err

    async def async_get_info(self) -> dict[str, Any]:
        """Verify connection to Spoolman via `GET /api/v1/info`."""
        status, data = await self._request("GET", "/info", timeout=5.0)
        if status >= 400 or not isinstance(data, dict):
            raise SpoolmanError(f"Unexpected response from Spoolman /info (HTTP {status})")
        return data

    async def async_get_spools(self, allow_archived: bool = False) -> list[dict[str, Any]]:
        """Return formatted spool summaries from Spoolman."""
        params = {"allow_archived": "true" if allow_archived else "false"}
        status, data = await self._request("GET", "/spool", params=params)
        if status >= 400 or not isinstance(data, list):
            raise SpoolmanError(f"Failed to list spools from Spoolman (HTTP {status})")
        return [format_spool_summary(item) for item in data if isinstance(item, dict) and "id" in item]

    async def async_get_spool(self, spool_id: int) -> dict[str, Any] | None:
        """Fetch a single spool summary by ID."""
        status, data = await self._request("GET", f"/spool/{int(spool_id)}")
        if status == 404 or not isinstance(data, dict) or "id" not in data:
            return None
        if status >= 400:
            raise SpoolmanError(f"Error fetching spool {spool_id} (HTTP {status})")
        return format_spool_summary(data)

    async def async_find_spool_by_tag(self, uid: str) -> dict[str, Any] | None:
        """Lookup a spool by NFC hardware UID using Spoolman v0.27.0+ `GET /api/v1/spool?tag=<uid>`."""
        norm_uid = normalize_tag_uid(uid)
        if not norm_uid:
            return None

        # Primary: Spoolman v0.27.0+ native tag query
        status, data = await self._request("GET", "/spool", params={"tag": norm_uid})
        if status == 200 and isinstance(data, list) and data:
            # Verify Spoolman actually filtered (older Spoolman <0.27 might ignore unknown query params)
            if len(data) == 1:
                return format_spool_summary(data[0])
            for raw_spool in data:
                summary = format_spool_summary(raw_spool)
                norm_tags = [normalize_tag_uid(t) for t in summary.get("tags", [])]
                if norm_uid in norm_tags:
                    return summary
                extra = summary.get("extra") or {}
                for k in ("tag", "nfc_id", "uid"):
                    if normalize_tag_uid(str(extra.get(k, "")).strip('"')) == norm_uid:
                        return summary
        return None

    async def async_bind_tag_to_spool(self, spool_id: int, uid: str) -> bool:
        """Attach an NFC hardware UID to a spool using Spoolman v0.27.0+ native tag endpoints."""
        norm_uid = normalize_tag_uid(uid)
        if not norm_uid:
            return False

        # 1. Spoolman v0.27.0+ sub-resource endpoint: POST /api/v1/spool/{id}/tag
        status, _ = await self._request(
            "POST", f"/spool/{int(spool_id)}/tag", json_body={"uid": norm_uid}
        )
        if status in (200, 201, 204):
            return True

        # 2. Spoolman v0.27.0+ PATCH /api/v1/spool/{id} with `tags` array
        existing = await self.async_get_spool(spool_id)
        current_tags = list(existing.get("tags", [])) if existing else []
        if norm_uid not in [normalize_tag_uid(t) for t in current_tags]:
            current_tags.append(norm_uid)

        status2, _ = await self._request(
            "PATCH", f"/spool/{int(spool_id)}", json_body={"tags": current_tags}
        )
        if status2 in (200, 201, 204):
            return True

        _LOGGER.warning(
            "Could not bind NFC tag %s to Spoolman spool %s (POST=%s, PATCH=%s). Ensure Spoolman >= v0.27.0.",
            norm_uid,
            spool_id,
            status,
            status2,
        )
        return False

    async def async_update_spool_location(
        self, spool_id: int, location: str
    ) -> dict[str, Any] | None:
        """Update the `location` field of a spool in Spoolman."""
        status, data = await self._request(
            "PATCH",
            f"/spool/{int(spool_id)}",
            json_body={"location": location},
        )
        if status >= 400:
            _LOGGER.warning(
                "Failed to update Spoolman location for spool %s (HTTP %s): %s",
                spool_id,
                status,
                data,
            )
            return None
        return format_spool_summary(data) if isinstance(data, dict) and "id" in data else None

    async def async_use_filament(
        self,
        spool_id: int,
        use_length_mm: float | None = None,
        use_weight_g: float | None = None,
    ) -> dict[str, Any] | None:
        """Deduct filament consumption via `PUT /api/v1/spool/{id}/use`.

        Spoolman requires strictly ONE of `use_length` or `use_weight` (supplying both returns HTTP 400).
        """
        payload: dict[str, Any] = {}
        if use_length_mm is not None and use_length_mm > 0:
            payload["use_length"] = round(float(use_length_mm), 2)
        elif use_weight_g is not None and use_weight_g > 0:
            payload["use_weight"] = round(float(use_weight_g), 2)
        else:
            return None

        status, data = await self._request(
            "PUT", f"/spool/{int(spool_id)}/use", json_body=payload
        )
        if status >= 400:
            _LOGGER.warning(
                "Failed to deduct usage (%s) on Spoolman spool %s (HTTP %s): %s",
                payload,
                spool_id,
                status,
                data,
            )
            return None
        return format_spool_summary(data) if isinstance(data, dict) and "id" in data else None
