"""
gestures.py — the only file you need to edit to add or change gesture actions.

HOW TO ADD A GESTURE
────────────────────
1. In the firmware, call sendGesture("MY_NEW_GESTURE") wherever you want.
2. Write a handler here and add it to GESTURE_MAP at the bottom.

HOW PROXIMITY WORKS
────────────────────
Each handler receives a ProximityInfo object:
    prox.zone        str   — "IMMEDIATE" | "NEAR" | "ROOM" | "ADJACENT" | "OUT_OF_RANGE"
    prox.distance_m  float — estimated distance in metres
    prox.rssi        int   — smoothed RSSI in dBm

Zones are calibrated for a small apartment (~10 m max range):

    IMMEDIATE   0 – 1 m    right next to you / same desk
    NEAR        1 – 3 m    same room, close
    ROOM        3 – 6 m    same room, far end
    ADJACENT    6 – 9 m    through a wall / next room
    OUT_OF_RANGE  > 9 m    too far — gestures suppressed, handlers NOT called

The distance estimate uses the log-distance path loss model. Tune
RSSI_AT_1M and PATH_LOSS_EXP below if your hardware reads differently.

Uses evdev UInput for Wayland-compatible key/mouse injection.
"""

import math
from typing import NamedTuple

import evdev
from evdev import UInput, ecodes as e

# ── Distance model calibration ────────────────────────────────────────────────
# RSSI_AT_1M: measured signal strength 1 metre from the device (dBm).
#   Hold the watch 1 m from your laptop, read the RSSI from the client log,
#   and paste it here. Default -59 is a reasonable BLE starting point.
RSSI_AT_1M    = -59
PATH_LOSS_EXP = 2.5   # 2.0 = free space; 2.5–3.5 = furnished indoor

# ── Zone distance cutoffs (metres) ────────────────────────────────────────────
# Edit these to shift where zone boundaries fall in your space.
ZONE_IMMEDIATE_M  = 1.0
ZONE_NEAR_M       = 3.0
ZONE_ROOM_M       = 6.0
ZONE_ADJACENT_M   = 9.0
# > ZONE_ADJACENT_M → OUT_OF_RANGE (gestures suppressed)

# ── ProximityInfo ─────────────────────────────────────────────────────────────
class ProximityInfo(NamedTuple):
    zone:       str    # one of the five zone names above
    distance_m: float  # estimated metres (one decimal place)
    rssi:       int    # smoothed RSSI in dBm

def estimate_distance(rssi: int) -> float:
    """Log-distance path loss model → metres."""
    return 10 ** ((RSSI_AT_1M - rssi) / (10 * PATH_LOSS_EXP))

def proximity_info(rssi: int) -> ProximityInfo:
    dist = round(estimate_distance(rssi), 1)
    if   dist <= ZONE_IMMEDIATE_M: zone = "IMMEDIATE"
    elif dist <= ZONE_NEAR_M:      zone = "NEAR"
    elif dist <= ZONE_ROOM_M:      zone = "ROOM"
    elif dist <= ZONE_ADJACENT_M:  zone = "ADJACENT"
    else:                          zone = "OUT_OF_RANGE"
    return ProximityInfo(zone=zone, distance_m=dist, rssi=rssi)

# ── Virtual input device ──────────────────────────────────────────────────────
# Add key codes here if you write new gestures that need them.
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
# Each handler receives a ProximityInfo. Use prox.zone or prox.distance_m
# to change behaviour based on how far away you are.
#
# Example — bigger volume step when you're across the room:
#   def on_tilt_up(prox):
#       steps = 3 if prox.zone in ("ROOM", "ADJACENT") else 1
#       for _ in range(steps): tap(e.KEY_VOLUMEUP)

def on_tilt_left(prox: ProximityInfo):
    tap(e.KEY_PREVIOUSSONG)

def on_tilt_right(prox: ProximityInfo):
    tap(e.KEY_NEXTSONG)

def on_tilt_up(prox: ProximityInfo):
    tap(e.KEY_VOLUMEUP)

def on_tilt_down(prox: ProximityInfo):
    tap(e.KEY_VOLUMEDOWN)

def on_shake(prox: ProximityInfo):
    tap(e.KEY_PLAYPAUSE)

def on_flick_forward(prox: ProximityInfo):
    """Flick wrist forward → next slide / step right."""
    tap(e.KEY_RIGHT)

def on_flick_back(prox: ProximityInfo):
    """Flick wrist back → previous slide / step left."""
    tap(e.KEY_LEFT)

def on_rotate_cw(prox: ProximityInfo):
    """Rotate clockwise → scroll down."""
    scroll(-3)

def on_rotate_ccw(prox: ProximityInfo):
    """Rotate counter-clockwise → scroll up."""
    scroll(3)

def on_btn_a(prox: ProximityInfo):
    """Big side button → space."""
    tap(e.KEY_SPACE)

def on_btn_b(prox: ProximityInfo):
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
