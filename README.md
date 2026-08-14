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
