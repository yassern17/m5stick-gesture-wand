"""
Claude Watch Manager GUI.

Everything is controllable from here — no CLI needed after install:
  - Flash firmware (auto-detects watch USB port)
  - Start / stop the persistent BLE daemon (for Claude sessions)
  - Monitor BLE connection and watch events
  - Send test notifications / approvals

When the daemon is running the GUI routes BLE commands through it.
When the daemon is stopped the GUI uses a direct BLE connection.
"""

import glob
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk

# ── BLEBridge import (direct mode) ───────────────────────────────────────────
from .ble_bridge import BLEBridge

try:
    import serial.tools.list_ports as _list_ports

    def _get_ports() -> list[str]:
        return [p.device for p in _list_ports.comports()]

    def _find_watch_port() -> str | None:
        candidates = list(_list_ports.comports())
        for p in candidates:
            if p.vid == 0x10C4 and p.pid == 0xEA60:
                return p.device
        for p in candidates:
            desc = (p.description or "").upper()
            if "CP210" in desc:
                return p.device
        for p in candidates:
            desc = (p.description or "").upper()
            if any(k in desc for k in ("M5", "ESP32", "SILICON", "USB SERIAL")):
                return p.device
        return None

except ImportError:
    def _get_ports() -> list[str]:
        if sys.platform == "win32":
            return [f"COM{i}" for i in range(1, 13)]
        return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))

    def _find_watch_port() -> str | None:
        return None


ROOT_DIR     = Path(__file__).parent.parent
FIRMWARE_DIR = ROOT_DIR / "firmware" / "m5stick_claude_wand"
FQBN         = "m5stack:esp32:m5stack_stickc_plus"
BUILD_DIR    = Path(sys.prefix) / "tmp" / "m5claude-build"
DAEMON_SCRIPT = ROOT_DIR / "watch_daemon.py"
VENV_PYTHON   = Path(sys.executable)


MAPPER_SCRIPT = ROOT_DIR / "gesture_mapper.py"
MAP_FILE      = ROOT_DIR / "gesture_map.json"

GESTURES = [
    "FLICK_FORWARD", "FLICK_BACK",
    "ROTATE_CW",     "ROTATE_CCW",
    "SHAKE",
    "TILT_UP",  "TILT_DOWN",
    "TILT_LEFT", "TILT_RIGHT",
    "BTN_A",    "BTN_A_LONG",
]

_PRESETS_LINUX = [
    ("— none —",           None),
    ("Next tab",           {"type": "key", "keys": "ctrl+Tab",              "label": "Next tab"}),
    ("Prev tab",           {"type": "key", "keys": "ctrl+shift+Tab",        "label": "Prev tab"}),
    ("Close tab",          {"type": "key", "keys": "ctrl+w",                "label": "Close tab"}),
    ("New tab",            {"type": "key", "keys": "ctrl+t",                "label": "New tab"}),
    ("Screenshot",         {"type": "key", "keys": "super+shift+s",         "label": "Screenshot"}),
    ("Volume up",          {"type": "key", "keys": "XF86AudioRaiseVolume",  "label": "Volume up"}),
    ("Volume down",        {"type": "key", "keys": "XF86AudioLowerVolume",  "label": "Volume down"}),
    ("Mute / unmute",      {"type": "key", "keys": "XF86AudioMute",         "label": "Mute"}),
    ("Play / pause",       {"type": "key", "keys": "XF86AudioPlay",         "label": "Play/pause"}),
    ("Next track",         {"type": "key", "keys": "XF86AudioNext",         "label": "Next track"}),
    ("Prev track",         {"type": "key", "keys": "XF86AudioPrev",         "label": "Prev track"}),
    ("Show desktop",       {"type": "key", "keys": "super+d",               "label": "Show desktop"}),
    ("Lock screen",        {"type": "key", "keys": "super+l",               "label": "Lock screen"}),
    ("Mic mute (PulseAudio)", {"type": "cmd",
                            "cmd":  "pactl set-source-mute @DEFAULT_SOURCE@ toggle",
                            "label": "Mic mute"}),
    ("Mic mute (PipeWire)",{"type": "cmd",
                            "cmd":  "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle",
                            "label": "Mic mute"}),
]

_PRESETS_WIN = [
    ("— none —",     None),
    ("Next tab",     {"type": "key", "keys": "ctrl+Tab",    "label": "Next tab"}),
    ("Prev tab",     {"type": "key", "keys": "ctrl+shift+Tab", "label": "Prev tab"}),
    ("Close tab",    {"type": "key", "keys": "ctrl+w",      "label": "Close tab"}),
    ("New tab",      {"type": "key", "keys": "ctrl+t",      "label": "New tab"}),
    ("Screenshot",   {"type": "key", "keys": "win+shift+s", "label": "Screenshot"}),
    ("Task view",    {"type": "key", "keys": "win+Tab",     "label": "Task view"}),
    ("Show desktop", {"type": "key", "keys": "win+d",       "label": "Show desktop"}),
    ("Lock screen",  {"type": "key", "keys": "win+l",       "label": "Lock screen"}),
    ("Volume up",    {"type": "cmd",
                      "cmd": "powershell -c \"$obj=New-Object -ComObject WMPlayer.OCX.7; $obj.settings.volume=[Math]::Min($obj.settings.volume+10,100)\"",
                      "label": "Volume up"}),
]

