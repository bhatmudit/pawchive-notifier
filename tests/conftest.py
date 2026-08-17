import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _email_secrets(monkeypatch):
    """Most tests exercise behavior downstream of secrets being present;
    they stub out send_email itself rather than caring about Resend
    credentials. Set fake ones by default so main.process()'s upfront
    secrets check doesn't fail every test that doesn't care about it.
    Tests that specifically want to exercise missing secrets should
    monkeypatch.delenv() to override this.
    """
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("NOTIFICATION_EMAIL", "test@example.com")
