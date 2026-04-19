#!/usr/bin/env bash
echo "Starting Claude Watch daemon (Ctrl+C to stop)"
cd "/home/aziz/Programs/m5stick-gesture-wand"
exec "/home/aziz/Programs/m5stick-gesture-wand/.venv/bin/python3" "/home/aziz/Programs/m5stick-gesture-wand/watch_daemon.py"
