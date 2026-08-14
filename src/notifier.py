"""Email building and sending via Resend."""

from __future__ import annotations

import html
import os
import re
from datetime import datetime
from typing import Any

import requests

from config import Creator

RESEND_URL = "https://api.resend.com/emails"


class EmailError(RuntimeError):
    """Raised when an email fails to build or send."""


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
        return parsed.strftime("%B %-d, %Y %H:%M UTC")
    except (ValueError, TypeError):
        return value


def _post_url(creator: Creator, post: dict[str, Any]) -> str:
    return f"https://pawchive.pw/{creator.service}/user/{creator.id}/post/{post['id']}"


def _wrap_html(body: str) -> str:
    style = "font-family:Arial,sans-serif;line-height:1.5;color:#222"
    return f'<!doctype html><html><body style="{style}">{body}</body></html>'


def build_email(
    notifications: list[tuple[Creator, list[dict[str, Any]], str]],
    preview_chars: int = 300,
    startup_notice: bool = False,
) -> tuple[str, str, str]:
    """Build the digest email covering new/edited posts across creators."""
    new_count = sum(len(posts) for _, posts, kind in notifications if kind == "new")
    edited_count = sum(len(posts) for _, posts, kind in notifications if kind == "edited")
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

    for creator, posts, kind in notifications:
        if not posts:
            continue
        html_sections.append(_render_creator_section_html(creator, posts, kind, preview_chars))
        text_lines.append(f"{creator.name} ({creator.service.upper()}) — {len(posts)} {kind}")
        for post in sorted(posts, key=lambda p: p.get("published") or p.get("added") or ""):
            text_lines.extend(_render_post_text(creator, post, kind))

    html_body = _wrap_html(
        f"<h1>Pawchive Updates — {total} post{'s' if total != 1 else ''}</h1>"
        + "".join(html_sections)
    )
    text_body = "\n".join(text_lines)
    return subject, html_body, text_body


def _digest_subject(
    notifications: list[tuple[Creator, list[dict[str, Any]], str]],
    new_count: int,
    edited_count: int,
    total: int,
) -> str:
    if len(notifications) == 1:
        creator, posts, kind = notifications[0]
        label = "new" if kind == "new" else "edited"
        return f"{creator.name} — {len(posts)} {label} post{'s' if len(posts) != 1 else ''}"

    bits = []
    if new_count:
        bits.append(f"{new_count} new")
    if edited_count:
        bits.append(f"{edited_count} edited")
    return " · ".join(bits) + f" post{'s' if total != 1 else ''}"


def _render_creator_section_html(
    creator: Creator, posts: list[dict[str, Any]], kind: str, preview_chars: int
) -> str:
    label = "new" if kind == "new" else "edited"
    heading = (
        f"<h2>{html.escape(creator.name)} "
        f"<small>({html.escape(creator.service.upper())}) — {len(posts)} {label}</small></h2>"
    )
    articles = "".join(
        _render_post_html(creator, post, kind, preview_chars)
        for post in sorted(posts, key=lambda p: p.get("published") or p.get("added") or "")
    )
    return heading + articles


def _render_post_html(creator: Creator, post: dict[str, Any], kind: str, preview_chars: int) -> str:
    title = str(post.get("title") or "Untitled post")
    url = _post_url(creator, post)
    date = _format_date(post.get("published") or post.get("added"))
    badge = "" if kind == "new" else " <span style='color:#996600'>(edited)</span>"

    preview_html = ""
    full_text = _strip_html(str(post.get("content") or ""))
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


def _render_post_text(creator: Creator, post: dict[str, Any], kind: str) -> list[str]:
    title = str(post.get("title") or "Untitled post")
    if kind == "edited":
        title += " (edited)"
    date = _format_date(post.get("published") or post.get("added"))
    url = _post_url(creator, post)
    return ["", title, f"Published: {date}", f"View on Pawchive: {url}"]


def build_status_email(kind: str, **context: Any) -> tuple[str, str, str]:
    """Build one of the non-digest status emails: startup, heartbeat, alert, recovered."""
    builders = {
        "startup": _build_startup_email,
        "heartbeat": _build_heartbeat_email,
        "alert": _build_alert_email,
        "recovered": _build_recovered_email,
    }
    builder = builders.get(kind)
    if builder is None:
        raise ValueError(f"unknown status email kind: {kind}")
    return builder(**context)


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
