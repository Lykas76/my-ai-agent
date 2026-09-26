# API v1

Base URL: http://127.0.0.1:8000/api/v1 for local development only.
All routes except health/ready require exactly one Authorization: Bearer header.
A user token is provisioned by the trusted local administrator, not through public signup.
Login validates that existing token. Token rotation returns a new raw token once and
revokes the old one; losing the response requires local administrator recovery.
Tokens expire after 30 days by default; local SecurityStore.issue_token accepts 60..2592000
seconds. Disabled users, revoked and expired tokens are rejected.

POST bodies must be JSON objects, UTF-8, Content-Length 1..65536 bytes, no transfer
encoding. Browser Origin requests are denied. Extra fields are rejected. In particular
chat does not accept user_id; identity is server-owned.
Success: {"ok":true,"result":{...}}. Health and readiness use {"status":"ok"/"ready"}.
Errors: {"error":"safe message", "code":"optional_stable_code"}.
401 unauthorized, 403 denied/not-owned confirmation or session, 400 invalid input,
413 body too large, 429 rate limited, 500/503 unavailable. Never automatically retry
a chat, tool, token rotation or confirmation execution after an ambiguous failure.

| Method | Path | Request / result |
|---|---|---|
| GET | /health | Liveness, public |
| GET | /ready | SQLite connection readiness, public; not external-service health |
| GET | /status | user_id, status, token_expires_at |
| POST | /auth/login | {} -> same identity/status |
| POST | /auth/token | {} -> user_id, token_id, token (one-time rotation) |
| POST | /auth/revoke | {} -> revoke current token |
| POST | /chat | message:string, optional session_id:string |
| GET | /sessions | items: at most 100 sessions, newest update first |
| POST | /sessions | {} -> new persistent session |
| GET | /sessions/{id}/history | items: last 100 messages, chronological |
| GET | /confirmations | items: up to 100 pending/approved, expiry checked |
| GET | /confirmations/{id} | exact stored action and lifecycle metadata |
| POST | /confirmations/{id}/approve | {} -> approve only |
| POST | /confirmations/{id}/cancel | {} -> cancel pending/approved |
| POST | /confirmations/{id}/execute | {} -> execute stored action once |
| GET | /tasks | items: at most 100 owned tasks |
| POST | /tasks | tool, arguments, next_run_at; optional session_id, interval_seconds, retry_limit |
| POST | /tasks/{id}/enabled | enabled:boolean |
| GET | /tasks/{id}/runs | items: last 100 runs with status and optional confirmation_id |
| GET | /reminders | owned tasks using reminders.notify |
| POST | /reminders | text, next_run_at; optional interval_seconds |
| GET | /notifications | at most 100 private reminder inbox entries |

Lists are bounded snapshots, not a full paginated archive in this MVP.
Legacy POST /requests remains authenticated and compatible with Blocks 1–5.

## Chat and confirmation example (no credentials)

```json
{"message":"/tool android.action {}"}
```

With the sensitive permission granted, result contains status=confirmation_required,
confirmation_id, session_id, tool, permission and expires_at.
Fetch confirmation details before approval: arguments and their hash, creation/expiry,
current lifecycle status. POST approve {}, then separately POST execute {}.
Clients cannot submit replacement arguments, tool, user or session to execute.

## Reminder

```json
{"text":"Take a break","next_run_at":"2030-01-01T12:00:00+02:00"}
```

The user needs reminders.notify/write. The scheduler worker must be running.
The action writes a private inbox entry, not an Android OS notification.

## Task model

task_id/user_id/session_id/tool/arguments/enabled/next_run_at/last_run_at/
interval_seconds/retry_limit/attempts/running_run/created_at.
Timestamps use ISO 8601 with timezone. Tool and arguments cannot be updated in place:
disable the old task and create a new one. Sensitive confirmation always binds one
specific run's exact arguments; a permission grant is never inferred from a task.

Run model: run_id, scheduled_at, started_at, finished_at, status,
confirmation_id. running after a restart means uncertain and quarantined.
