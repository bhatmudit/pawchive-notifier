"""Entry point: check monitored creators, send notifications, save state."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import Config, ConfigError, Creator, Settings, load_config
from notifier import (
    EmailError,
    Notification,
    NotificationKind,
    build_email,
    build_status_email,
    send_email,
)
from pawchive import PawchiveError, fetch_creator_posts
from state import get_creator_state, load_state, save_state

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "creators.json"
STATE_PATH = ROOT / "data" / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pawchive-notifier")


@dataclass
class CreatorResult:
    creator: Creator
    new_posts: list[dict[str, Any]]
    edited_posts: list[dict[str, Any]]
    failed: bool
    just_failed: bool
    just_recovered: bool


def _process_creator(
    creator: Creator,
    pending_state: dict[str, Any],
    settings: Settings,
    initial_import_notify: bool,
) -> CreatorResult:
    """Fetch one creator's posts and update its entry in pending_state.

    Returns what (if anything) should be notified about. Nothing here
    touches email or the on-disk state file directly.
    """
    current = get_creator_state(pending_state, creator.service, creator.id)
    known_posts: dict[str, Any] = current["posts"]
    known_ids = set(known_posts)
    bootstrap = not current["bootstrapped"]
    was_failing = current.get("consecutive_failures", 0) > 0

    try:
        posts = fetch_creator_posts(
            creator.service, creator.id, known_ids=known_ids, bootstrap=bootstrap
        )
    except PawchiveError as exc:
        log.error("%s: %s", creator.name, exc)
        current["consecutive_failures"] = current.get("consecutive_failures", 0) + 1
        just_failed = current["consecutive_failures"] == 1  # alert only on transition
        return CreatorResult(creator, [], [], failed=True, just_failed=just_failed, just_recovered=False)

    log.info(
        "%s: fetched %d posts%s", creator.name, len(posts), " (bootstrap)" if bootstrap else ""
    )

    just_recovered = was_failing
    current["consecutive_failures"] = 0

    new_posts = [p for p in posts if str(p["id"]) not in known_ids]
    edited_posts = (
        _find_edited_posts(posts, known_posts) if settings.notify_edits and not bootstrap else []
    )

    # Record everything fetched. This mutates pending_state, which is only
    # persisted by the caller after any required notification succeeds.
    for post in posts:
        pid = str(post["id"])
        known_posts[pid] = {"edited": post.get("edited"), "published": post.get("published")}
    current["bootstrapped"] = True

    if bootstrap and not initial_import_notify:
        log.info("%s: bootstrap complete; %d existing posts remembered", creator.name, len(posts))
        new_posts, edited_posts = [], []
    else:
        if new_posts:
            log.info("%s: %d new posts", creator.name, len(new_posts))
        if edited_posts:
            log.info("%s: %d edited posts", creator.name, len(edited_posts))

    return CreatorResult(
        creator, new_posts, edited_posts, failed=False, just_failed=False, just_recovered=just_recovered
    )


def _find_edited_posts(
    posts: list[dict[str, Any]], known_posts: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return posts whose 'edited' timestamp changed since we last saw them.

    Note: this also catches a post's *first* edit, i.e. one that went
    from no recorded edit timestamp (``old_edited`` is ``None``) to
    having one. An earlier version additionally required ``old_edited``
    to be truthy, which meant a post's first-ever edit was silently
    never reported.
    """
    edited = []
    for post in posts:
        pid = str(post["id"])
        if pid not in known_posts:
            continue  # brand new post, not an edit - handled separately
        old_edited = known_posts[pid].get("edited")
        new_edited = post.get("edited")
        if new_edited and new_edited != old_edited:
            edited.append(post)
    return edited


def _send_notice(kind: str, **context: Any) -> bool:
    """Best-effort status email. Status failures never block monitoring."""
    subject, html_body, text_body = build_status_email(kind, **context)
    try:
        send_email(subject=subject, html_body=html_body, text_body=text_body)
    except EmailError as exc:
        log.error("%s email failed to send: %s", kind, exc)
        return False
    log.info("%s email sent", kind)
    return True


def _latest_signal(meta: dict[str, Any]) -> str | None:
    """Return the newest of the timestamps that matter for heartbeat scheduling."""
    timestamps = [
        meta.get("last_heartbeat_at"),
        meta.get("last_digest_at"),
        meta.get("welcomed_at"),
    ]
    timestamps = [value for value in timestamps if value]
    if not timestamps:
        return None
    return max(timestamps, key=lambda value: datetime.fromisoformat(value))


