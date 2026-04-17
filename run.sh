#!/usr/bin/env bash
# Start the laptop BLE client
# Usage: ./run.sh              (auto-discover by name)
#        ./run.sh AA:BB:CC:..  (connect directly by MAC)
cd "$(dirname "$0")/laptop"
.venv/bin/python laptop_client.py "$@"
