"""Camera platform for FlashForge AD5X Native MJPEG/RTSP and custom External stream URLs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import shutil
from typing import Any

import aiohttp
from aiohttp import web

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import (
    async_aiohttp_proxy_stream,
    async_get_clientsession,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CAMERA_MODE_BOTH,
    CAMERA_MODE_EXTERNAL,
    CAMERA_MODE_NATIVE,
    CONF_CAMERA_MODE,
    CONF_ENABLE_CAMERA,
    CONF_EXTERNAL_CAMERA_ENTITY,
    CONF_EXTERNAL_STREAM_URL,
    DOMAIN,
)
from .coordinator import FlashForgeAD5XCoordinator

_LOGGER = logging.getLogger(__name__)


def _is_rtsp_or_hls(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith(("rtsp://", "rtsps://", "rtmp://")) or ".m3u8" in lowered


async def _extract_jpeg_from_mjpeg(
    session: aiohttp.ClientSession, url: str, timeout: float = 5.0
) -> bytes | None:
    """Fetch a still JPEG frame from an MJPEG stream (trying `?action=snapshot` first)."""
    if "action=stream" in url:
        snap_url = url.replace("action=stream", "action=snapshot")
        try:
            async with session.get(
                snap_url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == 200:
                    body = await resp.read()
                    if body.startswith(b"\xff\xd8"):
                        return body
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass

    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "image/jpeg" in ctype and "multipart" not in ctype:
                return await resp.read()

            buf = bytearray()
            async for chunk in resp.content.iter_chunked(4096):
                buf.extend(chunk)
                start = buf.find(b"\xff\xd8")
                if start != -1:
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end != -1:
                        return bytes(buf[start : end + 2])
                if len(buf) > 2_000_000:
                    break
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        pass
    return None


async def _extract_jpeg_via_ffmpeg(url: str, timeout: float = 8.0) -> bytes | None:
    """Grab a single JPEG frame from an RTSP/HLS stream using ffmpeg stdout pipe."""
    if not shutil.which("ffmpeg"):
        return None
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        url,
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0 and stdout.startswith(b"\xff\xd8"):
            return stdout
    except (asyncio.TimeoutError, OSError):
        pass
    return None


class AD5XStreamCamera(CoordinatorEntity[FlashForgeAD5XCoordinator], Camera):
    """Hybrid MJPEG / RTSP camera entity for FlashForge AD5X Native or External URL feeds."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FlashForgeAD5XCoordinator,
        key: str,
        name: str,
        url_getter: Callable[[FlashForgeAD5XCoordinator], str | None],
        require_printer_online: bool = False,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._key = key
        self._url_getter = url_getter
        self._require_online = require_printer_online
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = coordinator.device_info
        self._last_frame: bytes | None = None

    @property
    def _stream_url(self) -> str | None:
        return self._url_getter(self.coordinator)

    @property
    def supported_features(self) -> CameraEntityFeature:
        url = self._stream_url
        if url and _is_rtsp_or_hls(url):
            return CameraEntityFeature.STREAM
        return CameraEntityFeature(0)

    @property
    def available(self) -> bool:
        if self._require_online and not self.coordinator.printer_online:
            return False
        return bool(self._stream_url)

    async def stream_source(self) -> str | None:
        url = self._stream_url
        if url and _is_rtsp_or_hls(url):
            return url
        return None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        url = self._stream_url
        if not url or (self._require_online and not self.coordinator.printer_online):
            return self._last_frame

        frame: bytes | None
        if _is_rtsp_or_hls(url):
            frame = await _extract_jpeg_via_ffmpeg(url)
        else:
            session = async_get_clientsession(self.hass)
            frame = await _extract_jpeg_from_mjpeg(session, url)

        if frame:
            self._last_frame = frame
        return frame or self._last_frame

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        url = self._stream_url
        if not url:
            return None
        if _is_rtsp_or_hls(url):
            return await super().handle_async_mjpeg_stream(request)

        session = async_get_clientsession(self.hass)
        try:
            coro = session.get(url, timeout=aiohttp.ClientTimeout(connect=5.0))
            return await async_aiohttp_proxy_stream(
                self.hass, request, coro, "multipart/x-mixed-replace;boundary=--boundarydonotcross"
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.debug("MJPEG proxy stream error for %s: %s", self._key, err)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "ad5x_entry_id": self.coordinator.entry.entry_id,
            "ad5x_key": self._key,
            "stream_url": self._stream_url or "",
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AD5X camera entities only when the Camera module is enabled."""
    coordinator: FlashForgeAD5XCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.get_conf(CONF_ENABLE_CAMERA, False):
        return

    mode = str(coordinator.get_conf(CONF_CAMERA_MODE, CAMERA_MODE_EXTERNAL))
    entities: list[Camera] = []

    if mode in (CAMERA_MODE_NATIVE, CAMERA_MODE_BOTH):
        entities.append(
            AD5XStreamCamera(
                coordinator=coordinator,
                key="native_camera",
                name="Native Camera",
                url_getter=lambda c: c.native_camera_stream_url,
                require_printer_online=True,
            )
        )

    if mode in (CAMERA_MODE_EXTERNAL, CAMERA_MODE_BOTH):
        existing_cam_entity = str(
            coordinator.get_conf(CONF_EXTERNAL_CAMERA_ENTITY, "") or ""
        ).strip()
        raw_ext_url = str(
            coordinator.get_conf(CONF_EXTERNAL_STREAM_URL, "") or ""
        ).strip()
        if raw_ext_url and not existing_cam_entity:
            entities.append(
                AD5XStreamCamera(
                    coordinator=coordinator,
                    key="external_camera",
                    name="External Camera",
                    url_getter=lambda c: str(
                        c.get_conf(CONF_EXTERNAL_STREAM_URL, "") or ""
                    ).strip()
                    or None,
                    require_printer_online=False,
                )
            )

    if entities:
        async_add_entities(entities)
