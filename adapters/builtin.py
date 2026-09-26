"""Local notes plus safe placeholders. No external side effects."""
import json
from typing import Protocol
from tools.registry import Tool, ToolResult
from tools.schema import object_schema
from security.confirmations import canonical_arguments


class Adapter(Protocol):
    def execute(self, arguments, context) -> str: ...


class StubAdapter:
    def __init__(self, name):
        self.name = name

    def execute(self, arguments, context):
        context.remaining()
        return json.dumps({"status": "not_connected", "adapter": self.name})


def register_builtin(registry, memory=None, *, include_gmail_stub=True):
    text = {"type": "string", "minLength": 1, "maxLength": 1000}
    key = {"type": "string", "minLength": 1, "maxLength": 100}
    def note_put(args, context):
        context.remaining()
        canonical_arguments(args)  # reject recognizable credential input
        connection = context.access.store._connection
        with connection:
            connection.execute(
                "INSERT INTO user_memory(user_id,key,value) VALUES(?,?,?) "
                "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                (context.access.user_id, "note:" + args["key"], args["text"]))
        return ToolResult("notes.put", "Note saved")
    def note_get(args, context):
        context.remaining()
        connection = context.access.store._connection
        query = args["key"].strip()

        row = connection.execute(
            "SELECT value FROM user_memory WHERE user_id=? AND key=?",
            (context.access.user_id, "note:" + query),
        ).fetchone()

        if row:
            return row[0]

        pattern = "%" + query + "%"
        rows = connection.execute(
            "SELECT key, value FROM user_memory "
            "WHERE user_id=? AND key LIKE 'note:%' "
            "AND (key LIKE ? OR value LIKE ?) "
            "ORDER BY key LIMIT 5",
            (context.access.user_id, pattern, pattern),
        ).fetchall()

        if not rows:
            return "Note not found"

        return "\n".join(row[1] for row in rows)
    def notify(args, context):
        from uuid import uuid4
        from memory.sessions import utc_now
        canonical_arguments(args)
        context.remaining()
        with context.access.store._connection:
            context.access.store._connection.execute("INSERT INTO user_memory VALUES(?,?,?)",
                (context.access.user_id, "notification:" + utc_now() + ":" + str(uuid4()), args["text"]))
        return "Reminder added to private inbox"
    def reminder_schedule(args, context):
        if memory is None:
            raise ValueError("Scheduler unavailable")
        context.remaining()
        canonical_arguments(args)

        from scheduler.store import Scheduler

        task = Scheduler(memory, registry).create(
            context.access.user_id,
            {
                "tool": "reminders.notify",
                "arguments": {"text": args["text"]},
                "next_run_at": args["next_run_at"],
            },
        )

        return json.dumps(
            {
                "status": "scheduled",
                "task_id": task["task_id"],
                "next_run_at": task["next_run_at"],
                "text": args["text"],
            },
            ensure_ascii=False,
        )

    registry.register(Tool(
        "reminders.notify",
        notify,
        "Internal delivery of a due reminder to the private inbox",
        "write",
        schema=object_schema({"text": text}, ("text",)),
        contextual=True,
        expose_to_model=False,
    ), enabled=True)

    if memory is not None:
        registry.register(Tool(
            "reminders.schedule",
            reminder_schedule,
            "Schedule a reminder. next_run_at must be a timezone-aware ISO 8601 timestamp, "
            "for example 2026-09-22T09:00:00+03:00.",
            "write",
            schema=object_schema(
                {
                    "text": text,
                    "next_run_at": {
                        "type": "string",
                        "minLength": 20,
                        "maxLength": 64,
                    },
                },
                ("text", "next_run_at"),
            ),
            contextual=True,
        ), enabled=True)
    registry.register(Tool("notes.put", note_put, "Save a private note", "write",
                           schema=object_schema({"key": key, "text": text}, ("key", "text")), contextual=True), enabled=True)
    registry.register(Tool("notes.get", note_get, "Read a private note", "read",
                           schema=object_schema({"key": key}, ("key",)), contextual=True, retry_safe=True), enabled=True)
    stubs = [
        ("drive.list", "read"),
        ("calendar.list", "read"),
        ("web.search", "read"),
        ("telegram.send", "sensitive"),
        ("android.action", "sensitive"),
    ]
    if include_gmail_stub:
        stubs.insert(1, ("gmail.list", "read"))

    for name, permission in stubs:
        stub = StubAdapter(name)
        registry.register(Tool(name, stub.execute, "Stub: no external service connected", permission,
                               confirmation_schema={}, schema=object_schema(), contextual=True,
                               retry_safe=permission == "read"), enabled=True)