PRESETS = _PRESETS_WIN if sys.platform == "win32" else _PRESETS_LINUX


def _find_arduino_cli() -> str | None:
    found = shutil.which("arduino-cli")
    if found:
        return found
    exe   = "arduino-cli.exe" if sys.platform == "win32" else "arduino-cli"
    local = ROOT_DIR / "tools" / exe
    return str(local) if local.exists() else None

# Daemon IPC (must match watch_daemon.py)
if sys.platform == "win32":
    _SOCK_ADDR: tuple | str = ("127.0.0.1", 63185)
    _SOCK_FAMILY = socket.AF_INET
    PID_PATH = Path(tempfile.gettempdir()) / "claude-watch.pid"
else:
    _SOCK_ADDR = "/tmp/claude-watch.sock"
    _SOCK_FAMILY = socket.AF_UNIX
    PID_PATH = Path("/tmp/claude-watch.pid")

# ── Colours ───────────────────────────────────────────────────────────────────

BG       = "#1e1e2e"
PANEL    = "#2a2a3e"
ACCENT   = "#7c3aed"
GREEN    = "#22c55e"
YELLOW   = "#eab308"
CYAN     = "#06b6d4"
RED      = "#ef4444"
ORANGE   = "#f97316"
TEXT     = "#e2e8f0"
MUTED    = "#64748b"
WATCH_BG = "#000000"


# ── Daemon socket client ──────────────────────────────────────────────────────

