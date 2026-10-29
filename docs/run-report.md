# Run report contract

Each run writes under `public/runs/` (served at `/runs/...`), atomically:

- `<UTC date>.json`: the report of the last run that day.
- `latest.json`: identical to the newest report.
- `index.json`: list, newest first, one entry per date.

## Report fields

| Field | Type | Notes |
|---|---|---|
| `status` | string | `ok` or `failed` |
| `started_at` | string | ISO 8601 UTC, ends in `Z` |
| `duration_s` | integer | seconds |
| `added`, `updated`, `skipped`, `deleted`, `failed` | integer | article counts |
| `files` | integer | files in the vector store; omitted when `status` is `failed` and it is unknown |
| `chunks_estimate` | integer | estimated chunks; omitted when unknown, as `files` |
| `converter_version` | string | converter version used |
| `git_sha` | string | `GIT_SHA`, else `git rev-parse --short HEAD`, else `unknown` |
| `changes` | list | `{article_id: int, title: str, url: str, change: str}` |
| `failures` | list | `{article_id: int, title: str, reason: str}`, one per failed article |
| `error` | string | one line; only when `status` is `failed` |

`change` is `added`, `updated` or `deleted`.

```json
{
  "status": "ok",
  "started_at": "2026-10-29T03:00:01Z",
  "duration_s": 42,
  "added": 1, "updated": 0, "skipped": 30, "deleted": 0, "failed": 0,
  "files": 31, "chunks_estimate": 120,
  "converter_version": "2", "git_sha": "7f5e304",
  "changes": [{"article_id": 1, "title": "Setup", "url": "https://x/1", "change": "added"}],
  "failures": []
}
```

## index.json entry

`{date, status, started_at, added, updated, skipped, deleted, failed, files}`, for example
`{"date": "2026-10-29", "status": "ok", "started_at": "2026-10-29T03:00:01Z", "added": 1,
"updated": 0, "skipped": 30, "deleted": 0, "failed": 0, "files": 31}`. `files` is `null` for a failed run that did not get that far.
