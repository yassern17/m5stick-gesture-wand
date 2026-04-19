#!/usr/bin/env python3
"""
Claude Watch — one-shot installer.

    python3 install.py              # full setup
    python3 install.py --no-arduino # skip arduino-cli (already installed)

Sets up:
  1. Python venv + MCP server dependencies
  2. arduino-cli (downloads if not on PATH)
  3. M5Stack ESP32 core + M5StickCPlus library
  4. Claude Code MCP server registration
  5. GUI launcher script  (run_gui.sh / run_gui.bat)
  6. Desktop shortcut named "ClaudeWatch"
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

PROJECT   = Path(__file__).parent.resolve()
VENV      = PROJECT / ".venv"
TOOLS_DIR = PROJECT / "tools"
REQ_FILE  = PROJECT / "mcp_server" / "requirements.txt"
CLAUDE_JSON = Path.home() / ".claude.json"

# ── Arduino / M5Stack ─────────────────────────────────────────────────────────

M5_BOARD_URL = (
    "https://m5stack.oss-cn-shenzhen.aliyuncs.com"
    "/resource/arduino/package_m5stack_index.json"
)

_OS  = platform.system()
_CPU = platform.machine()

ARDUINO_CLI_URLS: dict[tuple[str, str], str] = {
    ("Linux",   "x86_64"):  "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_64bit.tar.gz",
    ("Linux",   "aarch64"): "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_ARM64.tar.gz",
    ("Windows", "AMD64"):   "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip",
    ("Darwin",  "x86_64"):  "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_macOS_64bit.tar.gz",
    ("Darwin",  "arm64"):   "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_macOS_ARM64.tar.gz",
}

# ── Terminal helpers ──────────────────────────────────────────────────────────

_PURPLE = "\033[1;35m"
_BLUE   = "\033[1;34m"
_CYAN   = "\033[1;36m"
_BOLD   = "\033[1m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_RESET  = "\033[0m"

# Disable colours on Windows cmd (but not Windows Terminal which supports ANSI)
if _OS == "Windows" and not os.environ.get("WT_SESSION"):
    _PURPLE = _BLUE = _CYAN = _BOLD = _GREEN = _YELLOW = _RED = _RESET = ""

def _step(msg: str)  : print(f"\n{_BLUE}==>{_RESET} {_BOLD}{msg}{_RESET}")
def _ok(msg: str)    : print(f"    {_GREEN}✓{_RESET}  {msg}")
def _warn(msg: str)  : print(f"    {_YELLOW}!{_RESET}  {msg}")
def _err(msg: str)   : print(f"    {_RED}✗{_RESET}  {msg}"); sys.exit(1)

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)

def _progress(count: int, block: int, total: int):
    pct = min(count * block * 100 // total, 100)
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    print(f"\r    [{bar}] {pct:3d}%", end="", flush=True)

# ── Step 1: Python venv ───────────────────────────────────────────────────────

def _venv_python() -> Path:
    if _OS == "Windows":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python3"

def setup_venv():
    _step("Python virtual environment")

    if sys.version_info < (3, 10):
        _err(f"Python 3.10+ required — found {sys.version.split()[0]}")

    _ok(f"Python {sys.version.split()[0]}")

    if not VENV.exists():
        _run([sys.executable, "-m", "venv", str(VENV)])
        _ok(f"Created venv at .venv/")
    else:
        _ok("Venv already exists — reusing")

    pip = str(_venv_python())
    _run([pip, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    _run([pip, "-m", "pip", "install", "--quiet", "-r", str(REQ_FILE)])
    _ok("Installed mcp, bleak, pyserial")

# ── Step 2: arduino-cli ───────────────────────────────────────────────────────

def _find_arduino_cli() -> str | None:
    found = shutil.which("arduino-cli")
    if found:
        return found
    exe = "arduino-cli.exe" if _OS == "Windows" else "arduino-cli"
    local = TOOLS_DIR / exe
    if local.exists():
        return str(local)
    return None

def install_arduino_cli() -> str | None:
    _step("arduino-cli")

    existing = _find_arduino_cli()
    if existing:
        _ok(f"Found at {existing}")
        return existing

    key = (_OS, _CPU)
    url = ARDUINO_CLI_URLS.get(key)
    if not url:
        _warn(f"No pre-built arduino-cli for {key}.")
        _warn("Install manually: https://arduino.github.io/arduino-cli/")
        return None

    TOOLS_DIR.mkdir(exist_ok=True)
    filename = url.split("/")[-1]
    archive  = TOOLS_DIR / filename

    print(f"    Downloading arduino-cli ({_OS} {_CPU})…")
    urllib.request.urlretrieve(url, archive, reporthook=_progress)
    print()

    exe_name = "arduino-cli.exe" if _OS == "Windows" else "arduino-cli"

    if filename.endswith(".tar.gz"):
        with tarfile.open(archive) as tf:
            member = next(m for m in tf.getmembers() if m.name.endswith("arduino-cli"))
            member.name = exe_name
            tf.extract(member, TOOLS_DIR, filter="data")
    else:
        with zipfile.ZipFile(archive) as zf:
            zf.extract(exe_name, TOOLS_DIR)

    archive.unlink()
    cli = TOOLS_DIR / exe_name
    cli.chmod(0o755)
    _ok(f"Installed to tools/arduino-cli")
    return str(cli)

def setup_m5stack(cli: str):
    _step("M5Stack board support")

    # Ensure the local tools/ dir is on PATH so arduino-cli can find itself
    env = os.environ.copy()
    env["PATH"] = str(TOOLS_DIR) + os.pathsep + env.get("PATH", "")
    kw = dict(env=env, capture_output=True)

    _run([cli, "config", "add", "board_manager.additional_urls", M5_BOARD_URL], **kw)
    _ok("Added M5Stack board URL")

    print("    Updating board index…")
    _run([cli, "core", "update-index"], **kw)

    installed = subprocess.run([cli, "core", "list"], capture_output=True, text=True).stdout
    if "m5stack:esp32" not in installed:
        print("    Installing m5stack:esp32 core (may take a few minutes)…")
        _run([cli, "core", "install", "m5stack:esp32"], **kw)
        _ok("Installed m5stack:esp32 core")
    else:
        _ok("m5stack:esp32 core already installed")

    libs = subprocess.run([cli, "lib", "list"], capture_output=True, text=True).stdout
    if "M5StickCPlus" not in libs:
        print("    Installing M5StickCPlus library…")
        _run([cli, "lib", "install", "M5StickCPlus"], **kw)
        _ok("Installed M5StickCPlus library")
    else:
        _ok("M5StickCPlus library already installed")

# ── Step 3: MCP server registration ──────────────────────────────────────────

def register_mcp():
    _step("Claude Code MCP server")

    python = str(_venv_python())

    if CLAUDE_JSON.exists():
        try:
            data = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}

    data.setdefault("mcpServers", {})["claude-watch"] = {
        "type":    "stdio",
        "command": python,
        "args":    ["-m", "mcp_server"],
        "env":     {"PYTHONPATH": str(PROJECT)},
    }

    CLAUDE_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _ok(f"Registered 'claude-watch' in {CLAUDE_JSON.name}")
    _ok(f"Python: {python}")

# ── Step 4: GUI launcher ──────────────────────────────────────────────────────

def create_launcher():
    _step("GUI launcher + daemon launcher")

    python = str(_venv_python())

    if _OS == "Windows":
        # GUI launcher
        gui = PROJECT / "run_gui.bat"
        gui.write_text(
            f'@echo off\n"{python}" -m mcp_server.gui\npause\n',
            encoding="utf-8",
        )
        _ok(f"Created {gui.name}")

        # Daemon launcher
        daemon = PROJECT / "run_daemon.bat"
        daemon.write_text(
            f'@echo off\necho Starting Claude Watch daemon...\n'
            f'"{python}" "{PROJECT / "watch_daemon.py"}"\n',
            encoding="utf-8",
        )
        _ok(f"Created {daemon.name}")

    else:
        # GUI launcher — use sg to activate the serial group immediately,
        # even if the user hasn't logged out since being added to it.
        gui = PROJECT / "run_gui.sh"
        gui.write_text(
            f'#!/usr/bin/env bash\n'
            f'SERIAL_GROUP=""\n'
            f'for g in uucp dialout; do\n'
            f'    getent group "$g" >/dev/null 2>&1 && SERIAL_GROUP="$g" && break\n'
            f'done\n'
            f'if [ -n "$SERIAL_GROUP" ]; then\n'
            f'    exec sg "$SERIAL_GROUP" -c "cd \'{PROJECT}\' && \'{python}\' -m mcp_server.gui"\n'
            f'else\n'
            f'    cd "{PROJECT}"\n'
            f'    exec "{python}" -m mcp_server.gui\n'
            f'fi\n',
            encoding="utf-8",
        )
        gui.chmod(0o755)
        _ok(f"Created {gui.name}")

        # Daemon launcher
        daemon = PROJECT / "run_daemon.sh"
        daemon.write_text(
            f'#!/usr/bin/env bash\n'
            f'echo "Starting Claude Watch daemon (Ctrl+C to stop)"\n'
            f'cd "{PROJECT}"\n'
            f'exec "{python}" "{PROJECT / "watch_daemon.py"}"\n',
            encoding="utf-8",
        )
        daemon.chmod(0o755)
        _ok(f"Created {daemon.name}")

# ── Step 5: Global CLAUDE.md ─────────────────────────────────────────────────

_CLAUDE_MD_BLOCK = """\
## Claude Watch (M5StickC Plus wrist companion)

