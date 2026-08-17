"""Persistent monitor state: known posts per creator, run metadata.

Stored as JSON and committed back to the repo by the GitHub Actions
workflow (see .github/workflows/monitor.yml), which only commits when
the content meaningfully changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_VERSION = 2

# Cap how many known posts we remember per creator, to keep state.json (and
# the git history it's committed into) from growing forever. Safe to prune
# the oldest entries: pawchive.fetch_creator_posts stops paginating for a
# creator as soon as it sees *any* known post id on a page, so as long as
# recent posts stay remembered, older pruned ones are never fetched again
# and therefore never mistaken for "new". Ranking is by published date
# (falling back to "" for posts missing one, which sorts them oldest).
DEFAULT_MAX_KNOWN_POSTS_PER_CREATOR = 1500


def _default_meta() -> dict[str, Any]:
    return {
        "welcomed": False,
        "welcomed_at": None,
        "last_digest_at": None,
        "last_heartbeat_at": None,
        "last_run_at": None,
        "last_success_at": None,
        "total_runs": 0,
    }


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "meta": _default_meta(),
        "creators": {},
    }


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Bring an on-disk state dict up to the current schema in place."""
    data.setdefault("version", STATE_VERSION)

    meta = data.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        data["meta"] = meta
    for key, value in _default_meta().items():
        meta.setdefault(key, value)

    # v2/v3 stored a redundant global failure counter. It's intentionally
    # ignored now; failure state lives per creator instead.
    meta.pop("consecutive_failures", None)

    creators = data.setdefault("creators", {})
    if not isinstance(creators, dict):
        creators = {}
        data["creators"] = creators

    for entry in creators.values():
        if isinstance(entry, dict):
            entry.setdefault("bootstrapped", False)
            entry.setdefault("consecutive_failures", 0)
            entry.setdefault("posts", {})

    data["version"] = STATE_VERSION
    return data


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read state file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("state.json must contain a JSON object")

    return _migrate(data)


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Write state atomically (write to a temp file, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def creator_key(service: str, creator_id: str) -> str:
    return f"{service}:{creator_id}"


def get_creator_state(state: dict[str, Any], service: str, creator_id: str) -> dict[str, Any]:
    """Get (creating if needed) the mutable per-creator state entry."""
    key = creator_key(service, creator_id)
    entry = state["creators"].setdefault(
        key,
        {"bootstrapped": False, "consecutive_failures": 0, "posts": {}},
    )
    entry.setdefault("bootstrapped", False)
    entry.setdefault("consecutive_failures", 0)
    entry.setdefault("posts", {})
    return entry


def prune_known_posts(
    known_posts: dict[str, Any], *, keep: int = DEFAULT_MAX_KNOWN_POSTS_PER_CREATOR
) -> int:
    """Drop the oldest entries from a creator's known-posts map in place.

    Keeps the `keep` most recently published posts and discards the rest.
    Returns how many were dropped (0 if under the limit). See the module-
    level comment on DEFAULT_MAX_KNOWN_POSTS_PER_CREATOR for why this is
    safe to do without risking a pruned post being re-notified later.
    """
    if len(known_posts) <= keep:
        return 0
    ranked = sorted(
        known_posts.items(), key=lambda item: item[1].get("published") or "", reverse=True
    )
    for post_id, _ in ranked[keep:]:
        del known_posts[post_id]
    return len(ranked) - keep
