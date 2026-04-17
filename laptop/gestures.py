"""
gestures.py — the only file you need to edit to add or change gesture actions.

HOW TO ADD A GESTURE
────────────────────
1. In the firmware, call sendGesture("MY_NEW_GESTURE") wherever you want.
2. Here, write a handler function and add it to GESTURE_MAP.

HOW PROXIMITY ZONES WORK
─────────────────────────
Each handler receives `zone`: "NEAR", "MEDIUM", or "FAR".
  NEAR   — within ~2 m  (RSSI ≥ PROXIMITY_NEAR dBm)
  MEDIUM — 2–5 m        (RSSI ≥ PROXIMITY_MEDIUM dBm)
  FAR    — beyond ~5 m  → gestures suppressed entirely, handlers not called

Uses evdev UInput for Wayland-compatible key/mouse injection.
"""

import evdev
from evdev import UInput, ecodes as e

# ── Proximity thresholds (dBm) ────────────────────────────────────────────────
PROXIMITY_NEAR   = -65
PROXIMITY_MEDIUM = -80

def proximity_zone(rssi: int) -> str:
    if rssi >= PROXIMITY_NEAR:   return "NEAR"
    if rssi >= PROXIMITY_MEDIUM: return "MEDIUM"
    return "FAR"

# ── Virtual input device ──────────────────────────────────────────────────────
# Declares every key/axis this device can emit. Add to these lists if you add
# new gestures that need keys not already listed.
_capabilities = {
    e.EV_KEY: [
        e.KEY_NEXTSONG, e.KEY_PREVIOUSSONG, e.KEY_PLAYPAUSE,
        e.KEY_VOLUMEUP, e.KEY_VOLUMEDOWN, e.KEY_MUTE,
        e.KEY_RIGHT, e.KEY_LEFT, e.KEY_UP, e.KEY_DOWN,
        e.KEY_SPACE, e.KEY_ESC, e.KEY_ENTER,
        e.KEY_BRIGHTNESSUP, e.KEY_BRIGHTNESSDOWN,
    ],
    e.EV_REL: [e.REL_WHEEL],
}
ui = UInput(_capabilities, name="M5GestureWand")

def tap(key_code: int):
    """Press and release a single key."""
    ui.write(e.EV_KEY, key_code, 1)
    ui.write(e.EV_KEY, key_code, 0)
    ui.syn()

def scroll(ticks: int):
    """Scroll the mouse wheel. Positive = up, negative = down."""
    ui.write(e.EV_REL, e.REL_WHEEL, ticks)
    ui.syn()

# ── Gesture handlers ──────────────────────────────────────────────────────────
# Each function receives zone: str ("NEAR" or "MEDIUM").

def on_tilt_left(zone: str):
    tap(e.KEY_PREVIOUSSONG)

def on_tilt_right(zone: str):
    tap(e.KEY_NEXTSONG)

def on_tilt_up(zone: str):
    tap(e.KEY_VOLUMEUP)

def on_tilt_down(zone: str):
    tap(e.KEY_VOLUMEDOWN)

def on_shake(zone: str):
    tap(e.KEY_PLAYPAUSE)

def on_flick_forward(zone: str):
    """Flick wrist forward → next slide / step right."""
    tap(e.KEY_RIGHT)

def on_flick_back(zone: str):
    """Flick wrist back → previous slide / step left."""
    tap(e.KEY_LEFT)

def on_rotate_cw(zone: str):
    """Rotate clockwise → scroll down."""
    scroll(-3)

def on_rotate_ccw(zone: str):
    """Rotate counter-clockwise → scroll up."""
    scroll(3)

def on_btn_a(zone: str):
    """Big side button → space."""
    tap(e.KEY_SPACE)

def on_btn_b(zone: str):
    """Small side button → escape."""
    tap(e.KEY_ESC)

# ── Gesture map ───────────────────────────────────────────────────────────────
# Keys must match EXACTLY what the firmware sends (case-sensitive).
# Set a value to None to silently ignore that gesture.

GESTURE_MAP: dict = {
    "TILT_LEFT":     on_tilt_left,
    "TILT_RIGHT":    on_tilt_right,
    "TILT_UP":       on_tilt_up,
    "TILT_DOWN":     on_tilt_down,
    "SHAKE":         on_shake,
    "FLICK_FORWARD": on_flick_forward,
    "FLICK_BACK":    on_flick_back,
    "ROTATE_CW":     on_rotate_cw,
    "ROTATE_CCW":    on_rotate_ccw,
    "BTN_A":         on_btn_a,
    "BTN_B":         on_btn_b,
}