A BLE watch is connected via the `claude-watch` MCP server. Use it proactively
every session without being asked. If `watch_connected()` returns False, skip
watch calls and continue normally.

### Always do this
- **`set_watch_status(status)`** — call at the start of every task and each major
  step. Up to 38 chars, word-wrapped over 2 lines on the watch.
- **`get_watch_events()`** — check at every major step for button/gesture input
  and react before continuing.

### Notifications & haptics
- **`notify_watch(message)`** — buzz + flash when a task finishes or needs attention
- **`buzz_watch(pattern)`** — haptic only: `"done"` (2 short), `"error"` (3 rapid),
  `"warn"` (1 long). Use instead of notify when display update isn't needed.
- **`ask_watch(question)`** — yes/no question on the watch (BTN_A=YES, BTN_B=NO);
  use before any destructive or hard-to-reverse action

### Progress indicator (3+ step tasks)
- **`set_watch_progress(step, total, label)`** — shows label + filled bar + "2/5"
  counter on the watch display. Call alongside `set_watch_status()` for sub-step
  text. Cleared automatically by `notify_watch` or `ask_watch`.

### Watch gesture meanings
| Event | Action |
|-------|--------|
| `BTN_A_LONG` | User wants a status summary — call `notify_watch` with ≤3 words |
| `SHAKE` / `INTERRUPT` | User wants to stop — pause and ask what to do next |
| `FLICK_FORWARD` | Proceed / approve / yes |
| `FLICK_BACK` | Cancel / undo / no |
| `BATTERY_LOW` | Battery < 15% — call `notify_watch("Battery low!")`, wrap up work |

