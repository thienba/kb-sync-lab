#!/usr/bin/env bash
# Run the kbsync job once, unless a previous run still holds the lock.
# Called by cron (see crontab.example). KBSYNC_DIR is overridable for tests only.
set -euo pipefail

dir="${KBSYNC_DIR:-/opt/kbsync}"
lock="$dir/data/kbsync.lock"
log="$dir/data/cron.log"

exec 9>"$lock"
if ! flock -n 9; then
	echo "kbsync: previous run still in progress, skipping." | tee -a "$log"
	exit 0
fi

echo "kbsync: run started $(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$log"
docker compose -f "$dir/compose.yaml" run --rm -T job >>"$log" 2>&1
