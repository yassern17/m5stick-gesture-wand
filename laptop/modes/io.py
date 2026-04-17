"""
modes/io.py — shared hardware I/O helpers used by every mode.

Centralises the evdev virtual device and all subprocess wrappers so modes
only need: from .io import tap, chord, scroll, notify, run, e
"""

import logging
import subprocess
from evdev import UInput, ecodes as e

log = logging.getLogger("gesturewand.io")

# ── Virtual input device ──────────────────────────────────────────────────────
_capabilities = {
    e.EV_KEY: [
        e.KEY_NEXTSONG, e.KEY_PREVIOUSSONG, e.KEY_PLAYPAUSE,
        e.KEY_VOLUMEUP, e.KEY_VOLUMEDOWN, e.KEY_MUTE,
        e.KEY_RIGHT, e.KEY_LEFT, e.KEY_UP, e.KEY_DOWN,
        e.KEY_SPACE, e.KEY_ESC, e.KEY_ENTER, e.KEY_TAB,
        e.KEY_BRIGHTNESSUP, e.KEY_BRIGHTNESSDOWN,
        e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_LEFTSHIFT, e.KEY_LEFTMETA,
        e.KEY_EQUAL, e.KEY_MINUS, e.KEY_Z, e.KEY_E, e.KEY_H,
        e.KEY_F5, e.KEY_B, e.KEY_W,
    ],
    e.EV_REL: [e.REL_WHEEL, e.REL_X, e.REL_Y],
}
ui = UInput(_capabilities, name="M5GestureWand")

def tap(key_code: int) -> None:
    """Press and release a single key."""
    ui.write(e.EV_KEY, key_code, 1)
    ui.write(e.EV_KEY, key_code, 0)
    ui.syn()

def chord(*keys: int) -> None:
    """Press all keys as a chord then release in reverse order."""
    for k in keys:
        ui.write(e.EV_KEY, k, 1)
    for k in reversed(keys):
        ui.write(e.EV_KEY, k, 0)
    ui.syn()

def scroll(ticks: int) -> None:
    """Scroll mouse wheel. Positive = up, negative = down."""
    ui.write(e.EV_REL, e.REL_WHEEL, ticks)
    ui.syn()

def move_mouse(dx: int, dy: int) -> None:
    """Move mouse cursor by relative pixels."""
    if dx: ui.write(e.EV_REL, e.REL_X, dx)
    if dy: ui.write(e.EV_REL, e.REL_Y, dy)
    ui.syn()

# ── System command helpers ────────────────────────────────────────────────────
def run(*cmd: str) -> str:
    """Run a command, return stdout (empty string on failure)."""
    try:
        result = subprocess.run(list(cmd), capture_output=True, text=True, timeout=3)
        return result.stdout.strip()
    except Exception as exc:
        log.debug("run%s failed: %s", cmd, exc)
        return ""

def notify(title: str, body: str = "", urgency: str = "normal", timeout_ms: int = 3000) -> None:
    """Show a desktop notification via notify-send."""
    run("notify-send", "-u", urgency, "-t", str(timeout_ms), "--", title, body)

def playerctl(*args: str) -> str:
    return run("playerctl", *args)

def pactl(*args: str) -> str:
    return run("pactl", *args)

def hypr(*args: str) -> str:
    return run("hyprctl", "dispatch", *args)

def brightnessctl(*args: str) -> str:
    return run("brightnessctl", *args)
