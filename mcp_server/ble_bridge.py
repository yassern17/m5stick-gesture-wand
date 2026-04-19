"""
BLE bridge — runs an asyncio event loop on a background thread so the
synchronous MCP tool handlers can talk to the watch without blocking.
"""

import asyncio
import queue
import sys
import threading
import time
from typing import Optional

from bleak import BleakClient, BleakScanner

DEVICE_NAME     = "M5ClaudeWand"
SERVICE_UUID    = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
EVENT_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # watch → laptop
CMD_CHAR_UUID   = "beb5483e-36e1-4688-b7f5-ea07361b26a9"  # laptop → watch


class BLEBridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._client: Optional[BleakClient] = None
        self._connected = False
        self._stopped = False
        self._event_queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="ble-bridge"
        )

    # ── Public API (called from MCP tool handlers, any thread) ───────────────

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Disconnect and stop the background loop."""
        self._stopped = True
        if self._client and self._connected:
            fut = asyncio.run_coroutine_threadsafe(
                self._client.disconnect(), self._loop)
            try:
                fut.result(timeout=3.0)
            except Exception:
                pass
        self._connected = False
        self._client = None

    @property
    def connected(self) -> bool:
        return self._connected

    def send(self, command: str) -> bool:
        """Write a command string to the watch. Returns False if disconnected."""
        if not self._connected or self._client is None:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._write(command.encode("utf-8")), self._loop
        )
        try:
            future.result(timeout=5.0)
            return True
        except Exception:
            return False

    def drain_events(self) -> list[str]:
        """Return and clear all pending watch events."""
        events: list[str] = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def wait_for_approval(self, timeout: float = 30.0) -> Optional[str]:
        """
        Block until APPROVE or REJECT arrives from the watch, ignoring any
        other events that slip through. Returns "APPROVE", "REJECT", or None
        on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                event = self._event_queue.get(timeout=min(remaining, 0.5))
                if event in ("APPROVE", "REJECT"):
                    return event
                # Gesture/button events in IDLE before state propagated — ignore
            except queue.Empty:
                pass
        return None

    # ── Background asyncio loop ───────────────────────────────────────────────

    def _run_loop(self) -> None:
        # Windows needs ProactorEventLoop for BLE via WinRT
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self) -> None:
        while not self._stopped:
            try:
                device = await BleakScanner.find_device_by_name(
                    DEVICE_NAME, timeout=10.0
                )
                if device is None:
                    await asyncio.sleep(3.0)
                    continue

                async with BleakClient(
                    device, disconnected_callback=self._on_disconnect
                ) as client:
                    self._client = client
                    self._connected = True
                    await client.start_notify(EVENT_CHAR_UUID, self._on_notify)
                    while client.is_connected:
                        await asyncio.sleep(0.5)

            except Exception:
                pass

            self._connected = False
            self._client = None
            await asyncio.sleep(5.0)

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._connected = False
        self._client = None

    def _on_notify(self, _sender, data: bytearray) -> None:
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg:
            self._event_queue.put(msg)

    async def _write(self, data: bytes) -> None:
        if self._client and self._connected:
            await self._client.write_gatt_char(
                CMD_CHAR_UUID, data, response=True
            )
