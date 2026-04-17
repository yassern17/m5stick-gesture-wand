"""
modes/media.py — 🎵 MEDIA mode

Controls music playback and system volume. Scroll speed and volume step
size scale with distance so gestures feel natural from across the room.

  TILT_LEFT      Previous track
  TILT_RIGHT     Next track
  TILT_UP        Volume up  (×3 steps from ROOM/ADJACENT)
  TILT_DOWN      Volume down (×3 steps from ROOM/ADJACENT)
  SHAKE          Play / pause
  FLICK_FORWARD  Seek +30 s in current track
  FLICK_BACK     Seek −30 s in current track
  ROTATE_CW      Scroll down (more ticks = further away you are)
  ROTATE_CCW     Scroll up
  BTN_B          Toggle shuffle
"""

from .base import Mode
from .io import tap, scroll, notify, playerctl, e


class MediaMode(Mode):
    icon = "🎵"
    name = "MEDIA"
    description = "Tilt=vol · shake=play · flick=seek · rotate=scroll · B=shuffle"

    def on_tilt_left(self, prox):
        playerctl("previous")

    def on_tilt_right(self, prox):
        playerctl("next")

    def on_tilt_up(self, prox):
        # Bigger steps when you're far away — louder reach across the room
        steps = 3 if prox.zone in ("ROOM", "ADJACENT") else 1
        for _ in range(steps):
            tap(e.KEY_VOLUMEUP)

    def on_tilt_down(self, prox):
        steps = 3 if prox.zone in ("ROOM", "ADJACENT") else 1
        for _ in range(steps):
            tap(e.KEY_VOLUMEDOWN)

    def on_shake(self, prox):
        playerctl("play-pause")

    def on_flick_forward(self, prox):
        """Seek forward 30 seconds in whatever's playing."""
        playerctl("position", "30+")
        notify("⏩ +30s", urgency="low", timeout_ms=1200)

    def on_flick_back(self, prox):
        """Seek backward 30 seconds."""
        playerctl("position", "30-")
        notify("⏪ −30s", urgency="low", timeout_ms=1200)

    def on_rotate_cw(self, prox):
        # More scroll ticks the further away you are
        ticks = max(1, round(prox.distance_m * 0.8))
        scroll(-ticks)

    def on_rotate_ccw(self, prox):
        ticks = max(1, round(prox.distance_m * 0.8))
        scroll(ticks)

    def on_btn_b(self, prox):
        result = playerctl("shuffle", "Toggle")
        status = playerctl("shuffle")
        notify("🔀 Shuffle " + ("on" if status.lower() == "on" else "off"),
               urgency="low", timeout_ms=1500)
