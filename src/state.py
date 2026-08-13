from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_VERSION = 2


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
    data.setdefault("version", STATE_VERSION)

    meta = data.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        data["meta"] = meta

    for key, value in _default_meta().items():
        meta.setdefault(key, value)

    # v2/v3 stored a redundant global failure counter. It is intentionally
    # ignored; failure state now lives with each creator.
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
        raise RuntimeError(
            f"cannot read state file {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError("state.json must contain a JSON object")

    return _migrate(data)


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def creator_key(service: str, creator_id: str) -> str:
    return f"{service}:{creator_id}"


def get_creator_state(
    state: dict[str, Any],
    service: str,
    creator_id: str,
) -> dict[str, Any]:
    key = creator_key(service, creator_id)
    entry = state["creators"].setdefault(
        key,
        {
            "bootstrapped": False,
            "consecutive_failures": 0,
            "posts": {},
        },
    )
    entry.setdefault("bootstrapped", False)
    entry.setdefault("consecutive_failures", 0)
    entry.setdefault("posts", {})
    return entry
