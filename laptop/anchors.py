"""
anchors.py — UDP listener for ESP32 BLE anchor nodes.

Each anchor broadcasts a small JSON packet to UDP port ANCHOR_PORT on the
local subnet, for example:

    {"id": "esp32-a", "rssi": -72.3, "seq": 123, "fresh": true}

This module receives those packets and exposes the latest reading per anchor
with freshness timestamps so the mapper can build a multi-source RSSI vector.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("gesturewand.anchors")

ANCHOR_PORT           = 42042
ANCHOR_STALE_AFTER_S  = 6.0   # samples older than this are considered lost


@dataclass
class AnchorSample:
    anchor_id:   str
    rssi:        float
    fresh:       bool          # as reported by the anchor (saw watch this cycle)
    received_at: float         # time.monotonic()


class AnchorListener:
    """Async UDP server that collects RSSI packets from anchor ESP32s."""

    def __init__(self, port: int = ANCHOR_PORT) -> None:
        self.port = port
        self.samples: dict[str, AnchorSample] = {}
        self._transport: Optional[asyncio.DatagramTransport] = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _AnchorProtocol(self),
            local_addr=("0.0.0.0", self.port),
            allow_broadcast=True,
        )
        log.info("Listening for anchors on UDP :%d", self.port)

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _handle(self, data: bytes) -> None:
        try:
            msg = json.loads(data.decode("utf-8", errors="replace"))
            aid   = str(msg["id"])
            rssi  = float(msg["rssi"])
            fresh = bool(msg.get("fresh", True))
        except (ValueError, KeyError, TypeError):
            return
        self.samples[aid] = AnchorSample(
            anchor_id   = aid,
            rssi        = rssi,
            fresh       = fresh,
            received_at = time.monotonic(),
        )

    def live_vector(self, laptop_rssi: Optional[float]) -> dict[str, float]:
        """Return the current RSSI vector keyed by source.

        Drops anchors whose last packet is older than ANCHOR_STALE_AFTER_S or
        that flagged themselves as not-fresh. The 'laptop' dimension is
        included iff laptop_rssi is not None.
        """
        now = time.monotonic()
        vec: dict[str, float] = {}
        if laptop_rssi is not None:
            vec["laptop"] = float(laptop_rssi)
        for aid, s in self.samples.items():
            if now - s.received_at > ANCHOR_STALE_AFTER_S:
                continue
            if not s.fresh:
                continue
            vec[aid] = s.rssi
        return vec

    def anchor_status(self) -> dict[str, dict]:
        """UI-friendly per-anchor snapshot: rssi, fresh, age_s."""
        now = time.monotonic()
        return {
            aid: {
                "rssi":  s.rssi,
                "fresh": s.fresh and (now - s.received_at <= ANCHOR_STALE_AFTER_S),
                "age_s": now - s.received_at,
            }
            for aid, s in self.samples.items()
        }


class _AnchorProtocol(asyncio.DatagramProtocol):
    def __init__(self, listener: AnchorListener) -> None:
        self.listener = listener

    def datagram_received(self, data: bytes, addr) -> None:
        self.listener._handle(data)
