#!/usr/bin/env python3
"""
watch_ctl.py — send a command to the watch.

Connects to the daemon socket if running, otherwise falls back to direct BLE.

Usage:
  python watch_ctl.py connected
  python watch_ctl.py status "Reading files..."
  python watch_ctl.py notify "Build done!"
  python watch_ctl.py ask "Delete these files?"
  python watch_ctl.py buzz done
  python watch_ctl.py buzz error
  python watch_ctl.py buzz warn
  python watch_ctl.py progress 2 5 "Running tests"
  python watch_ctl.py events
"""
import asyncio
import json
import socket
import sys

from bleak import BleakClient, BleakScanner

DEVICE_NAME     = "M5ClaudeWand"
EVENT_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
CMD_CHAR_UUID   = "beb5483e-36e1-4688-b7f5-ea07361b26a9"
if sys.platform == "win32":
    SOCKET_PATH = None
    TCP_HOST    = "127.0.0.1"
    TCP_PORT    = 63185
else:
    SOCKET_PATH = "/tmp/claude-watch.sock"
    TCP_HOST    = None
    TCP_PORT    = None


# ── Via daemon (preferred) ────────────────────────────────────────────────────

def _via_daemon(cmd: str, args: list[str]) -> bool:
    """Send command via daemon socket. Returns True if daemon handled it."""
    try:
        req: dict = {"cmd": cmd}
        if cmd == "ask":
            req["text"]    = args[0] if args else ""
            req["timeout"] = 30
        elif cmd in ("status", "notify"):
            req["text"] = args[0] if args else ""
        elif cmd == "buzz":
            req["pattern"] = args[0] if args else "done"
        elif cmd == "progress":
            req["step"]  = int(args[0]) if len(args) > 0 else 0
            req["total"] = int(args[1]) if len(args) > 1 else 0
            req["label"] = args[2] if len(args) > 2 else ""

        if sys.platform == "win32":
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(35.0)
            sock.connect((TCP_HOST, TCP_PORT))
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(35.0)
            sock.connect(SOCKET_PATH)
        sock.sendall((json.dumps(req) + "\n").encode())

        resp_line = b""
        while not resp_line.endswith(b"\n"):
            chunk = sock.recv(256)
            if not chunk:
                break
            resp_line += chunk
        sock.close()

        resp = json.loads(resp_line.decode())

        if cmd == "connected":
            print("connected" if resp.get("result") else "not_connected")
        elif cmd == "ask":
            print(resp.get("result", "timeout"))
        elif cmd == "events":
            events = resp.get("result", [])
            print("\n".join(events) if events else "")
        else:
            print("ok" if resp.get("ok") else "not_connected")

        return True

    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return False  # daemon not running — fall through to direct BLE


# ── Direct BLE (fallback) ─────────────────────────────────────────────────────

async def _direct(cmd: str, args: list[str]) -> None:
    msg = args[0] if args else ""

    device = None
    for _ in range(4):
        device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=6.0)
        if device is not None:
            break
    if device is None:
        print("not_connected")
        return

    async with BleakClient(device) as client:
        if cmd == "connected":
            print("connected")

        elif cmd == "status":
            await client.write_gatt_char(
                CMD_CHAR_UUID, f"S:{msg[:38]}".encode(), response=True)
            print("ok")

        elif cmd == "notify":
            await client.write_gatt_char(
                CMD_CHAR_UUID, f"N:{msg[:38]}".encode(), response=True)
            await asyncio.sleep(0.3)
            print("ok")

        elif cmd == "buzz":
            pattern = msg or "done"
            await client.write_gatt_char(
                CMD_CHAR_UUID, f"B:{pattern}".encode(), response=True)
            print("ok")

        elif cmd == "progress":
            step  = int(args[0]) if len(args) > 0 else 0
            total = int(args[1]) if len(args) > 1 else 0
            label = args[2] if len(args) > 2 else ""
            await client.write_gatt_char(
                CMD_CHAR_UUID, f"P:{step}/{total}:{label[:38]}".encode(),
                response=True)
            print("ok")

        elif cmd == "ask":
            result = None
            done   = asyncio.Event()

            def handler(_, data: bytearray):
                nonlocal result
                val = data.decode("utf-8", errors="ignore").strip()
                if val in ("APPROVE", "REJECT"):
                    result = val
                    done.set()

            await client.start_notify(EVENT_CHAR_UUID, handler)
            await client.write_gatt_char(
                CMD_CHAR_UUID, f"A:{msg[:38]}".encode(), response=True)
            try:
                await asyncio.wait_for(done.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                result = "timeout"
            print(result or "timeout")

        elif cmd == "events":
            print("")  # no persistent queue in direct mode


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(
            "usage: watch_ctl.py <connected|status|notify|ask|buzz|progress|events>"
            " [args...]"
        )
        sys.exit(1)
    cmd  = sys.argv[1]
    args = sys.argv[2:]

    if _via_daemon(cmd, args):
        return
    asyncio.run(_direct(cmd, args))


if __name__ == "__main__":
    main()
