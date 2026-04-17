"""
modes/presentation.py — 📊 PRESENTATION mode

Full wireless presentation remote. Two sub-modes toggle with ROTATE:
  • Slide mode  (default) — gesture = slide navigation
  • Pointer mode          — tilt steers the mouse cursor like a laser pointer

  TILT_LEFT      Previous slide
  TILT_RIGHT     Next slide
  TILT_UP        [slide] Zoom in (Ctrl++) · [pointer] cursor up
  TILT_DOWN      [slide] Zoom out (Ctrl+-) · [pointer] cursor down
  SHAKE          Toggle black screen (key B in most presentation apps)
  FLICK_FORWARD  Jump forward 5 slides
  FLICK_BACK     Jump back 5 slides
  ROTATE_CW      Toggle pointer mode on
  ROTATE_CCW     Toggle pointer mode off
  BTN_B          Escape — end presentation / exit fullscreen
"""

from .base import Mode
from .io import tap, chord, scroll, move_mouse, notify, e


class PresentationMode(Mode):
    icon = "📊"
    name = "PRESENTATION"
    description = "Tilt=slides · shake=blackout · flick=jump5 · rotate=pointer · B=ESC"

    def __init__(self):
        self.pointer_mode = False

    # ── Slide navigation ──────────────────────────────────────────────────────

    def on_tilt_left(self, prox):
        if self.pointer_mode:
            move_mouse(-30, 0)
        else:
            tap(e.KEY_LEFT)

    def on_tilt_right(self, prox):
        if self.pointer_mode:
            move_mouse(30, 0)
        else:
            tap(e.KEY_RIGHT)

    def on_tilt_up(self, prox):
        if self.pointer_mode:
            move_mouse(0, -30)
        else:
            chord(e.KEY_LEFTCTRL, e.KEY_EQUAL)   # Ctrl++ zoom in

    def on_tilt_down(self, prox):
        if self.pointer_mode:
            move_mouse(0, 30)
        else:
            chord(e.KEY_LEFTCTRL, e.KEY_MINUS)   # Ctrl+- zoom out

    def on_shake(self, prox):
        """Toggle black screen. Works in LibreOffice Impress, Evince, etc."""
        tap(e.KEY_B)
        notify("⬛ Screen toggled", urgency="low", timeout_ms=1000)

    def on_flick_forward(self, prox):
        """Jump 5 slides forward."""
        for _ in range(5):
            tap(e.KEY_RIGHT)
        notify("⏭  +5 slides", urgency="low", timeout_ms=1000)

    def on_flick_back(self, prox):
        """Jump 5 slides back."""
        for _ in range(5):
            tap(e.KEY_LEFT)
        notify("⏮  −5 slides", urgency="low", timeout_ms=1000)

    # ── Pointer mode toggle ───────────────────────────────────────────────────

    def on_rotate_cw(self, prox):
        self.pointer_mode = True
        notify("🖱  Pointer mode ON", urgency="low", timeout_ms=1500)

    def on_rotate_ccw(self, prox):
        self.pointer_mode = False
        notify("🖼  Slide mode ON", urgency="low", timeout_ms=1500)

    def on_btn_b(self, prox):
        self.pointer_mode = False
        tap(e.KEY_ESC)

    # ── Reset pointer mode on exit ────────────────────────────────────────────

    def on_exit(self):
        self.pointer_mode = False
