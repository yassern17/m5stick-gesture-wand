"""
modes/base.py — Mode base class.

Subclass this to create a new mode. Override any on_<GESTURE_NAME> method.
All gesture names are lower-cased before lookup, so on_tilt_left handles
the "TILT_LEFT" gesture.
"""

import logging
from .io import notify

log = logging.getLogger("gesturewand.mode")


class Mode:
    icon: str = "⚙️"
    name: str = "BASE"
    description: str = ""

    @property
    def label(self) -> str:
        return f"{self.icon}  {self.name}"

    def handle(self, gesture: str, prox) -> bool:
        """Dispatch a gesture to the matching on_* method. Returns True if handled."""
        method = getattr(self, "on_" + gesture.lower(), None)
        if method is None:
            return False
        try:
            method(prox)
        except Exception as exc:
            log.error("[%s] %s raised: %s", self.name, gesture, exc)
        return True

    def on_enter(self) -> None:
        """Called when this mode becomes active."""
        notify(self.label, self.description, urgency="low", timeout_ms=2500)

    def on_exit(self) -> None:
        """Called when leaving this mode."""
        pass
