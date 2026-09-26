import os
import sqlite3
import sys


def ping():
    return "PYTHON_INSIDE_ANDROID_OK"


def info():
    return {
        "status": "ok",
        "python": sys.version,
        "mode": "standalone_android"
    }


def sqlite_test(files_dir):
    db_path = os.path.join(str(files_dir), "assistant_mobile.db")
    connection = sqlite3.connect(db_path)

    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS mobile_memory (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )

        connection.execute(
            "INSERT OR REPLACE INTO mobile_memory (key, value) VALUES (?, ?)",
            ("standalone_test", "memory_on_phone_ok")
        )

        connection.commit()

        row = connection.execute(
            "SELECT value FROM mobile_memory WHERE key = ?",
            ("standalone_test",)
        ).fetchone()

        if row and row[0] == "memory_on_phone_ok":
            return "PYTHON_SQLITE_OK"

        return "PYTHON_SQLITE_FAILED"

    finally:
        connection.close()


def save_note(files_dir, text):
    import os
    import sqlite3
    from datetime import datetime

    text = str(text).strip()
    if not text:
        return "EMPTY"

    db_path = os.path.join(str(files_dir), "assistant_mobile.db")
    connection = sqlite3.connect(db_path)

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        connection.execute(
            "INSERT INTO notes (text, created_at) VALUES (?, ?)",
            (text, datetime.now().isoformat(timespec="minutes"))
        )

        connection.commit()
        return "SAVED"

    finally:
        connection.close()


def list_notes(files_dir):
    import os
    import sqlite3

    db_path = os.path.join(str(files_dir), "assistant_mobile.db")
    connection = sqlite3.connect(db_path)

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        rows = connection.execute(
            "SELECT text, created_at FROM notes ORDER BY id DESC LIMIT 30"
        ).fetchall()

        if not rows:
            return ""

        return "\n\n".join(
            f"{created_at}\n{text}"
            for text, created_at in rows
        )

    finally:
        connection.close()



def _ensure_chat_table(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)


