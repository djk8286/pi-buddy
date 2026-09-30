#!/usr/bin/env bash
# Starts Pi Buddy. Restarts it automatically if it crashes.
# All output (including face/display errors) also goes to data/run.log.
cd "$(dirname "$0")"
mkdir -p data
source .venv/bin/activate
# At boot, give the desktop a few seconds to finish loading before opening the face.
if [ "$(cut -d. -f1 /proc/uptime)" -lt 90 ]; then sleep 8; fi
while true; do
    echo "=== starting $(date) ===" >> data/run.log
    python -m buddy.main 2>&1 | tee -a data/run.log
    code=${PIPESTATUS[0]}
    [ "$code" -eq 0 ] && break   # clean exit (Esc / long press) — don't restart
    echo "Pi Buddy exited with $code, restarting in 3s..." | tee -a data/run.log
    sleep 3
done
