"""Zero-dependency async UDP multicast/broadcast LAN discovery for FlashForge AD5X."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import socket
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MULTICAST_ADDRESS = "225.0.0.9"
MODERN_PACKET_MIN_LEN = 276  # 0x114 bytes on AD5X / 5M / Creator 5 firmware
DISCOVERY_PORTS = (8899, 19000, 48899)

# Authoritative USB Product IDs reported at offset 0x88 in the 276-byte UDP response
MODERN_PRODUCT_IDS: dict[int, str] = {
    0x0026: "AD5X",
    0x0023: "Adventurer 5M",
    0x0024: "Adventurer 5M Pro",
    0x0028: "Creator 5",
    0x0029: "Creator 5 Pro",
}


@dataclass(slots=True)
class DiscoveredFlashForgePrinter:
    """Metadata parsed from a FlashForge modern LAN UDP discovery packet."""

    ip_address: str
    serial_number: str
    name: str
    model: str
    http_port: int = 8898
    tcp_port: int = 8899
    product_id: int = 0

    @property
    def is_ad5x(self) -> bool:
        return self.product_id == 0x0026 or "AD5X" in self.model.upper() or "AD5X" in self.name.upper()


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Asyncio UDP datagram protocol feeding responses into an async queue."""

    def __init__(self, queue: asyncio.Queue[tuple[bytes, str]]) -> None:
        self._queue = queue

    def datagram_received(self, data: bytes, addr: tuple[str, int] | Any) -> None:
        host = addr[0] if isinstance(addr, tuple) and addr else ""
        if data and host:
            self._queue.put_nowait((data, str(host)))


def parse_discovery_packet(
    buffer: bytes, ip_address: str
) -> DiscoveredFlashForgePrinter | None:
    """Parse a 276-byte modern FlashForge UDP discovery response."""
    if not buffer or len(buffer) < MODERN_PACKET_MIN_LEN:
        return None

    try:
        name = (
            buffer[0:0x84]
            .decode("utf-8", errors="ignore")
            .split("\x00", 1)[0]
            .strip()
        )
        command_port = int.from_bytes(buffer[0x84:0x86], byteorder="big") or 8899
        product_id = int.from_bytes(buffer[0x88:0x8A], byteorder="big")
        product_type = int.from_bytes(buffer[0x8C:0x8E], byteorder="big")
        event_port = int.from_bytes(buffer[0x8E:0x90], byteorder="big") or 8898
        serial_number = (
            buffer[0x92 : 0x92 + 128]
            .decode("utf-8", errors="ignore")
            .split("\x00", 1)[0]
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None

    if not serial_number:
        return None

    if product_id in MODERN_PRODUCT_IDS:
        model = MODERN_PRODUCT_IDS[product_id]
    elif "AD5X" in name.upper():
        model = "AD5X"
    elif product_type == 0x5A02:
        model = "Adventurer 5M Pro" if "PRO" in name.upper() else "Adventurer 5M"
    else:
        model = "FlashForge"

    return DiscoveredFlashForgePrinter(
        ip_address=ip_address,
        serial_number=serial_number,
        name=name or model,
        model=model,
        http_port=event_port,
        tcp_port=command_port,
        product_id=product_id,
    )


async def _async_get_broadcast_addresses(hass: HomeAssistant | None) -> list[str]:
    """Collect directed subnet broadcast addresses via HA network adapters + stdlib fallback."""
    broadcasts: list[str] = []

    if hass is not None:
        try:
            from homeassistant.components import network as ha_network

            adapters = await ha_network.async_get_adapters(hass)
            for adapter in adapters:
                if not adapter.get("enabled"):
                    continue
                for ipv4 in adapter.get("ipv4", []):
                    addr = ipv4.get("address")
                    prefix = ipv4.get("network_prefix")
                    if addr and prefix and not str(addr).startswith("127."):
                        iface = ipaddress.IPv4Interface(f"{addr}/{prefix}")
                        bcast = str(iface.network.broadcast_address)
                        if bcast not in broadcasts:
                            broadcasts.append(bcast)
        except Exception:  # noqa: BLE001
            pass

    if not broadcasts:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tmp_sock:
                tmp_sock.connect(("10.255.255.255", 1))
                local_ip = tmp_sock.getsockname()[0]
                if local_ip and not local_ip.startswith("127."):
                    iface = ipaddress.IPv4Interface(f"{local_ip}/24")
                    broadcasts.append(str(iface.network.broadcast_address))
        except OSError:
            pass

    if "255.255.255.255" not in broadcasts:
        broadcasts.append("255.255.255.255")
    return broadcasts


async def async_discover_printers(
    hass: HomeAssistant | None = None,
    timeout: float = 1.8,
    idle_timeout: float = 0.45,
) -> list[DiscoveredFlashForgePrinter]:
    """Broadcast UDP discovery packets on the LAN and return discovered modern FlashForge printers."""
    queue: asyncio.Queue[tuple[bytes, str]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    broadcasts = await _async_get_broadcast_addresses(hass)

    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DiscoveryProtocol(queue),
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
        )
    except OSError as err:
        _LOGGER.debug("Could not open UDP socket for AD5X discovery: %s", err)
        return []

    found: dict[str, DiscoveredFlashForgePrinter] = {}
    try:
        raw_sock = transport.get_extra_info("socket")
        if raw_sock is not None:
            try:
                membership = socket.inet_aton(MULTICAST_ADDRESS) + socket.inet_aton(
                    "0.0.0.0"
                )
                raw_sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership
                )
            except OSError:
                pass

        def _send_probes() -> None:
            for mport in (8899, 19000):
                try:
                    transport.sendto(b"", (MULTICAST_ADDRESS, mport))
                except OSError:
                    pass
            for bcast in broadcasts:
                for bport in DISCOVERY_PORTS:
                    try:
                        transport.sendto(b"", (bcast, bport))
                    except OSError:
                        pass

        _send_probes()
        started = loop.time()
        last_rx: float | None = None
        resent = False

        while True:
            now = loop.time()
            elapsed = now - started
            if elapsed >= timeout:
                break
            if last_rx is not None and (now - last_rx) >= idle_timeout:
                break
            if not resent and not found and elapsed >= (timeout * 0.5):
                resent = True
                _send_probes()

            wait = min(0.15, max(0.01, timeout - elapsed))
            try:
                data, sender_ip = await asyncio.wait_for(queue.get(), timeout=wait)
            except asyncio.TimeoutError:
                continue

            printer = parse_discovery_packet(data, sender_ip)
            if printer is not None:
                last_rx = loop.time()
                found[printer.serial_number.upper()] = printer
    finally:
        transport.close()

    return sorted(
        found.values(),
        key=lambda p: (0 if p.is_ad5x else 1, p.name.lower(), p.ip_address),
    )
