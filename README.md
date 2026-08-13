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

## Notifications

- New posts: digest email.
- Edited posts: optional via `notify_edits`.
- Startup: one-time email, enabled by default.
- Fetch failure: one alert when failures begin.
- Recovery: one email when fetching returns to normal.
- Heartbeat: disabled by default; optionally enable with `heartbeat.enabled`.

State metadata is automatically migrated if an older `state.json` is present.


## v4 behavior

- Heartbeats use the newest of the last heartbeat, last digest, and startup timestamps.
- Failure/recovery tracking is per creator.
- Failure alerts are sent only when a creator transitions from healthy to failing.
- Recovery alerts are sent only when a previously failing creator succeeds again.
- The redundant global failure counter is removed; old state files are migrated automatically.
- Digest delivery is transactional: if Resend fails, newly discovered posts remain pending for the next run.
- Status emails are best-effort and never prevent state commits.
