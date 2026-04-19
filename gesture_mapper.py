#!/usr/bin/env python3
"""
gesture_mapper.py — map watch gestures to laptop actions.

Reads gesture_map.json from this directory and executes mapped actions
whenever the watch daemon delivers a gesture event.

Run standalone:
    python gesture_mapper.py

Or start/stop from the ClaudeWatch GUI.

gesture_map.json format:
{
  "FLICK_FORWARD": {"type": "key",  "keys": "ctrl+Tab",  "label": "Next tab"},
  "SHAKE":         {"type": "cmd",  "cmd":  "scrot -s",  "label": "Screenshot"}
}

Action types:
  key  — keyboard shortcut sent via xdotool (Linux) or pyautogui / WScript (Windows)
         keys format: "ctrl+Tab", "super+shift+s", "XF86AudioPlay"
  cmd  — shell command run with subprocess (shell=True)
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT  = Path(__file__).parent.resolve()
MAP_FILE = PROJECT / "gesture_map.json"

if sys.platform == "win32":
    _SOCK_ADDR   = ("127.0.0.1", 63185)
    _SOCK_FAMILY = socket.AF_INET
else:
    _SOCK_ADDR   = "/tmp/claude-watch.sock"
    _SOCK_FAMILY = socket.AF_UNIX


def load_map() -> dict:
    try:
        return json.loads(MAP_FILE.read_text()) if MAP_FILE.exists() else {}
    except Exception:
        return {}


def run_action(action: dict) -> None:
    kind = action.get("type", "cmd")
    if kind == "key":
        _send_hotkey(action.get("keys", ""))
    elif kind == "cmd":
        cmd = action.get("cmd", "")
        if cmd:
            subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _send_hotkey(keys: str) -> None:
    if not keys:
        return
    if sys.platform == "linux":
        subprocess.Popen(["xdotool", "key", keys],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        _win_send(keys)


def _win_send(keys: str) -> None:
    """Send a hotkey on Windows. Uses pyautogui if available, else WScript.Shell."""
    try:
        import pyautogui
        parts = keys.lower().split("+")
        pyautogui.hotkey(*parts)
        return
    except ImportError:
        pass

    # WScript.Shell fallback — no Win-key support
    _sk = {
        "ctrl": "^", "shift": "+", "alt": "%",
        "tab":   "{TAB}",   "enter": "{ENTER}", "esc":  "{ESC}",
        "up":    "{UP}",    "down":  "{DOWN}",  "left": "{LEFT}", "right": "{RIGHT}",
        "home":  "{HOME}",  "end":   "{END}",   "pgup": "{PGUP}", "pgdn": "{PGDN}",
        "del":   "{DEL}",   "ins":   "{INS}",   "bs":   "{BS}",
        "f1":  "{F1}", "f2":  "{F2}", "f3":  "{F3}",  "f4":  "{F4}",
        "f5":  "{F5}", "f6":  "{F6}", "f7":  "{F7}",  "f8":  "{F8}",
        "f9":  "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",
    }
    parts  = keys.lower().split("+")
    mods   = "".join(_sk[p] for p in parts if p in ("ctrl", "shift", "alt"))
    key    = "".join(_sk.get(p, p) for p in parts if p not in ("ctrl", "shift", "alt", "win"))
    ps_cmd = f'(New-Object -ComObject WScript.Shell).SendKeys("{mods}{key}")'
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run():
    print("[mapper] started", flush=True)
    gesture_map = load_map()

    while True:
        sock = None
        try:
            sock = socket.socket(_SOCK_FAMILY, socket.SOCK_STREAM)
            sock.connect(_SOCK_ADDR)
            sock.sendall(b'{"cmd": "subscribe"}\n')

            # Consume the ack line
            buf = b""
            while not buf.endswith(b"\n"):
                buf += sock.recv(256)

            print("[mapper] subscribed", flush=True)
            buf = b""

            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("ping"):
                        continue
                    event = msg.get("event", "")
                    if event:
                        gesture_map = load_map()   # hot-reload on every event
                        if event in gesture_map:
                            action = gesture_map[event]
                            label  = action.get("label", event)
                            print(f"[mapper] {event} → {label}", flush=True)
                            run_action(action)

        except (ConnectionRefusedError, FileNotFoundError, OSError):
            time.sleep(5)
        except Exception as e:
            print(f"[mapper] error: {e}", flush=True)
            time.sleep(3)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        print("[mapper] reconnecting…", flush=True)


if __name__ == "__main__":
    run()
