import json
import random
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from kbsync.errors import ListingError
from kbsync.zendesk import USER_AGENT, fetch_articles

FIXTURES = Path(__file__).parent / "fixtures" / "articles"


def item(n, draft=False):
    return {
        "id": n,
        "title": f"t{n}",
        "body": f"<p>{n}</p>",
        "html_url": f"https://x/{n}",
        "updated_at": "2026-01-01T00:00:00Z",
        "draft": draft,
    }


def page(items, next_page, count):
    body = json.dumps({"articles": items, "next_page": next_page, "count": count}).encode()
    return 200, {}, body


class FakeZendesk:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def run(responses, **kwargs):
    sleeps = []
    fake = FakeZendesk(responses)
    kwargs.setdefault("rng", random.Random(1))
    result = fetch_articles(get=fake, sleep=sleeps.append, **kwargs)
    return result, fake, sleeps


def three_pages():
    u = "https://x/api?per_page=100&page="
    return [
        page([item(i) for i in range(100)], u + "2", 215),
        page([item(i) for i in range(100, 200)], u + "3", 215),
        page([item(i) for i in range(200, 215)], None, 215),
    ]


def test_three_pages_return_all_articles_in_order():
    result, fake, _ = run(three_pages())
    assert [a.id for a in result.articles] == list(range(215))
    assert len(fake.calls) == 3
    assert "per_page=100" in fake.calls[0][0]
    assert all("per_page=100" in url for url, _ in fake.calls)
    assert result.complete is True


def test_pause_between_pages_only():
    _, _, sleeps = run(three_pages(), pause=0.25)
    assert sleeps == [0.25, 0.25]


def test_drafts_are_skipped():
    items = [item(1), item(2, draft=True), item(3), item(4), item(5)]
    result, _, _ = run([page(items, None, 5)])
    assert [a.id for a in result.articles] == [1, 3, 4, 5]


def test_user_agent_on_every_request():
    _, fake, _ = run(three_pages())
    assert all(h["User-Agent"] == USER_AGENT for _, h in fake.calls)
    assert USER_AGENT.startswith("kbsync/")


def test_429_retry_after_is_respected():
    result, fake, sleeps = run([(429, {"Retry-After": "2"}, b""), page([item(1)], None, 1)])
    assert len(result.articles) == 1
    assert len(fake.calls) == 2
    assert sleeps[0] >= 2


def test_503_three_times_then_success():
    bad = (503, {}, b"")
    result, _, sleeps = run([bad, bad, bad, page([item(1)], None, 1)])
    assert len(result.articles) == 1
    assert len(sleeps) == 3


def test_backoff_has_growing_bounds_and_jitter():
    bad = (503, {}, b"")
    _, _, a = run([bad] * 4 + [page([], None, 0)], rng=random.Random(1))
    _, _, b = run([bad] * 4 + [page([], None, 0)], rng=random.Random(2))
    assert a != b
    for sleeps in (a, b):
        for i, s in enumerate(sleeps):
            assert 2**i / 2 <= s <= 2**i
    assert all(x <= y for x, y in zip(a, a[1:], strict=False))


def test_five_failed_attempts_raise_listing_error():
    with pytest.raises(ListingError, match="^Zendesk listing failed:"):
        run([(500, {}, b"")] * 5)


def test_network_error_is_retried_then_fails():
    with pytest.raises(ListingError, match="^Zendesk listing failed:"):
        run([OSError("boom")] * 5)


def test_failure_on_page_two_returns_no_partial_list():
    responses = [page([item(i) for i in range(100)], "https://x/2", 215)] + [(500, {}, b"")] * 5
    with pytest.raises(ListingError, match="^Zendesk listing failed:"):
        run(responses)


def test_client_error_fails_without_retry():
    with pytest.raises(ListingError, match="^Zendesk listing failed:") as exc:
        run([(404, {}, b"")])
    assert "404" in str(exc.value)


def test_count_mismatch_raises_incomplete():
    with pytest.raises(ListingError) as exc:
        run([page([item(1), item(2)], None, 3)])
    assert str(exc.value).startswith("Zendesk listing incomplete: got 2 of 3 articles.")


def test_limit_stops_early_and_is_incomplete():
    result, fake, _ = run(three_pages(), limit=5)
    assert [a.id for a in result.articles] == [0, 1, 2, 3, 4]
    assert len(fake.calls) == 1
    assert result.complete is False


def test_limit_skips_drafts():
    items = [item(1, draft=True)] + [item(i) for i in range(2, 9)]
    result, _, _ = run([page(items, None, 8)], limit=5)
    assert [a.id for a in result.articles] == [2, 3, 4, 5, 6]


def test_fixtures_cover_required_elements():
    files = list(FIXTURES.glob("*.json"))
    assert len([f for f in files if f.stem.isdigit()]) >= 8
    soup = BeautifulSoup("".join(json.loads(f.read_text())["body"] for f in files), "html.parser")
    for tag in ("h2", "h3", "ol", "ul", "table", "img"):
        assert soup.find(tag), tag
    assert any("youtube" in i.get("src", "") for i in soup.find_all("iframe"))
    assert soup.find("pre") or soup.find("code")
    assert any(a.get("href", "").startswith("/") for a in soup.find_all("a"))


def test_fixtures_are_small_and_hold_no_credentials():
    for f in FIXTURES.glob("*.json"):
        text = f.read_text()
        assert len(text.encode()) < 200_000
        assert "authorization" not in text.lower()
