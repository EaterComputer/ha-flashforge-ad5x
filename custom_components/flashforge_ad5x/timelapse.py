"""Configurable Native & External camera timelapse capture and async ffmpeg MP4 compiler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.const import STATE_OFF
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    CAMERA_MODE_BOTH,
    CAMERA_MODE_EXTERNAL,
    CAMERA_MODE_NATIVE,
    CONF_CAMERA_MODE,
    CONF_ENABLE_CAMERA,
    CONF_ENABLE_TIMELAPSE,
    CONF_EXTERNAL_CAMERA_ENTITY,
    CONF_EXTERNAL_CAMERA_SWITCH,
    CONF_EXTERNAL_STREAM_URL,
    CONF_NATIVE_STREAM_OVERRIDE,
    CONF_TIMELAPSE_CROP,
    CONF_TIMELAPSE_FPS,
    CONF_TIMELAPSE_OUTPUT_DIR,
    DEFAULT_TIMELAPSE_FPS,
    DEFAULT_TIMELAPSE_OUTPUT_DIR,
    TIMELAPSE_15S,
    TIMELAPSE_30S,
    TIMELAPSE_60S,
    TIMELAPSE_BY_LAYER,
    TIMELAPSE_DISABLED,
    TIMELAPSE_MODES,
)

if TYPE_CHECKING:
    from .coordinator import FlashForgeAD5XCoordinator

_LOGGER = logging.getLogger(__name__)


class TimelapseChannel:
    """State container for a single timelapse channel ('external' or 'native')."""

    def __init__(self, key: str, label: str, base_dir: Path) -> None:
        """Initialize timelapse channel."""
        self.key = key
        self.label = label
        self.snapshots_dir = base_dir / f"snapshots_{key}"
        self.videos_dir = base_dir
        self.mode: str = TIMELAPSE_DISABLED
        self.frame_count: int = 0
        self.is_compiling: bool = False
        self.last_video_path: str | None = None


class TimelapseManager:
    """Manages snapshot capture and MP4 timelapse compilation for External and/or Native cameras."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FlashForgeAD5XCoordinator,
    ) -> None:
        """Initialize timelapse manager."""
        self.hass = hass
        self.coordinator = coordinator
        self.channels: dict[str, TimelapseChannel] = {}
        self._unsub_timer: Callable[[], None] | None = None
        self._unsub_cam_switch: Callable[[], None] | None = None
        self._tick_counter: int = 0

    @property
    def enabled(self) -> bool:
        """Return True if both Camera and Timelapse modules are enabled."""
        return bool(
            self.coordinator.get_conf(CONF_ENABLE_CAMERA, False)
            and self.coordinator.get_conf(CONF_ENABLE_TIMELAPSE, False)
        )

    @property
    def fps(self) -> int:
        return max(1, int(self.coordinator.get_conf(CONF_TIMELAPSE_FPS, DEFAULT_TIMELAPSE_FPS)))

    @property
    def output_dir(self) -> Path:
        raw = str(
            self.coordinator.get_conf(CONF_TIMELAPSE_OUTPUT_DIR, DEFAULT_TIMELAPSE_OUTPUT_DIR)
        ).strip()
        return Path(raw or DEFAULT_TIMELAPSE_OUTPUT_DIR)

    @property
    def crop_filter(self) -> str:
        """Optional ffmpeg crop string `w:h:x:y` (empty = no crop)."""
        raw = str(self.coordinator.get_conf(CONF_TIMELAPSE_CROP, "") or "").strip()
        if raw.lower().startswith("crop="):
            raw = raw[5:]
        return raw

    def active_channel_keys(self) -> list[str]:
        """Return enabled camera channel keys ('external', 'native')."""
        if not self.enabled:
            return []
        mode = self.coordinator.get_conf(CONF_CAMERA_MODE, CAMERA_MODE_EXTERNAL)
        if mode == CAMERA_MODE_BOTH:
            return ["external", "native"]
        if mode == CAMERA_MODE_NATIVE:
            return ["native"]
        return ["external"]

    async def async_setup(self) -> None:
        """Initialize directories, count existing frames, and attach interval/guardian listeners."""
        self.async_unload()
        self.channels.clear()
        if not self.enabled:
            return

        out_dir = self.output_dir
        saved_modes: dict[str, Any] = (
            self.coordinator.persistent_state.get("timelapse_modes") or {}
        )

        for key in self.active_channel_keys():
            label = "External" if key == "external" else "Native"
            ch = TimelapseChannel(key, label, out_dir)
            if saved_modes.get(key) in TIMELAPSE_MODES:
                ch.mode = saved_modes[key]
            await asyncio.to_thread(ch.snapshots_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(ch.videos_dir.mkdir, parents=True, exist_ok=True)
            ch.frame_count = await asyncio.to_thread(self._count_jpegs, ch.snapshots_dir)
            self.channels[key] = ch

        # Base 15-second timer drives 15s, 30s, and 60s modes cleanly
        self._unsub_timer = async_track_time_interval(
            self.hass, self._handle_timer_tick, timedelta(seconds=15)
        )

        cam_sw = str(self.coordinator.get_conf(CONF_EXTERNAL_CAMERA_SWITCH, "") or "").strip()
        if cam_sw and "external" in self.channels:

            @callback
            def _on_cam_sw(event: Event) -> None:
                new_st = event.data.get("new_state")
                if new_st and new_st.state == STATE_OFF:
                    self.hass.async_create_task(
                        self.async_set_mode("external", TIMELAPSE_DISABLED)
                    )

            self._unsub_cam_switch = async_track_state_change_event(
                self.hass, [cam_sw], _on_cam_sw
            )

    @staticmethod
    def _count_jpegs(folder: Path) -> int:
        if not folder.is_dir():
            return 0
        return sum(1 for p in folder.glob("*.jpg") if p.is_file())

    async def async_set_mode(self, channel_key: str, mode: str) -> None:
        """Update timelapse capture mode for a channel, applying guardian checks."""
        ch = self.channels.get(channel_key)
        if not ch or mode not in TIMELAPSE_MODES:
            return

        if mode != TIMELAPSE_DISABLED:
            if channel_key == "external":
                cam_sw = str(
                    self.coordinator.get_conf(CONF_EXTERNAL_CAMERA_SWITCH, "") or ""
                ).strip()
                if cam_sw:
                    st = self.hass.states.get(cam_sw)
                    if st and st.state == STATE_OFF:
                        _LOGGER.warning(
                            "External camera switch %s is off; keeping timelapse Disabled.",
                            cam_sw,
                        )
                        mode = TIMELAPSE_DISABLED
            elif channel_key == "native" and not self.coordinator.native_camera_stream_url:
                _LOGGER.warning("Native camera stream URL unavailable; keeping timelapse Disabled.")
                mode = TIMELAPSE_DISABLED

        ch.mode = mode
        modes = dict(self.coordinator.persistent_state.get("timelapse_modes") or {})
        modes[channel_key] = mode
        await self.coordinator.async_update_persistent_state({"timelapse_modes": modes})
        self.coordinator.async_update_listeners()

    @callback
    def _handle_timer_tick(self, _now: datetime) -> None:
        """Trigger interval-based snapshots while printing."""
        self._tick_counter = (self._tick_counter + 1) % 4
        if not self.coordinator.is_printing:
            return

        for key, ch in self.channels.items():
            if ch.mode == TIMELAPSE_15S:
                self.hass.async_create_task(self.async_capture_snapshot(key))
            elif ch.mode == TIMELAPSE_30S and (self._tick_counter % 2 == 0):
                self.hass.async_create_task(self.async_capture_snapshot(key))
            elif ch.mode == TIMELAPSE_60S and (self._tick_counter == 0):
                self.hass.async_create_task(self.async_capture_snapshot(key))

    def on_layer_changed(self, new_layer: int) -> None:
        """Capture a snapshot on layer change for channels in 'By Layer' mode."""
        if not self.enabled or not self.coordinator.is_printing or new_layer <= 0:
            return
        for key, ch in self.channels.items():
            if ch.mode == TIMELAPSE_BY_LAYER:
                self.hass.async_create_task(self.async_capture_snapshot(key))

    def on_print_completed(self) -> None:
        """Compile MP4 timelapse and clean snapshots when print completes."""
        if not self.enabled:
            return
        for key, ch in self.channels.items():
            if ch.mode != TIMELAPSE_DISABLED and ch.frame_count > 0:
                self.hass.async_create_task(self._async_finish_print_timelapse(key))

    async def _async_finish_print_timelapse(self, channel_key: str) -> None:
        await self.async_set_mode(channel_key, TIMELAPSE_DISABLED)
        await self.async_compile_video(channel_key, clear_after=True)

    async def async_capture_snapshot(self, channel_key: str) -> bool:
        """Capture a single JPEG frame for the specified channel."""
        ch = self.channels.get(channel_key)
        if not ch:
            return False

        await asyncio.to_thread(ch.snapshots_dir.mkdir, parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        dest = ch.snapshots_dir / f"frame_{stamp}.jpg"
        crop = self.crop_filter if channel_key == "external" else ""

        # Path 1: Existing HA camera entity without crop -> direct internal frame fetch
        if channel_key == "external" and not crop:
            cam_ent = str(
                self.coordinator.get_conf(CONF_EXTERNAL_CAMERA_ENTITY, "") or ""
            ).strip()
            if cam_ent:
                try:
                    from homeassistant.components.camera import async_get_image

                    img = await async_get_image(self.hass, cam_ent)
                    if img and img.content:
                        await asyncio.to_thread(dest.write_bytes, img.content)
                        ch.frame_count = await asyncio.to_thread(
                            self._count_jpegs, ch.snapshots_dir
                        )
                        self.coordinator.async_update_listeners()
                        return True
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Direct snapshot from %s failed: %s", cam_ent, err)

        # Path 2: Stream URL (or HA camera entity stream with ffmpeg crop)
        stream_url = ""
        if channel_key == "external":
            stream_url = str(
                self.coordinator.get_conf(CONF_EXTERNAL_STREAM_URL, "") or ""
            ).strip()
            if not stream_url:
                cam_ent = str(
                    self.coordinator.get_conf(CONF_EXTERNAL_CAMERA_ENTITY, "") or ""
                ).strip()
                if cam_ent:
                    try:
                        from homeassistant.components.camera import async_get_stream_source  # type: ignore[attr-defined]
                    except ImportError:
                        async_get_stream_source = None  # type: ignore[assignment]
                    if async_get_stream_source is not None:
                        stream_url = await async_get_stream_source(self.hass, cam_ent) or ""
                    if not stream_url and crop:
                        # Capture raw image to temp file then crop with ffmpeg
                        from homeassistant.components.camera import async_get_image

                        img = await async_get_image(self.hass, cam_ent)
                        if img and img.content:
                            tmp_in = ch.snapshots_dir / f".raw_{stamp}.jpg"
                            await asyncio.to_thread(tmp_in.write_bytes, img.content)
                            ok = await self._run_ffmpeg_snapshot(str(tmp_in), dest, crop)
                            await asyncio.to_thread(tmp_in.unlink, missing_ok=True)
                            if ok:
                                ch.frame_count = await asyncio.to_thread(
                                    self._count_jpegs, ch.snapshots_dir
                                )
                                self.coordinator.async_update_listeners()
                            return ok
        else:
            stream_url = self.coordinator.native_camera_stream_url or ""

        if not stream_url:
            _LOGGER.debug("No stream URL available for timelapse channel %s", channel_key)
            return False

        ok = await self._run_ffmpeg_snapshot(stream_url, dest, crop)
        if ok:
            ch.frame_count = await asyncio.to_thread(self._count_jpegs, ch.snapshots_dir)
            self.coordinator.async_update_listeners()
        return ok

    async def _run_ffmpeg_snapshot(
        self, input_src: str, output_file: Path, crop: str
    ) -> bool:
        if not shutil.which("ffmpeg"):
            _LOGGER.error("ffmpeg binary not found in PATH; cannot capture timelapse frame.")
            return False

        cmd = ["ffmpeg", "-y", "-i", input_src]
        if crop:
            cmd.extend(["-vf", f"crop={crop}"])
        cmd.extend(["-frames:v", "1", str(output_file)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            if proc.returncode != 0:
                _LOGGER.warning(
                    "ffmpeg snapshot exited with %s: %s",
                    proc.returncode,
                    stderr.decode("utf-8", errors="replace")[-200:],
                )
                return False
            return output_file.is_file()
        except (asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("ffmpeg snapshot error: %s", err)
            return False

    async def async_clear_snapshots(self, channel_key: str) -> None:
        """Delete temporary `.jpg` frames for a channel."""
        ch = self.channels.get(channel_key)
        if not ch:
            return

        def _clear() -> None:
            if ch.snapshots_dir.is_dir():
                for jpg in ch.snapshots_dir.glob("*.jpg"):
                    jpg.unlink(missing_ok=True)

        await asyncio.to_thread(_clear)
        ch.frame_count = 0
        self.coordinator.async_update_listeners()

    async def async_compile_video(
        self, channel_key: str, clear_after: bool = True
    ) -> str | None:
        """Compile captured `.jpg` snapshots into an H.264 `.mp4` timelapse."""
        ch = self.channels.get(channel_key)
        if not ch or ch.is_compiling:
            return None
        if not shutil.which("ffmpeg"):
            _LOGGER.error("ffmpeg binary not found; cannot compile timelapse video.")
            return None

        frames = await asyncio.to_thread(self._count_jpegs, ch.snapshots_dir)
        if frames == 0:
            return None

        ch.is_compiling = True
        self.coordinator.async_update_listeners()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_mp4 = ch.videos_dir / f"ad5x_{channel_key}_{stamp}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(self.fps),
            "-pattern_type",
            "glob",
            "-i",
            str(ch.snapshots_dir / "*.jpg"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out_mp4),
        ]

        try:
            await asyncio.to_thread(ch.videos_dir.mkdir, parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300.0)
            if proc.returncode != 0 or not out_mp4.is_file():
                _LOGGER.error(
                    "ffmpeg timelapse compile failed (%s): %s",
                    proc.returncode,
                    stderr.decode("utf-8", errors="replace")[-300:],
                )
                return None

            ch.last_video_path = str(out_mp4)
            if clear_after:
                await self.async_clear_snapshots(channel_key)

            # Build /media/local/... path if stored under /media
            video_media_url = None
            out_str = str(out_mp4)
            if out_str.startswith("/media/"):
                video_media_url = f"/media/local/{out_str[len('/media/'):]}"

            await self.coordinator.async_send_notification(
                title=f"🎬 AD5X Timelapse Ready ({ch.label})",
                message=f"Compiled {frames} frames into {out_mp4.name}.",
                tag=f"ad5x_timelapse_{channel_key}",
                video=video_media_url,
                click_url="/media-browser",
            )
            return str(out_mp4)
        except (asyncio.TimeoutError, OSError) as err:
            _LOGGER.error("ffmpeg compile error: %s", err)
            return None
        finally:
            ch.is_compiling = False
            self.coordinator.async_update_listeners()

    def async_unload(self) -> None:
        """Unsubscribe timers and listeners."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_cam_switch:
            self._unsub_cam_switch()
            self._unsub_cam_switch = None
