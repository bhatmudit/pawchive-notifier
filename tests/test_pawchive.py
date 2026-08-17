import pytest
import requests

import pawchive


class FakeResponse:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # Keep tests fast: skip real delays for both retry backoff and
    # the inter-page pause.
    monkeypatch.setattr(pawchive.time, "sleep", lambda seconds: None)


def test_fetch_creator_posts_single_page(monkeypatch):
    page = [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(
        pawchive._session, "get", lambda *a, **k: FakeResponse(200, page)
    )

    posts = pawchive.fetch_creator_posts("patreon", "123")
    assert [p["id"] for p in posts] == [1, 2]


def test_fetch_creator_posts_stops_pagination_on_known_id(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        offset = (params or {}).get("o", 0)
        calls.append(offset)
        if offset == 0:
            return FakeResponse(200, [{"id": i} for i in range(50)])
        return FakeResponse(200, [{"id": i} for i in range(50, 100)])

    monkeypatch.setattr(pawchive._session, "get", fake_get)

    posts = pawchive.fetch_creator_posts("patreon", "123", known_ids={"10"})
    # First page (50 posts) is returned in full even though it contains a
    # known id; pagination stops there rather than fetching page two.
    assert len(posts) == 50
    assert calls == [0]


def test_fetch_creator_posts_bootstrap_ignores_known_ids(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        offset = (params or {}).get("o", 0)
        if offset == 0:
            return FakeResponse(200, [{"id": i} for i in range(50)])
        return FakeResponse(200, [{"id": i} for i in range(50, 60)])

    monkeypatch.setattr(pawchive._session, "get", fake_get)

    posts = pawchive.fetch_creator_posts(
        "patreon", "123", known_ids={"10"}, bootstrap=True
    )
    assert len(posts) == 60


def test_fetch_creator_posts_404_raises_immediately(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pawchive._session,
        "get",
        lambda *a, **k: (calls.append(1), FakeResponse(404))[1],
    )

    with pytest.raises(pawchive.PawchiveError, match="creator not found"):
        pawchive.fetch_creator_posts("patreon", "123")
    assert len(calls) == 1  # no retries on a 404


def test_fetch_creator_posts_retries_transient_5xx_then_succeeds(monkeypatch):
    responses = [FakeResponse(503, text="down"), FakeResponse(200, [{"id": 1}])]

    def fake_get(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(pawchive._session, "get", fake_get)

    posts = pawchive.fetch_creator_posts("patreon", "123")
    assert [p["id"] for p in posts] == [1]


def test_fetch_creator_posts_honors_retry_after_header(monkeypatch):
    # A 429 with a Retry-After header should be waited on as given, not
    # overridden by the fixed exponential backoff schedule.
    responses = [
        FakeResponse(429, text="slow down", headers={"Retry-After": "7"}),
        FakeResponse(200, [{"id": 1}]),
    ]
    monkeypatch.setattr(pawchive._session, "get", lambda *a, **k: responses.pop(0))

    sleeps = []
    monkeypatch.setattr(pawchive.time, "sleep", lambda seconds: sleeps.append(seconds))

    posts = pawchive.fetch_creator_posts("patreon", "123")

    assert [p["id"] for p in posts] == [1]
    assert sleeps[0] == 7.0  # the header's value, not RETRY_BASE_DELAY_SECONDS


def test_fetch_creator_posts_falls_back_to_backoff_without_retry_after(monkeypatch):
    responses = [FakeResponse(429, text="slow down"), FakeResponse(200, [{"id": 1}])]
    monkeypatch.setattr(pawchive._session, "get", lambda *a, **k: responses.pop(0))

    sleeps = []
    monkeypatch.setattr(pawchive.time, "sleep", lambda seconds: sleeps.append(seconds))

    pawchive.fetch_creator_posts("patreon", "123")

    assert sleeps[0] == pawchive.RETRY_BASE_DELAY_SECONDS


def test_fetch_creator_posts_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(
        pawchive._session, "get", lambda *a, **k: FakeResponse(503, text="down")
    )

    with pytest.raises(pawchive.PawchiveError, match="failed after"):
        pawchive.fetch_creator_posts("patreon", "123")


def test_fetch_creator_posts_network_error_retries(monkeypatch):
    attempts = {"n": 0}

    def fake_get(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise requests.ConnectionError("boom")
        return FakeResponse(200, [{"id": 1}])

    monkeypatch.setattr(pawchive._session, "get", fake_get)

    posts = pawchive.fetch_creator_posts("patreon", "123")
    assert [p["id"] for p in posts] == [1]
    assert attempts["n"] == 2


def test_fetch_creator_posts_non_transient_4xx_no_retry(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        return FakeResponse(401, text="unauthorized")

    monkeypatch.setattr(pawchive._session, "get", fake_get)

    with pytest.raises(pawchive.PawchiveError, match="401"):
        pawchive.fetch_creator_posts("patreon", "123")
    assert len(calls) == 1


def test_fetch_creator_posts_invalid_json_raises(monkeypatch):
    monkeypatch.setattr(
        pawchive._session, "get", lambda *a, **k: FakeResponse(200, payload=None)
    )

    with pytest.raises(pawchive.PawchiveError, match="invalid JSON"):
        pawchive.fetch_creator_posts("patreon", "123")


def test_fetch_creator_posts_non_list_response_raises(monkeypatch):
    monkeypatch.setattr(
        pawchive._session, "get", lambda *a, **k: FakeResponse(200, payload={"not": "a list"})
    )

    with pytest.raises(pawchive.PawchiveError, match="unexpected"):
        pawchive.fetch_creator_posts("patreon", "123")


def test_fetch_creator_posts_filters_malformed_entries(monkeypatch):
    # Defensive: a page with junk entries (non-dicts, or dicts missing
    # 'id') should not crash - those entries are simply dropped.
    page = [{"id": 1}, "not a post", 42, None, {"title": "no id field"}, {"id": 2}]
    monkeypatch.setattr(pawchive._session, "get", lambda *a, **k: FakeResponse(200, page))

    posts = pawchive.fetch_creator_posts("patreon", "123")
    assert [p["id"] for p in posts] == [1, 2]


def test_fetch_creator_posts_continues_when_full_page_has_no_known_ids(monkeypatch):
    # A full page (== PAGE_SIZE) whose ids are all unknown should
    # continue on to the next page rather than stopping early.
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        offset = (params or {}).get("o", 0)
        calls.append(offset)
        if offset == 0:
            return FakeResponse(200, [{"id": i} for i in range(50)])
        return FakeResponse(200, [{"id": i} for i in range(50, 55)])

    monkeypatch.setattr(pawchive._session, "get", fake_get)

    posts = pawchive.fetch_creator_posts("patreon", "123", known_ids={"999"})
    assert len(posts) == 55
    assert calls == [0, 50]


def test_fetch_creator_posts_empty_first_page(monkeypatch):
    monkeypatch.setattr(pawchive._session, "get", lambda *a, **k: FakeResponse(200, []))

    posts = pawchive.fetch_creator_posts("patreon", "123")
    assert posts == []


def test_fetch_creator_posts_aborts_instead_of_hanging_forever(monkeypatch):
    # A pagination bug (e.g. the API ignoring the offset param and always
    # returning a full, never-known page) must not hang the run forever -
    # it should raise once MAX_PAGES is hit rather than looping endlessly.
    monkeypatch.setattr(pawchive, "MAX_PAGES", 3)

    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, [{"id": i} for i in range(pawchive.PAGE_SIZE)])

    monkeypatch.setattr(pawchive._session, "get", fake_get)

    with pytest.raises(pawchive.PawchiveError, match="pagination did not terminate"):
        pawchive.fetch_creator_posts("patreon", "123", known_ids={"999"})
