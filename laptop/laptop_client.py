#!/usr/bin/env python3
"""
M5GestureWand — laptop BLE client.

Usage:
    python laptop_client.py              # auto-scan and connect
    python laptop_client.py AA:BB:CC:..  # connect directly by MAC address

Gestures, zones, and distance calibration live in gestures.py.
This file handles BLE connection, RSSI smoothing, and reconnection only.
"""

import asyncio
import logging
import sys

from bleak import BleakClient, BleakScanner

from gestures import GESTURE_MAP, proximity_info

# ── BLE identifiers — must match firmware ─────────────────────────────────────
DEVICE_NAME  = "M5GestureWand"
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
GESTURE_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

# ── RSSI smoothing — exponential moving average ───────────────────────────────
# Alpha 0.0 = frozen, 1.0 = raw (no smoothing). 0.25 is snappy but stable.
RSSI_EMA_ALPHA    = 0.25
RSSI_SCAN_INTERVAL = 4.0   # seconds between proximity scans

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gesturewand")

# ── Shared RSSI state ─────────────────────────────────────────────────────────
_rssi_smooth: float = -60.0   # EMA-smoothed RSSI; starts optimistic (NEAR)

def _update_rssi(raw: int) -> None:
    global _rssi_smooth
    _rssi_smooth = RSSI_EMA_ALPHA * raw + (1 - RSSI_EMA_ALPHA) * _rssi_smooth

def _current_prox():
    return proximity_info(int(round(_rssi_smooth)))

# ── Gesture notification callback ─────────────────────────────────────────────
def _on_gesture(sender, data: bytearray):
    gesture = data.decode(errors="replace").strip()
    prox    = _current_prox()

    if prox.zone == "OUT_OF_RANGE":
        log.debug("Suppressed %-16s  (~%.1f m, OUT_OF_RANGE)", gesture, prox.distance_m)
        return

    handler = GESTURE_MAP.get(gesture)
    if handler is None:
        if gesture in GESTURE_MAP:
            return  # deliberately mapped to None — silenced
        log.warning("Unknown gesture %r — add it to GESTURE_MAP in gestures.py", gesture)
        return

    log.info("%-16s  zone=%-11s  ~%4.1f m  RSSI=%d dBm",
             gesture, prox.zone, prox.distance_m, prox.rssi)
    try:
        handler(prox)
    except Exception as exc:
        log.error("Handler for %r raised: %s", gesture, exc)

# ── Background RSSI monitor ───────────────────────────────────────────────────
async def _rssi_monitor(address: str):
    """Scan every RSSI_SCAN_INTERVAL seconds and EMA-smooth the result."""
    while True:
        await asyncio.sleep(RSSI_SCAN_INTERVAL)
        try:
            found = await BleakScanner.discover(timeout=3.0, return_adv=True)
            for device, adv in found.values():
                if device.address.upper() == address.upper() and adv.rssi is not None:
                    _update_rssi(adv.rssi)
                    prox = _current_prox()
                    log.debug("RSSI raw=%d  smooth=%d  zone=%-11s  ~%.1f m",
                              adv.rssi, int(_rssi_smooth), prox.zone, prox.distance_m)
                    break
        except Exception as exc:
            log.debug("RSSI scan error: %s", exc)

# ── Device discovery ──────────────────────────────────────────────────────────
async def _find_device() -> tuple[str, int]:
    log.info("Scanning for '%s'...", DEVICE_NAME)
    while True:
        found = await BleakScanner.discover(timeout=10.0, return_adv=True)
        for device, adv in found.values():
            if device.name == DEVICE_NAME:
                rssi = adv.rssi if adv.rssi is not None else -99
                prox = proximity_info(rssi)
                log.info("Found %s  address=%s  ~%.1f m  zone=%s  RSSI=%d dBm",
                         device.name, device.address,
                         prox.distance_m, prox.zone, rssi)
                return device.address, rssi
        log.info("Not found — retrying scan...")

# ── Main connection loop ──────────────────────────────────────────────────────
async def run(address: str | None = None):
    global _rssi_smooth

    if address is None:
        address, first_rssi = await _find_device()
        _rssi_smooth = float(first_rssi)   # seed EMA with real first reading
    else:
        log.info("Connecting to %s", address)

    while True:
        try:
            async with BleakClient(address, timeout=20.0) as client:
                log.info("Connected. Listening for gestures. (Ctrl-C to quit)")
                await client.start_notify(GESTURE_UUID, _on_gesture)

                rssi_task = asyncio.create_task(_rssi_monitor(address))
                try:
                    while client.is_connected:
                        await asyncio.sleep(1.0)
                finally:
                    rssi_task.cancel()

            log.info("Disconnected. Reconnecting...")
        except Exception as exc:
            log.error("Connection error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5.0)

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mac = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        asyncio.run(run(mac))
    except KeyboardInterrupt:
        log.info("Stopped.")
