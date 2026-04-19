"""
Claude Watch Manager GUI.

Standalone app for flashing the watch firmware and monitoring BLE connection.
Run with:
    python -m mcp_server.gui

NOTE: close this GUI before starting the MCP server — only one BLE client
can connect to the watch at a time.
"""

import glob
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
import tkinter as tk
from tkinter import ttk

from .ble_bridge import BLEBridge

try:
    import serial.tools.list_ports as _list_ports
    def _get_ports():
        return [p.device for p in _list_ports.comports()]
except ImportError:
    def _get_ports():
        if sys.platform == "win32":
            return [f"COM{i}" for i in range(1, 13)]
        return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))

ROOT_DIR     = Path(__file__).parent.parent
FIRMWARE_DIR = ROOT_DIR / "firmware" / "m5stick_claude_wand"
FQBN         = "m5stack:esp32:m5stack_stickc_plus"
BUILD_DIR    = Path(sys.prefix) / "tmp" / "m5claude-build"

# ─── Colours ─────────────────────────────────────────────────────────────────

BG        = "#1e1e2e"   # dark background
PANEL     = "#2a2a3e"   # slightly lighter panel
ACCENT    = "#7c3aed"   # purple accent
GREEN     = "#22c55e"
YELLOW    = "#eab308"
CYAN      = "#06b6d4"
RED       = "#ef4444"
ORANGE    = "#f97316"
TEXT      = "#e2e8f0"
MUTED     = "#64748b"
WATCH_BG  = "#000000"


# ─── Watch display canvas ─────────────────────────────────────────────────────

class WatchCanvas(tk.Canvas):
    """
    Scaled replica of the 240 × 135 watch LCD.
    Scale = 0.65 → 156 × 87 px.
    """
    S  = 0.65
    CW = int(240 * S)   # 156
    CH = int(135 * S)   # 87

    def __init__(self, parent, **kw):
        kw.setdefault("bg", WATCH_BG)
        super().__init__(
            parent,
            width=self.CW, height=self.CH,
            highlightthickness=2,
            highlightbackground=MUTED,
            **kw,
        )
        self._connected = False
        self._state     = "idle"
        self._status    = "Waiting..."
        self._msg       = ""
        self._redraw()

    def set(self, connected: bool, state: str, status: str, msg: str):
        changed = (
            self._connected != connected or self._state != state
            or self._status != status or self._msg != msg
        )
        if changed:
            self._connected = connected
            self._state     = state
            self._status    = status
            self._msg       = msg
            self._redraw()

    # ── internal ──────────────────────────────────────────────────────────────

    def _redraw(self):
        self.delete("all")
        s = self.S

        if not self._connected:
            self._label("Advertising...", ORANGE, 5*s, 32*s, size=9)
            self._label("M5ClaudeWand",   MUTED,  5*s, 52*s, size=7)
            return

        {
            "idle":      self._draw_idle,
            "notifying": self._draw_notify,
            "asking":    self._draw_ask,
        }.get(self._state, self._draw_idle)()

    def _draw_idle(self):
        s = self.S
        self._label("CLAUDE WAND",     GREEN,  5*s,  4*s, size=7)
        self.create_line(0, int(14*s), self.CW, int(14*s), fill="#333333")
        self._label(self._status[:17], TEXT,   5*s, 22*s, size=10)

    def _draw_notify(self):
        s = self.S
        self._label("NOTIFICATION",    YELLOW, 5*s,  4*s, size=7)
        self.create_line(0, int(14*s), self.CW, int(14*s), fill=YELLOW)
        self._label(self._msg[:17],    TEXT,   5*s, 22*s, size=10)

    def _draw_ask(self):
        s = self.S
        self._label("APPROVE?",        CYAN,   5*s,   4*s, size=7)
        self.create_line(0, int(14*s), self.CW, int(14*s), fill=CYAN)
        self._label(self._msg[:17],    TEXT,   5*s,  22*s, size=10)
        self._label("[A] YES",         GREEN,  5*s, 118*s, size=7)
        self._label("[B] NO",          RED,  170*s, 118*s, size=7)

    def _label(self, text, color, x, y, size=9):
        self.create_text(
            int(x), int(y), text=text, fill=color,
            anchor="nw", font=("Courier", int(size)),
        )


# ─── Main app ─────────────────────────────────────────────────────────────────

