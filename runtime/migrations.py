"""Additive transactionally versioned migrations after legacy tables exist."""
from datetime import datetime, timedelta

VERSION = 1


def migrate(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)")
        current = connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
        if current > VERSION:
            raise ValueError("Database schema is newer than this application")
        if current < 1:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(api_tokens)")}
            if "expires_at" not in columns:
                connection.execute("ALTER TABLE api_tokens ADD COLUMN expires_at TEXT")
                for token_id, created in connection.execute("SELECT token_id, created_at FROM api_tokens").fetchall():
                    expiry = (datetime.fromisoformat(created) + timedelta(days=30)).isoformat()
                    connection.execute("UPDATE api_tokens SET expires_at=? WHERE token_id=?", (expiry, token_id))
            connection.execute("""CREATE TABLE tasks(
                task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
                session_id TEXT, tool TEXT NOT NULL, arguments TEXT NOT NULL,
                enabled INTEGER NOT NULL, next_run_at TEXT NOT NULL, last_run_at TEXT,
                interval_seconds INTEGER, retry_limit INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                running_run TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(user_id,session_id) REFERENCES sessions(user_id,session_id))""")
            connection.execute("""CREATE TABLE task_runs(
                run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                scheduled_at TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL, confirmation_id TEXT,
                UNIQUE(task_id,scheduled_at))""")
            connection.execute("CREATE INDEX tasks_due ON tasks(enabled,next_run_at)")
            connection.execute("""CREATE TABLE telegram_links(
                telegram_id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), session_id TEXT,
                FOREIGN KEY(user_id,session_id) REFERENCES sessions(user_id,session_id))""")
            connection.execute("CREATE TABLE telegram_updates(update_id INTEGER PRIMARY KEY, claimed_at TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_migrations VALUES (1)")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
