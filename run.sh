#!/usr/bin/env bash
# Starts Pi Buddy. Restarts it automatically if it crashes.
cd "$(dirname "$0")"
source .venv/bin/activate
while true; do
    python -m buddy.main
    code=$?
    [ "$code" -eq 0 ] && break   # clean exit (Esc / long press) — don't restart
    echo "Pi Buddy exited with $code, restarting in 3s..." >&2
    sleep 3
done
