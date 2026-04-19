#!/usr/bin/env python3
"""
watch_daemon.py — persistent BLE connection manager for M5ClaudeWand.

Maintains a permanent BLE connection to the watch and exposes a local socket
so that watch_ctl.py can send commands instantly without re-scanning.

  Linux/Mac : Unix socket at /tmp/claude-watch.sock
  Windows   : TCP on 127.0.0.1:63185

Run once per session:
    python watch_daemon.py

Commands over the socket (newline-terminated JSON):
    {"cmd": "connected"}
    {"cmd": "status",  "text": "..."}
    {"cmd": "notify",  "text": "..."}
    {"cmd": "ask",     "text": "...", "timeout": 30}
    {"cmd": "events"}
    {"cmd": "time_sync"}
    {"cmd": "quit"}

Each command gets a JSON response line back.
"""

import asyncio
import json
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
import tempfile

from bleak import BleakClient, BleakScanner

DEVICE_NAME     = "M5ClaudeWand"
EVENT_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
CMD_CHAR_UUID   = "beb5483e-36e1-4688-b7f5-ea07361b26a9"

# Platform-specific IPC config
if sys.platform == "win32":
    SOCKET_PATH = None
    TCP_HOST    = "127.0.0.1"
    TCP_PORT    = 63185
    _tmp        = Path(tempfile.gettempdir())
    PID_PATH    = str(_tmp / "claude-watch.pid")
else:
    SOCKET_PATH = "/tmp/claude-watch.sock"
    TCP_HOST    = None
    TCP_PORT    = None
    PID_PATH    = "/tmp/claude-watch.pid"


