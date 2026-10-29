#!/usr/bin/env bash
# Run on the server: update the clone in /opt/kbsync/src, build the job image
# locally (no registry) and refresh the stack files (installed root-owned).
# Everything is inside main() and the script exits before reading further, so the
# `git pull` cannot rewrite the file bash is still reading.
set -euo pipefail

main() {
	local dir="${KBSYNC_DIR:-/opt/kbsync}"
	cd "$dir/src"
	git pull --ff-only
	docker build -t kbsync:local --build-arg GIT_SHA="$(git rev-parse --short HEAD)" .
	sudo install -m 644 deploy/compose.yaml deploy/Caddyfile "$dir/"
	sudo install -m 755 deploy/run-job.sh "$dir/"
}

main "$@"
exit
