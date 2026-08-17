"""Shared accessors for raw Pawchive post dicts.

Posts arrive from the API as untyped JSON objects and get passed around
as ``dict[str, Any]`` (see the note in pawchive.py about why we don't
validate the full shape). Previously each of main.py and notifier.py read
fields like "id", "published", "added", and "edited" directly, so a field
rename on Pawchive's side would require hunting down every call site and
silently misbehave (falling back to "Untitled post" / "Unknown") anywhere
that got missed. Routing every read through these functions makes the
field names greppable in one place and gives call sites a single spot to
add validation later if we ever want it.
"""

from __future__ import annotations

from typing import Any


def post_id(post: dict[str, Any]) -> str:
    return str(post["id"])


def post_title(post: dict[str, Any]) -> str:
    return str(post.get("title") or "Untitled post")


def post_content(post: dict[str, Any]) -> str:
    return str(post.get("content") or "")


def post_edited_at(post: dict[str, Any]) -> str | None:
    return post.get("edited")


def post_display_date(post: dict[str, Any]) -> str | None:
    """The timestamp to show/sort by: published date, falling back to added."""
    return post.get("published") or post.get("added")
