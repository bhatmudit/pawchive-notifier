"""Pawchive API client."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger("pawchive-notifier")

BASE_URL = "https://pawchive.pw/api/v1"
PAGE_SIZE = 50
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2.0


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

    while True:
        page = _fetch_page(service, creator_id, offset=offset, timeout=timeout)
        page_posts = [post for post in page if isinstance(post, dict) and "id" in post]
        posts.extend(page_posts)

        if not page_posts or len(page_posts) < PAGE_SIZE:
            break

        if not bootstrap and any(str(post["id"]) in known_ids for post in page_posts):
            break

        offset += PAGE_SIZE
        time.sleep(0.05)

    return posts


def _fetch_page(service: str, creator_id: str, *, offset: int, timeout: int) -> list[Any]:
    """Fetch a single page, retrying transient failures with backoff.

    404 and other non-transient client errors fail immediately, since
    retrying won't help. Network errors, 429, and 5xx are treated as
    transient and retried with exponential backoff.
    """
    url = f"{BASE_URL}/{service}/user/{creator_id}"
    params = {"o": offset} if offset else {}

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
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
            else:
                # Non-transient client error (400, 401, 403, ...) - don't retry.
                raise PawchiveError(
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )

        if attempt < MAX_ATTEMPTS:
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "%s/%s: attempt %d/%d failed (%s); retrying in %.0fs",
                service, creator_id, attempt, MAX_ATTEMPTS, last_error, delay,
            )
            time.sleep(delay)

    raise PawchiveError(
        f"request failed after {MAX_ATTEMPTS} attempts: {last_error}"
    ) from last_error