class WatchDaemon:
    def __init__(self):
        self._loop      = asyncio.new_event_loop()
        self._client    = None
        self._connected = False
        self._events: queue.Queue[str] = queue.Queue()
        self._subscribers: list[queue.Queue] = []
        self._subscribers_lock = threading.Lock()
        self._ble_thread = threading.Thread(
            target=self._run_ble, daemon=True, name="ble")

    # ── Public helpers (called from socket handler thread) ────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    def send(self, payload: str) -> bool:
        if not self._connected:
            return False
        fut = asyncio.run_coroutine_threadsafe(
            self._write(payload.encode()), self._loop)
        try:
            fut.result(timeout=5.0)
            return True
        except Exception:
            return False

    def drain_events(self) -> list[str]:
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def wait_approval(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                ev = self._events.get(timeout=min(remaining, 0.5))
                if ev in ("APPROVE", "REJECT"):
                    return ev
            except queue.Empty:
                pass
        return "timeout"

    # ── BLE background thread ─────────────────────────────────────────────────

    def _run_ble(self):
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self):
        while True:
            try:
                print(f"[ble] scanning for {DEVICE_NAME}…", flush=True)
                device = await BleakScanner.find_device_by_name(
                    DEVICE_NAME, timeout=10.0)
                if device is None:
                    await asyncio.sleep(3.0)
                    continue

                print(f"[ble] found {device.address}, connecting…", flush=True)
                async with BleakClient(
                    device,
                    disconnected_callback=self._on_disconnect,
                ) as client:
                    self._client    = client
                    self._connected = True
                    print("[ble] connected", flush=True)
                    await client.start_notify(EVENT_CHAR_UUID, self._on_notify)
                    # Sync time immediately on connect
                    ts = str(int(time.time()) + time.localtime().tm_gmtoff)
                    await client.write_gatt_char(
                        CMD_CHAR_UUID, f"T:{ts}".encode(), response=False)
                    while client.is_connected:
                        await asyncio.sleep(0.5)

            except Exception as e:
                print(f"[ble] error: {e}", flush=True)

            self._connected = False
            self._client    = None
            print("[ble] disconnected — will retry", flush=True)
            await asyncio.sleep(5.0)

    def _on_disconnect(self, _):
        self._connected = False
        self._client    = None

    def _on_notify(self, _, data: bytearray):
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg:
            self._events.put(msg)
            print(f"[watch] {msg}", flush=True)
            with self._subscribers_lock:
                for q in self._subscribers:
                    q.put(msg)

    async def _write(self, data: bytes):
        if self._client and self._connected:
            await self._client.write_gatt_char(
                CMD_CHAR_UUID, data, response=True)

    # ── Socket server ─────────────────────────────────────────────────────────

    def _handle_cmd(self, req: dict) -> dict:
        cmd = req.get("cmd", "")

        if cmd == "connected":
            return {"ok": True, "result": self._connected}

        if cmd == "status":
            ok = self.send(f"S:{req.get('text','')[:38]}")
            return {"ok": ok}

        if cmd == "notify":
            ok = self.send(f"N:{req.get('text','')[:38]}")
            return {"ok": ok}

        if cmd == "ask":
            if not self._connected:
                return {"ok": False, "result": "not_connected"}
            self.drain_events()
            # Alert the user before blocking — buzz + flash so they know to look
            self.send("N:Approval needed")
            time.sleep(0.4)  # let the notification animate before ask overwrites it
            ok = self.send(f"A:{req.get('text','')[:38]}")
            if not ok:
                return {"ok": False, "result": "not_connected"}
            result = self.wait_approval(float(req.get("timeout", 30)))
            return {"ok": True, "result": result}

        if cmd == "buzz":
            pattern = str(req.get("pattern", "done"))
            ok = self.send(f"B:{pattern}")
            return {"ok": ok}

        if cmd == "progress":
            step  = int(req.get("step", 0))
            total = int(req.get("total", 0))
            label = str(req.get("label", ""))[:38]
            ok = self.send(f"P:{step}/{total}:{label}")
            return {"ok": ok}

        if cmd == "events":
            return {"ok": True, "result": self.drain_events()}

        if cmd == "time_sync":
            ok = self.send(f"T:{int(time.time()) + time.localtime().tm_gmtoff}")
            return {"ok": ok}

        if cmd == "quit":
            return {"ok": True, "quit": True}

        # subscribe is handled specially in the socket loop (streaming)
        return {"ok": False, "error": f"unknown command: {cmd}"}

    async def _socket_server(self):
        async def handle(reader, writer):
            sub_queue: queue.Queue | None = None
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    try:
                        req = json.loads(line.decode())
                    except json.JSONDecodeError:
                        writer.write(b'{"ok":false,"error":"bad json"}\n')
                        await writer.drain()
                        continue

                    # subscribe: stream events until client disconnects
                    if req.get("cmd") == "subscribe":
                        sub_queue = queue.Queue()
                        with self._subscribers_lock:
                            self._subscribers.append(sub_queue)
                        writer.write(b'{"ok":true,"streaming":true}\n')
                        await writer.drain()
                        # Stream events from the subscriber queue
                        loop = asyncio.get_event_loop()
                        while True:
                            try:
                                ev = await loop.run_in_executor(
                                    None, lambda: sub_queue.get(timeout=1.0))
                                writer.write(
                                    (json.dumps({"event": ev}) + "\n").encode())
                                await writer.drain()
                            except queue.Empty:
                                # Send keepalive ping
                                try:
                                    writer.write(b'{"ping":true}\n')
                                    await writer.drain()
                                except Exception:
                                    break
                        break

                    resp = self._handle_cmd(req)
                    writer.write((json.dumps(resp) + "\n").encode())
                    await writer.drain()

                    if resp.get("quit"):
                        os._exit(0)
            except Exception:
                pass
            finally:
                if sub_queue is not None:
                    with self._subscribers_lock:
                        try:
                            self._subscribers.remove(sub_queue)
                        except ValueError:
                            pass
                writer.close()

        if sys.platform == "win32":
            server = await asyncio.start_server(
                handle, host=TCP_HOST, port=TCP_PORT)
            print(f"[socket] listening on {TCP_HOST}:{TCP_PORT}", flush=True)
        else:
            if SOCKET_PATH and os.path.exists(SOCKET_PATH):
                os.unlink(SOCKET_PATH)
            server = await asyncio.start_unix_server(handle, path=SOCKET_PATH)
            os.chmod(SOCKET_PATH, 0o600)
            print(f"[socket] listening on {SOCKET_PATH}", flush=True)

        async with server:
            await server.serve_forever()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        # Write PID file
        Path(PID_PATH).write_text(str(os.getpid()))

        self._ble_thread.start()

        # Run socket server on the main asyncio loop (separate from BLE loop)
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        main_loop = asyncio.new_event_loop()
        try:
            main_loop.run_until_complete(self._socket_server())
        finally:
            if SOCKET_PATH and os.path.exists(SOCKET_PATH):
                os.unlink(SOCKET_PATH)
            if os.path.exists(PID_PATH):
                os.unlink(PID_PATH)


if __name__ == "__main__":
    daemon = WatchDaemon()

    def _shutdown(*_):
        # Disconnect BLE cleanly so BlueZ doesn't hold a stale connection
        if daemon._client and daemon._connected:
            fut = asyncio.run_coroutine_threadsafe(
                daemon._client.disconnect(), daemon._loop)
            try:
                fut.result(timeout=3.0)
            except Exception:
                pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)
    daemon.run()