### Bypass mode (required for watch approval flow)
Run Claude Code with `--dangerouslySkipPermissions` so it never halts at a
terminal permission prompt mid-task. Use `ask_watch()` for all approvals instead.

    claude --dangerouslySkipPermissions

### watch_ctl.py (sessions without MCP tools)
In this project directory:

    .venv/bin/python3 watch_ctl.py status "What I'm doing"
    .venv/bin/python3 watch_ctl.py notify "Task done"
    .venv/bin/python3 watch_ctl.py ask "Delete files?"     # → APPROVE/REJECT/timeout
    .venv/bin/python3 watch_ctl.py buzz done
    .venv/bin/python3 watch_ctl.py progress 2 5 "Running tests"
"""

_CLAUDE_MD_MARKER = "## Claude Watch"


def setup_claude_md():
    _step("Global CLAUDE.md")
    import re

    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(exist_ok=True)
    claude_md  = claude_dir / "CLAUDE.md"

    new_block = _CLAUDE_MD_BLOCK.strip()

    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if _CLAUDE_MD_MARKER in existing:
            # Replace existing section (from ## Claude Watch to next ## or EOF)
            updated = re.sub(
                r"## Claude Watch.*?(?=\n## |\Z)",
                new_block,
                existing,
                flags=re.DOTALL,
            ).rstrip() + "\n"
            claude_md.write_text(updated, encoding="utf-8")
            _ok(f"Updated Claude Watch section in {claude_md}")
        else:
            claude_md.write_text(
                existing.rstrip() + "\n\n" + new_block + "\n", encoding="utf-8")
            _ok(f"Appended Claude Watch section to {claude_md}")
    else:
        claude_md.write_text(new_block + "\n", encoding="utf-8")
        _ok(f"Created {claude_md}")


# ── Step 6: Desktop shortcut ─────────────────────────────────────────────────

# Simple watch-face SVG that matches the project colour scheme
_ICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
  <!-- watch strap top / bottom -->
  <rect x="14" y="2"  width="20" height="8"  rx="3" fill="#7c3aed"/>
  <rect x="14" y="38" width="20" height="8"  rx="3" fill="#7c3aed"/>
  <!-- watch body -->
  <circle cx="24" cy="24" r="18" fill="#1e1e2e" stroke="#7c3aed" stroke-width="2"/>
  <circle cx="24" cy="24" r="14" fill="#2a2a3e"/>
  <!-- screen text -->
  <text x="24" y="20" text-anchor="middle" fill="#22c55e"
        font-family="monospace" font-size="4.5" font-weight="bold">CLAUDE</text>
  <text x="24" y="27" text-anchor="middle" fill="#e2e8f0"
        font-family="monospace" font-size="4.5">WATCH</text>
  <!-- crown button -->
  <rect x="41" y="22" width="4" height="4" rx="1" fill="#7c3aed"/>
</svg>
"""