def _send_required_notification(
    notifications: list[Notification],
    config: Config,
    needs_startup: bool,
    total_new: int,
    total_edited: int,
) -> bool:
    """Send the digest (or startup notice) email. Returns True on success.

    This is the one email whose failure must NOT be swallowed: a failure
    here means the caller should not commit newly discovered posts to
    state, so they get picked up again next run instead of being lost.
    """
    if notifications:
        subject, html_body, text_body = build_email(
            notifications,
            preview_chars=config.settings.max_preview_chars,
            startup_notice=needs_startup,
        )
    else:
        subject, html_body, text_body = build_status_email("startup", creators=config.creators)

    try:
        send_email(subject=subject, html_body=html_body, text_body=text_body)
    except EmailError as exc:
        log.error("required notification failed; state will NOT be committed: %s", exc)
        return False

    if notifications:
        log.info("digest sent: %d new, %d edited", total_new, total_edited)
    return True


def _maybe_send_heartbeat(settings: Settings, meta: dict[str, Any], config: Config, now: datetime) -> None:
    if not settings.heartbeat.enabled:
        return

    last_signal = _latest_signal(meta)
    due = True
    if last_signal:
        elapsed = now - datetime.fromisoformat(last_signal)
        due = elapsed >= timedelta(hours=settings.heartbeat.interval_hours)

    if due:
        sent = _send_notice(
            "heartbeat",
            creator_count=len(config.creators),
            total_runs=meta.get("total_runs", 0),
            last_digest_at=meta.get("last_digest_at"),
        )
        if sent:
            meta["last_heartbeat_at"] = now.isoformat()


@dataclass
class RunResults:
    notifications: list[Notification]
    had_failures: bool
    failed_creators: list[str]
    recovered_creators: list[str]


def _collect_results(
    creators: list[Creator],
    pending_state: dict[str, Any],
    settings: Settings,
    initial_import_notify: bool,
) -> RunResults:
    """Fetch every configured creator and gather what needs reporting.

    Mutates pending_state in place (via _process_creator) but never
    touches email or the on-disk state file itself.
    """
    notifications: list[Notification] = []
    had_failures = False
    failed_creators: list[str] = []
    recovered_creators: list[str] = []

    for creator in creators:
        result = _process_creator(creator, pending_state, settings, initial_import_notify)

        if result.failed:
            had_failures = True
            if result.just_failed:
                failed_creators.append(creator.name)
            continue

        if result.just_recovered:
            recovered_creators.append(creator.name)
        if result.new_posts:
            notifications.append(Notification(creator, result.new_posts, NotificationKind.NEW))
        if result.edited_posts:
            notifications.append(Notification(creator, result.edited_posts, NotificationKind.EDITED))

    return RunResults(notifications, had_failures, failed_creators, recovered_creators)


def process(args: argparse.Namespace) -> int:
    try:
        config = load_config(CONFIG_PATH)
    except ConfigError as exc:
        log.error("invalid configuration: %s", exc)
        return 1

    state = load_state(STATE_PATH)
    settings = config.settings

    initial_import_notify = (
        args.notify_existing if args.notify_existing is not None else settings.initial_import_notify
    )

    # Work on a deep copy so a required-notification failure can't
    # accidentally commit newly discovered posts.
    pending_state = json.loads(json.dumps(state))
    meta = pending_state["meta"]

    results = _collect_results(config.creators, pending_state, settings, initial_import_notify)
    notifications = results.notifications
    had_failures = results.had_failures
    failed_creators = results.failed_creators
    recovered_creators = results.recovered_creators

    total_new = sum(len(n.posts) for n in notifications if n.kind is NotificationKind.NEW)
    total_edited = sum(len(n.posts) for n in notifications if n.kind is NotificationKind.EDITED)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    needs_startup = settings.startup_email and not meta.get("welcomed", False)

    if notifications or needs_startup:
        sent = _send_required_notification(
            notifications, config, needs_startup, total_new, total_edited
        )
        if not sent:
            return 1  # pending_state (with new posts) is deliberately discarded

        if notifications:
            meta["last_digest_at"] = now_iso
        if needs_startup:
            meta["welcomed"] = True
            meta["welcomed_at"] = now_iso
    else:
        _maybe_send_heartbeat(settings, meta, config, now)

    # Failure/recovery status is best-effort and never blocks a state commit.
    if settings.alert_on_failure and failed_creators:
        _send_notice("alert", failed=failed_creators)
    if settings.alert_on_failure and recovered_creators:
        _send_notice("recovered", recovered=recovered_creators)

    meta["total_runs"] = meta.get("total_runs", 0) + 1
    meta["last_run_at"] = now_iso
    if not had_failures:
        meta["last_success_at"] = now_iso

    save_state(STATE_PATH, pending_state)

    if had_failures:
        log.error("one or more creators failed; successful state was committed and failures will retry")
        return 1

    log.info("run completed successfully")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Pawchive creators and email new posts.")
    parser.add_argument(
        "--notify-existing",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override bootstrap behavior and notify about currently existing posts",
    )
    args = parser.parse_args()
    return process(args)


if __name__ == "__main__":
    raise SystemExit(main())
