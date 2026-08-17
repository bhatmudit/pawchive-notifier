"""Email building and sending via Resend."""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import requests

from config import Creator
from constants import SITE_BASE_URL
from post import post_content, post_display_date, post_id, post_title

RESEND_URL = "https://api.resend.com/emails"

# Cap how many posts render inside a single digest email. Without this, a
# creator posting a large backlog at once (or a Pawchive bug returning way
# more "new" posts than expected) produces an arbitrarily large HTML
# payload. If that trips Resend's size limit, send_email fails, the
# transactional-state logic in main.py discards the pending state so
# nothing is lost - but the *next* run produces the exact same oversized
# email and fails the same way, forever. Capping the rendered list breaks
# that potential deadlock; the full set is still recorded in state.json,
# only the email is truncated.
MAX_POSTS_PER_SECTION = 25


class EmailError(RuntimeError):
    """Raised when an email fails to build or send."""


class NotificationKind(str, Enum):
    """What kind of change a batch of posts represents in a digest."""

    NEW = "new"
    EDITED = "edited"

    @property
    def label(self) -> str:
        return "new" if self is NotificationKind.NEW else "edited"


@dataclass(frozen=True)
class Notification:
    """One creator's batch of new or edited posts for a single digest.

    Replaces the previous ``(Creator, list[dict], str)`` tuple so call
    sites read as ``n.creator`` / ``n.posts`` / ``n.kind`` instead of
    unlabeled ``n[0]`` / ``n[1]`` / ``n[2]`` indexing.
    """

    creator: Creator
    posts: list[dict[str, Any]]
    kind: NotificationKind


def _strip_html(value: str) -> str:
    """Convert simple HTML content into readable plain text."""
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _format_date(value: str | None) -> str:
    if not value:
        return "Unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return value
    # Avoid the "%-d" (no leading zero) strftime extension: it works on
    # Linux/macOS glibc but is not portable to Windows, so the day is
    # formatted manually instead.
    return f"{parsed:%B} {parsed.day}, {parsed:%Y %H:%M} UTC"


def _post_url(creator: Creator, post: dict[str, Any]) -> str:
    return f"{SITE_BASE_URL}/{creator.service}/user/{creator.id}/post/{post_id(post)}"


def _wrap_html(body: str) -> str:
    style = "font-family:Arial,sans-serif;line-height:1.5;color:#222"
    return f'<!doctype html><html><body style="{style}">{body}</body></html>'


def _posts_sort_key(post: dict[str, Any]) -> str:
    return post_display_date(post) or ""


def build_email(
    notifications: list[Notification],
    preview_chars: int = 300,
    startup_notice: bool = False,
) -> tuple[str, str, str]:
    """Build the digest email covering new/edited posts across creators."""
    new_count = sum(len(n.posts) for n in notifications if n.kind is NotificationKind.NEW)
    edited_count = sum(len(n.posts) for n in notifications if n.kind is NotificationKind.EDITED)
    total = new_count + edited_count

    subject = "[Pawchive] " + _digest_subject(notifications, new_count, edited_count, total)

    html_sections: list[str] = []
    text_lines: list[str] = []

    if startup_notice:
        html_sections.append(
            "<p style='color:#0a7d32'><strong>&check; Notifier is up and "
            "running.</strong> This is the first email it has sent.</p>"
        )
        text_lines.append("Notifier is up and running. This is the first email it has sent.\n")

    for notification in notifications:
        if not notification.posts:
            continue
        html_sections.append(_render_creator_section_html(notification, preview_chars))
        text_lines.append(
            f"{notification.creator.name} ({notification.creator.service.upper()}) — "
            f"{len(notification.posts)} {notification.kind.label}"
        )
        shown, hidden = _split_for_rendering(notification.posts)
        for post in shown:
            text_lines.extend(_render_post_text(notification.creator, post, notification.kind))
        if hidden:
            text_lines.append(f"...and {hidden} more (see state.json / Pawchive for the rest)")

    html_body = _wrap_html(
        f"<h1>Pawchive Updates — {total} post{'s' if total != 1 else ''}</h1>"
        + "".join(html_sections)
    )
    text_body = "\n".join(text_lines)
    return subject, html_body, text_body


