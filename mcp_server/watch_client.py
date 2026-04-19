"""
Unified watch client — prefers the daemon socket, falls back to direct BLE.

This is what the MCP server uses so it never conflicts with the daemon.
"""

import json
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from .ble_bridge import BLEBridge

if sys.platform == "win32":
    _SOCK_ADDR: tuple | str = ("127.0.0.1", 63185)
    _SOCK_FAMILY = socket.AF_INET
else:
    _SOCK_ADDR = "/tmp/claude-watch.sock"
    _SOCK_FAMILY = socket.AF_UNIX


def _daemon_call(req: dict, timeout: float = 5.0) -> dict | None:
    try:
        s = socket.socket(_SOCK_FAMILY, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(_SOCK_ADDR)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(256)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode())
    except Exception:
        return None


def _daemon_reachable() -> bool:
    return _daemon_call({"cmd": "connected"}) is not None


class WatchClient:
    """
    Drop-in replacement for BLEBridge in the MCP server.
    Routes through the daemon when it's running so MCP + daemon coexist.
    """

    def __init__(self) -> None:
        self._bridge: Optional[BLEBridge] = None

    def start(self) -> None:
        if not _daemon_reachable():
            self._bridge = BLEBridge()
            self._bridge.start()

    def _ensure_bridge(self) -> None:
        """Start direct bridge if daemon disappeared after startup."""
        if self._bridge is None and not _daemon_reachable():
            self._bridge = BLEBridge()
            self._bridge.start()

    @property
    def connected(self) -> bool:
        if _daemon_reachable():
            r = _daemon_call({"cmd": "connected"})
            return bool(r and r.get("result"))
        self._ensure_bridge()
        return bool(self._bridge and self._bridge.connected)

    def send(self, command: str) -> bool:
        if _daemon_reachable():
            if command.startswith("N:"):
                r = _daemon_call({"cmd": "notify", "text": command[2:]}, timeout=6.0)
            elif command.startswith("S:"):
                r = _daemon_call({"cmd": "status", "text": command[2:]})
            elif command.startswith("A:"):
                r = _daemon_call({"cmd": "ask", "text": command[2:], "timeout": 30},
                                 timeout=35.0)
            elif command.startswith("P:"):
                rest  = command[2:]
                slash = rest.index('/')
                colon = rest.index(':', slash + 1)
                r = _daemon_call({
                    "cmd":   "progress",
                    "step":  int(rest[:slash]),
                    "total": int(rest[slash + 1:colon]),
                    "label": rest[colon + 1:],
                })
            elif command.startswith("B:"):
                r = _daemon_call({"cmd": "buzz", "pattern": command[2:]})
            else:
                return False
            return bool(r and r.get("ok"))
        self._ensure_bridge()
        return bool(self._bridge and self._bridge.send(command))

    def drain_events(self) -> list[str]:
        if _daemon_reachable():
            r = _daemon_call({"cmd": "events"})
            return r.get("result", []) if r else []
        self._ensure_bridge()
        return self._bridge.drain_events() if self._bridge else []

    def wait_for_approval(self, timeout: float = 30.0) -> Optional[str]:
        if _daemon_reachable():
            r = _daemon_call({"cmd": "ask", "timeout": timeout},
                             timeout=timeout + 5)
            return r.get("result") if r else None
        self._ensure_bridge()
        return self._bridge.wait_for_approval(timeout) if self._bridge else None
