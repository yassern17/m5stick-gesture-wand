#!/usr/bin/env python3
"""
M5GestureWand — laptop BLE client.

Usage:
    python laptop_client.py              # auto-scan and connect
    python laptop_client.py AA:BB:CC:..  # connect directly by MAC address

Gestures and proximity thresholds live in gestures.py.
This file handles BLE connection, RSSI monitoring, and reconnection only.
"""

import asyncio
import logging
import sys

from bleak import BleakClient, BleakScanner

from gestures import GESTURE_MAP, proximity_zone

# ── BLE identifiers — must match firmware ─────────────────────────────────────
DEVICE_NAME   = "M5GestureWand"
SERVICE_UUID  = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
GESTURE_UUID  = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

# ── RSSI scan interval ────────────────────────────────────────────────────────
# How often to re-scan for the device to update proximity.
# Lower = more responsive proximity, but more radio traffic.
RSSI_SCAN_INTERVAL = 8.0  # seconds

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gesturewand")

# ── Shared state ──────────────────────────────────────────────────────────────
_rssi = -60  # initialise to NEAR so gestures work before first RSSI scan

# ── Gesture notification callback ────────────────────────────────────────────
def _on_gesture(sender, data: bytearray):
    gesture = data.decode(errors="replace").strip()
    zone    = proximity_zone(_rssi)

    if zone == "FAR":
        log.debug("Suppressed %-16s (FAR,  RSSI %d dBm)", gesture, _rssi)
        return

    handler = GESTURE_MAP.get(gesture)
    if handler is None:
        if gesture in GESTURE_MAP:
            return  # deliberately mapped to None — silenced
        log.warning("Unknown gesture %r — add it to GESTURE_MAP in gestures.py", gesture)
        return

    log.info("%-16s  zone=%-6s  RSSI=%d dBm", gesture, zone, _rssi)
    try:
        handler(zone)
    except Exception as exc:
        log.error("Handler for %r raised: %s", gesture, exc)

# ── Background RSSI monitor ───────────────────────────────────────────────────
async def _rssi_monitor(address: str):
    """Periodically scan for the device to update _rssi for proximity gating."""
    global _rssi
    while True:
        await asyncio.sleep(RSSI_SCAN_INTERVAL)
        try:
            found = await BleakScanner.discover(timeout=3.0, return_adv=True)
            for device, adv in found.values():
                if device.address.upper() == address.upper():
                    if adv.rssi is not None:
                        _rssi = adv.rssi
                    log.debug("RSSI %d dBm → zone=%s", _rssi, proximity_zone(_rssi))
                    break
        except Exception as exc:
            log.debug("RSSI scan error: %s", exc)

# ── Device discovery ──────────────────────────────────────────────────────────
async def _find_device() -> str:
    log.info("Scanning for '%s'...", DEVICE_NAME)
    while True:
        found = await BleakScanner.discover(timeout=10.0, return_adv=True)
        for device, adv in found.values():
            if device.name == DEVICE_NAME:
                rssi = adv.rssi if adv.rssi is not None else -99
                log.info("Found %s  address=%s  RSSI=%d dBm", device.name, device.address, rssi)
                return device.address
        log.info("Not found — retrying scan...")

# ── Main connection loop ──────────────────────────────────────────────────────
async def run(address: str | None = None):
    if address is None:
        address = await _find_device()

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
