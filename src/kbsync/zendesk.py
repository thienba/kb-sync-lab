import json
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from kbsync.errors import ListingError

LISTING_URL = "https://support.optisigns.com/api/v2/help_center/en-us/articles.json?per_page=100"
USER_AGENT = "kbsync/0.1 (+https://github.com/thienba/kb-sync-lab)"
MAX_ATTEMPTS = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 60.0
PAGE_PAUSE = 0.5

# (status, headers, body) for one GET; a network failure raises OSError.
Response = tuple[int, dict[str, str], bytes]
Get = Callable[[str, dict[str, str]], Response]


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    body_html: str
    html_url: str
    updated_at: str
    draft: bool


@dataclass(frozen=True)
class Listing:
    articles: list[Article]
    complete: bool


def http_get(url: str, headers: dict[str, str]) -> Response:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from exc


def _retry_after(headers: dict[str, str]) -> float:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except ValueError:
                return 0.0
    return 0.0


def get_page(
    url: str,
    get: Get = http_get,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> dict:
    """GET one listing page as JSON, retrying 429, 5xx and network errors with backoff."""
    rng = rng or random.Random()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    reason = ""
    for attempt in range(MAX_ATTEMPTS):
        retry_after = 0.0
        try:
            status, resp_headers, body = get(url, headers)
        except OSError as exc:
            reason = f"network error: {exc}"
        else:
            if status == 200:
                try:
                    return json.loads(body)
                except ValueError as exc:
                    raise ListingError(f"Zendesk listing failed: invalid JSON from {url}") from exc
            reason = f"HTTP {status}"
            if status != 429 and status < 500:
                raise ListingError(f"Zendesk listing failed: HTTP {status} from {url}")
            retry_after = _retry_after(resp_headers)
        if attempt < MAX_ATTEMPTS - 1:
            upper = min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt)
            sleep(max(retry_after, upper / 2 + rng.random() * upper / 2))
    raise ListingError(f"Zendesk listing failed: {reason} from {url} after {MAX_ATTEMPTS} attempts")


def fetch_articles(
    limit: int | None = None,
    *,
    get: Get = http_get,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    pause: float = PAGE_PAUSE,
) -> Listing:
    """Fetch non-draft articles page by page; raise ListingError if the listing is not complete."""
    articles: list[Article] = []
    seen = 0
    url: str | None = LISTING_URL
    first = True
    while url:
        if not first:
            sleep(pause)
        first = False
        page = get_page(url, get, sleep, rng)
        items = page["articles"]
        seen += len(items)
        for item in items:
            if item.get("draft"):
                continue
            articles.append(
                Article(
                    id=item["id"],
                    title=item["title"],
                    body_html=item.get("body") or "",
                    html_url=item["html_url"],
                    updated_at=item["updated_at"],
                    draft=False,
                )
            )
            if limit is not None and len(articles) >= limit:
                return Listing(articles, complete=False)
        url = page.get("next_page")
    count = page.get("count")
    if count != seen:
        raise ListingError(f"Zendesk listing incomplete: got {seen} of {count} articles.")
    return Listing(articles, complete=True)