def create_shortcut():
    _step("Desktop shortcut")

    if _OS == "Linux":
        _shortcut_linux()
    elif _OS == "Windows":
        _shortcut_windows()
    else:
        _warn(f"Desktop shortcuts not implemented for {_OS} — skip")


def _shortcut_linux():
    launcher = PROJECT / "run_gui.sh"

    # Write SVG icon into the standard hicolor icon theme
    icon_dir = Path.home() / ".local/share/icons/hicolor/scalable/apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    icon_path = icon_dir / "claude-watch.svg"
    icon_path.write_text(_ICON_SVG, encoding="utf-8")

    desktop_entry = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Name=ClaudeWatch\n"
        "Comment=M5StickC Plus Claude companion\n"
        f"Exec={launcher}\n"
        "Icon=claude-watch\n"
        "Terminal=false\n"
        "Type=Application\n"
        "Categories=Utility;\n"
        "StartupNotify=true\n"
    )

    # Application menu entry
    apps_dir = Path.home() / ".local/share/applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    menu_file = apps_dir / "ClaudeWatch.desktop"
    menu_file.write_text(desktop_entry, encoding="utf-8")
    _ok(f"Added to application menu  ({menu_file})")

    # Desktop icon (if ~/Desktop exists)
    desktop_dir = Path.home() / "Desktop"
    if desktop_dir.exists():
        shortcut = desktop_dir / "ClaudeWatch.desktop"
        shortcut.write_text(desktop_entry, encoding="utf-8")
        shortcut.chmod(0o755)
        _ok(f"Added desktop icon  ({shortcut})")

    # Refresh icon cache (best-effort — silently ignore if tool absent)
    subprocess.run(
        ["gtk-update-icon-cache", "-f", "-t",
         str(Path.home() / ".local/share/icons/hicolor")],
        capture_output=True,
    )


def _shortcut_windows():
    launcher  = PROJECT / "run_gui.bat"
    icon_path = PROJECT / "tools" / "claude-watch.ico"
    desktop   = Path.home() / "Desktop"

    # Write the SVG as an ICO via PowerShell (SVG → PNG → ICO needs extra tools,
    # so we store the SVG and point the shortcut at the Python exe icon instead)
    icon_arg = ""
    py_exe = _venv_python()
    if py_exe.exists():
        icon_arg = f'$Shortcut.IconLocation = "{py_exe},0"'

    ps = (
        '$s = New-Object -ComObject WScript.Shell\n'
        f'$sc = $s.CreateShortcut("{desktop}\\\\ClaudeWatch.lnk")\n'
        f'$sc.TargetPath = "{launcher}"\n'
        f'$sc.Description = "M5StickC Plus Claude companion"\n'
        f'$sc.WorkingDirectory = "{PROJECT}"\n'
        f'{icon_arg}\n'
        '$sc.Save()\n'
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
    )
    if result.returncode == 0:
        _ok(f"Created Desktop shortcut  ({desktop}\\ClaudeWatch.lnk)")
    else:
        _warn("Could not create Windows shortcut via PowerShell")
        _warn(f"Create it manually: right-click {launcher} → Send to → Desktop")


# ── Step 6: Linux BLE permissions ────────────────────────────────────────────

