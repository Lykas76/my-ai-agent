# Local MVP architecture

Android is the primary client. Telegram is an optional additional channel.
SQLite is the only persistent store. Nothing connects to external services on import.

## Components and trust boundaries

```text
Android -> Bearer HTTP /api/v1 -> V1API -> per-user Assistant
Telegram -> private-account binding -> V1API -> per-user Assistant
Assistant -> bounded memory/history -> LocalProvider or OpenAICompatibleProvider
structured tool call -> ToolRegistry -> permissions -> confirmation -> adapter
Scheduler worker -> atomic SQLite claim -> same ToolRegistry -> adapter
```

Only server-created AccessContext supplies identity. A model cannot issue tokens,
grant permissions, approve confirmations or create scheduler tasks through model tools.
The typed tool catalogue contains only registered, enabled, explicitly granted tools.
One model tool call per turn is allowed; arbitrary model text is never executable.
Explicit legacy /tool commands remain supported.

## Storage and migrations

Legacy memory, sessions, messages, users, permissions and confirmations are preserved.
schema_migrations version 1 adds finite token expiry, tasks, task_runs, Telegram links
and update claims. Migration uses BEGIN IMMEDIATE and is additive. A newer version
is rejected. Existing legacy token expiry is creation time + 30 days: old credentials
may need replacement by the local administrator.
Each process/thread owns its SQLite connection. Foreign keys and a 10-second busy
timeout are enabled. No shared connection is used by a threaded HTTP server.
The HTTP server deliberately handles requests serially; provider latency therefore
blocks later API requests. This is a local MVP, not a multi-tenant public server.

## Confirmation invariants (Block 5 preserved)

Random confirmation ID; exact user/session/tool/permission/canonical arguments binding;
default TTL 120 seconds (configurable 1..3600); pending -> approved -> consumed,
pending/approved -> cancelled or expired. Get/list/consume lazily persist expiry.
Approval does not extend TTL and does not execute the handler.
Consume and its audit event commit in one BEGIN IMMEDIATE transaction before handler
entry. At most one consumer can cross that boundary, including independent connections.
Handler failure/crash leaves consumed. A retry with the same ID is denied.
A new attempt needs a new confirmation and independent checking of any prior effects.
This is at-most-once invocation, not exactly-once successful external delivery.

## Scheduler semantics

Tasks accept timezone-aware ISO 8601 timestamps; storage normalizes UTC. Recurrence
is a fixed interval of 60..31536000 seconds, measured from completion, not cron or a
wall-clock/DST calendar. No backlog catch-up. One-time tasks disable after execution.
BEGIN IMMEDIATE atomically inserts a unique run and sets running_run before invocation.
A crash leaves a running record and quarantines that task. Restart restores pending
tasks but never steals/replays uncertain in-flight work. Inspect its effects and create
a new task manually if appropriate. There is no automatic unsafe lease recovery.
Completion records success/failed/confirmation_required and timestamps.
Retries (0..3, delayed 30/60/90 seconds) are only for registered retry_safe read tools.
A disabled task stays disabled even if an in-flight worker finishes.
Sensitive tasks create a confirmation and pause. Approve and execute via the client;
recurring sensitive tasks require manual re-enable and a new confirmation for each run.
An already completed one-time task cannot be re-enabled.
Results are not copied into the run log; private reminders are written to an inbox.

## Tool contract and timeout

Tool declares JSON Schema subset (object, string, integer, number, boolean, enum,
required, bounds); extra arguments are rejected. All built-in tools are typed.
ToolResult is the unified internal/result API value; execute() retains its legacy string
return for existing callers. Older trusted custom registrations may omit schema but
are never advertised to the LLM or accepted as scheduled tasks.
Sensitive arguments also use the original restricted confirmation schema: no arbitrary
strings/credentials; use numeric references or enums when implementing real actions.
Contextual adapters receive server identity and a monotonic deadline. Network transport
has explicit socket timeouts. Deadline violations produce a safe uncertain-result error,
never an automatic retry of writes. Deadlines are cooperative: arbitrary in-process
Python cannot be safely killed. Trusted handlers must check remaining() and propagate
the remaining budget to I/O; a blocking legacy handler may return after its deadline.
A process-isolated tool runner is required before allowing untrusted plugins.

## Providers and adapters

LocalProvider is deterministic/offline, the default.
OpenAI-compatible Chat Completions transport is opt-in; HTTPS only, no redirects,
model/timeouts configured externally, bounded input/output, one structured call.
Retries only on explicit HTTP 429/502/503/504 before tools (max 2); ambiguous network
failures and requests after tool execution are not retried. Retries can still have
provider billing implications; set retries=0 if desired.
All external service keys remain in environment, never in SQLite.
Notes and reminder inbox are real local adapters with user isolation.
Drive/Gmail/Calendar/web/Telegram-send/Android-action are explicit not_connected stubs.
The Telegram client itself can send replies when deliberately launched with credentials;
the sensitive telegram.send tool remains a stub.
STT/TTS are interfaces with unavailable implementations, not working speech recognition.

## Client limitations

Android skeleton: Java Views, network models, login/chat/sessions/history/confirmations/
reminders, memory-only credential, debug loopback HTTP only, no secrets in saved state.
No APK was built in this environment; SDK/JDK/Gradle installation is manual.
No background Android push notifications or offline queue. Reminder inbox is polled
when opened. Voice implementation, Keystore-backed opt-in token persistence, UI
instrumentation tests and accessibility polish are future work.
Telegram: administrator binding, private chats only, persistent session and update
claim. Claimed updates are not replayed after a crash; replies can be lost. There is
no exactly-once Telegram delivery guarantee, and only one polling bot instance per
database/bot token should be used.
