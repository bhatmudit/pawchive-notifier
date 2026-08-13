from __future__ import annotations

import time
from typing import Any

import requests

BASE_URL = "https://pawchive.pw/api/v1"
PAGE_SIZE = 50


class PawchiveError(RuntimeError):
    pass


def fetch_creator_posts(
    service: str,
    creator_id: str,
    *,
    known_ids: set[str] | None = None,
    bootstrap: bool = False,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    known_ids = known_ids or set()
    posts: list[dict[str, Any]] = []
    offset = 0

    while True:
        url = f"{BASE_URL}/{service}/user/{creator_id}"

        try:
            response = requests.get(
                url,
                params={"o": offset} if offset else {},
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise PawchiveError(
                f"request failed: {exc}"
            ) from exc

        if response.status_code == 404:
            raise PawchiveError(
                f"creator not found: {service}/{creator_id}"
            )

        if response.status_code != 200:
            raise PawchiveError(
                f"HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            page = response.json()
        except ValueError as exc:
            raise PawchiveError(
                "Pawchive returned invalid JSON"
            ) from exc

        if not isinstance(page, list):
            raise PawchiveError(
                "unexpected Pawchive response shape"
            )

        page_posts = [
            post
            for post in page
            if isinstance(post, dict) and "id" in post
        ]

        posts.extend(page_posts)

        if not page_posts or len(page_posts) < PAGE_SIZE:
            break

        # During normal operation, once we encounter something already
        # known, everything after it should already be known as well.
        if not bootstrap and any(
            str(post["id"]) in known_ids
            for post in page_posts
        ):
            break

        offset += PAGE_SIZE
        time.sleep(0.05)

    return posts
