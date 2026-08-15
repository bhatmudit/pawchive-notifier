from config import Creator
from notifier import Notification, NotificationKind, build_email, build_status_email


CREATOR = Creator(service="patreon", id="123", name="Some Creator")


def _post(id_="1", title="Hello", content="<p>Hi <b>there</b></p>", published="2026-01-01T00:00:00Z"):
    return {"id": id_, "title": title, "content": content, "published": published}


def _notify(creator, posts, kind):
    return Notification(creator, posts, kind)


def test_build_email_single_new_post_subject():
    subject, html_body, text_body = build_email(
        [_notify(CREATOR, [_post()], NotificationKind.NEW)]
    )

    assert subject == "[Pawchive] Some Creator — 1 new post"
    assert "Hello" in html_body
    assert "Hi there" in html_body  # HTML stripped from preview
    assert "Hello" in text_body
    assert "https://pawchive.pw/patreon/user/123/post/1" in text_body


def test_build_email_multiple_creators_subject_combines_counts():
    other = Creator(service="patreon", id="456", name="Another Creator")
    subject, _, _ = build_email(
        [
            _notify(CREATOR, [_post("1"), _post("2")], NotificationKind.NEW),
            _notify(other, [_post("3")], NotificationKind.EDITED),
        ]
    )
    assert subject == "[Pawchive] 2 new · 1 edited posts"


def test_build_email_startup_notice_included():
    _, html_body, text_body = build_email(
        [_notify(CREATOR, [_post()], NotificationKind.NEW)], startup_notice=True
    )
    assert "first email" in html_body
    assert "first email" in text_body


def test_build_email_preview_truncated():
    long_content = "<p>" + ("word " * 200) + "</p>"
    _, html_body, _ = build_email(
        [_notify(CREATOR, [_post(content=long_content)], NotificationKind.NEW)],
        preview_chars=20,
    )
    assert "…" in html_body


def test_build_email_edited_post_gets_badge():
    _, html_body, text_body = build_email(
        [_notify(CREATOR, [_post()], NotificationKind.EDITED)]
    )
    assert "(edited)" in html_body
    assert "(edited)" in text_body


def test_build_email_preview_exactly_at_limit_has_no_ellipsis():
    # Content that is exactly preview_chars long after HTML stripping
    # should not get a trailing ellipsis - there's nothing left to cut.
    content = "<p>" + ("a" * 20) + "</p>"
    _, html_body, _ = build_email(
        [_notify(CREATOR, [_post(content=content)], NotificationKind.NEW)],
        preview_chars=20,
    )
    assert "…" not in html_body


def test_build_email_empty_content_has_no_preview_paragraph():
    _, html_body, _ = build_email(
        [_notify(CREATOR, [_post(content="")], NotificationKind.NEW)]
    )
    # No stray empty <p></p> from an empty/whitespace-only preview.
    assert "<p></p>" not in html_body


def test_build_email_escapes_html_in_title():
    malicious = _post(title="<script>alert(1)</script>")
    _, html_body, _ = build_email([_notify(CREATOR, [malicious], NotificationKind.NEW)])
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_build_email_skips_notification_with_no_posts():
    # A Notification with an empty posts list (defensive case) should
    # not add an empty section or crash the sort/formatting logic.
    subject, html_body, text_body = build_email(
        [
            _notify(CREATOR, [], NotificationKind.NEW),
            _notify(CREATOR, [_post()], NotificationKind.NEW),
        ]
    )
    assert "Hello" in html_body
    assert subject  # still builds a sensible subject from the real batch


def test_build_status_email_startup():
    subject, html_body, text_body = build_status_email("startup", creators=[CREATOR])
    assert "up and running" in subject.lower()
    assert "Some Creator" in html_body
    assert "Some Creator" in text_body


def test_build_status_email_unknown_kind_raises():
    import pytest

    with pytest.raises(ValueError):
        build_status_email("not-a-real-kind")
