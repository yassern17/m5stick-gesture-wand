#!/usr/bin/env python3
"""
M5GestureWand — laptop BLE client + spatial mapper.

Usage:
    python laptop_client.py                 # auto-discover, launch GUI
    python laptop_client.py --no-gui        # headless, original behaviour
    python laptop_client.py AA:BB:CC:..     # connect by MAC

Spatial mapping fuses:
  - the laptop's own BLE RSSI
  - zero or more ESP32 anchor nodes broadcasting RSSI over UDP

Every gesture is matched against the stored fingerprints and the resulting
label is included in the ProximityInfo passed to handlers (gestures.py).
"""

import argparse
import asyncio
import logging
import threading
import time
from typing import Optional

from bleak import BleakClient, BleakScanner

from anchors     import AnchorListener
from fingerprint import FingerprintStore
from gestures    import GESTURE_MAP, proximity_info

# ── BLE identifiers — must match firmware ────────────────────────────────────
DEVICE_NAME  = "M5GestureWand"
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
GESTURE_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

# ── RSSI smoothing ────────────────────────────────────────────────────────────
RSSI_EMA_ALPHA     = 0.25
RSSI_SCAN_INTERVAL = 4.0

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)-22s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gesturewand")


# ── Shared application state ─────────────────────────────────────────────────
class AppState:
    def __init__(self) -> None:
        self.laptop_rssi:  Optional[float] = -60.0   # optimistic seed
        self.connection:   str             = "starting"
        self.last_gesture: Optional[tuple[str, float]] = None
        self.anchors      = AnchorListener()
        self.fingerprints = FingerprintStore()

    def update_rssi(self, raw: int) -> None:
        if self.laptop_rssi is None:
            self.laptop_rssi = float(raw)
        else:
            self.laptop_rssi = (
                RSSI_EMA_ALPHA * raw
                + (1.0 - RSSI_EMA_ALPHA) * self.laptop_rssi
            )


# ── Gesture notification callback ────────────────────────────────────────────
def make_gesture_callback(state: AppState):
    def _on_gesture(_sender, data: bytearray) -> None:
        gesture = data.decode(errors="replace").strip()

        rssi_int = int(round(state.laptop_rssi)) if state.laptop_rssi is not None else -99
        prox = proximity_info(rssi_int)

        vec   = state.anchors.live_vector(state.laptop_rssi)
        match = state.fingerprints.match(vec)
        if match is not None:
            prox = prox._replace(
                location   = match.label,
                confidence = match.distance,
            )

        state.last_gesture = (gesture, time.monotonic())

        if prox.zone == "OUT_OF_RANGE" and match is None:
            log.debug("Suppressed %-16s  (~%.1f m, OUT_OF_RANGE)",
                      gesture, prox.distance_m)
            return

        handler = GESTURE_MAP.get(gesture)
        if handler is None:
            if gesture in GESTURE_MAP:
                return  # deliberately mapped to None
            log.warning("Unknown gesture %r — add it to GESTURE_MAP", gesture)
            return

        log.info("%-16s  zone=%-11s  loc=%-14s  ~%4.1f m  RSSI=%d dBm",
                 gesture, prox.zone, prox.location,
                 prox.distance_m, prox.rssi)
        try:
            handler(prox)
        except Exception as exc:
            log.error("Handler for %r raised: %s", gesture, exc)

    return _on_gesture


# ── Background laptop-side RSSI scan ─────────────────────────────────────────
async def _rssi_monitor(state: AppState, address: str) -> None:
    while True:
        await asyncio.sleep(RSSI_SCAN_INTERVAL)
        try:
            found = await BleakScanner.discover(timeout=3.0, return_adv=True)
            for device, adv in found.values():
                if (device.address.upper() == address.upper()
                        and adv.rssi is not None):
                    state.update_rssi(adv.rssi)
                    break
        except Exception as exc:
            log.debug("RSSI scan error: %s", exc)


# ── Discovery ────────────────────────────────────────────────────────────────
async def _find_device(state: AppState) -> tuple[str, int]:
    log.info("Scanning for '%s'...", DEVICE_NAME)
    state.connection = "scanning"
    while True:
        found = await BleakScanner.discover(timeout=10.0, return_adv=True)
        for device, adv in found.values():
            if device.name == DEVICE_NAME:
                rssi = adv.rssi if adv.rssi is not None else -99
                log.info("Found %s  address=%s  RSSI=%d dBm",
                         device.name, device.address, rssi)
                return device.address, rssi
        log.info("Not found — retrying scan...")


# ── Main BLE connection loop ─────────────────────────────────────────────────
async def ble_loop(state: AppState, address: Optional[str]) -> None:
    if address is None:
        address, first_rssi = await _find_device(state)
        state.laptop_rssi = float(first_rssi)
    else:
        log.info("Connecting to %s", address)

    callback = make_gesture_callback(state)

    while True:
        try:
            state.connection = "connecting"
            async with BleakClient(address, timeout=20.0) as client:
                state.connection = "connected"
                log.info("Connected. Listening for gestures.")
                await client.start_notify(GESTURE_UUID, callback)

                rssi_task = asyncio.create_task(_rssi_monitor(state, address))
                try:
                    while client.is_connected:
                        await asyncio.sleep(1.0)
                finally:
                    rssi_task.cancel()
            state.connection = "reconnecting"
            log.info("Disconnected. Reconnecting...")
        except Exception as exc:
            state.connection = "error"
            log.error("Connection error: %s — retrying in 5 s", exc)
            await asyncio.sleep(5.0)


# ── Async driver — owns anchor listener + BLE loop ───────────────────────────
async def async_main(state: AppState, address: Optional[str]) -> None:
    await state.anchors.start()
    await ble_loop(state, address)


# ── Thread glue: asyncio in worker, Tk in main ───────────────────────────────
def start_async_thread(state: AppState, address: Optional[str]) -> threading.Thread:
    def _runner() -> None:
        try:
            asyncio.run(async_main(state, address))
        except Exception as exc:
            log.error("Async loop crashed: %s", exc)
    t = threading.Thread(target=_runner, daemon=True, name="gesturewand-async")
    t.start()
    return t


def run_gui(state: AppState) -> None:
    import tkinter as tk
    from gui import MapperGUI

    root = tk.Tk()
    MapperGUI(
        root,
        get_laptop_rssi      = lambda: state.laptop_rssi,
        anchors              = state.anchors,
        fingerprints         = state.fingerprints,
        get_last_gesture     = lambda: state.last_gesture,
        get_connection_state = lambda: state.connection,
    )
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass


# ── Entry point ──────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("address", nargs="?",
                        help="BLE MAC address (optional; otherwise auto-discover)")
    parser.add_argument("--no-gui", action="store_true",
                        help="run headless, without the mapper GUI")
    args = parser.parse_args()

    state = AppState()

    if args.no_gui:
        try:
            asyncio.run(async_main(state, args.address))
        except KeyboardInterrupt:
            log.info("Stopped.")
        return

    start_async_thread(state, args.address)
    run_gui(state)


if __name__ == "__main__":
    main()
