import pytest
import requests

import pawchive


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

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
        pawchive.requests, "get", lambda *a, **k: FakeResponse(200, page)
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

    monkeypatch.setattr(pawchive.requests, "get", fake_get)

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

    monkeypatch.setattr(pawchive.requests, "get", fake_get)

    posts = pawchive.fetch_creator_posts(
        "patreon", "123", known_ids={"10"}, bootstrap=True
    )
    assert len(posts) == 60


def test_fetch_creator_posts_404_raises_immediately(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pawchive.requests,
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

    monkeypatch.setattr(pawchive.requests, "get", fake_get)

    posts = pawchive.fetch_creator_posts("patreon", "123")
    assert [p["id"] for p in posts] == [1]


def test_fetch_creator_posts_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(
        pawchive.requests, "get", lambda *a, **k: FakeResponse(503, text="down")
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

    monkeypatch.setattr(pawchive.requests, "get", fake_get)

    posts = pawchive.fetch_creator_posts("patreon", "123")
    assert [p["id"] for p in posts] == [1]
    assert attempts["n"] == 2


def test_fetch_creator_posts_non_transient_4xx_no_retry(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        return FakeResponse(401, text="unauthorized")

    monkeypatch.setattr(pawchive.requests, "get", fake_get)

    with pytest.raises(pawchive.PawchiveError, match="401"):
        pawchive.fetch_creator_posts("patreon", "123")
    assert len(calls) == 1