def list_chat_history(files_dir, limit=40):
    import json
    import os
    import sqlite3

    db_path = os.path.join(str(files_dir), "assistant_mobile.db")
    connection = sqlite3.connect(db_path)

    try:
        _ensure_chat_table(connection)

        rows = connection.execute(
            """
            SELECT role, content, created_at
            FROM chat_messages
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),)
        ).fetchall()

        rows.reverse()

        return json.dumps(
            [
                {
                    "role": role,
                    "content": content,
                    "created_at": created_at
                }
                for role, content, created_at in rows
            ],
            ensure_ascii=False
        )

    finally:
        connection.close()


def mobile_ai_chat(api_key, message, files_dir):
    import json
    import os
    import sqlite3
    from datetime import datetime
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    api_key = str(api_key).strip()
    message = str(message).strip()
    files_dir = str(files_dir)

    if not api_key:
        return "OPENROUTER_KEY_MISSING"

    if not message:
        return "EMPTY_MESSAGE"

    db_path = os.path.join(files_dir, "assistant_mobile.db")
    connection = sqlite3.connect(db_path)

    try:
        _ensure_chat_table(connection)

        note_rows = connection.execute(
            """
            SELECT text, created_at
            FROM notes
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        history_rows = connection.execute(
            """
            SELECT role, content
            FROM chat_messages
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        history_rows.reverse()

    finally:
        connection.close()

    memory_items = [
        {
            "text": text,
            "created_at": created_at
        }
        for text, created_at in note_rows
    ]

    messages = [
        {
            "role": "system",
            "content": (
                "?? ???????????? AI-????????? ?? ?????????. "
                "??????? ?? ??????? ?????, ???? ???????????? ????? ??-??????. "
                "? ???? ???? ????????? ??????? ???????????? ? ??????? ?????????. "
                "????????? ?? ?????? ????? ??? ????????? ? ???????. "
                "?? ????????? ?????."
            )
        }
    ]

    for role, content in history_rows:
        if role in ("user", "assistant"):
            messages.append({
                "role": role,
                "content": content
            })

    messages.append({
        "role": "user",
        "content": json.dumps(
            {
                "message": message,
                "local_memory": memory_items
            },
            ensure_ascii=False
        )
    })

    payload = {
        "models": [
            "inclusionai/ling-3.0-flash-vl:free",
            "inclusionai/ling-3.0-flash-fin:free"
        ],
        "messages": messages,
        "max_completion_tokens": 1024
    }

    data = json.dumps(
        payload,
        ensure_ascii=False
    ).encode("utf-8")

    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=data,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "X-Title": "AI Assistant Android"
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read(1048576)
            result = json.loads(raw.decode("utf-8"))

        reply = result["choices"][0]["message"]["content"]

        if not isinstance(reply, str) or not reply.strip():
            return "AI_EMPTY_RESPONSE"

        reply = reply.strip()

        connection = sqlite3.connect(db_path)

        try:
            _ensure_chat_table(connection)

            now = datetime.now().isoformat(timespec="seconds")

            connection.execute(
                """
                INSERT INTO chat_messages(role, content, created_at)
                VALUES (?, ?, ?)
                """,
                ("user", message, now)
            )

            connection.execute(
                """
                INSERT INTO chat_messages(role, content, created_at)
                VALUES (?, ?, ?)
                """,
                ("assistant", reply, now)
            )

            connection.commit()

        finally:
            connection.close()

        return reply

    except HTTPError as exc:
        return "AI_HTTP_ERROR_" + str(exc.code)

    except URLError:
        return "AI_NETWORK_ERROR"

    except Exception:
        return "AI_RESPONSE_ERROR"


def _ensure_reminders_table(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS local_reminders (
            task_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            next_run_at TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)


def _reminder_item(task_id, text, next_run_at, enabled):
    return {
        "status": "ok",
        "task_id": task_id,
        "tool": "reminders.notify",
        "next_run_at": next_run_at,
        "enabled": bool(enabled),
        "arguments": {
            "text": text
        }
    }


def create_local_reminder(files_dir, text, next_run_at):
    import json
    import os
    import sqlite3
    import uuid
    from datetime import datetime, timezone

    text = str(text).strip()
    next_run_at = str(next_run_at).strip()

    if not text or not next_run_at:
        return json.dumps(
            {"status": "error", "error": "missing_fields"}
        )

    try:
        parsed = datetime.fromisoformat(
            next_run_at.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            return json.dumps(
                {"status": "error", "error": "timezone_required"}
            )

        if parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            return json.dumps(
                {"status": "error", "error": "time_is_past"}
            )

    except Exception:
        return json.dumps(
            {"status": "error", "error": "invalid_datetime"}
        )

    task_id = uuid.uuid4().hex
    db_path = os.path.join(
        str(files_dir),
        "assistant_mobile.db"
    )

    connection = sqlite3.connect(db_path)

    try:
        _ensure_reminders_table(connection)

        connection.execute(
            """
            INSERT INTO local_reminders
            (task_id, text, next_run_at, enabled, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (
                task_id,
                text,
                next_run_at,
                datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
            )
        )

        connection.commit()

    finally:
        connection.close()

    return json.dumps(
        _reminder_item(
            task_id,
            text,
            next_run_at,
            True
        ),
        ensure_ascii=False
    )


def list_local_reminders(files_dir):
    import json
    import os
    import sqlite3

    db_path = os.path.join(
        str(files_dir),
        "assistant_mobile.db"
    )

    connection = sqlite3.connect(db_path)

    try:
        _ensure_reminders_table(connection)

        rows = connection.execute(
            """
            SELECT task_id, text, next_run_at, enabled
            FROM local_reminders
            ORDER BY next_run_at ASC
            """
        ).fetchall()

        items = [
            _reminder_item(
                task_id,
                text,
                next_run_at,
                enabled
            )
            for task_id, text, next_run_at, enabled
            in rows
        ]

        return json.dumps(
            items,
            ensure_ascii=False
        )

    finally:
        connection.close()


def set_local_reminder_enabled(files_dir, task_id, enabled):
    import json
    import os
    import sqlite3

    db_path = os.path.join(
        str(files_dir),
        "assistant_mobile.db"
    )

    connection = sqlite3.connect(db_path)

    try:
        _ensure_reminders_table(connection)

        connection.execute(
            """
            UPDATE local_reminders
            SET enabled = ?
            WHERE task_id = ?
            """,
            (
                1 if enabled else 0,
                str(task_id)
            )
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT task_id, text, next_run_at, enabled
            FROM local_reminders
            WHERE task_id = ?
            """,
            (str(task_id),)
        ).fetchone()

        if row is None:
            return json.dumps(
                {"status": "error", "error": "not_found"}
            )

        return json.dumps(
            _reminder_item(*row),
            ensure_ascii=False
        )

    finally:
        connection.close()


def _save_chat_turn(files_dir, user_text, assistant_text):
    import os
    import sqlite3
    from datetime import datetime

    db_path = os.path.join(
        str(files_dir),
        "assistant_mobile.db"
    )

    connection = sqlite3.connect(db_path)

    try:
        _ensure_chat_table(connection)

        now = datetime.now().isoformat(
            timespec="seconds"
        )

        connection.execute(
            """
            INSERT INTO chat_messages
            (role, content, created_at)
            VALUES (?, ?, ?)
            """,
            ("user", user_text, now)
        )

        connection.execute(
            """
            INSERT INTO chat_messages
            (role, content, created_at)
            VALUES (?, ?, ?)
            """,
            ("assistant", assistant_text, now)
        )

        connection.commit()

    finally:
        connection.close()



def _parse_local_relative_reminder(message):
    import re
    from datetime import datetime, timedelta

    original = str(message).strip()

    # "??????? ??? ..." / "??????? ..."
    prefix_pattern = re.compile(
        r"^\s*\u043d\u0430\u043f\u043e\u043c\u043d\u0438"
        r"(?:\s+\u043c\u043d\u0435)?\s+",
        re.IGNORECASE
    )

    if not prefix_pattern.search(original):
        return None

    # "????? 5 ?????", "????? 2 ????"
    relative_pattern = re.compile(
        r"\b\u0447\u0435\u0440\u0435\u0437\s+(\d+)\s*"
        r"("
        r"\u043c\u0438\u043d\u0443\u0442\u0443|"
        r"\u043c\u0438\u043d\u0443\u0442\u044b|"
        r"\u043c\u0438\u043d\u0443\u0442|"
        r"\u043c\u0438\u043d|"
        r"\u0447\u0430\u0441|"
        r"\u0447\u0430\u0441\u0430|"
        r"\u0447\u0430\u0441\u043e\u0432"
        r")\b",
        re.IGNORECASE
    )

    match = relative_pattern.search(original)

    if not match:
        return None

    amount = int(match.group(1))

    if amount <= 0:
        return None

    unit = match.group(2).casefold()
    now = datetime.now().astimezone()

    if unit.startswith("\u0447"):
        target = now + timedelta(hours=amount)
    else:
        target = now + timedelta(minutes=amount)

    reminder_text = prefix_pattern.sub(
        "",
        original,
        count=1
    )

    reminder_text = relative_pattern.sub(
        "",
        reminder_text,
        count=1
    ).strip(" ,.-")

    if not reminder_text:
        reminder_text = "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435"

    return {
        "text": reminder_text,
        "next_run_at": target.isoformat(timespec="seconds")
    }


def _relative_reminder_time(message):
    parsed = _parse_local_relative_reminder(message)

    if parsed:
        return parsed["next_run_at"]

    return None


def mobile_ai_turn(api_key, message, files_dir):
    import json
    import os
    import sqlite3
    from datetime import datetime
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    api_key = str(api_key).strip()
    message = str(message).strip()
    files_dir = str(files_dir)

    if not message:
        return json.dumps({
            "reply": "\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u043f\u0443\u0441\u0442\u043e\u0435."
        }, ensure_ascii=False)

    # -------------------------------------------------
    # FAST LOCAL PATH:
    # "??????? ??? ????? 5 ????? ..."
    # No OpenRouter is required for this.
    # -------------------------------------------------
    local_reminder = _parse_local_relative_reminder(
        message
    )

    if local_reminder:
        created = json.loads(
            create_local_reminder(
                files_dir,
                local_reminder["text"],
                local_reminder["next_run_at"]
            )
        )

        if created.get("status") != "ok":
            return json.dumps({
                "reply": "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435.",
                "error": created.get("error")
            }, ensure_ascii=False)

        reply = (
            "\u0413\u043e\u0442\u043e\u0432\u043e. "
            "\u041d\u0430\u043f\u043e\u043c\u043d\u044e: "
            + local_reminder["text"]
        )

        _save_chat_turn(
            files_dir,
            message,
            reply
        )

        return json.dumps({
            "reply": reply,
            "reminder": created
        }, ensure_ascii=False)

    # -------------------------------------------------
    # Ordinary AI / complex reminders
    # -------------------------------------------------
    if not api_key:
        return json.dumps({
            "reply": "OpenRouter API key is not configured."
        }, ensure_ascii=False)

    db_path = os.path.join(
        files_dir,
        "assistant_mobile.db"
    )

    connection = sqlite3.connect(db_path)

    try:
        _ensure_chat_table(connection)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        note_rows = connection.execute(
            """
            SELECT text, created_at
            FROM notes
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        history_rows = connection.execute(
            """
            SELECT role, content
            FROM chat_messages
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        history_rows.reverse()

    finally:
        connection.close()

    memory_items = [
        {
            "text": text,
            "created_at": created_at
        }
        for text, created_at in note_rows
    ]

    now_local = datetime.now().astimezone()

    messages = [{
        "role": "system",
        "content": (
            "You are a personal AI assistant on a smartphone. "
            "Current local date and time: "
            + now_local.isoformat(timespec="seconds") +
            ". You have local user notes and recent chat history. "
            "Use them only when relevant and never invent facts. "
            "If the user asks for a reminder which was not already "
            "handled locally, use the create_reminder tool. "
            "Convert requested times to timezone-aware ISO 8601. "
            "Reply in the same language as the user."
        )
    }]

    for role, content in history_rows:
        if role in ("user", "assistant"):
            messages.append({
                "role": role,
                "content": content
            })

    messages.append({
        "role": "user",
        "content": json.dumps(
            {
                "message": message,
                "local_memory": memory_items
            },
            ensure_ascii=False
        )
    })

    payload = {
        "models": [
            "inclusionai/ling-3.0-flash-vl:free",
            "inclusionai/ling-3.0-flash-fin:free"
        ],
        "messages": messages,
        "tools": [{
            "type": "function",
            "function": {
                "name": "create_reminder",
                "description": "Create a local reminder on the smartphone",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string"
                        },
                        "next_run_at": {
                            "type": "string",
                            "description": "Timezone-aware ISO 8601 timestamp"
                        }
                    },
                    "required": [
                        "text",
                        "next_run_at"
                    ]
                }
            }
        }],
        "tool_choice": "auto",
        "max_completion_tokens": 1024
    }

    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "X-Title": "AI Assistant Android"
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read(1048576)
            result = json.loads(
                raw.decode("utf-8")
            )

        ai_message = result["choices"][0]["message"]
        tool_calls = ai_message.get("tool_calls") or []

        if tool_calls:
            function = (
                tool_calls[0].get("function")
                or {}
            )

            if function.get("name") != "create_reminder":
                return json.dumps({
                    "reply": "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435."
                }, ensure_ascii=False)

            raw_args = function.get("arguments") or {}

            if isinstance(raw_args, str):
                args = json.loads(raw_args)
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}

            reminder_text = str(
                args.get("text", "")
            ).strip()

            if not reminder_text:
                reminder_text = "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435"

            next_run_at = str(
                args.get("next_run_at", "")
            ).strip()

            created = json.loads(
                create_local_reminder(
                    files_dir,
                    reminder_text,
                    next_run_at
                )
            )

            if created.get("status") != "ok":
                return json.dumps({
                    "reply": "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0437\u0434\u0430\u0442\u044c \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435.",
                    "error": created.get("error")
                }, ensure_ascii=False)

            reply = (
                "\u0413\u043e\u0442\u043e\u0432\u043e. "
                "\u041d\u0430\u043f\u043e\u043c\u043d\u044e: "
                + reminder_text
            )

            _save_chat_turn(
                files_dir,
                message,
                reply
            )

            return json.dumps({
                "reply": reply,
                "reminder": created
            }, ensure_ascii=False)

        reply = ai_message.get("content")

        if not isinstance(reply, str) or not reply.strip():
            reply = "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u043e\u0442\u0432\u0435\u0442."

        reply = reply.strip()

        _save_chat_turn(
            files_dir,
            message,
            reply
        )

        return json.dumps({
            "reply": reply
        }, ensure_ascii=False)

    except HTTPError as exc:
        return json.dumps({
            "reply": "AI_HTTP_ERROR_" + str(exc.code)
        })

    except URLError:
        return json.dumps({
            "reply": "AI_NETWORK_ERROR"
        })

    except Exception as exc:
        return json.dumps({
            "reply": (
                "AI_RESPONSE_ERROR_"
                + exc.__class__.__name__
            )
        })
