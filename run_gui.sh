#!/usr/bin/env bash
SERIAL_GROUP=""
for g in uucp dialout; do
    getent group "$g" >/dev/null 2>&1 && SERIAL_GROUP="$g" && break
done
if [ -n "$SERIAL_GROUP" ]; then
    exec sg "$SERIAL_GROUP" -c "cd '/home/aziz/Programs/m5stick-gesture-wand' && '/home/aziz/Programs/m5stick-gesture-wand/.venv/bin/python3' -m mcp_server.gui"
else
    cd "/home/aziz/Programs/m5stick-gesture-wand"
    exec "/home/aziz/Programs/m5stick-gesture-wand/.venv/bin/python3" -m mcp_server.gui
fi
