# Run on Windows

Python 3.10+ is required. The core backend and tests use the standard library.
Run commands from the repository root. No system installation is performed by scripts.

1. Optional isolated Python environment:
   `python -m venv .venv`, then `.\.venv\Scripts\Activate.ps1`.
2. Default provider is offline. Environment variables may be set in the current
   PowerShell process; an ignored .env is loaded only if optional python-dotenv is
   installed. .env.example contains names only and is not a usable credential file.
3. `python -m api.admin create-user` -> retain returned user_id.
4. `python -m api.admin issue-token --user-id <user_id>` -> token shown once.
   Enter it in Android's masked login field. Do not put it into command history,
   Git, screenshots, support logs or source code.
5. Explicit local grants, only for capabilities you want:
   `python -m api.admin grant --user-id <user_id> --tool notes.get --permission read`
   `python -m api.admin grant --user-id <user_id> --tool notes.put --permission write`
   `python -m api.admin grant --user-id <user_id> --tool reminders.notify --permission write`
   Optional stub demonstration:
   `python -m api.admin grant --user-id <user_id> --tool android.action --permission sensitive`
6. `.\scripts\start.ps1 api` or `python -B -m api.server`.
7. In a separate console: `.\scripts\start.ps1 scheduler`.
   Ctrl+C/SIGTERM stops accepting new work and closes owned resources. In-flight
   trusted tool code must return within its deadline for prompt shutdown.
8. Backup: `python -m runtime.backup data/assistant.sqlite3 backups/snapshot.sqlite3`.
   Destination must be new. Protect backup permissions like the original database.
   Restore manually with all API/worker/bot processes stopped; keep the original copy.

## Optional remote LLM

Set ASSISTANT_PROVIDER=openai, ASSISTANT_LLM_MODEL to a model supported by your
provider, OPENAI_API_KEY through a private environment/config source.
OPENAI_BASE_URL defaults to https://api.openai.com/v1; custom endpoints must be HTTPS.
ASSISTANT_LLM_TIMEOUT defaults to 20 seconds (1..60); ASSISTANT_LLM_RETRIES defaults to
1 (0..2). No real key is supplied. Running the real provider sends bounded user memory,
history and messages to that configured service; obtain user consent before using
personal information. Live integration was not tested here.

## Optional Telegram

Install only into the virtual environment when ready:
`python -m pip install -r requirements-telegram.txt`.
Set BOT_TOKEN privately. A bot is never launched by the API or tests.
Bind a verified Telegram account using the local administrator console:
`python -m clients.telegram --bind <numeric_telegram_id> --user-id <local_user_id>`.
Then `python -m clients.telegram`. Commands: /start, /status, /confirmations, /tasks.
Only private chat is accepted. Approve and Execute are separate buttons. Telegram
content crosses Telegram's service boundary. The bot dependency and live delivery
were not verified in this environment.

## Deployment assumptions

Backend binds 127.0.0.1 only. Do NOT publish the built-in HTTPServer or forward its
unencrypted port to the public Internet/LAN. For Android USB development use
`adb reverse tcp:8000 tcp:8000`; device connects to http://127.0.0.1:8000 in debug.
An emulator can use http://10.0.2.2:8000. A real remote deployment needs a reviewed
TLS reverse proxy, authenticated network access, hardened hosting, backups and
appropriate rate/connection limits. Do not disable Android certificate validation.
No blanket CORS allowlist is supplied. Forwarded-For is deliberately not trusted;
the in-process rate limiter is per peer IP (default 60/minute), resets on restart and
is not a replacement for a reverse proxy. Backend is single-threaded.

SQLite and backups contain private history; they are not encrypted by the app.
Restrict filesystem access and use device/full-disk encryption. Hashes protect
API credentials in storage but cannot protect a compromised host. Revocation is
checked on each operation, not a rollback of work already executing.

## Verification

Only after all changes:
`python -B -m unittest discover -s tests -v`
`python -B scripts/scan_secrets.py`
`git diff --check`

See ARCHITECTURE.md for at-most-once, crash recovery and timeout limitations.
