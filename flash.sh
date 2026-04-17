#!/usr/bin/env bash
# Flash the GestureWand firmware to the M5StickC Plus on /dev/ttyUSB0
set -e

SKETCH="$(dirname "$0")/firmware/m5stick_gesture"
PORT="${1:-/dev/ttyUSB0}"
FQBN="m5stack:esp32:m5stack_stickc_plus"

export PATH="$HOME/.local/bin:$PATH"

echo "==> Compiling..."
arduino-cli compile --fqbn "$FQBN" --output-dir /tmp/gesture-build "$SKETCH"

echo "==> Uploading to $PORT..."
arduino-cli upload --fqbn "$FQBN" --port "$PORT" --input-dir /tmp/gesture-build "$SKETCH"

echo "==> Done! The M5Stick should now show 'Advertising...'"
