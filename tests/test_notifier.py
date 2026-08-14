from config import Creator
from notifier import build_email, build_status_email


CREATOR = Creator(service="patreon", id="123", name="Some Creator")


def _post(id_="1", title="Hello", content="<p>Hi <b>there</b></p>", published="2026-01-01T00:00:00Z"):
    return {"id": id_, "title": title, "content": content, "published": published}


def test_build_email_single_new_post_subject():
    subject, html_body, text_body = build_email([(CREATOR, [_post()], "new")])

    assert subject == "[Pawchive] Some Creator — 1 new post"
    assert "Hello" in html_body
    assert "Hi there" in html_body  # HTML stripped from preview
    assert "Hello" in text_body
    assert "https://pawchive.pw/patreon/user/123/post/1" in text_body


def test_build_email_multiple_creators_subject_combines_counts():
    other = Creator(service="patreon", id="456", name="Another Creator")
    subject, _, _ = build_email(
        [
            (CREATOR, [_post("1"), _post("2")], "new"),
            (other, [_post("3")], "edited"),
        ]
    )
    assert subject == "[Pawchive] 2 new · 1 edited posts"


def test_build_email_startup_notice_included():
    _, html_body, text_body = build_email([(CREATOR, [_post()], "new")], startup_notice=True)
    assert "first email" in html_body
    assert "first email" in text_body


def test_build_email_preview_truncated():
    long_content = "<p>" + ("word " * 200) + "</p>"
    _, html_body, _ = build_email(
        [(CREATOR, [_post(content=long_content)], "new")], preview_chars=20
    )
    assert "…" in html_body


def test_build_status_email_startup():
    subject, html_body, text_body = build_status_email("startup", creators=[CREATOR])
    assert "up and running" in subject.lower()
    assert "Some Creator" in html_body
    assert "Some Creator" in text_body


def test_build_status_email_unknown_kind_raises():
    import pytest

    with pytest.raises(ValueError):
        build_status_email("not-a-real-kind")
