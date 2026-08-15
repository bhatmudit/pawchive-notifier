import argparse
import json

import main
from notifier import EmailError


def _write_config(tmp_path, **settings):
    path = tmp_path / "creators.json"
    path.write_text(
        json.dumps(
            {
                "settings": settings,
                "creators": [{"service": "patreon", "id": "1", "name": "Creator One"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _args(notify_existing=None):
    return argparse.Namespace(notify_existing=notify_existing)


def _patch_paths(monkeypatch, tmp_path, config_path):
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "STATE_PATH", tmp_path / "state.json")


def _patch_posts(monkeypatch, posts):
    monkeypatch.setattr(main, "fetch_creator_posts", lambda *a, **k: posts)


def test_bootstrap_run_sends_startup_email_and_commits_state(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path, startup_email=True)
    _patch_paths(monkeypatch, tmp_path, config_path)
    _patch_posts(monkeypatch, [{"id": "1", "title": "Existing", "published": "2026-01-01T00:00:00Z"}])

    sent = []
    monkeypatch.setattr(
        main, "send_email", lambda **kwargs: sent.append(kwargs["subject"])
    )

    rc = main.process(_args())

    assert rc == 0
    assert sent == ["[Pawchive] Notifier is up and running"]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["meta"]["welcomed"] is True
    entry = state["creators"]["patreon:1"]
    assert entry["bootstrapped"] is True
    assert "1" in entry["posts"]


def test_new_post_after_bootstrap_sends_digest(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path, startup_email=False)
    _patch_paths(monkeypatch, tmp_path, config_path)

    # First run: bootstrap, no notification expected (startup_email off).
    _patch_posts(monkeypatch, [{"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z"}])
    monkeypatch.setattr(main, "send_email", lambda **kwargs: None)
    assert main.process(_args()) == 0

    # Second run: one new post shows up -> digest email required.
    _patch_posts(
        monkeypatch,
        [
            {"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z"},
            {"id": "2", "title": "New", "published": "2026-01-02T00:00:00Z"},
        ],
    )
    sent = []
    monkeypatch.setattr(main, "send_email", lambda **kwargs: sent.append(kwargs["subject"]))
    assert main.process(_args()) == 0
    assert sent == ["[Pawchive] Creator One — 1 new post"]

    state = json.loads((tmp_path / "state.json").read_text())
    assert set(state["creators"]["patreon:1"]["posts"]) == {"1", "2"}


def test_digest_failure_does_not_commit_new_post(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path, startup_email=False)
    _patch_paths(monkeypatch, tmp_path, config_path)

    _patch_posts(monkeypatch, [{"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z"}])
    monkeypatch.setattr(main, "send_email", lambda **kwargs: None)
    assert main.process(_args()) == 0

    _patch_posts(
        monkeypatch,
        [
            {"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z"},
            {"id": "2", "title": "New", "published": "2026-01-02T00:00:00Z"},
        ],
    )

    def failing_send_email(**kwargs):
        raise EmailError("resend is down")

    monkeypatch.setattr(main, "send_email", failing_send_email)

    rc = main.process(_args())
    assert rc == 1

    # Post "2" must NOT have been committed - it should be retried next run.
    state = json.loads((tmp_path / "state.json").read_text())
    assert set(state["creators"]["patreon:1"]["posts"]) == {"1"}


def test_first_edit_on_a_post_is_detected(monkeypatch, tmp_path):
    # Regression test: a post's *first* edit (old "edited" was null,
    # new "edited" is now set) must be reported. An earlier version of
    # _find_edited_posts required the old "edited" value to be truthy,
    # which silently swallowed a post's first-ever edit notification.
    config_path = _write_config(tmp_path, startup_email=False, notify_edits=True)
    _patch_paths(monkeypatch, tmp_path, config_path)

    _patch_posts(
        monkeypatch,
        [{"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z", "edited": None}],
    )
    monkeypatch.setattr(main, "send_email", lambda **kwargs: None)
    assert main.process(_args()) == 0

    _patch_posts(
        monkeypatch,
        [
            {
                "id": "1",
                "title": "Old",
                "published": "2026-01-01T00:00:00Z",
                "edited": "2026-01-02T00:00:00Z",
            }
        ],
    )
    sent = []
    monkeypatch.setattr(main, "send_email", lambda **kwargs: sent.append(kwargs["subject"]))
    assert main.process(_args()) == 0
    assert sent == ["[Pawchive] Creator One — 1 edited post"]


def test_edits_not_reported_when_notify_edits_disabled(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path, startup_email=False, notify_edits=False)
    _patch_paths(monkeypatch, tmp_path, config_path)

    _patch_posts(
        monkeypatch,
        [{"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z", "edited": None}],
    )
    monkeypatch.setattr(main, "send_email", lambda **kwargs: None)
    assert main.process(_args()) == 0

    _patch_posts(
        monkeypatch,
        [
            {
                "id": "1",
                "title": "Old",
                "published": "2026-01-01T00:00:00Z",
                "edited": "2026-01-02T00:00:00Z",
            }
        ],
    )
    sent = []
    monkeypatch.setattr(main, "send_email", lambda **kwargs: sent.append(kwargs["subject"]))
    assert main.process(_args()) == 0
    assert sent == []  # no digest - nothing new, edits not being tracked


def test_heartbeat_sent_on_first_run_with_no_prior_signal(monkeypatch, tmp_path):
    # With no last_heartbeat_at/last_digest_at/welcomed_at recorded yet,
    # a heartbeat is due immediately rather than waiting a full interval.
    config_path = _write_config(
        tmp_path, startup_email=False, heartbeat={"enabled": True, "interval_hours": 1}
    )
    _patch_paths(monkeypatch, tmp_path, config_path)
    _patch_posts(monkeypatch, [{"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z"}])

    sent = []
    monkeypatch.setattr(main, "send_email", lambda **kwargs: sent.append(kwargs["subject"]))
    assert main.process(_args()) == 0
    assert sent == ["[Pawchive] Still watching"]

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["meta"]["last_heartbeat_at"] is not None


def test_heartbeat_not_sent_again_before_interval_elapses(monkeypatch, tmp_path):
    config_path = _write_config(
        tmp_path, startup_email=False, heartbeat={"enabled": True, "interval_hours": 1}
    )
    _patch_paths(monkeypatch, tmp_path, config_path)
    _patch_posts(monkeypatch, [{"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z"}])

    sent = []
    monkeypatch.setattr(main, "send_email", lambda **kwargs: sent.append(kwargs["subject"]))
    assert main.process(_args()) == 0  # first run: heartbeat due immediately, interval starts now
    assert sent == ["[Pawchive] Still watching"]

    sent.clear()
    assert main.process(_args()) == 0  # immediately again - not due yet
    assert sent == []


def test_heartbeat_disabled_never_sends(monkeypatch, tmp_path):
    config_path = _write_config(
        tmp_path, startup_email=False, heartbeat={"enabled": False, "interval_hours": 1}
    )
    _patch_paths(monkeypatch, tmp_path, config_path)
    _patch_posts(monkeypatch, [{"id": "1", "title": "Old", "published": "2026-01-01T00:00:00Z"}])

    sent = []
    monkeypatch.setattr(main, "send_email", lambda **kwargs: sent.append(kwargs["subject"]))
    assert main.process(_args()) == 0
    assert main.process(_args()) == 0
    assert sent == []


def test_invalid_config_fails_fast_without_state_changes(monkeypatch, tmp_path):
    config_path = tmp_path / "creators.json"
    config_path.write_text(json.dumps({"creators": [{"id": "1"}]}), encoding="utf-8")  # missing service
    _patch_paths(monkeypatch, tmp_path, config_path)

    rc = main.process(_args())

    assert rc == 1
    assert not (tmp_path / "state.json").exists()