def _digest_subject(
    notifications: list[Notification],
    new_count: int,
    edited_count: int,
    total: int,
) -> str:
    if len(notifications) == 1:
        notification = notifications[0]
        count = len(notification.posts)
        return (
            f"{notification.creator.name} — {count} {notification.kind.label} "
            f"post{'s' if count != 1 else ''}"
        )

    bits = []
    if new_count:
        bits.append(f"{new_count} new")
    if edited_count:
        bits.append(f"{edited_count} edited")
    return " · ".join(bits) + f" post{'s' if total != 1 else ''}"


def _split_for_rendering(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Sort posts newest-first and cap how many get rendered into the email.

    Returns (posts_to_render, count_hidden). Nothing is dropped from
    state - this only bounds email size (see MAX_POSTS_PER_SECTION).
    """
    ordered = sorted(posts, key=_posts_sort_key, reverse=True)
    return ordered[:MAX_POSTS_PER_SECTION], max(0, len(ordered) - MAX_POSTS_PER_SECTION)


def _render_creator_section_html(notification: Notification, preview_chars: int) -> str:
    creator = notification.creator
    heading = (
        f"<h2>{html.escape(creator.name)} "
        f"<small>({html.escape(creator.service.upper())}) — "
        f"{len(notification.posts)} {notification.kind.label}</small></h2>"
    )
    shown, hidden = _split_for_rendering(notification.posts)
    articles = "".join(
        _render_post_html(creator, post, notification.kind, preview_chars) for post in shown
    )
    if hidden:
        articles += (
            f"<p><em>...and {hidden} more not shown here "
            "(all are recorded; see Pawchive for the full list).</em></p>"
        )
    return heading + articles


def _render_post_html(
    creator: Creator, post: dict[str, Any], kind: NotificationKind, preview_chars: int
) -> str:
    title = post_title(post)
    url = _post_url(creator, post)
    date = _format_date(post_display_date(post))
    badge = "" if kind is NotificationKind.NEW else " <span style='color:#996600'>(edited)</span>"

    preview_html = ""
    full_text = _strip_html(post_content(post))
    preview = full_text[:preview_chars]
    if preview:
        ellipsis = "…" if len(full_text) > preview_chars else ""
        preview_html = f"<p>{html.escape(preview)}{ellipsis}</p>"

    return (
        "<article>"
        f"<h3>{html.escape(title)}{badge}</h3>"
        f"<p><strong>Published:</strong> {html.escape(date)}</p>"
        f"{preview_html}"
        f"<p><a href='{html.escape(url, quote=True)}'>View on Pawchive &rarr;</a></p>"
        "</article>"
    )


def _render_post_text(creator: Creator, post: dict[str, Any], kind: NotificationKind) -> list[str]:
    title = post_title(post)
    if kind is NotificationKind.EDITED:
        title += " (edited)"
    date = _format_date(post_display_date(post))
    url = _post_url(creator, post)
    return ["", title, f"Published: {date}", f"View on Pawchive: {url}"]


class StatusKind(str, Enum):
    """The non-digest status emails. Mirrors NotificationKind's pattern.

    build_status_email() still takes a plain string (main.py's call sites
    read more naturally as _send_notice("alert", ...) than with an enum
    import at every call site), but dispatch internally goes through this
    enum instead of a bare dict of string literals, so a typo'd kind is
    still one clear ValueError instead of a silent KeyError-shaped bug.
    """

    STARTUP = "startup"
    HEARTBEAT = "heartbeat"
    ALERT = "alert"
    RECOVERED = "recovered"


def _build_startup_email(creators: list[Creator]) -> tuple[str, str, str]:
    items_html = "".join(
        f"<li>{html.escape(c.name)} ({html.escape(c.service)})</li>" for c in creators
    )
    items_text = "\n".join(f"- {c.name} ({c.service})" for c in creators)
    return (
        "[Pawchive] Notifier is up and running",
        _wrap_html(
            "<h1>&check; Pawchive Notifier is running</h1>"
            "<p>Setup is complete. You will only hear from it again when "
            "there's something to report (or, if enabled, an occasional "
            "heartbeat).</p>"
            f"<p><strong>Monitoring {len(creators)} creator(s):</strong></p>"
            f"<ul>{items_html}</ul>"
        ),
        "Pawchive Notifier is running.\n\n"
        f"Monitoring {len(creators)} creator(s):\n{items_text}\n\n"
        "You will only hear from it again when there's something to report.",
    )


def _build_heartbeat_email(
    creator_count: int, total_runs: int, last_digest_at: str | None
) -> tuple[str, str, str]:
    last_digest_display = last_digest_at or "none yet"
    return (
        "[Pawchive] Still watching",
        _wrap_html(
            "<h1>Still watching</h1>"
            f"<p>Monitoring {creator_count} creator(s), {total_runs} runs so far.</p>"
            f"<p>Last new post: {html.escape(last_digest_display)}</p>"
            "<p>No news is good news — this is just a periodic check-in.</p>"
        ),
        "Still watching.\n\n"
        f"Monitoring {creator_count} creator(s), {total_runs} runs so far.\n"
        f"Last new post: {last_digest_display}",
    )


def _build_alert_email(failed: list[str]) -> tuple[str, str, str]:
    items_html = "".join(f"<li>{html.escape(name)}</li>" for name in failed)
    items_text = "\n".join(f"- {name}" for name in failed)
    return (
        "[Pawchive] Creator fetch failure",
        _wrap_html(
            "<h1 style='color:#b00020'>Fetch failure</h1>"
            "<p>The following creator(s) have just started failing:</p>"
            f"<ul>{items_html}</ul>"
            "<p>They will keep being retried automatically. You will not "
            "receive repeated alerts while the failure continues.</p>"
        ),
        "Fetch failure.\n\n"
        f"Creators that just started failing:\n{items_text}\n\n"
        "They will be retried automatically.",
    )


def _build_recovered_email(recovered: list[str]) -> tuple[str, str, str]:
    items_html = "".join(f"<li>{html.escape(name)}</li>" for name in recovered)
    items_text = "\n".join(f"- {name}" for name in recovered)
    return (
        "[Pawchive] Creator recovered",
        _wrap_html(
            "<h1 style='color:#0a7d32'>Back to normal</h1>"
            "<p>The following creator(s) are fetching successfully again:</p>"
            f"<ul>{items_html}</ul>"
        ),
        "Creator recovery.\n\n"
        f"Creators back to normal:\n{items_text}",
    )


_STATUS_BUILDERS = {
    StatusKind.STARTUP: _build_startup_email,
    StatusKind.HEARTBEAT: _build_heartbeat_email,
    StatusKind.ALERT: _build_alert_email,
    StatusKind.RECOVERED: _build_recovered_email,
}


def build_status_email(kind: str, **context: Any) -> tuple[str, str, str]:
    """Build one of the non-digest status emails: startup, heartbeat, alert, recovered."""
    try:
        resolved = StatusKind(kind)
    except ValueError as exc:
        raise ValueError(f"unknown status email kind: {kind}") from exc
    return _STATUS_BUILDERS[resolved](**context)


def send_email(
    *,
    subject: str,
    html_body: str,
    text_body: str,
    api_key: str | None = None,
    recipient: str | None = None,
    sender: str | None = None,
    timeout: int = 30,
) -> None:
    api_key = api_key or os.environ.get("RESEND_API_KEY")
    recipient = recipient or os.environ.get("NOTIFICATION_EMAIL")
    sender = sender or os.environ.get(
        "RESEND_FROM_EMAIL", "Pawchive Notifier <onboarding@resend.dev>"
    )

    if not api_key:
        raise EmailError("RESEND_API_KEY is not set")
    if not recipient:
        raise EmailError("NOTIFICATION_EMAIL is not set")

    try:
        response = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": sender,
                "to": [recipient],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise EmailError(f"Resend request failed: {exc}") from exc

    if response.status_code >= 300:
        raise EmailError(f"Resend returned HTTP {response.status_code}: {response.text[:500]}")