class WatchManagerApp:
    POLL_MS = 300

    def __init__(self, root: tk.Tk):
        self.root   = root
        self.bridge = BLEBridge()
        self.bridge.start()

        # Mirror of what we last sent to the watch
        self._vs       = "idle"      # virtual state
        self._vstatus  = "Waiting..."
        self._vmsg     = ""

        root.title("Claude Watch Manager")
        root.configure(bg=BG)
        root.resizable(False, False)

        self._style()
        self._build()
        self._poll()

    # ── ttk style ────────────────────────────────────────────────────────────

    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".",           background=BG,    foreground=TEXT,
                                   font=("Segoe UI", 10) if sys.platform == "win32"
                                        else ("Inter", 10))
        s.configure("TFrame",      background=BG)
        s.configure("TLabelframe", background=BG,    foreground=MUTED,
                                   bordercolor=MUTED, relief="flat",
                                   padding=6)
        s.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                                         font=(None, 9))
        s.configure("TButton",     background=PANEL,  foreground=TEXT,
                                   borderwidth=0,  focuscolor=ACCENT,
                                   relief="flat",  padding=(10, 5))
        s.map("TButton",
              background=[("active", ACCENT), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Flash.TButton", background=ACCENT, foreground=TEXT,
                                     font=(None, 10, "bold"), padding=(12, 6))
        s.map("Flash.TButton",
              background=[("active", "#6d28d9"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("TCombobox",   fieldbackground=PANEL, background=PANEL,
                                   foreground=TEXT, borderwidth=0,
                                   selectbackground=ACCENT)
        s.configure("TEntry",      fieldbackground=PANEL, foreground=TEXT,
                                   borderwidth=0, insertcolor=TEXT)
        s.configure("TLabel",      background=BG, foreground=TEXT)

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack()

        # ── row 0: watch preview  +  connection & test ────────────────────────
        row0 = ttk.Frame(outer)
        row0.grid(row=0, column=0, sticky="nsew", pady=(0, 8))

        # watch preview
        wf = ttk.LabelFrame(row0, text="Watch Preview")
        wf.pack(side="left", padx=(0, 10))
        self.watch = WatchCanvas(wf, bg=WATCH_BG)
        self.watch.pack(padx=8, pady=8)

        # right side
        right = ttk.Frame(row0)
        right.pack(side="left", fill="both", expand=True)

        # connection status
        cf = ttk.LabelFrame(right, text="Connection")
        cf.pack(fill="x", pady=(0, 8))

        dot_row = ttk.Frame(cf)
        dot_row.pack(fill="x", pady=(2, 0))
        self._dot = tk.Canvas(dot_row, width=12, height=12,
                              bg=BG, highlightthickness=0)
        self._dot.pack(side="left", padx=(0, 6))
        self._dot.create_oval(1, 1, 11, 11, fill=MUTED, outline="", tags="dot")
        self._conn_label = tk.Label(dot_row, text="Scanning for M5ClaudeWand…",
                                    bg=BG, fg=TEXT, anchor="w")
        self._conn_label.pack(side="left", fill="x", expand=True)

        ttk.Button(cf, text="Force Reconnect",
                   command=self._reconnect).pack(
            fill="x", pady=(6, 2))

        # test panel
        tf = ttk.LabelFrame(right, text="Test Controls")
        tf.pack(fill="x")

        btn_row = ttk.Frame(tf)
        btn_row.pack(fill="x", pady=(0, 4))
        ttk.Button(btn_row, text="Send Notify",
                   command=self._test_notify).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row, text="Send Ask",
                   command=self._test_ask).pack(side="left")

        entry_row = ttk.Frame(tf)
        entry_row.pack(fill="x")
        self._status_var = tk.StringVar(value="Hello!")
        ttk.Entry(entry_row, textvariable=self._status_var,
                  width=16).pack(side="left", padx=(0, 4))
        ttk.Button(entry_row, text="Set Status",
                   command=self._test_status).pack(side="left")

        # ── row 1: flash panel ────────────────────────────────────────────────
        ff = ttk.LabelFrame(outer, text="Flash Firmware")
        ff.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        port_row = ttk.Frame(ff)
        port_row.pack(fill="x", pady=(0, 6))
        tk.Label(port_row, text="Serial port:", bg=BG, fg=MUTED,
                 font=(None, 9)).pack(side="left", padx=(0, 6))
        self._port_var = tk.StringVar()
        self._port_cb  = ttk.Combobox(port_row, textvariable=self._port_var,
                                       width=22, state="readonly")
        self._port_cb.pack(side="left", padx=(0, 6))
        ttk.Button(port_row, text="Refresh",
                   command=self._refresh_ports).pack(side="left")

        self._flash_btn = ttk.Button(ff, text="⚡  Flash Watch",
                                      style="Flash.TButton",
                                      command=self._flash)
        self._flash_btn.pack(fill="x", pady=(0, 6))

        self._flash_out = tk.Text(
            ff, height=6, state="disabled",
            font=("Courier", 9),
            bg="#0d1117", fg="#8b949e",
            selectbackground=ACCENT,
            relief="flat", padx=6, pady=4,
        )
        self._flash_out.pack(fill="x")

        # ── row 2: event log ─────────────────────────────────────────────────
        lf = ttk.LabelFrame(outer, text="Watch Events")
        lf.grid(row=2, column=0, sticky="ew")

        self._event_log = tk.Text(
            lf, height=7, state="disabled",
            font=("Courier", 9),
            bg="#0d1117", fg="#7ee787",
            selectbackground=ACCENT,
            relief="flat", padx=6, pady=4,
        )
        self._event_log.pack(fill="both", expand=True)
        # color tags
        self._event_log.tag_config("approve", foreground=GREEN)
        self._event_log.tag_config("reject",  foreground=RED)
        self._event_log.tag_config("btn",     foreground=CYAN)
        self._event_log.tag_config("gesture", foreground=YELLOW)
        self._event_log.tag_config("ts",      foreground=MUTED)

        self._refresh_ports()

    # ── periodic poll ─────────────────────────────────────────────────────────

    def _poll(self):
        connected = self.bridge.connected

        color = GREEN if connected else MUTED
        self._dot.itemconfig("dot", fill=color)
        self._conn_label.config(
            text="Connected — M5ClaudeWand" if connected
                 else "Scanning for M5ClaudeWand…"
        )

        self.watch.set(connected, self._vs, self._vstatus, self._vmsg)

        for ev in self.bridge.drain_events():
            self._log_event(ev)
            if ev in ("APPROVE", "REJECT"):
                self._vs      = "idle"
                self._vstatus = "Approved" if ev == "APPROVE" else "Rejected"
                self._vmsg    = ""

        self.root.after(self.POLL_MS, self._poll)

    # ── actions ───────────────────────────────────────────────────────────────

    def _reconnect(self):
        self._vs      = "idle"
        self._vstatus = "Waiting..."
        self._vmsg    = ""

    def _test_notify(self):
        msg = "Task complete!"
        self.bridge.send(f"N:{msg}")
        self._vs    = "notifying"
        self._vmsg  = msg
        self.root.after(3100, self._auto_idle)

    def _test_ask(self):
        q = "Approve action?"
        self.bridge.send(f"A:{q}")
        self._vs   = "asking"
        self._vmsg = q

    def _test_status(self):
        text = (self._status_var.get().strip() or "Ready")[:20]
        self.bridge.send(f"S:{text}")
        self._vs      = "idle"
        self._vstatus = text

    def _auto_idle(self):
        if self._vs == "notifying":
            self._vs = "idle"

    # ── port listing ──────────────────────────────────────────────────────────

    def _refresh_ports(self):
        ports = _get_ports()
        if not ports:
            ports = ["COM3"] if sys.platform == "win32" else ["/dev/ttyUSB0"]
        self._port_cb["values"] = ports
        if ports:
            self._port_cb.current(0)

    # ── flashing ──────────────────────────────────────────────────────────────

    def _flash(self):
        port = self._port_var.get().strip()
        if not port:
            self._flash_append("No port selected.\n", err=True)
            return
        self._flash_btn.config(state="disabled")
        self._flash_clear()
        threading.Thread(target=self._do_flash, args=(port,), daemon=True).start()

    def _do_flash(self, port: str):
        BUILD_DIR.mkdir(parents=True, exist_ok=True)

        steps = [
            ("Compiling…", [
                "arduino-cli", "compile",
                "--fqbn", FQBN,
                "--output-dir", str(BUILD_DIR),
                str(FIRMWARE_DIR),
            ]),
            (f"Uploading to {port}…", [
                "arduino-cli", "upload",
                "--fqbn", FQBN,
                "--port", port,
                "--input-dir", str(BUILD_DIR),
                str(FIRMWARE_DIR),
            ]),
        ]

        for label, cmd in steps:
            self._flash_append(f"==> {label}\n")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    self._flash_append(line)
                proc.wait()
                if proc.returncode != 0:
                    self._flash_append(
                        f"[FAILED] exit code {proc.returncode}\n", err=True
                    )
                    self.root.after(0, lambda: self._flash_btn.config(state="normal"))
                    return
            except FileNotFoundError:
                self._flash_append(
                    "[ERROR] arduino-cli not found — install it and add it to PATH.\n"
                    "        See: https://arduino.github.io/arduino-cli/\n",
                    err=True,
                )
                self.root.after(0, lambda: self._flash_btn.config(state="normal"))
                return

        self._flash_append("==> Done!  Watch shows 'Advertising…'\n")
        self.root.after(0, lambda: self._flash_btn.config(state="normal"))

    # ── log helpers ───────────────────────────────────────────────────────────

    def _flash_append(self, text: str, err: bool = False):
        color = RED if err else "#8b949e"
        def _do():
            self._flash_out.config(state="normal")
            start = self._flash_out.index("end-1c")
            self._flash_out.insert("end", text)
            if err:
                self._flash_out.tag_add("err", start, "end-1c")
                self._flash_out.tag_config("err", foreground=RED)
            self._flash_out.see("end")
            self._flash_out.config(state="disabled")
        self.root.after(0, _do)

    def _flash_clear(self):
        self._flash_out.config(state="normal")
        self._flash_out.delete("1.0", "end")
        self._flash_out.config(state="disabled")

    def _log_event(self, event: str):
        ts  = datetime.now().strftime("%H:%M:%S")
        tag = (
            "approve" if event == "APPROVE" else
            "reject"  if event == "REJECT"  else
            "btn"     if event.startswith("BTN") else
            "gesture"
        )
        self._event_log.config(state="normal")
        self._event_log.insert("end", f"{ts}  ", "ts")
        self._event_log.insert("end", f"{event}\n", tag)
        self._event_log.see("end")
        self._event_log.config(state="disabled")


# ─── Entry point ─────────────────────────────────────────────────────────────

def run():
    root = tk.Tk()
    WatchManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