def check_ble_permissions():
    if _OS != "Linux":
        return
    _step("Bluetooth / serial permissions")

    import grp
    user = os.environ.get("USER") or os.environ.get("LOGNAME", "")
    needs_relogin = False

    # BLE group
    try:
        members = grp.getgrnam("bluetooth").gr_mem
        if user in members:
            _ok(f"'{user}' is already in the bluetooth group")
        else:
            _warn(f"'{user}' is not in the bluetooth group — adding…")
            result = subprocess.run(
                ["sudo", "usermod", "-aG", "bluetooth", user],
                capture_output=True,
            )
            if result.returncode == 0:
                _ok("Added to bluetooth group")
                needs_relogin = True
            else:
                _warn("Could not add automatically. Run manually:")
                print(f"\n        sudo usermod -aG bluetooth {user}\n")
    except KeyError:
        _ok("No 'bluetooth' group (may use 'plugdev' — BLE should work)")

    # Serial port group — dialout (Debian/Ubuntu/Fedora) or uucp (Arch)
    serial_group = None
    for g in ("dialout", "uucp"):
        try:
            grp.getgrnam(g)
            serial_group = g
            break
        except KeyError:
            continue

    if serial_group is None:
        _ok("No dialout/uucp group found — serial access may work via udev rules")
    else:
        try:
            members = grp.getgrnam(serial_group).gr_mem
        except KeyError:
            members = []
        if user in members:
            _ok(f"'{user}' is already in the {serial_group} group")
        else:
            _warn(f"'{user}' is not in the {serial_group} group — adding…")
            result = subprocess.run(
                ["sudo", "usermod", "-aG", serial_group, user],
                capture_output=True,
            )
            if result.returncode == 0:
                _ok(f"Added to {serial_group} group")
                needs_relogin = True
            else:
                _warn("Could not add automatically. Run manually:")
                print(f"\n        sudo usermod -aG {serial_group} {user}\n")

    if needs_relogin:
        _warn("Group changes won't take effect until you start a new login session.")
        _warn("To apply them NOW without logging out, run:")
        print(f"\n        newgrp {serial_group or 'dialout'}\n")

# ── Step 7: Daemon autostart ─────────────────────────────────────────────────

def setup_autostart():
    _step("Daemon autostart")

    python = str(_venv_python())
    daemon = str(PROJECT / "watch_daemon.py")

    if _OS == "Linux":
        _autostart_linux(python, daemon)
    elif _OS == "Windows":
        _autostart_windows(python, daemon)
    else:
        _warn(f"Autostart not implemented for {_OS} — start daemon manually")


def _autostart_linux(python: str, daemon: str):
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service = systemd_dir / "claude-watch.service"

    service.write_text(
        "[Unit]\n"
        "Description=Claude Watch BLE daemon\n"
        "After=bluetooth.target\n\n"
        "[Service]\n"
        f"ExecStart={python} {daemon}\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
    )
    if result.returncode != 0:
        _warn("systemctl not available — daemon won't auto-start on login")
        _warn(f"Service file written to {service} for manual use")
        return

    subprocess.run(["systemctl", "--user", "enable", "claude-watch"],
                   capture_output=True)
    subprocess.run(["systemctl", "--user", "start",  "claude-watch"],
                   capture_output=True)
    _ok("Daemon enabled as systemd user service (claude-watch)")
    _ok("Starts automatically on login, restarts on failure")


def _autostart_windows(python: str, daemon: str):
    # Register in HKCU Run key so it starts on login
    ps = (
        '$path = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"\n'
        f'Set-ItemProperty -Path $path -Name "ClaudeWatchDaemon" '
        f'-Value \'"{python}" "{daemon}"\'\n'
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
    )
    if result.returncode == 0:
        _ok("Daemon registered in Windows startup (HKCU Run)")
    else:
        _warn("Could not register startup entry — start daemon manually from GUI")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Claude Watch installer")
    parser.add_argument("--no-arduino", action="store_true",
                        help="Skip arduino-cli setup")
    args = parser.parse_args()

    print(f"{_PURPLE}╔══════════════════════════════════╗{_RESET}")
    print(f"{_PURPLE}║    Claude Watch — Installer      ║{_RESET}")
    print(f"{_PURPLE}╚══════════════════════════════════╝{_RESET}")

    setup_venv()

    if args.no_arduino:
        _ok("Skipping arduino-cli setup  (--no-arduino)")
    else:
        cli = install_arduino_cli()
        if cli:
            setup_m5stack(cli)

    register_mcp()
    setup_claude_md()
    create_launcher()
    create_shortcut()
    check_ble_permissions()
    setup_autostart()

    print(f"\n{_GREEN}{_BOLD}✓ All done!{_RESET}\n")
    print("  Next steps:")
    print("  1. Open ClaudeWatch (GUI) to flash the firmware onto the watch")
    print("  2. The daemon starts automatically on login — or start it from the GUI")
    print(f"  3. Start Claude Code {_BOLD}with bypass mode{_RESET} so the watch handles approvals:")
    print(f"\n       {_CYAN}claude --dangerouslySkipPermissions{_RESET}\n")
    print("     Claude will use ask_watch() for any destructive actions instead of")
    print("     halting at a terminal permission prompt.")
    print()


if __name__ == "__main__":
    main()