class DaemonClient:
    """Thin wrapper that talks to the background daemon via its socket."""

    def _call(self, req: dict, timeout: float = 5.0) -> dict | None:
        try:
            s = socket.socket(_SOCK_FAMILY, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(_SOCK_ADDR)
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(256)
                if not chunk:
                    break
                buf += chunk
            s.close()
            return json.loads(buf.decode())
        except Exception:
            return None

    @property
    def connected(self) -> bool:
        r = self._call({"cmd": "connected"})
        return bool(r and r.get("result"))

    @property
    def reachable(self) -> bool:
        return self._call({"cmd": "connected"}) is not None

    def send(self, command: str) -> bool:
        if command.startswith("N:"):
            r = self._call({"cmd": "notify", "text": command[2:]}, timeout=6.0)
        elif command.startswith("S:"):
            r = self._call({"cmd": "status", "text": command[2:]})
        elif command.startswith("A:"):
            r = self._call({"cmd": "ask", "text": command[2:], "timeout": 30},
                           timeout=35.0)
        elif command.startswith("B:"):
            r = self._call({"cmd": "buzz", "pattern": command[2:]})
        elif command.startswith("P:"):
            rest = command[2:]
            try:
                slash = rest.index("/")
                colon = rest.index(":", slash + 1)
                r = self._call({
                    "cmd":   "progress",
                    "step":  int(rest[:slash]),
                    "total": int(rest[slash + 1:colon]),
                    "label": rest[colon + 1:],
                })
            except (ValueError, IndexError):
                return False
        else:
            return False
        return bool(r and r.get("ok"))

    def drain_events(self) -> list[str]:
        r = self._call({"cmd": "events"})
        return r.get("result", []) if r else []

    def wait_for_approval(self, timeout: float = 30.0) -> str | None:
        r = self._call({"cmd": "ask", "timeout": timeout}, timeout=timeout + 5)
        if r:
            return r.get("result")
        return None


# ── Watch canvas ──────────────────────────────────────────────────────────────

class WatchCanvas(tk.Canvas):
    S  = 0.65
    CW = int(240 * S)
    CH = int(135 * S)

    def __init__(self, parent, **kw):
        kw.setdefault("bg", WATCH_BG)
        super().__init__(parent, width=self.CW, height=self.CH,
                         highlightthickness=2, highlightbackground=MUTED, **kw)
        self._connected = False
        self._state     = "idle"
        self._status    = "Waiting..."
        self._msg       = ""
        self._redraw()

    def set(self, connected: bool, state: str, status: str, msg: str):
        changed = (self._connected != connected or self._state != state
                   or self._status != status or self._msg != msg)
        if changed:
            self._connected = connected
            self._state     = state
            self._status    = status
            self._msg       = msg
            self._redraw()

    def _redraw(self):
        self.delete("all")
        s = self.S
        if not self._connected:
            self._label("Advertising...", ORANGE, 5*s, 32*s, size=9)
            self._label("M5ClaudeWand",   MUTED,  5*s, 52*s, size=7)
            return
        {"idle": self._draw_idle, "notifying": self._draw_notify,
         "asking": self._draw_ask}.get(self._state, self._draw_idle)()

    def _draw_idle(self):
        s = self.S
        self._label("CLAUDE WAND", GREEN, 5*s, 4*s, size=7)
        self.create_line(0, int(14*s), self.CW, int(14*s), fill="#333333")
        self._wrapped(self._status, TEXT, 5*s, 22*s, size=10)

    def _draw_notify(self):
        s = self.S
        self._label("NOTIFICATION", YELLOW, 5*s, 4*s, size=7)
        self.create_line(0, int(14*s), self.CW, int(14*s), fill=YELLOW)
        self._wrapped(self._msg, TEXT, 5*s, 22*s, size=10)

    def _draw_ask(self):
        s = self.S
        self._label("APPROVE?", CYAN, 5*s, 4*s, size=7)
        self.create_line(0, int(14*s), self.CW, int(14*s), fill=CYAN)
        self._wrapped(self._msg, TEXT, 5*s, 22*s, size=10)
        self._label("[A] YES", GREEN, 5*s,   118*s, size=7)
        self._label("[B] NO",  RED,   170*s, 118*s, size=7)

    def _label(self, text, color, x, y, size=9):
        self.create_text(int(x), int(y), text=text, fill=color,
                         anchor="nw", font=("Courier", int(size)))

    def _wrapped(self, text: str, color, x, y, size=10, max_lines=2):
        """Draw text with 2-line word-wrap matching the firmware display."""
        chars = max(1, int((self.CW - x - 4) / (size * 0.65)))
        line_h = int(size * 1.7)
        pos = 0
        for line in range(max_lines):
            if pos >= len(text):
                break
            end = min(pos + chars, len(text))
            if end < len(text):
                brk = text.rfind(" ", pos, end)
                if brk > pos:
                    end = brk
            self._label(text[pos:end], color, x, y + line * line_h, size=size)
            pos = end
            while pos < len(text) and text[pos] == " ":
                pos += 1


# ── Main app ──────────────────────────────────────────────────────────────────

class WatchManagerApp:
    POLL_MS = 500

    def __init__(self, root: tk.Tk):
        self.root = root

        # Active client — either DaemonClient or BLEBridge
        self._daemon_client = DaemonClient()
        self._bridge: BLEBridge | None = None
        self._daemon_proc: subprocess.Popen | None = None

        self._vs           = "idle"
        self._vstatus      = "Waiting..."
        self._vmsg         = ""
        self._mapper_proc: subprocess.Popen | None = None
        self._gesture_map: dict = self._load_map()

        root.title("Claude Watch Manager")
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._style()
        self._build()

        # Start in whichever mode makes sense
        if self._daemon_running():
            self._log_event("DAEMON already running — connecting via socket")
        else:
            self._start_direct_ble()

        self._poll()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _daemon_running(self) -> bool:
        return self._daemon_client.reachable

    def _using_daemon(self) -> bool:
        return self._daemon_running()

    @property
    def _client_connected(self) -> bool:
        if self._using_daemon():
            return self._daemon_client.connected
        return bool(self._bridge and self._bridge.connected)

    def _send(self, cmd: str) -> bool:
        if self._using_daemon():
            return self._daemon_client.send(cmd)
        return bool(self._bridge and self._bridge.send(cmd))

    def _drain_events(self) -> list[str]:
        if self._using_daemon():
            return self._daemon_client.drain_events()
        return self._bridge.drain_events() if self._bridge else []

    # ── Direct BLE lifecycle ──────────────────────────────────────────────────

    def _start_direct_ble(self):
        if self._bridge:
            return
        self._bridge = BLEBridge()
        self._bridge.start()

    def _stop_direct_ble(self):
        if self._bridge:
            threading.Thread(target=self._bridge.stop, daemon=True).start()
            self._bridge = None

    # ── Daemon lifecycle ──────────────────────────────────────────────────────

    def _start_daemon(self):
        if self._daemon_running():
            self._log_event("Daemon already running")
            return
        self._stop_direct_ble()
        time.sleep(1.5)  # let BLE disconnect before daemon scans
        self._daemon_proc = subprocess.Popen(
            [str(VENV_PYTHON), str(DAEMON_SCRIPT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._log_event("Daemon started")
        self._update_daemon_btn()

    def _stop_daemon(self):
        # Try graceful quit via socket first
        try:
            self._daemon_client._call({"cmd": "quit"}, timeout=2.0)
        except Exception:
            pass

        # Kill tracked subprocess if we spawned it
        if self._daemon_proc:
            try:
                self._daemon_proc.terminate()
                self._daemon_proc.wait(timeout=3)
            except Exception:
                try:
                    self._daemon_proc.kill()
                except Exception:
                    pass
            self._daemon_proc = None

        # Kill by PID file if started externally
        if PID_PATH.exists():
            try:
                pid = int(PID_PATH.read_text().strip())
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                   capture_output=True)
                else:
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

        time.sleep(0.5)
        self._start_direct_ble()
        self._log_event("Daemon stopped")
        self._update_daemon_btn()

    def _disconnect_ble(self):
        """Drop active BLE connection (useful after flashing)."""
        if self._using_daemon():
            # Send quit to daemon — it will reconnect when restarted
            self._stop_daemon()
            self._log_event("Disconnected (daemon stopped)")
        else:
            threading.Thread(target=self._do_direct_disconnect,
                             daemon=True).start()

    def _do_direct_disconnect(self):
        import asyncio
        async def _d():
            if self._bridge and self._bridge._client:
                try:
                    await self._bridge._client.disconnect()
                except Exception:
                    pass
        try:
            asyncio.run(_d())
        except Exception:
            pass

    # ── ttk style ─────────────────────────────────────────────────────────────

    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        font = ("Segoe UI", 10) if sys.platform == "win32" else ("Inter", 10)
        s.configure(".",              background=BG,    foreground=TEXT, font=font)
        s.configure("TFrame",         background=BG)
        s.configure("TLabelframe",    background=BG,    foreground=MUTED,
                                      bordercolor=MUTED, relief="flat", padding=6)
        s.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                                         font=(None, 9))
        s.configure("TButton",        background=PANEL, foreground=TEXT,
                                      borderwidth=0, focuscolor=ACCENT,
                                      relief="flat", padding=(10, 5))
        s.map("TButton",
              background=[("active", ACCENT), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Flash.TButton",  background=ACCENT, foreground=TEXT,
                                      font=(None, 10, "bold"), padding=(12, 6))
        s.map("Flash.TButton",
              background=[("active", "#6d28d9"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Daemon.TButton", background=GREEN, foreground="#000",
                                      font=(None, 10, "bold"), padding=(10, 5))
        s.map("Daemon.TButton",
              background=[("active", "#16a34a"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Stop.TButton",   background=RED,   foreground=TEXT,
                                      font=(None, 10, "bold"), padding=(10, 5))
        s.map("Stop.TButton",
              background=[("active", "#b91c1c"), ("disabled", PANEL)])
        s.configure("TCombobox",      fieldbackground=PANEL, background=PANEL,
                                      foreground=TEXT, borderwidth=0,
                                      selectbackground=ACCENT)
        s.configure("TEntry",         fieldbackground=PANEL, foreground=TEXT,
                                      borderwidth=0, insertcolor=TEXT)
        s.configure("TLabel",         background=BG, foreground=TEXT)
        s.configure("Treeview",       background=PANEL, foreground=TEXT,
                                      fieldbackground=PANEL, rowheight=22,
                                      borderwidth=0)
        s.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                                        borderwidth=0, relief="flat")
        s.map("Treeview",
              background=[("selected", ACCENT)],
              foreground=[("selected", TEXT)])

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack()

        # ── Row 0: watch preview  +  connection & daemon ──────────────────────
        row0 = ttk.Frame(outer)
        row0.grid(row=0, column=0, sticky="nsew", pady=(0, 8))

        wf = ttk.LabelFrame(row0, text="Watch Preview")
        wf.pack(side="left", padx=(0, 10))
        self.watch = WatchCanvas(wf, bg=WATCH_BG)
        self.watch.pack(padx=8, pady=8)

        right = ttk.Frame(row0)
        right.pack(side="left", fill="both", expand=True)

        # Connection status
        cf = ttk.LabelFrame(right, text="BLE Connection")
        cf.pack(fill="x", pady=(0, 6))

        dot_row = ttk.Frame(cf)
        dot_row.pack(fill="x", pady=(2, 0))
        self._dot = tk.Canvas(dot_row, width=12, height=12,
                              bg=BG, highlightthickness=0)
        self._dot.pack(side="left", padx=(0, 6))
        self._dot.create_oval(1, 1, 11, 11, fill=MUTED, outline="", tags="dot")
        self._conn_label = tk.Label(dot_row, text="Scanning…",
                                    bg=BG, fg=TEXT, anchor="w")
        self._conn_label.pack(side="left", fill="x", expand=True)

        btn_row = ttk.Frame(cf)
        btn_row.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row, text="Reconnect",
                   command=self._reconnect).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row, text="Disconnect",
                   command=self._disconnect_ble).pack(side="left")

        # Daemon control
        df = ttk.LabelFrame(right, text="Claude Daemon")
        df.pack(fill="x")

        daemon_dot_row = ttk.Frame(df)
        daemon_dot_row.pack(fill="x", pady=(2, 4))
        self._daemon_dot = tk.Canvas(daemon_dot_row, width=12, height=12,
                                     bg=BG, highlightthickness=0)
        self._daemon_dot.pack(side="left", padx=(0, 6))
        self._daemon_dot.create_oval(1, 1, 11, 11, fill=MUTED, outline="",
                                     tags="dot")
        self._daemon_label = tk.Label(daemon_dot_row,
                                      text="Stopped — Claude can't use watch",
                                      bg=BG, fg=MUTED, anchor="w", font=(None, 9))
        self._daemon_label.pack(side="left", fill="x", expand=True)

        self._daemon_btn = ttk.Button(df, text="▶  Start Daemon",
                                      style="Daemon.TButton",
                                      command=self._toggle_daemon)
        self._daemon_btn.pack(fill="x")

        # ── Row 1: test panel ─────────────────────────────────────────────────
        tf = ttk.LabelFrame(outer, text="Test Controls")
        tf.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        btn_row2 = ttk.Frame(tf)
        btn_row2.pack(fill="x", pady=(0, 4))
        ttk.Button(btn_row2, text="Send Notify",
                   command=self._test_notify).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row2, text="Send Ask",
                   command=self._test_ask).pack(side="left")

        entry_row = ttk.Frame(tf)
        entry_row.pack(fill="x")
        self._status_var = tk.StringVar(value="Hello!")
        ttk.Entry(entry_row, textvariable=self._status_var,
                  width=16).pack(side="left", padx=(0, 4))
        ttk.Button(entry_row, text="Set Status",
                   command=self._test_status).pack(side="left")

        # ── Row 2: flash panel ────────────────────────────────────────────────
        ff = ttk.LabelFrame(outer, text="Flash Firmware")
        ff.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        port_row = ttk.Frame(ff)
        port_row.pack(fill="x", pady=(0, 2))
        tk.Label(port_row, text="Serial port:", bg=BG, fg=MUTED,
                 font=(None, 9)).pack(side="left", padx=(0, 6))
        self._port_var = tk.StringVar()
        self._port_cb  = ttk.Combobox(port_row, textvariable=self._port_var,
                                       width=22, state="readonly")
        self._port_cb.pack(side="left", padx=(0, 6))
        ttk.Button(port_row, text="Refresh",
                   command=self._refresh_ports).pack(side="left")
        self._port_hint = tk.Label(ff, text="", bg=BG, fg=GREEN, font=(None, 9))
        self._port_hint.pack(anchor="w", padx=2)

        self._flash_btn = ttk.Button(ff, text="⚡  Flash Watch",
                                      style="Flash.TButton",
                                      command=self._flash)
        self._flash_btn.pack(fill="x", pady=(4, 6))

        self._flash_out = tk.Text(ff, height=6, state="disabled",
                                   font=("Courier", 9),
                                   bg="#0d1117", fg="#8b949e",
                                   selectbackground=ACCENT,
                                   relief="flat", padx=6, pady=4)
        self._flash_out.pack(fill="x")

        # ── Row 3: event log ──────────────────────────────────────────────────
        lf = ttk.LabelFrame(outer, text="Watch Events")
        lf.grid(row=3, column=0, sticky="ew")

        self._event_log = tk.Text(lf, height=6, state="disabled",
                                   font=("Courier", 9),
                                   bg="#0d1117", fg="#7ee787",
                                   selectbackground=ACCENT,
                                   relief="flat", padx=6, pady=4)
        self._event_log.pack(fill="both", expand=True)
        self._event_log.tag_config("approve", foreground=GREEN)
        self._event_log.tag_config("reject",  foreground=RED)
        self._event_log.tag_config("btn",     foreground=CYAN)
        self._event_log.tag_config("gesture", foreground=YELLOW)
        self._event_log.tag_config("info",    foreground=MUTED)
        self._event_log.tag_config("ts",      foreground=MUTED)

        self._refresh_ports()
        self.root.after(2000, self._auto_refresh_ports)

        # ── Row 4: gesture mapper ─────────────────────────────────────────────
        self._build_mapper_panel(outer)

    # ── Periodic poll ─────────────────────────────────────────────────────────

    def _poll(self):
        connected    = self._client_connected
        daemon_alive = self._daemon_running()

        # BLE dot
        self._dot.itemconfig("dot", fill=GREEN if connected else MUTED)
        if connected:
            mode = "via daemon" if daemon_alive else "direct"
            self._conn_label.config(text=f"Connected — M5ClaudeWand ({mode})",
                                    fg=TEXT)
        else:
            self._conn_label.config(
                text="Scanning for M5ClaudeWand…" if not daemon_alive
                     else "Daemon running — watch not found yet",
                fg=MUTED)

        # Daemon dot
        self._daemon_dot.itemconfig("dot", fill=GREEN if daemon_alive else MUTED)
        if daemon_alive:
            self._daemon_label.config(
                text="Running — Claude can use the watch", fg=GREEN)
        else:
            self._daemon_label.config(
                text="Stopped — Claude can't use the watch", fg=MUTED)

        # Mapper dot
        mapper_alive = self._mapper_running()
        self._mapper_dot.itemconfig("dot", fill=GREEN if mapper_alive else MUTED)
        if mapper_alive:
            self._mapper_label.config(text="Running — gestures active", fg=GREEN)
            self._mapper_btn.config(text="■  Stop Mapper", style="Stop.TButton")
        else:
            self._mapper_label.config(text="Stopped", fg=MUTED)
            self._mapper_btn.config(text="▶  Start Mapper", style="Daemon.TButton")

        # Watch preview
        self.watch.set(connected, self._vs, self._vstatus, self._vmsg)

        # Drain events
        for ev in self._drain_events():
            self._log_event(ev)
            if ev in ("APPROVE", "REJECT"):
                self._vs      = "idle"
                self._vstatus = "Approved" if ev == "APPROVE" else "Rejected"
                self._vmsg    = ""

        self.root.after(self.POLL_MS, self._poll)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _toggle_daemon(self):
        if self._daemon_running():
            threading.Thread(target=self._stop_daemon, daemon=True).start()
        else:
            threading.Thread(target=self._start_daemon, daemon=True).start()

    def _update_daemon_btn(self):
        def _do():
            if self._daemon_running():
                self._daemon_btn.config(text="■  Stop Daemon",
                                        style="Stop.TButton")
            else:
                self._daemon_btn.config(text="▶  Start Daemon",
                                        style="Daemon.TButton")
        self.root.after(0, _do)

    def _reconnect(self):
        if not self._using_daemon():
            self._stop_direct_ble()
            self._start_direct_ble()
        self._vs      = "idle"
        self._vstatus = "Waiting..."
        self._vmsg    = ""

    def _test_notify(self):
        msg = "Task complete!"
        self._send(f"N:{msg}")
        self._vs   = "notifying"
        self._vmsg = msg
        self.root.after(3100, self._auto_idle)

    def _test_ask(self):
        q = "Approve action?"
        self._send(f"A:{q}")
        self._vs   = "asking"
        self._vmsg = q

    def _test_status(self):
        text = (self._status_var.get().strip() or "Ready")[:38]
        self._send(f"S:{text}")
        self._vs      = "idle"
        self._vstatus = text

    def _auto_idle(self):
        if self._vs == "notifying":
            self._vs = "idle"

    # ── Port listing ──────────────────────────────────────────────────────────

    def _refresh_ports(self):
        ports = _get_ports()
        if not ports:
            ports = ["COM3"] if sys.platform == "win32" else ["/dev/ttyUSB0"]
        self._port_cb["values"] = ports

        watch_port = _find_watch_port()
        if watch_port and watch_port in ports:
            self._port_var.set(watch_port)
            self._port_hint.config(
                text=f"✓ Auto-selected {watch_port}  (M5StickC Plus)", fg=GREEN)
        elif ports:
            if not self._port_var.get() or self._port_var.get() not in ports:
                self._port_cb.current(0)
            self._port_hint.config(
                text="Watch not detected — select port manually", fg=YELLOW)

    def _auto_refresh_ports(self):
        self._refresh_ports()
        self.root.after(2000, self._auto_refresh_ports)

    # ── Flashing ──────────────────────────────────────────────────────────────

    def _flash(self):
        port = self._port_var.get().strip()
        if not port:
            self._flash_append("No port selected.\n", err=True)
            return
        self._flash_btn.config(state="disabled")
        self._flash_clear()
        threading.Thread(target=self._do_flash, args=(port,), daemon=True).start()

    def _do_flash(self, port: str):
        cli = _find_arduino_cli()
        if not cli:
            self._flash_append(
                "[ERROR] arduino-cli not found.\n"
                "        Run install.py first, or install arduino-cli manually.\n"
                "        See: https://arduino.github.io/arduino-cli/\n",
                err=True)
            self.root.after(0, lambda: self._flash_btn.config(state="normal"))
            return

        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        steps = [
            ("Compiling…", [
                cli, "compile", "--fqbn", FQBN,
                "--output-dir", str(BUILD_DIR), str(FIRMWARE_DIR),
            ]),
            (f"Uploading to {port}…", [
                cli, "upload", "--fqbn", FQBN,
                "--port", port, "--input-dir", str(BUILD_DIR), str(FIRMWARE_DIR),
            ]),
        ]

        for label, cmd in steps:
            self._flash_append(f"==> {label}\n")
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    self._flash_append(line)
                proc.wait()
                if proc.returncode != 0:
                    self._flash_append(
                        f"[FAILED] exit code {proc.returncode}\n", err=True)
                    self.root.after(0, lambda: self._flash_btn.config(state="normal"))
                    return
            except FileNotFoundError:
                self._flash_append(f"[ERROR] could not run: {cmd[0]}\n", err=True)
                self.root.after(0, lambda: self._flash_btn.config(state="normal"))
                return

        self._flash_append("==> Done! Disconnecting BLE so watch can re-advertise…\n")
        self.root.after(0, self._disconnect_ble)
        self.root.after(0, lambda: self._flash_btn.config(state="normal"))

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _flash_append(self, text: str, err: bool = False):
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
        tag = ("approve" if event == "APPROVE" else
               "reject"  if event == "REJECT"  else
               "btn"     if event.startswith("BTN") else
               "info"    if event.startswith("Daemon") or event.startswith("Disc") else
               "gesture")
        self._event_log.config(state="normal")
        self._event_log.insert("end", f"{ts}  ", "ts")
        self._event_log.insert("end", f"{event}\n", tag)
        self._event_log.see("end")
        self._event_log.config(state="disabled")

    # ── Gesture mapper panel ──────────────────────────────────────────────────

    def _build_mapper_panel(self, parent):
        mf = ttk.LabelFrame(parent, text="Gesture Mapper")
        mf.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        # Status row
        sr = ttk.Frame(mf)
        sr.pack(fill="x", pady=(0, 4))
        self._mapper_dot = tk.Canvas(sr, width=12, height=12,
                                     bg=BG, highlightthickness=0)
        self._mapper_dot.pack(side="left", padx=(0, 6))
        self._mapper_dot.create_oval(1, 1, 11, 11, fill=MUTED, outline="", tags="dot")
        self._mapper_label = tk.Label(sr, text="Stopped",
                                      bg=BG, fg=MUTED, anchor="w", font=(None, 9))
        self._mapper_label.pack(side="left", fill="x", expand=True)
        self._mapper_btn = ttk.Button(sr, text="▶  Start Mapper",
                                      style="Daemon.TButton",
                                      command=self._toggle_mapper)
        self._mapper_btn.pack(side="right")

        # Treeview
        tree_row = ttk.Frame(mf)
        tree_row.pack(fill="x")
        cols = ("gesture", "label", "action")
        self._map_tree = ttk.Treeview(tree_row, columns=cols,
                                       show="headings", height=5)
        self._map_tree.heading("gesture", text="Gesture")
        self._map_tree.heading("label",   text="Name")
        self._map_tree.heading("action",  text="Action")
        self._map_tree.column("gesture", width=148, stretch=False)
        self._map_tree.column("label",   width=120, stretch=False)
        self._map_tree.column("action",  width=250, stretch=True)
        sb = ttk.Scrollbar(tree_row, orient="vertical",
                           command=self._map_tree.yview)
        self._map_tree.configure(yscrollcommand=sb.set)
        self._map_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._map_tree.bind("<Double-1>", lambda _: self._edit_mapping())

        # Button row
        br = ttk.Frame(mf)
        br.pack(fill="x", pady=(4, 0))
        ttk.Button(br, text="Add",    command=self._add_mapping).pack(side="left", padx=(0, 4))
        ttk.Button(br, text="Edit",   command=self._edit_mapping).pack(side="left", padx=(0, 4))
        ttk.Button(br, text="Delete", command=self._delete_mapping).pack(side="left")

        self._refresh_map_tree()

    # ── Mapper lifecycle ──────────────────────────────────────────────────────

    def _load_map(self) -> dict:
        try:
            return json.loads(MAP_FILE.read_text()) if MAP_FILE.exists() else {}
        except Exception:
            return {}

    def _save_map(self) -> None:
        MAP_FILE.write_text(json.dumps(self._gesture_map, indent=2))

    def _mapper_running(self) -> bool:
        return bool(self._mapper_proc and self._mapper_proc.poll() is None)

    def _toggle_mapper(self):
        if self._mapper_running():
            self._stop_mapper()
        else:
            threading.Thread(target=self._start_mapper, daemon=True).start()

    def _start_mapper(self):
        if self._mapper_running():
            return
        self._mapper_proc = subprocess.Popen(
            [str(VENV_PYTHON), str(MAPPER_SCRIPT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._log_event("Mapper started")

    def _stop_mapper(self):
        if self._mapper_proc:
            try:
                self._mapper_proc.terminate()
                self._mapper_proc.wait(timeout=3)
            except Exception:
                try:
                    self._mapper_proc.kill()
                except Exception:
                    pass
            self._mapper_proc = None
        self._log_event("Mapper stopped")

    def _refresh_map_tree(self):
        self._map_tree.delete(*self._map_tree.get_children())
        for gesture, action in sorted(self._gesture_map.items()):
            label  = action.get("label", "")
            kind   = action.get("type", "cmd")
            detail = action.get("keys", "") if kind == "key" else action.get("cmd", "")
            self._map_tree.insert("", "end", iid=gesture,
                                  values=(gesture, label, f"{kind}: {detail}"))

    # ── Mapping editor ────────────────────────────────────────────────────────

    def _add_mapping(self):
        result = self._map_dialog()
        if result:
            gesture, action = result
            self._gesture_map[gesture] = action
            self._save_map()
            self._refresh_map_tree()

    def _edit_mapping(self):
        sel = self._map_tree.selection()
        if not sel:
            return
        gesture = sel[0]
        action  = self._gesture_map.get(gesture, {})
        result  = self._map_dialog(gesture=gesture, action=action)
        if result:
            new_gesture, new_action = result
            if new_gesture != gesture:
                del self._gesture_map[gesture]
            self._gesture_map[new_gesture] = new_action
            self._save_map()
            self._refresh_map_tree()

    def _delete_mapping(self):
        sel = self._map_tree.selection()
        if not sel:
            return
        gesture = sel[0]
        del self._gesture_map[gesture]
        self._save_map()
        self._refresh_map_tree()

    def _map_dialog(self, gesture: str | None = None,
                    action: dict | None = None) -> tuple | None:
        """Open an add/edit dialog. Returns (gesture, action) or None if cancelled."""
        action = action or {}
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Mapping" if gesture else "Add Mapping")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        pad = {"padx": 8, "pady": 4}

        def row(label_text, widget_fn, r):
            tk.Label(dlg, text=label_text, bg=BG, fg=MUTED,
                     font=(None, 9), anchor="e", width=10).grid(
                row=r, column=0, sticky="e", **pad)
            w = widget_fn(dlg)
            w.grid(row=r, column=1, sticky="ew", **pad)
            return w

        # Gesture picker
        g_var = tk.StringVar(value=gesture or GESTURES[0])
        g_cb  = ttk.Combobox(dlg, textvariable=g_var, values=GESTURES,
                              state="readonly", width=22)
        tk.Label(dlg, text="Gesture", bg=BG, fg=MUTED,
                 font=(None, 9), anchor="e", width=10).grid(
            row=0, column=0, sticky="e", **pad)
        g_cb.grid(row=0, column=1, sticky="ew", **pad)

        # Preset picker
        preset_names = [p[0] for p in PRESETS]
        p_var = tk.StringVar(value=preset_names[0])
        p_cb  = ttk.Combobox(dlg, textvariable=p_var, values=preset_names,
                              state="readonly", width=22)
        tk.Label(dlg, text="Preset", bg=BG, fg=MUTED,
                 font=(None, 9), anchor="e", width=10).grid(
            row=1, column=0, sticky="e", **pad)
        p_cb.grid(row=1, column=1, sticky="ew", **pad)

        ttk.Separator(dlg, orient="horizontal").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=4)

        # Label
        lbl_var = tk.StringVar(value=action.get("label", ""))
        tk.Label(dlg, text="Name", bg=BG, fg=MUTED,
                 font=(None, 9), anchor="e", width=10).grid(
            row=3, column=0, sticky="e", **pad)
        ttk.Entry(dlg, textvariable=lbl_var, width=24).grid(
            row=3, column=1, sticky="ew", **pad)

        # Type radio
        type_var = tk.StringVar(value=action.get("type", "key"))
        type_frame = ttk.Frame(dlg)
        type_frame.grid(row=4, column=1, sticky="w", padx=8)
        tk.Label(dlg, text="Type", bg=BG, fg=MUTED,
                 font=(None, 9), anchor="e", width=10).grid(
            row=4, column=0, sticky="e", **pad)
        for val, txt in (("key", "Key shortcut"), ("cmd", "Shell command")):
            tk.Radiobutton(type_frame, text=txt, variable=type_var, value=val,
                           bg=BG, fg=TEXT, selectcolor=PANEL,
                           activebackground=BG, activeforeground=TEXT).pack(
                side="left", padx=(0, 8))

        # Action field (label changes with type)
        action_lbl = tk.Label(dlg, text="Keys", bg=BG, fg=MUTED,
                               font=(None, 9), anchor="e", width=10)
        action_lbl.grid(row=5, column=0, sticky="e", **pad)
        init_val = (action.get("keys", "") if action.get("type", "key") == "key"
                    else action.get("cmd", ""))
        action_var = tk.StringVar(value=init_val)
        action_entry = ttk.Entry(dlg, textvariable=action_var, width=24)
        action_entry.grid(row=5, column=1, sticky="ew", **pad)

        def on_type_change(*_):
            action_lbl.config(text="Keys" if type_var.get() == "key" else "Command")
        type_var.trace_add("write", on_type_change)

        def on_preset(*_):
            name = p_var.get()
            for pname, pval in PRESETS:
                if pname == name and pval:
                    lbl_var.set(pval.get("label", ""))
                    type_var.set(pval.get("type", "key"))
                    action_var.set(
                        pval.get("keys", "") if pval.get("type") == "key"
                        else pval.get("cmd", ""))
                    break
        p_cb.bind("<<ComboboxSelected>>", on_preset)

        # Buttons
        result_holder = [None]

        def on_save():
            g = g_var.get()
            t = type_var.get()
            a = action_var.get().strip()
            l = lbl_var.get().strip() or (a[:30] if a else g)
            if not g or not a:
                return
            entry = {"type": t, "label": l}
            if t == "key":
                entry["keys"] = a
            else:
                entry["cmd"] = a
            result_holder[0] = (g, entry)
            dlg.destroy()

        btn_row = ttk.Frame(dlg)
        btn_row.grid(row=6, column=0, columnspan=2, pady=(4, 8))
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Save",   command=on_save,
                   style="Flash.TButton").pack(side="left")

        dlg.columnconfigure(1, weight=1)
        dlg.wait_window()
        return result_holder[0]

    # ── Window close ─────────────────────────────────────────────────────────

    def _on_close(self):
        # Stop direct BLE thread (daemon + mapper keep running)
        self._bridge = None
        self.root.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    root = tk.Tk()
    WatchManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
