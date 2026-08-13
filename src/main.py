from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from notifier import EmailError, build_email, build_status_email, send_email
from pawchive import PawchiveError, fetch_creator_posts
from state import get_creator_state, load_state, save_state

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "creators.json"
STATE_PATH = ROOT / "data" / "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("pawchive-notifier")


def load_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {CONFIG_PATH}: {exc}") from exc

    if not isinstance(config, dict) or not isinstance(config.get("creators"), list):
        raise RuntimeError("config/creators.json must contain a creators array")
    return config


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
    """Return the newest relevant timestamp for heartbeat scheduling."""
    timestamps = [
        meta.get("last_heartbeat_at"),
        meta.get("last_digest_at"),
        meta.get("welcomed_at"),
    ]
    timestamps = [value for value in timestamps if value]
    if not timestamps:
        return None
    return max(timestamps, key=lambda value: datetime.fromisoformat(value))


def process(args: argparse.Namespace) -> int:
    config = load_config()
    state = load_state(STATE_PATH)
    settings = config.get("settings", {})

    notify_edits = bool(settings.get("notify_edits", False))
    preview_chars = int(settings.get("max_preview_chars", 300))
    startup_email_enabled = bool(settings.get("startup_email", True))
    alert_on_failure = bool(settings.get("alert_on_failure", True))

    heartbeat_cfg = settings.get("heartbeat") or {}
    heartbeat_enabled = bool(heartbeat_cfg.get("enabled", False))
    heartbeat_interval_hours = float(heartbeat_cfg.get("interval_hours", 168))

    initial_import_notify = (
        args.notify_existing
        if args.notify_existing is not None
        else bool(settings.get("initial_import_notify", False))
    )

    notifications: list[
        tuple[dict[str, Any], list[dict[str, Any]], str]
    ] = []

    # Work on a deep copy so a required digest failure cannot accidentally
    # commit newly discovered posts.
    pending_state = json.loads(json.dumps(state))
    meta = pending_state["meta"]

    had_failures = False
    failed_creators: list[str] = []
    recovered_creators: list[str] = []

    for creator in config["creators"]:
        service = str(creator.get("service", "")).strip()
        creator_id = str(creator.get("id", "")).strip()
        name = str(creator.get("name") or f"{service}/{creator_id}")

        if not service or not creator_id:
            log.error("invalid creator entry: %r", creator)
            had_failures = True
            failed_creators.append(name)
            continue

        creator_cfg = {
            "service": service,
            "id": creator_id,
            "name": name,
        }

        current = get_creator_state(
            pending_state,
            service,
            creator_id,
        )
        known_posts: dict[str, Any] = current["posts"]
        known_ids = set(known_posts)
        bootstrap = not current["bootstrapped"]
        was_failing = current.get("consecutive_failures", 0) > 0

        try:
            posts = fetch_creator_posts(
                service,
                creator_id,
                known_ids=known_ids,
                bootstrap=bootstrap,
            )
        except PawchiveError as exc:
            log.error("%s: %s", name, exc)
            had_failures = True
            current["consecutive_failures"] = (
                current.get("consecutive_failures", 0) + 1
            )

            # Alert only on the transition into failure.
            if current["consecutive_failures"] == 1:
                failed_creators.append(name)
            continue

        log.info(
            "%s: fetched %d posts%s",
            name,
            len(posts),
            " (bootstrap)" if bootstrap else "",
        )

        if was_failing:
            recovered_creators.append(name)

        current["consecutive_failures"] = 0

        new_posts = [p for p in posts if str(p["id"]) not in known_ids]

        edited_posts: list[dict[str, Any]] = []
        if notify_edits and not bootstrap:
            for post in posts:
                pid = str(post["id"])
                old_edited = known_posts.get(pid, {}).get("edited")
                new_edited = post.get("edited")

                if (
                    pid in known_posts
                    and old_edited
                    and new_edited
                    and new_edited != old_edited
                ):
                    edited_posts.append(post)

        # Record successfully fetched posts in the pending state. This is
        # committed only after any required digest email succeeds.
        for post in posts:
            pid = str(post["id"])
            known_posts[pid] = {
                "edited": post.get("edited"),
                "published": post.get("published"),
            }

        current["bootstrapped"] = True

        if bootstrap and not initial_import_notify:
            log.info(
                "%s: bootstrap complete; %d existing posts remembered",
                name,
                len(posts),
            )
        else:
            if new_posts:
                notifications.append((creator_cfg, new_posts, "new"))
                log.info("%s: %d new posts", name, len(new_posts))

            if edited_posts:
                notifications.append((creator_cfg, edited_posts, "edited"))
                log.info("%s: %d edited posts", name, len(edited_posts))

    total_new = sum(
        len(posts)
        for _, posts, kind in notifications
        if kind == "new"
    )
    total_edited = sum(
        len(posts)
        for _, posts, kind in notifications
        if kind == "edited"
    )
    total_notifications = total_new + total_edited

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    needs_startup = (
        startup_email_enabled
        and not meta.get("welcomed", False)
    )

    # Required notification path. If the digest/startup email fails, do not
    # commit the pending state because doing so could permanently lose posts.
    if total_notifications or needs_startup:
        if total_notifications:
            subject, html_body, text_body = build_email(
                notifications,
                preview_chars=preview_chars,
                startup_notice=needs_startup,
            )
        else:
            subject, html_body, text_body = build_status_email(
                "startup",
                creators=[
                    {
                        "name": c.get("name")
                        or f"{c.get('service')}/{c.get('id')}",
                        "service": c.get("service"),
                    }
                    for c in config["creators"]
                ],
            )

        try:
            send_email(
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except EmailError as exc:
            log.error(
                "required notification failed; state will NOT be committed: %s",
                exc,
            )
            return 1

        if total_notifications:
            meta["last_digest_at"] = now_iso
            log.info(
                "digest sent: %d new, %d edited",
                total_new,
                total_edited,
            )

        if needs_startup:
            meta["welcomed"] = True
            meta["welcomed_at"] = now_iso

    elif heartbeat_enabled:
        last_signal = _latest_signal(meta)
        due = True

        if last_signal:
            elapsed = now - datetime.fromisoformat(last_signal)
            due = elapsed >= timedelta(hours=heartbeat_interval_hours)

        if due:
            ok = _send_notice(
                "heartbeat",
                creator_count=len(config["creators"]),
                total_runs=meta.get("total_runs", 0),
                last_digest_at=meta.get("last_digest_at"),
            )
            if ok:
                meta["last_heartbeat_at"] = now_iso

    # Failure/recovery status is per creator. Status emails are best effort and
    # never block a successful state commit.
    if alert_on_failure and failed_creators:
        _send_notice(
            "alert",
            failed=failed_creators,
        )

    if alert_on_failure and recovered_creators:
        _send_notice(
            "recovered",
            recovered=recovered_creators,
        )

    meta["total_runs"] = meta.get("total_runs", 0) + 1
    meta["last_run_at"] = now_iso

    if not had_failures:
        meta["last_success_at"] = now_iso

    save_state(STATE_PATH, pending_state)

    if had_failures:
        log.error(
            "one or more creators failed; successful state was committed "
            "and failures will retry"
        )
        return 1

    log.info("run completed successfully")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor Pawchive creators and email new posts."
    )
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
