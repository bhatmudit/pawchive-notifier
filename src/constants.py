"""Values shared across modules so there's exactly one place to change them.

Previously the Pawchive domain was hardcoded independently in both
pawchive.py (API base URL) and notifier.py (post links). A domain change
meant remembering to update two files; now there's one.
"""

from __future__ import annotations

PAWCHIVE_DOMAIN = "pawchive.pw"
API_BASE_URL = f"https://{PAWCHIVE_DOMAIN}/api/v1"
SITE_BASE_URL = f"https://{PAWCHIVE_DOMAIN}"

USER_AGENT = (
    "pawchive-notifier/1.0 "
    "(+https://github.com/bhatmudit/pawchive-notifier; unattended monitor bot; "
    "contact: simplelogin-newsletter.conform524@simplelogin.com)"
)
