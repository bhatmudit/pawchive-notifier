# Changelog

Notable fixes and refactors, newest first. This is mostly here for
archaeology if something ever seems off and you want to know when/why it
changed — day-to-day use of the notifier doesn't require reading this.

## CI / infra

- Added `.github/dependabot.yml` for weekly pip + GitHub Actions update PRs.
- Added `.github/workflows/test.yml` to run the test suite on every push/PR
  to `main`, separate from the scheduled `monitor.yml` job.
- Bumped `actions/checkout` and `actions/setup-python` to v7, `requests` to
  2.34.2, `pytest` to 9.1.1.

## Reliability

- `pawchive.fetch_creator_posts` no longer loops forever if the API
  misbehaves (e.g. ignores the pagination offset). It aborts with a clear
  `PawchiveError` after `MAX_PAGES` (400) pages instead of hanging until
  the job's own timeout kills it hours later with no alert sent.
- Digest emails are capped at `notifier.MAX_POSTS_PER_SECTION` (25)
  rendered posts per creator, with an "...and N more" note. A creator
  posting a huge backlog at once could previously produce an oversized
  email that fails to send and then retries with the same oversized email
  forever. Nothing is dropped from `state.json`, only from the email body.
- Missing/invalid `RESEND_API_KEY` or `NOTIFICATION_EMAIL` now fails the
  run immediately, instead of only being discovered the first time an
  email actually needs sending (which, with `startup_email` disabled,
  could be silently broken for a long time).
- `state.prune_known_posts` caps remembered posts per creator (1500 by
  default) so `data/state.json` — and the git history it's committed
  into — doesn't grow forever.
- `pawchive.py` reuses a single `requests.Session` across all page fetches
  in a run instead of opening a fresh connection per request, and honors a
  `Retry-After` header on 429 responses instead of always using the fixed
  exponential backoff.
- Heartbeat scheduling no longer crashes on a corrupted or hand-edited
  `state.json` timestamp — an unparseable or timezone-naive value is
  logged and treated as "no signal" instead of raising.
- A post's *first* edit (no previous `edited` timestamp) is now correctly
  reported when `notify_edits` is on — previously silently dropped.
- Malformed numeric settings (e.g. non-numeric `max_preview_chars`) now
  raise a clear `ConfigError` instead of an unhandled `ValueError`.
- Duplicate `(service, id)` creator entries in `creators.json` are
  rejected at load time — two such entries would otherwise silently share
  one `state.json` key.
- Fixed a Windows-incompatible `strftime("%-d")` call in date formatting.
- Requests to the Pawchive API send an identifying `User-Agent`.

## Code organization

- `constants.py` and `post.py`: the Pawchive domain (previously hardcoded
  independently in `pawchive.py` and `notifier.py`) and raw post-dict
  field names (previously read as bare `post["id"]`, `post.get("published")`,
  etc. in multiple places) each now live in exactly one place.
- `notifier.Notification` (a dataclass) replaced `(Creator, list[dict],
  str)` tuples; `NotificationKind`/`StatusKind` enums replaced `"new"` /
  `"edited"` / `"startup"` / etc. magic strings.
- `main.process()` delegates the per-creator fetch loop to a
  `_collect_results()` helper for readability.
