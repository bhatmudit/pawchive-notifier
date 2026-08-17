"""Pawchive API client."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from constants import API_BASE_URL, USER_AGENT

log = logging.getLogger("pawchive-notifier")

BASE_URL = API_BASE_URL
PAGE_SIZE = 50
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2.0

# Hard ceiling on pages fetched for a single creator in one run. Without
# this, an API bug that ignores the offset param (or repeats a page) turns
# the "while True" pagination loop below into an infinite hang that only
# ends when the CI job's own timeout kills it hours later, with no alert
# email ever sent. 400 pages is 20,000 posts - far beyond any real
# creator's post count, but small enough to fail fast if pagination is
# broken.
MAX_PAGES = 400

# A shared Session gives connection pooling/keep-alive across all the
# _fetch_page calls in a run (multiple pages per creator, multiple
# creators), instead of a fresh TCP+TLS handshake per request. Headers
# that don't vary per-request live on the session once instead of being
# rebuilt on every call.
_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})


class PawchiveError(RuntimeError):
    """Raised when a Pawchive API call ultimately fails."""


def fetch_creator_posts(
    service: str,
    creator_id: str,
    *,
    known_ids: set[str] | None = None,
    bootstrap: bool = False,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Fetch all posts for a creator, paginating until exhausted.

    Outside of bootstrap, pagination stops early once a page contains
    a post ID we already know about, since everything after it should
    already be known too.
    """
    known_ids = known_ids or set()
    posts: list[dict[str, Any]] = []
    offset = 0

    for page_number in range(1, MAX_PAGES + 1):
        page = _fetch_page(service, creator_id, offset=offset, timeout=timeout)
        page_posts = [post for post in page if isinstance(post, dict) and "id" in post]
        posts.extend(page_posts)

        if not page_posts or len(page_posts) < PAGE_SIZE:
            return posts

        if not bootstrap and any(str(post["id"]) in known_ids for post in page_posts):
            return posts

        offset += PAGE_SIZE
        time.sleep(0.05)

    raise PawchiveError(
        f"{service}/{creator_id}: pagination did not terminate after {MAX_PAGES} pages "
        f"({len(posts)} posts fetched) - the API may be returning a broken or "
        f"repeating offset; aborting instead of hanging"
    )


def _fetch_page(service: str, creator_id: str, *, offset: int, timeout: int) -> list[Any]:
    """Fetch a single page, retrying transient failures with backoff.

    404 and other non-transient client errors fail immediately, since
    retrying won't help. Network errors, 429, and 5xx are treated as
    transient and retried with exponential backoff.
    """
    url = f"{BASE_URL}/{service}/user/{creator_id}"
    params = {"o": offset} if offset else {}

    last_error: Exception | None = None
    retry_after: float | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        retry_after = None
        try:
            response = _session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
        else:
            if response.status_code == 404:
                raise PawchiveError(f"creator not found: {service}/{creator_id}")

            if response.status_code == 200:
                try:
                    page = response.json()
                except ValueError as exc:
                    raise PawchiveError("Pawchive returned invalid JSON") from exc
                if not isinstance(page, list):
                    raise PawchiveError("unexpected Pawchive response shape")
                return page

            if response.status_code == 429 or response.status_code >= 500:
                last_error = PawchiveError(
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            else:
                # Non-transient client error (400, 401, 403, ...) - don't retry.
                raise PawchiveError(
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )

        if attempt < MAX_ATTEMPTS:
            # Prefer the server's own Retry-After hint (common on 429s)
            # over our fixed exponential backoff when it's given.
            delay = retry_after if retry_after is not None else RETRY_BASE_DELAY_SECONDS * (
                2 ** (attempt - 1)
            )
            log.warning(
                "%s/%s: attempt %d/%d failed (%s); retrying in %.0fs",
                service, creator_id, attempt, MAX_ATTEMPTS, last_error, delay,
            )
            time.sleep(delay)

    raise PawchiveError(
        f"request failed after {MAX_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (seconds form only; ignore HTTP-date form)."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
