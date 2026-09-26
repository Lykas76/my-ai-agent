"""At-most-once claims. Crashed in-flight tasks are deliberately quarantined."""
import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from memory.sessions import utc_now
from security.store import AccessContext
from security.confirmations import canonical_arguments
from tools.schema import validate
from tools.registry import ConfirmationRequired


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timezone-aware ISO timestamp required")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError("Timezone-aware ISO timestamp required") from None


class Scheduler:
    def __init__(self, memory, registry):
        self.memory, self.registry = memory, registry
        self.connection = memory._connection

    def create(self, user_id, payload):
        allowed = {"tool", "arguments", "next_run_at", "interval_seconds", "retry_limit", "session_id"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("Invalid task fields")
        name = payload.get("tool")
        if not isinstance(name, str):
            raise ValueError("Invalid tool")
        tool = self.registry._tools.get(name)
        if (not tool or name not in self.registry._enabled or tool.schema is None or
                not self.memory.security.is_allowed(user_id, name, tool.permission)):
            raise ValueError("Tool execution denied")
        arguments = payload.get("arguments", {})
        validate(arguments, tool.schema)
        canonical, _ = canonical_arguments(arguments)
        if tool.permission == "sensitive":
            from security.confirmations import validate_schema
            validate_schema(arguments, tool.confirmation_schema)
        session_id = payload.get("session_id")
        if session_id is not None:
            self.memory.sessions.get_session(user_id, session_id)
        due = timestamp(payload.get("next_run_at"))
        interval = payload.get("interval_seconds")
        retry = payload.get("retry_limit", 0)
        if interval is not None and (type(interval) is not int or not 60 <= interval <= 31536000):
            raise ValueError("Interval must be 60..31536000 seconds")
        if type(retry) is not int or not 0 <= retry <= 3 or (retry and not (tool.permission == "read" and tool.retry_safe)):
            raise ValueError("Retries require an explicitly safe read tool")
        task_id = str(uuid4())
        with self.connection:
            self.connection.execute(
                "INSERT INTO tasks(task_id,user_id,session_id,tool,arguments,enabled,next_run_at,interval_seconds,retry_limit,created_at) "
                "VALUES(?,?,?,?,?,1,?,?,?,?)", (task_id,user_id,session_id,name,canonical,due,interval,retry,utc_now()))
        return self.get(user_id, task_id)

    def get(self, user_id, task_id):
        row = self.connection.execute("SELECT * FROM tasks WHERE user_id=? AND task_id=?", (user_id,task_id))
        columns = [item[0] for item in row.description]
        value = row.fetchone()
        if value is None:
            raise ValueError("Task not found")
        result = dict(zip(columns, value))
        result["arguments"] = json.loads(result["arguments"])
        result["enabled"] = bool(result["enabled"])
        return result

    def list(self, user_id):
        ids = self.connection.execute("SELECT task_id FROM tasks WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user_id,))
        return [self.get(user_id, row[0]) for row in ids.fetchall()]

    def runs(self, user_id, task_id):
        self.get(user_id, task_id)
        rows = self.connection.execute("SELECT run_id,scheduled_at,started_at,finished_at,status,confirmation_id "
                                       "FROM task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT 100", (task_id,))
        keys = [item[0] for item in rows.description]
        return [dict(zip(keys,row)) for row in rows]

    def enable(self, user_id, task_id, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        task = self.get(user_id,task_id)
        if enabled and task["last_run_at"] is not None and task["interval_seconds"] is None:
            raise ValueError("Create a new one-time task after execution")
        with self.connection:
            self.connection.execute("UPDATE tasks SET enabled=? WHERE user_id=? AND task_id=?", (int(enabled),user_id,task_id))
        return self.get(user_id,task_id)

    def claim(self, now=None):
        now = timestamp(now or utc_now())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT task_id,user_id,next_run_at FROM tasks WHERE enabled=1 AND running_run IS NULL "
                "AND next_run_at<=? ORDER BY next_run_at,task_id LIMIT 1", (now,)).fetchone()
            if row is None:
                self.connection.commit()
                return None
            task_id, user_id, due = row
            run_id = str(uuid4())
            self.connection.execute("INSERT INTO task_runs(run_id,task_id,scheduled_at,started_at,status) VALUES(?,?,?,?,'running')",
                                    (run_id,task_id,due,now))
            self.connection.execute("UPDATE tasks SET running_run=?,last_run_at=? WHERE task_id=?", (run_id,now,task_id))
            self.connection.commit()
            return self.get(user_id,task_id)
        except BaseException:
            self.connection.rollback()
            raise

    def run_one(self, now=None):
        task = self.claim(now)
        if task is None:
            return None
        status, confirmation_id = "success", None
        try:
            self.registry.execute_result(task["tool"],task["arguments"],
                access=AccessContext(task["user_id"],self.memory.security),session_id=task["session_id"])
        except ConfirmationRequired as exc:
            status, confirmation_id = "confirmation_required", exc.confirmation["confirmation_id"]
        except Exception:
            status = "failed"
        now = timestamp(now or utc_now())
        tool = self.registry._tools.get(task["tool"])
        retry = (status == "failed" and task["attempts"] < task["retry_limit"] and tool is not None
                 and tool.permission == "read" and tool.retry_safe
                 and self.memory.security.is_allowed(task["user_id"],tool.name,tool.permission))
        interval = task["interval_seconds"]
        enabled = bool(retry or (interval and status != "confirmation_required"))
        seconds = 30 * (task["attempts"] + 1) if retry else (interval or 0)
        next_run = (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat()
        with self.connection:
            self.connection.execute("UPDATE task_runs SET status=?,finished_at=?,confirmation_id=? WHERE run_id=?",
                                    (status,now,confirmation_id,task["running_run"]))
            # A concurrent disable must survive completion.
            self.connection.execute("UPDATE tasks SET enabled=MIN(enabled,?),next_run_at=?,attempts=?,running_run=NULL WHERE task_id=?",
                                    (int(enabled),next_run,task["attempts"]+1 if retry else 0,task["task_id"]))
        return {"run_id": task["running_run"], "status": status, "confirmation_id": confirmation_id}
