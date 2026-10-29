#!/usr/bin/env bash
# Test deploy/run-job.sh lock behaviour in an ubuntu:24.04 container with a stub `docker`.
# Usage: scripts/test_run_job.sh   (needs a local Docker daemon)
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"

docker run --rm -v "$root/deploy/run-job.sh:/run-job.sh:ro" ubuntu:24.04 bash -euc '
export KBSYNC_DIR=/work
mkdir -p "$KBSYNC_DIR/data" /stub
printf "#!/bin/sh\necho \"stub docker \$*\"\n" >/stub/docker
chmod +x /stub/docker
export PATH="/stub:$PATH"
log="$KBSYNC_DIR/data/cron.log"

echo "== case 1: lock held -> skip, exit 0, job not run"
flock "$KBSYNC_DIR/data/kbsync.lock" sleep 3 &
sleep 1
out="$(bash /run-job.sh)"; code=$?
echo "$out"; echo "exit=$code"
[ "$code" -eq 0 ]
[ "$out" = "kbsync: previous run still in progress, skipping." ]
! grep -q "stub docker" "$log"
wait

echo "== case 2: lock free -> job runs, output appended to cron.log"
bash /run-job.sh; echo "exit=$?"
grep -q "stub docker compose -f /work/compose.yaml run --rm -T job" "$log"
cat "$log"
echo "PASS"
'
