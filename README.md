# Pawchive Notifier

A small bot that watches a list of Pawchive creators and emails you when
they post. Runs on a schedule via GitHub Actions — no server to maintain.

```
Pawchive API → GitHub Actions (every 10 min) → Python → Resend → your inbox
```

## Setup

1. Push this repo to GitHub.
2. Edit `config/creators.json` — add the creators you want to follow (see
   below).
3. Add these repository secrets (Settings → Secrets and variables →
   Actions):
   - `RESEND_API_KEY`
   - `NOTIFICATION_EMAIL` — where alerts go
   - `RESEND_FROM_EMAIL` — optional, defaults to Resend's shared address
4. Run the **Pawchive monitor** workflow manually once (Actions tab →
   Run workflow), leaving `notify_existing` unchecked. This bootstraps
   `data/state.json` with each creator's current posts *without* emailing
   you about all of their back catalog.
5. From then on it runs itself every 10 minutes via cron.

## Adding or removing creators

Edit `config/creators.json`:

```json
{
  "creators": [
    { "service": "patreon", "id": "6038973", "name": "Warby Picus" }
  ]
}
```

`id` is the numeric creator ID from the Pawchive URL, not the display
name. A newly added creator is bootstrapped silently on its first run —
same as the initial setup — so you won't get a flood of "new post"
emails for their entire history.

## Settings

Also in `config/creators.json`, under `"settings"`:

| Setting | Default | What it does |
|---|---|---|
| `notify_edits` | `false` | Email when an already-known post is edited, not just when a new one appears. |
| `initial_import_notify` | `false` | If `true`, send notifications for a creator's existing posts on first bootstrap instead of importing them silently. |
| `startup_email` | `true` | One-time "notifier is up and running" email the first time it ever runs. |
| `alert_on_failure` | `true` | Email once when a creator's feed starts failing to fetch (not every failed run — see below). |
| `max_preview_chars` | `300` | How much of a post's content to include in the email preview. |
| `heartbeat.enabled` | `false` | Periodic "still alive, nothing broke" email, independent of any actual posts. |
| `heartbeat.interval_hours` | `168` (1 week) | How often the heartbeat fires, if enabled. |

## What you'll actually get emailed about

- **New posts** — a digest, batched if a creator posts more than once
  between runs.
- **Edited posts** — only if `notify_edits` is on.
- **Startup** — once, the first time the notifier ever runs (if
  `startup_email` is on).
- **Fetch failure** — once, when a creator's feed *starts* failing
  (network errors, Pawchive down, etc.), not on every failed attempt.
  You won't get spammed every 10 minutes while something's broken.
- **Recovery** — once, when a previously-failing creator starts working
  again.
- **Heartbeat** — optional, see above.

If Resend itself fails to send a digest, nothing is lost — the newly
found posts stay pending and get retried (and re-emailed, not silently
dropped) on the next run.

## Project layout

```
src/
  config.py    # loads + validates config/creators.json
  pawchive.py  # Pawchive API client (pagination, retry/backoff)
  notifier.py  # builds and sends emails via Resend
  state.py     # on-disk state: known posts per creator, run metadata
  post.py      # shared accessors for raw post dicts
  constants.py # shared URLs/constants
  main.py      # orchestration: fetch -> decide notifications -> send -> commit
tests/         # pytest suite covering all of the above
```

## Development

```
pip install -r requirements-dev.txt
pytest
```

Tests run automatically on every push/PR via `.github/workflows/test.yml`.
See `CHANGELOG.md` for the history of what's changed and why, if you're
ever debugging something and wondering "wait, when did that happen."

## A few non-obvious behaviors worth knowing

- **State commits are skipped when nothing meaningful changed.** Every
  run touches bookkeeping fields like `last_run_at`, but a git commit only
  happens when something real changed (new posts, a failure/recovery
  transition, etc.) — so the repo's history doesn't fill up with a commit
  every 10 minutes forever.
- **A bad `creators.json` fails the run immediately** (visible as a failed
  Action), rather than emailing you a repeat alert every 10 minutes.
- **Transient errors are retried**: network errors and HTTP 429/5xx get up
  to 3 attempts with backoff before a creator is marked as failing. A 404
  or other 4xx fails immediately, no retry.
