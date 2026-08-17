# Pawchive Notifier

Pawchive → GitHub Actions → Python → Resend → email.

## Setup

1. Create a GitHub repository and push this project.
2. Edit `config/creators.json` to add monitored creators.
3. Add repository Actions secrets:
   - `RESEND_API_KEY`
   - `NOTIFICATION_EMAIL`
   - optional `RESEND_FROM_EMAIL`
4. Run **Pawchive monitor** manually once with `notify_existing` disabled.
5. Existing posts are bootstrapped into `data/state.json` without notification.
6. Subsequent scheduled runs check every 10 minutes.

## Project layout

```
src/
  config.py    # loads + validates config/creators.json into typed dataclasses
  pawchive.py  # Pawchive API client (pagination, retry/backoff)
  notifier.py  # email building + sending (Resend)
  state.py     # on-disk state: known posts per creator, run metadata
  main.py      # orchestration: fetch -> decide notifications -> send -> commit
tests/         # pytest suite covering all of the above
```

Run tests locally with:

```
pip install -r requirements-dev.txt
pytest
```

## Notifications

- New posts: digest email.
- Edited posts: optional via `notify_edits`.
- Startup: one-time email, enabled by default.
- Fetch failure: one alert when failures begin.
- Recovery: one email when fetching returns to normal.
- Heartbeat: disabled by default; optionally enable with `heartbeat.enabled`.

State metadata is automatically migrated if an older `state.json` is present.

## Refactor notes (latest revision)

- `constants.py` and `post.py` are new: the Pawchive domain (previously
  hardcoded independently in both `pawchive.py` and `notifier.py`) and the
  raw post-dict field names (previously read as bare `post["id"]`,
  `post.get("published")`, etc. in both `main.py` and `notifier.py`) each
  now live in exactly one place.
- Fixed: `fetch_creator_posts` no longer loops forever if the Pawchive API
  misbehaves (e.g. ignores the pagination offset). It aborts with a clear
  `PawchiveError` after `pawchive.MAX_PAGES` (400) pages instead of hanging
  until the CI job's own timeout kills it hours later with no alert sent.
- Fixed: a digest email is now capped at `notifier.MAX_POSTS_PER_SECTION`
  (25) rendered posts per creator, with an "...and N more" note. A creator
  posting a huge backlog at once could previously produce an oversized
  email that fails to send, which (per the transactional-state design)
  would retry with the same oversized email forever — a permanent stuck
  state. Nothing is dropped from `state.json`, only from the email body.
- Fixed: missing/invalid `RESEND_API_KEY` or `NOTIFICATION_EMAIL` now fails
  the run immediately, the same way a bad `creators.json` does. Previously
  this was only discovered the first time an email needed sending, which
  (with `startup_email` disabled) could be silently broken for a long time.
- Added: `state.prune_known_posts` caps remembered posts per creator (1500
  by default) so `data/state.json` — and the git history it's committed
  into — doesn't grow forever. Safe because pagination already stops as
  soon as it sees a known post id, so pruning old, unreachable entries
  can't cause them to be re-notified later.
- Requests to the Pawchive API now send a `User-Agent` identifying this
  bot, rather than Python's default one.
- `notifier.StatusKind` replaces the bare `"startup"/"heartbeat"/"alert"/
  "recovered"` string dispatch dict with an enum internally (mirroring
  `NotificationKind`); `build_status_email()`'s public signature is
  unchanged and still raises `ValueError` on an unrecognized kind.

## Refactor notes (this revision)

- `pawchive.py` now reuses a single `requests.Session` across all page
  fetches in a run instead of opening a fresh connection per request
  (connection pooling/keep-alive), and honors a `Retry-After` header on
  429 responses instead of always using the fixed exponential backoff.
- Heartbeat scheduling (`_latest_signal`) no longer crashes on a
  corrupted or hand-edited `state.json` timestamp - an unparseable or
  timezone-naive value is logged and treated as "no signal" (heartbeat
  fires immediately) instead of raising deep inside scheduling.
- Added `.github/dependabot.yml` for weekly pip + GitHub Actions update
  PRs, since `requirements.txt` uses loose ranges with no lockfile and
  nothing previously surfaced new releases automatically.

## Earlier refactor notes

- `notifier.Notification` (a small dataclass) replaces the previous
  `(Creator, list[dict], str)` tuples passed around between `main.py` and
  `notifier.py`; a `NotificationKind` enum replaces the `"new"`/`"edited"`
  magic strings.
- Fixed: a post's *first* edit (no previous `edited` timestamp) is now
  correctly reported when `notify_edits` is on. Previously it was silently
  dropped because the old check required the *old* `edited` value to be
  truthy.
- Fixed: malformed numeric settings (e.g. a non-numeric
  `max_preview_chars` or `heartbeat.interval_hours`) now raise a clear
  `ConfigError` instead of an unhandled `ValueError`. Zero/negative
  `heartbeat.interval_hours` and negative `max_preview_chars` are also
  rejected.
- Added: duplicate `(service, id)` creator entries in `creators.json` are
  now rejected at load time, since two such entries would silently share
  one `state.json` key.
- Fixed a Windows-incompatible `strftime("%-d")` call in date formatting.
- `main.process()` now delegates the per-creator fetch loop to a
  `_collect_results()` helper for readability.

## Configuration errors fail the run

`config/creators.json` is validated once, up front. A missing/malformed
`service` or `id` on any creator entry causes the run to fail immediately
(visible as a failed Action) rather than emailing a repeat alert every
10 minutes — fix the config and re-run.

## State commits are skipped when nothing meaningful changed

Every run updates `meta.last_run_at` and `meta.total_runs`, but the
workflow ignores those two fields when deciding whether to commit
`data/state.json`. A commit only happens when something that actually
matters changed — new/edited posts, a failure/recovery transition, a
digest or heartbeat timestamp, etc. This keeps the repo's history from
filling up with a commit every 10 minutes forever.

## Behavior notes

- Heartbeats use the newest of the last heartbeat, last digest, and startup timestamps.
- Failure/recovery tracking is per creator.
- Failure alerts are sent only when a creator transitions from healthy to failing.
- Recovery alerts are sent only when a previously failing creator succeeds again.
- Digest delivery is transactional: if Resend fails, newly discovered posts remain pending for the next run.
- Status emails are best-effort and never prevent state commits.
- Transient fetch errors (network errors, HTTP 429/5xx) are retried up to 3 times with
  exponential backoff before a creator is marked as failing. Non-transient errors
  (404, other 4xx) fail immediately without retrying.
