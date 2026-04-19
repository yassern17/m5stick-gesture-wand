"""
fingerprint.py — persistent location fingerprints.

A "fingerprint" is a labeled RSSI vector captured at a physical spot
(e.g. standing next to the TV). At runtime the live vector is k-NN matched
against stored fingerprints to identify which calibrated spot is closest in
signal space.

Fingerprints live at ~/.config/gesturewand/fingerprints.json.
"""

import json
import logging
import math
import os
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("gesturewand.fingerprint")

CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
) / "gesturewand"
FINGERPRINTS_FILE = CONFIG_DIR / "fingerprints.json"


@dataclass
class Fingerprint:
    label:  str
    vector: dict[str, float]     # source-id -> rssi in dBm


@dataclass
class Match:
    label:         str
    distance:      float          # Euclidean dB over shared dims
    shared_dims:   int
    second_best:   Optional[tuple[str, float]] = None


class FingerprintStore:
    """Thread-safe JSON-backed store of labeled RSSI fingerprints."""

    def __init__(self, path: Path = FINGERPRINTS_FILE) -> None:
        self.path   = path
        self._lock  = threading.RLock()
        self._prints: list[Fingerprint] = []
        self.load()

    # ── persistence ──────────────────────────────────────────────────────────
    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._prints = []
                return
            try:
                data = json.loads(self.path.read_text())
                self._prints = [
                    Fingerprint(label=p["label"], vector=dict(p["vector"]))
                    for p in data
                ]
                log.info("Loaded %d fingerprints from %s",
                         len(self._prints), self.path)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                log.warning("Could not load %s: %s — starting empty",
                            self.path, exc)
                self._prints = []

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp  = self.path.with_suffix(".tmp")
            data = [asdict(p) for p in self._prints]
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self.path)

    # ── mutation ─────────────────────────────────────────────────────────────
    def add_or_replace(self, label: str, vector: dict[str, float]) -> None:
        with self._lock:
            self._prints = [p for p in self._prints if p.label != label]
            self._prints.append(Fingerprint(label=label, vector=dict(vector)))
            self.save()

    def remove(self, label: str) -> None:
        with self._lock:
            before = len(self._prints)
            self._prints = [p for p in self._prints if p.label != label]
            if len(self._prints) != before:
                self.save()

    def all(self) -> list[Fingerprint]:
        with self._lock:
            return list(self._prints)

    # ── matching ─────────────────────────────────────────────────────────────
    def match(self, vector: dict[str, float]) -> Optional[Match]:
        """Nearest fingerprint by Euclidean distance in dB-space.

        Compares only dimensions present in both the live vector and the
        stored fingerprint. Fingerprints trained with more dimensions than
        are currently live are slightly penalised so a 2-source fingerprint
        doesn't lose to a 1-source fingerprint that happens to be tied on
        the one dimension they share.

        Returns None when there are no fingerprints, or no stored fingerprint
        shares any dimension with the live vector.
        """
        if not vector:
            return None
        with self._lock:
            candidates = list(self._prints)

        ranked: list[tuple[str, float, int]] = []
        for p in candidates:
            shared = set(vector) & set(p.vector)
            if not shared:
                continue
            d_sq = sum((vector[k] - p.vector[k]) ** 2 for k in shared)
            # Small penalty per missing dimension — calibrated by hand to
            # be roughly the cost of a single "noticeable" dB mismatch.
            missing = len(set(p.vector) - shared)
            d = math.sqrt(d_sq) + 4.0 * missing
            ranked.append((p.label, d, len(shared)))

        if not ranked:
            return None
        ranked.sort(key=lambda x: x[1])
        best_label, best_d, best_shared = ranked[0]
        second = (ranked[1][0], ranked[1][1]) if len(ranked) > 1 else None
        return Match(
            label       = best_label,
            distance    = best_d,
            shared_dims = best_shared,
            second_best = second,
        )
