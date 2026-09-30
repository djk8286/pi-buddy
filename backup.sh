#!/usr/bin/env bash
# Backs up the buddy's memory and conversation history to a dated tar file.
# Usage: bash backup.sh [destination-folder]   (default: ~/pi-buddy-backups)
cd "$(dirname "$0")"
DEST="${1:-$HOME/pi-buddy-backups}"
mkdir -p "$DEST"
sqlite3 data/history.db ".backup data/history-backup.db" 2>/dev/null || cp data/history.db data/history-backup.db
tar czf "$DEST/pi-buddy-$(date +%F).tgz" data/memories data/history-backup.db config.toml personality.md
rm -f data/history-backup.db
ls -1t "$DEST"/pi-buddy-*.tgz | tail -n +31 | xargs -r rm   # keep the last 30
echo "Backed up to $DEST"
