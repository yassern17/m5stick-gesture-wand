# M5StickC Plus Claude Wand

Wear the M5StickC Plus on your wrist while working with Claude Code. The watch shows what Claude is doing, buzzes when tasks finish, and lets you approve or reject actions with a button press — no keyboard required.

## What it does

- **Status display** — Claude pushes short status strings ("Reading files…", "Running tests…", "Done") to the watch display in real time
- **Task notifications** — when Claude finishes something it buzzes the watch and flashes the LED
- **Approvals** — Claude can ask a yes/no question on the watch before taking any irreversible action; you press BTN_A (yes) or BTN_B (no)
- **Watch-to-Claude events** — gestures and button presses are queued and readable by Claude via `get_watch_events`

## Hardware

[M5StickC Plus](https://shop.m5stack.com/products/m5stickc-plus-esp32-pico-mini-iot-development-kit) — ESP32-PICO-D4, MPU6886 IMU, BLE 4.2, built-in buzzer and LED.

## Quick start

Requires Python 3.10+. Works on Windows 10/11, Ubuntu 20.04+, Fedora 36+, Arch.

```bash
python3 install.py
```

That's it. The installer:
- Creates a Python venv and installs dependencies
- Downloads `arduino-cli` if not already on PATH
- Installs the M5Stack ESP32 core and M5StickCPlus library
- Registers the MCP server with Claude Code
- Creates `run_gui.sh` (or `run_gui.bat` on Windows)

Then open the GUI to flash the watch and verify the connection:

```bash
./run_gui.sh
```

Click **⚡ Flash Watch**, select your serial port, and wait for it to finish. The watch shows **"Advertising…"** when ready.

> **Note:** close the GUI before opening a Claude Code session — only one BLE client can connect at a time. Claude Code starts the MCP server automatically.

### Skip arduino-cli setup

If you already have `arduino-cli` on PATH:

```bash
python3 install.py --no-arduino
```

## MCP tools

Once connected, Claude has access to these tools:

| Tool | Description |
|---|---|
| `set_watch_status(status)` | Update the status line on the watch display |
| `notify_watch(message)` | Buzz + LED flash + show message for 3 s |
| `ask_watch(question, timeout_seconds)` | Block until BTN_A (yes) or BTN_B (no) |
| `get_watch_events()` | Read pending gestures / button presses |
| `watch_connected()` | Check if the watch is connected |

### Example Claude usage

You can tell Claude things like:

- *"Use the watch to notify me when the build finishes"*
- *"Ask me on the watch before running any destructive command"*
- *"Keep the watch updated with what you're doing"*

Claude will call `ask_watch` before actions like `rm -rf`, `git push --force`, or dropping database tables, and will call `notify_watch` when long tasks complete.

## Watch buttons & gestures

| Input | IDLE state | ASKING state |
|---|---|---|
| BTN_A (large) | Sends `BTN_A` event | Sends `APPROVE` → yes |
| BTN_B (small) | Sends `BTN_B` event | Sends `REJECT` → no |
| Shake | Sends `SHAKE` | — (ignored) |
| Tilt up/down/left/right | Sends `TILT_*` | — (ignored) |
| Flick / rotate | Sends `FLICK_*` / `ROTATE_*` | — (ignored) |

Gestures are accessible via `get_watch_events()`.

## BLE protocol

Two GATT characteristics on service `4fafc201-1fb5-459e-8fcc-c5c9c331914b`:

**Watch → Laptop** (notify, UUID `…26a8`): plain UTF-8 event strings (`APPROVE`, `REJECT`, `BTN_A`, `SHAKE`, `TILT_UP`, …)

**Laptop → Watch** (write, UUID `…26a9`): single-character commands:

| Command | Effect |
|---|---|
| `S:<text>` | Set status display |
| `N:<text>` | Notification (buzz + LED + 3 s display) |
| `A:<text>` | Ask yes/no (enters ASKING state) |
| `B:<ms>` | Buzz for N milliseconds |
| `C` | Clear / return to idle |

## Flashing the old gesture-wand firmware

The original gesture-to-keypress firmware is still available:

```bash
./flash.sh gesture
```

See `firmware/m5stick_gesture/` and `laptop/` for that setup.
