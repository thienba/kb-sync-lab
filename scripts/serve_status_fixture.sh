#!/usr/bin/env bash
# Serve the status page with a fixture's runs at http://127.0.0.1:8099/status/.
# Usage: scripts/serve_status_fixture.sh fresh|stale|empty|hostile|broken
# Open the page with the printed ?now= value so the fixture's clock is fixed.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
name="${1:-}"
fixture="$root/status/fixtures/$name"
if [[ -z "$name" || ! -d "$fixture/runs" ]]; then
	echo "usage: $0 <fixture>; one of: fresh stale empty hostile broken" >&2
	exit 2
fi

site="$(mktemp -d)"
trap 'rm -rf "$site"' EXIT
mkdir "$site/status"
ln -s "$root/status/index.html" "$site/status/index.html"
ln -s "$fixture/runs" "$site/runs"

echo "Open: http://127.0.0.1:8099/status/?now=$(cat "$fixture/now.txt")"
cd "$site"
python3 -m http.server 8099 --bind 127.0.0.1
