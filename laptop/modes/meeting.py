"""
modes/meeting.py — 🎤 MEETING mode

Wrist-based meeting controls. Mic and camera toggles work at OS level
(PipeWire/PulseAudio) so they work across Meet, Teams, Zoom, and browser calls.

  SHAKE          Mute / unmute microphone (system default source)
  TILT_UP        Raise hand (Ctrl+Alt+H — Google Meet / Teams)
  TILT_DOWN      Lower hand  (same shortcut)
  FLICK_FORWARD  Next participant / Tab
  FLICK_BACK     Previous participant
  ROTATE_CW      Pause all notifications (focus/DND)
  ROTATE_CCW     Resume notifications
  BTN_B          Toggle camera (Ctrl+E — Meet/Teams)
  BTN_A (global) Switch mode
"""

from .base import Mode
from .io import chord, tap, notify, pactl, run, e


class MeetingMode(Mode):
    icon = "🎤"
    name = "MEETING"
    description = "Shake=mute · tilt=hand · rotate=DND · B=camera"

    def __init__(self):
        self._mic_muted = False
        self._hand_raised = False
        self._dnd = False

    # ── Mic control ───────────────────────────────────────────────────────────

    def on_shake(self, prox):
        pactl("set-source-mute", "@DEFAULT_SOURCE@", "toggle")
        self._mic_muted = not self._mic_muted
        if self._mic_muted:
            notify("🔇 Muted", urgency="critical", timeout_ms=2000)
        else:
            notify("🎤 Unmuted", urgency="normal", timeout_ms=2000)

    # ── Hand raise ────────────────────────────────────────────────────────────

    def on_tilt_up(self, prox):
        if not self._hand_raised:
            chord(e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_H)
            self._hand_raised = True
            notify("✋ Hand raised", urgency="low", timeout_ms=2000)

    def on_tilt_down(self, prox):
        if self._hand_raised:
            chord(e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_H)
            self._hand_raised = False
            notify("✋ Hand lowered", urgency="low", timeout_ms=2000)

    # ── Participant navigation ────────────────────────────────────────────────

    def on_flick_forward(self, prox):
        tap(e.KEY_TAB)

    def on_flick_back(self, prox):
        chord(e.KEY_LEFTSHIFT, e.KEY_TAB)

    # ── Do Not Disturb ────────────────────────────────────────────────────────

    def on_rotate_cw(self, prox):
        """Pause all desktop notifications."""
        run("dunstctl", "set-paused", "true")
        self._dnd = True
        notify("🔕 Notifications paused", urgency="low", timeout_ms=2000)

    def on_rotate_ccw(self, prox):
        """Resume notifications."""
        run("dunstctl", "set-paused", "false")
        self._dnd = False
        notify("🔔 Notifications resumed", urgency="low", timeout_ms=2000)

    # ── Camera ────────────────────────────────────────────────────────────────

    def on_btn_b(self, prox):
        """Ctrl+E toggles camera in Google Meet and Teams."""
        chord(e.KEY_LEFTCTRL, e.KEY_E)
        notify("📷 Camera toggled", urgency="low", timeout_ms=1500)

    # ── Cleanup on exit ───────────────────────────────────────────────────────

    def on_exit(self):
        if self._mic_muted:
            pactl("set-source-mute", "@DEFAULT_SOURCE@", "0")
            self._mic_muted = False
            notify("🎤 Mic unmuted (mode exit)", urgency="low", timeout_ms=1500)
        if self._dnd:
            run("dunstctl", "set-paused", "false")
            self._dnd = False
