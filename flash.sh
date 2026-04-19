#!/usr/bin/env bash
# Flash firmware to the M5StickC Plus or an ESP32 anchor node.
#
# Usage:
#   ./flash.sh                         claude-wand firmware on /dev/ttyUSB0
#   ./flash.sh claude                  same as above
#   ./flash.sh gesture                 old gesture-wand firmware
#   ./flash.sh scanner                 ESP32 anchor on /dev/ttyUSB0
#   ./flash.sh claude   /dev/ttyACM0   specify port
#   ./flash.sh /dev/ttyUSB1            claude-wand on that port (short form)
set -e

TARGET="claude"
PORT="/dev/ttyUSB0"

for arg in "$@"; do
  case "$arg" in
    claude|gesture|scanner)  TARGET="$arg" ;;
    /dev/*)                  PORT="$arg" ;;
    *)                       echo "Unknown argument: $arg"; exit 2 ;;
  esac
done

case "$TARGET" in
  claude)
    SKETCH="$(dirname "$0")/firmware/m5stick_claude_wand"
    FQBN="m5stack:esp32:m5stack_stickc_plus"
    BUILD_DIR="/tmp/gesture-build-claude"
    DONE_MSG="Watch shows 'Advertising...' — start the MCP server and connect Claude Code."
    ;;
  gesture)
    SKETCH="$(dirname "$0")/firmware/m5stick_gesture"
    FQBN="m5stack:esp32:m5stack_stickc_plus"
    BUILD_DIR="/tmp/gesture-build-watch"
    DONE_MSG="The M5Stick should now show 'Advertising...'"
    ;;
  scanner)
    SKETCH="$(dirname "$0")/firmware/esp32_scanner"
    FQBN="${ESP32_FQBN:-esp32:esp32:esp32}"
    BUILD_DIR="/tmp/gesture-build-scanner"
    DONE_MSG="The anchor will log to serial and broadcast RSSI on UDP :42042."
    ;;
esac

export PATH="$HOME/.local/bin:$PATH"

echo "==> Target:   $TARGET"
echo "==> Sketch:   $SKETCH"
echo "==> FQBN:     $FQBN"
echo "==> Port:     $PORT"

echo "==> Compiling..."
arduino-cli compile --fqbn "$FQBN" --output-dir "$BUILD_DIR" "$SKETCH"

echo "==> Uploading to $PORT..."
arduino-cli upload --fqbn "$FQBN" --port "$PORT" --input-dir "$BUILD_DIR" "$SKETCH"

echo "==> Done! $DONE_MSG"
