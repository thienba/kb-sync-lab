import http.client
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from kbsync.logs import redact
from kbsync.sync import SyncResult

log = logging.getLogger("kbsync")

PING_TIMEOUT = 10
PING_RETRIES = 2
INDEX_KEYS = ("status", "started_at", "added", "updated", "skipped", "deleted", "failed", "files")

# Status code of one GET; a network failure or timeout raises OSError.
PingGet = Callable[[str, float], int]


def git_sha() -> str:
    sha = os.environ.get("GIT_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def build_report(
    result: SyncResult,
    started_at: datetime,
    duration_s: float,
    files: int,
    chunks_estimate: int,
    converter_version: str,
    error: str | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {
        "status": "failed" if error else "ok",
        "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": round(duration_s),
        "added": result.added,
        "updated": result.updated,
        "skipped": result.skipped,
        "deleted": result.deleted,
        "failed": result.failed,
        "files": files,
        "chunks_estimate": chunks_estimate,
        "converter_version": converter_version,
        "git_sha": git_sha(),
        "changes": result.changes,
    }
    if error:
        report["error"] = redact(error.splitlines()[0] if error.strip() else error)
    return report


def _atomic_write(path: Path, data: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_report(public_dir: Path, report: dict[str, object]) -> None:
    runs = Path(public_dir) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    date = str(report["started_at"])[:10]
    _atomic_write(runs / f"{date}.json", report)
    _atomic_write(runs / "latest.json", report)
    index_path = runs / "index.json"
    entry = {"date": date, **{key: report[key] for key in INDEX_KEYS}}
    index: list[dict[str, object]] = []
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            log.warning("index.json is corrupt, rebuilding it: %s", type(exc).__name__)
    index = [e for e in index if e["date"] != date] + [entry]
    index.sort(key=lambda e: e["date"], reverse=True)
    _atomic_write(index_path, index)


def _ping_get(url: str, timeout: float) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from exc


def ping(url: str | None, ok: bool, get: PingGet = _ping_get) -> None:
    """Ping the heartbeat URL (`/fail` after a failure); never raises."""
    if not url:
        return
    target = url.rstrip("/") + ("" if ok else "/fail")
    host = urlsplit(url).hostname or "unknown host"
    reason = ""
    for _ in range(PING_RETRIES + 1):
        try:
            status = get(target, PING_TIMEOUT)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            reason = type(exc).__name__
            continue
        if status < 400:
            return
        reason = f"HTTP {status}"
    log.warning("Heartbeat ping failed: %s (%s)", reason, host)
