"""
gui.py — Tkinter GUI for the GestureWand spatial mapper.

Shows live RSSI from the laptop + every anchor, the nearest calibrated
fingerprint, and a 2D signature-space canvas where stored fingerprints are
plotted alongside the watch's live position.

Capture averages ~3 seconds of live RSSI and stores the result under a
user-provided label, so small fluctuations don't poison a calibration.
"""

import logging
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from anchors import AnchorListener
from fingerprint import FingerprintStore

log = logging.getLogger("gesturewand.gui")

# ── Display bounds (dBm) ─────────────────────────────────────────────────────
RSSI_MIN = -100
RSSI_MAX = -30

# ── Refresh + capture cadence ────────────────────────────────────────────────
POLL_MS             = 200
CAPTURE_SECONDS     = 3.0
CAPTURE_TICK_MS     = 200


class MapperGUI:
    def __init__(
        self,
        root: tk.Tk,
        get_laptop_rssi: Callable[[], Optional[float]],
        anchors: AnchorListener,
        fingerprints: FingerprintStore,
        get_last_gesture: Callable[[], Optional[tuple[str, float]]],
        get_connection_state: Callable[[], str],
    ) -> None:
        self.root                 = root
        self.get_laptop_rssi      = get_laptop_rssi
        self.anchors              = anchors
        self.fingerprints         = fingerprints
        self.get_last_gesture     = get_last_gesture
        self.get_connection_state = get_connection_state

        self._capture_state: Optional[dict] = None

        root.title("M5 GestureWand — Mapper")
        root.geometry("940x640")
        root.minsize(760, 520)

        self._build_widgets()
        self._refresh_spots_list()
        self.root.after(POLL_MS, self._poll)

    # ── widgets ──────────────────────────────────────────────────────────────
    def _build_widgets(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Status.TLabel", font=("Helvetica", 11))
        style.configure("Big.TLabel",    font=("Helvetica", 16, "bold"))
        style.configure("Mono.TLabel",   font=("TkFixedFont", 10))
        style.configure("Section.TLabelframe.Label", font=("Helvetica", 10, "bold"))

        # ── top status bar ──
        top = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top.pack(fill=tk.X)
        self.conn_label = ttk.Label(top, text="Watch: …", style="Status.TLabel")
        self.conn_label.pack(side=tk.LEFT)
        self.gesture_label = ttk.Label(top, text="Last gesture: —", style="Status.TLabel")
        self.gesture_label.pack(side=tk.RIGHT)

        body = ttk.Frame(self.root, padding=(8, 0))
        body.pack(fill=tk.BOTH, expand=True)

        # ── left column: live RSSI + match ──
        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))

        live_box = ttk.LabelFrame(left, text="Live RSSI", padding=8,
                                  style="Section.TLabelframe")
        live_box.pack(fill=tk.X)
        self.rssi_table = ttk.Treeview(
            live_box, columns=("rssi", "age"),
            show="tree headings", height=8,
        )
        self.rssi_table.heading("#0",   text="Source")
        self.rssi_table.heading("rssi", text="dBm")
        self.rssi_table.heading("age",  text="Age")
        self.rssi_table.column("#0",    width=110, anchor=tk.W)
        self.rssi_table.column("rssi",  width=60,  anchor=tk.E)
        self.rssi_table.column("age",   width=70,  anchor=tk.E)
        self.rssi_table.pack(fill=tk.X)

        match_box = ttk.LabelFrame(left, text="Matched spot",
                                   padding=8, style="Section.TLabelframe")
        match_box.pack(fill=tk.X, pady=(8, 0))
        self.match_label  = ttk.Label(match_box, text="—", style="Big.TLabel")
        self.match_label.pack(anchor=tk.W)
        self.match_detail = ttk.Label(match_box, text="", style="Mono.TLabel",
                                      justify=tk.LEFT)
        self.match_detail.pack(anchor=tk.W)

        # ── middle: signature-space canvas ──
        canvas_box = ttk.LabelFrame(body, text="Signature map",
                                    padding=6, style="Section.TLabelframe")
        canvas_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        self.canvas = tk.Canvas(canvas_box, bg="#111", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw_canvas())

        # ── right column: calibrated spots ──
        right = ttk.LabelFrame(body, text="Calibrated spots", padding=8,
                               style="Section.TLabelframe")
        right.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 0))
        self.spots_list = tk.Listbox(right, height=12, width=28,
                                     selectmode=tk.SINGLE,
                                     exportselection=False,
                                     font=("TkFixedFont", 10))
        self.spots_list.pack(fill=tk.Y, expand=True)
        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_row, text="Delete selected",
                   command=self._delete_selected).pack(side=tk.LEFT)

        # ── bottom: capture controls ──
        capture = ttk.LabelFrame(self.root, text="Register current spot",
                                 padding=8, style="Section.TLabelframe")
        capture.pack(fill=tk.X, padx=8, pady=(4, 8))
        ttk.Label(capture, text="Label:").pack(side=tk.LEFT)
        self.label_entry = ttk.Entry(capture)
        self.label_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.label_entry.bind("<Return>", lambda _e: self._start_capture())
        self.capture_btn = ttk.Button(capture, text="Capture",
                                      command=self._start_capture)
        self.capture_btn.pack(side=tk.LEFT)

    # ── periodic refresh ─────────────────────────────────────────────────────
    def _poll(self) -> None:
        try:
            self._refresh_status()
            self._refresh_rssi_table()
            self._refresh_match()
            self._draw_canvas()
        except Exception:
            log.exception("Poll failed")
        self.root.after(POLL_MS, self._poll)

    def _refresh_status(self) -> None:
        self.conn_label.config(text=f"Watch: {self.get_connection_state()}")
        g = self.get_last_gesture()
        if g is None:
            self.gesture_label.config(text="Last gesture: —")
            return
        name, when = g
        age = max(0.0, time.monotonic() - when)
        if age < 60:
            self.gesture_label.config(text=f"Last gesture: {name}  ({age:.1f}s ago)")
        else:
            self.gesture_label.config(text=f"Last gesture: {name}  (>1m ago)")

    def _refresh_rssi_table(self) -> None:
        for iid in self.rssi_table.get_children():
            self.rssi_table.delete(iid)
        laptop = self.get_laptop_rssi()
        if laptop is not None:
            self.rssi_table.insert("", tk.END, iid="laptop", text="laptop",
                                   values=(f"{laptop:.0f}", "live"))
        for aid, info in sorted(self.anchors.anchor_status().items()):
            age = info["age_s"]
            age_str = f"{age:.1f}s" if info["fresh"] else f"stale {age:.0f}s"
            self.rssi_table.insert("", tk.END, iid=f"anchor:{aid}", text=aid,
                                   values=(f"{info['rssi']:.0f}", age_str))

    def _refresh_match(self) -> None:
        vec = self._current_vector()
        if not vec:
            self.match_label.config(text="— no signal —")
            self.match_detail.config(text="")
            return
        m = self.fingerprints.match(vec)
        if m is None:
            self.match_label.config(text="— no fingerprints —")
            self.match_detail.config(text=f"live dims: {', '.join(sorted(vec))}")
            return
        self.match_label.config(text=m.label)
        lines = [f"distance: {m.distance:4.1f} dB   dims: {m.shared_dims}"]
        if m.second_best:
            s_label, s_d = m.second_best
            lines.append(f"2nd:  {s_label} ({s_d:.1f} dB)")
        self.match_detail.config(text="\n".join(lines))

    # ── canvas ───────────────────────────────────────────────────────────────
    def _pick_axes(self) -> tuple[Optional[str], Optional[str]]:
        """Choose two dimensions to plot.

        Prefer 'laptop' on X. For Y, pick the source seen most often across
        live samples and stored fingerprints so the chosen axes line up with
        the most fingerprints possible.
        """
        vec    = self._current_vector()
        counts: dict[str, int] = {}
        for fp in self.fingerprints.all():
            for k in fp.vector:
                counts[k] = counts.get(k, 0) + 1
        for k in vec:
            counts[k] = counts.get(k, 0) + 1
        ordered = sorted(counts, key=lambda k: (-counts[k], k))
        x = "laptop" if "laptop" in ordered else (ordered[0] if ordered else None)
        y_candidates = [c for c in ordered if c != x]
        y = y_candidates[0] if y_candidates else None
        return x, y

    def _draw_canvas(self) -> None:
        self.canvas.delete("all")
        w = max(self.canvas.winfo_width(),  10)
        h = max(self.canvas.winfo_height(), 10)
        pad = 40

        x_dim, y_dim = self._pick_axes()

        def to_px(dbm_x: float, dbm_y: float) -> tuple[float, float]:
            fx = (dbm_x - RSSI_MIN) / (RSSI_MAX - RSSI_MIN)
            fy = (dbm_y - RSSI_MIN) / (RSSI_MAX - RSSI_MIN)
            fx = min(max(fx, 0.0), 1.0)
            fy = min(max(fy, 0.0), 1.0)
            return pad + fx * (w - 2 * pad), h - pad - fy * (h - 2 * pad)

        # Grid lines every 10 dB.
        for dbm in range(RSSI_MIN, RSSI_MAX + 1, 10):
            f  = (dbm - RSSI_MIN) / (RSSI_MAX - RSSI_MIN)
            px = pad + f * (w - 2 * pad)
            py = h - pad - f * (h - 2 * pad)
            self.canvas.create_line(px, pad,       px,      h - pad, fill="#222")
            self.canvas.create_line(pad, py,       w - pad, py,      fill="#222")
            self.canvas.create_text(px, h - pad + 14, text=str(dbm),
                                    fill="#666", font=("TkFixedFont", 8))
            self.canvas.create_text(pad - 20, py,     text=str(dbm),
                                    fill="#666", font=("TkFixedFont", 8))

        # Axis labels.
        self.canvas.create_text(
            w / 2, h - 10,
            text=f"X = {x_dim or '—'}   (→ stronger)",
            fill="#aaa", font=("Helvetica", 9),
        )
        self.canvas.create_text(
            12, h / 2,
            text=f"Y = {y_dim or '—'}",
            fill="#aaa", font=("Helvetica", 9), angle=90,
        )

        if x_dim is None or y_dim is None:
            self.canvas.create_text(
                w / 2, h / 2,
                text="No data — waiting for BLE + anchors",
                fill="#888", font=("Helvetica", 11),
            )
            return

        # Stored fingerprints.
        for fp in self.fingerprints.all():
            if x_dim not in fp.vector or y_dim not in fp.vector:
                continue
            x, y = to_px(fp.vector[x_dim], fp.vector[y_dim])
            r = 8
            self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                    fill="#4aa0ff", outline="#bcd9ff", width=2)
            self.canvas.create_text(x + r + 6, y, text=fp.label,
                                    anchor=tk.W, fill="#cde",
                                    font=("Helvetica", 10, "bold"))

        # Live watch position.
        vec = self._current_vector()
        if x_dim in vec and y_dim in vec:
            lx, ly = to_px(vec[x_dim], vec[y_dim])
            r = 11
            self.canvas.create_oval(lx - r - 5, ly - r - 5,
                                    lx + r + 5, ly + r + 5,
                                    outline="#ffdd55", width=1)
            self.canvas.create_oval(lx - r, ly - r, lx + r, ly + r,
                                    fill="#ff7733", outline="#fff", width=2)
            self.canvas.create_text(lx, ly - r - 12, text="watch",
                                    fill="#ffd", font=("Helvetica", 9, "bold"))

    # ── capture ──────────────────────────────────────────────────────────────
    def _current_vector(self) -> dict[str, float]:
        return self.anchors.live_vector(self.get_laptop_rssi())

    def _start_capture(self) -> None:
        if self._capture_state is not None:
            return
        label = self.label_entry.get().strip()
        if not label:
            messagebox.showwarning(
                "Label required",
                "Type a label before capturing (e.g. 'tv', 'desk', 'kitchen').",
            )
            return
        self._capture_state = {
            "label":     label,
            "samples":   [],
            "remaining": int(round(CAPTURE_SECONDS * 1000 / CAPTURE_TICK_MS)),
            "total":     int(round(CAPTURE_SECONDS * 1000 / CAPTURE_TICK_MS)),
        }
        self.capture_btn.config(state=tk.DISABLED)
        self._capture_tick()

    def _capture_tick(self) -> None:
        st = self._capture_state
        if st is None:
            return
        vec = self._current_vector()
        if vec:
            st["samples"].append(vec)
        st["remaining"] -= 1
        if st["remaining"] <= 0:
            self._finish_capture()
            return
        secs_left = st["remaining"] * CAPTURE_TICK_MS / 1000.0
        self.capture_btn.config(text=f"Sampling {secs_left:.1f}s…")
        self.root.after(CAPTURE_TICK_MS, self._capture_tick)

    def _finish_capture(self) -> None:
        st = self._capture_state or {}
        self._capture_state = None
        self.capture_btn.config(state=tk.NORMAL, text="Capture")

        samples: list[dict[str, float]] = st.get("samples", [])
        label: str = st.get("label", "")
        if not samples:
            messagebox.showwarning(
                "No signal",
                "No RSSI was received during sampling. "
                "Check the watch is connected and anchors are online.",
            )
            return
        keys = set().union(*(s.keys() for s in samples))
        avg: dict[str, float] = {}
        for k in keys:
            vals = [s[k] for s in samples if k in s]
            if vals:
                avg[k] = sum(vals) / len(vals)
        self.fingerprints.add_or_replace(label, avg)
        log.info("Captured fingerprint %r from %d samples: %s",
                 label, len(samples),
                 ", ".join(f"{k}={v:.0f}" for k, v in sorted(avg.items())))
        self.label_entry.delete(0, tk.END)
        self._refresh_spots_list()

    # ── spot list ────────────────────────────────────────────────────────────
    def _refresh_spots_list(self) -> None:
        sel_label: Optional[str] = None
        sel = self.spots_list.curselection()
        if sel:
            sel_label = self.spots_list.get(sel[0]).split("  —  ", 1)[0]

        self.spots_list.delete(0, tk.END)
        for fp in self.fingerprints.all():
            vec_str = "  ".join(f"{k}={v:.0f}" for k, v in sorted(fp.vector.items()))
            self.spots_list.insert(tk.END, f"{fp.label}  —  {vec_str}")
            if fp.label == sel_label:
                self.spots_list.selection_set(tk.END)

    def _delete_selected(self) -> None:
        sel = self.spots_list.curselection()
        if not sel:
            return
        label = self.spots_list.get(sel[0]).split("  —  ", 1)[0]
        if messagebox.askyesno("Delete spot", f"Delete fingerprint '{label}'?"):
            self.fingerprints.remove(label)
            self._refresh_spots_list()
