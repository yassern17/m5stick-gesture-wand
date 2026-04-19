#!/usr/bin/env python3
"""
watch_events.py — subscribe to live watch events from the daemon.

Each event is printed as a line to stdout, intended for use with
Claude Code's Monitor tool so gestures trigger real-time responses.

Usage:
    python watch_events.py              # all events
    python watch_events.py --gestures   # gestures + buttons only
    python watch_events.py --interrupt  # INTERRUPT/SHAKE only
"""

import json
import socket
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":
    _SOCK_ADDR: tuple | str = ("127.0.0.1", 63185)
    _SOCK_FAMILY = socket.AF_INET
else:
    _SOCK_ADDR = "/tmp/claude-watch.sock"
    _SOCK_FAMILY = socket.AF_UNIX

# Human-readable action hints emitted alongside the raw event
_ACTION_HINTS = {
    "SHAKE":         "SHAKE — interrupt Claude",
    "INTERRUPT":     "INTERRUPT — interrupt Claude",
    "FLICK_FORWARD": "FLICK_FORWARD — proceed / approve",
    "FLICK_BACK":    "FLICK_BACK — undo / cancel",
    "ROTATE_CW":     "ROTATE_CW — next option",
    "ROTATE_CCW":    "ROTATE_CCW — previous option",
    "BTN_A_LONG":    "BTN_A_LONG — status summary requested",
    "BTN_A":         "BTN_A — button pressed",
    "BTN_B":         "BTN_B — button pressed",
}

_GESTURE_EVENTS = set(_ACTION_HINTS.keys()) | {
    "TILT_UP", "TILT_DOWN", "TILT_LEFT", "TILT_RIGHT",
}


def main():
    mode = "all"
    if "--gestures" in sys.argv:
        mode = "gestures"
    elif "--interrupt" in sys.argv:
        mode = "interrupt"

    try:
        s = socket.socket(_SOCK_FAMILY, socket.SOCK_STREAM)
        s.connect(_SOCK_ADDR)
        s.sendall(b'{"cmd": "subscribe"}\n')

        buf = b""
        while not buf.endswith(b"\n"):
            buf += s.recv(256)
        ack = json.loads(buf.decode())
        if not ack.get("ok"):
            print("ERROR: daemon rejected subscribe", flush=True)
            sys.exit(1)

        buf = b""
        while True:
            chunk = s.recv(256)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode())
                except Exception:
                    continue

                if "ping" in msg:
                    continue

                event = msg.get("event", "")
                if not event:
                    continue

                if mode == "interrupt" and event not in ("INTERRUPT", "SHAKE"):
                    continue
                if mode == "gestures" and event not in _GESTURE_EVENTS:
                    continue

                # Emit the action hint if available, else raw event
                print(_ACTION_HINTS.get(event, event), flush=True)

    except (FileNotFoundError, ConnectionRefusedError):
        print("ERROR: daemon not running — start it from the ClaudeWatch GUI",
              flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
