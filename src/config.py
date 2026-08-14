"""Typed config loading for the Pawchive notifier.

Validation happens once, at load time. A malformed config raises
ConfigError and the run fails loudly (visible in the Actions log)
instead of silently emailing a repeat alert every 10 minutes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    """Raised when config/creators.json is missing, malformed, or invalid."""


@dataclass(frozen=True)
class Creator:
    service: str
    id: str
    name: str


@dataclass(frozen=True)
class HeartbeatSettings:
    enabled: bool = False
    interval_hours: float = 168.0


@dataclass(frozen=True)
class Settings:
    notify_edits: bool = False
    initial_import_notify: bool = False
    startup_email: bool = True
    alert_on_failure: bool = True
    max_preview_chars: int = 300
    heartbeat: HeartbeatSettings = field(default_factory=HeartbeatSettings)


@dataclass(frozen=True)
class Config:
    creators: list[Creator]
    settings: Settings


def load_config(path: Path) -> Config:
    """Load and validate config/creators.json.

    Raises ConfigError with a specific, actionable message on any
    problem, so a bad config fails the run instead of degrading into
    repeated per-run alert emails.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a JSON object")

    raw_creators = raw.get("creators")
    if not isinstance(raw_creators, list) or not raw_creators:
        raise ConfigError(f"{path} must contain a non-empty 'creators' array")

    creators = [_parse_creator(entry, i) for i, entry in enumerate(raw_creators)]
    settings = _parse_settings(raw.get("settings") or {})
    return Config(creators=creators, settings=settings)


def _parse_creator(entry: Any, index: int) -> Creator:
    if not isinstance(entry, dict):
        raise ConfigError(f"creators[{index}] must be an object, got {entry!r}")

    service = str(entry.get("service", "")).strip()
    creator_id = str(entry.get("id", "")).strip()
    if not service or not creator_id:
        raise ConfigError(
            f"creators[{index}] is missing a required 'service' or 'id' "
            f"field: {entry!r}"
        )

    name = str(entry.get("name") or f"{service}/{creator_id}")
    return Creator(service=service, id=creator_id, name=name)


def _parse_settings(raw: dict[str, Any]) -> Settings:
    heartbeat_raw = raw.get("heartbeat") or {}
    if not isinstance(heartbeat_raw, dict):
        raise ConfigError("settings.heartbeat must be an object")

    heartbeat = HeartbeatSettings(
        enabled=bool(heartbeat_raw.get("enabled", False)),
        interval_hours=float(heartbeat_raw.get("interval_hours", 168)),
    )
    return Settings(
        notify_edits=bool(raw.get("notify_edits", False)),
        initial_import_notify=bool(raw.get("initial_import_notify", False)),
        startup_email=bool(raw.get("startup_email", True)),
        alert_on_failure=bool(raw.get("alert_on_failure", True)),
        max_preview_chars=int(raw.get("max_preview_chars", 300)),
        heartbeat=heartbeat,
    )
