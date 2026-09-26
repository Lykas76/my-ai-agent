import json

from assistant.core import Assistant
from assistant.models import Request
from memory.sqlite import SQLiteMemory
from security.store import AccessContext
from tools.registry import Tool, ToolRegistry


class Provider:
    def generate(self, request):
        return "ok"


def _assistant(memory, registry):
    user = memory.security.create_user("alice")

    for name in (
        "gmail.search",
        "gmail.list_unread",
        "gmail.list_recent",
    ):
        memory.security.grant("alice", name, "read")

    return Assistant(
        memory,
        registry,
        Provider(),
        access=AccessContext("alice", memory.security),
    )


def test_gmail_search_ids_are_saved_in_session_state():
    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        registry.register(
            Tool(
                "gmail.search",
                lambda args: json.dumps(
                    [
                        {"id": "msg-1"},
                        {"id": "msg-2"},
                        {"id": "msg-3"},
                    ]
                ),
                permission="read",
            ),
            enabled=True,
        )

        assistant = _assistant(memory, registry)

        response = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "message": (
                        "\u041d\u0430\u0439\u0434\u0438 "
                        "\u043f\u0438\u0441\u044c\u043c\u0430 "
                        "\u043e\u0442 Google"
                    ),
                },
            )
        )

        assert response.ok, response.error

        session_id = response.result["session_id"]

        state = json.loads(
            memory.sessions.get_state(
                "alice",
                session_id,
                "gmail.last_results",
            )
        )

        assert state == {
            "source": "gmail.search",
            "message_ids": [
                "msg-1",
                "msg-2",
                "msg-3",
            ],
        }


def test_empty_gmail_result_replaces_previous_result():
    responses = [
        json.dumps(
            [
                {"id": "old-1"},
                {"id": "old-2"},
            ]
        ),
        "[]",
    ]

    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        registry.register(
            Tool(
                "gmail.search",
                lambda args: responses.pop(0),
                permission="read",
            ),
            enabled=True,
        )

        assistant = _assistant(memory, registry)

        first = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "message": (
                        "\u041d\u0430\u0439\u0434\u0438 "
                        "\u043f\u0438\u0441\u044c\u043c\u0430 "
                        "\u043e\u0442 Google"
                    ),
                },
            )
        )

        assert first.ok, first.error
        session_id = first.result["session_id"]

        second = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "session_id": session_id,
                    "message": (
                        "\u041d\u0430\u0439\u0434\u0438 "
                        "\u043f\u0438\u0441\u044c\u043c\u0430 "
                        "\u043e\u0442 Nobody"
                    ),
                },
            )
        )

        assert second.ok, second.error

        state = json.loads(
            memory.sessions.get_state(
                "alice",
                session_id,
                "gmail.last_results",
            )
        )

        assert state["message_ids"] == []


def test_non_gmail_tool_does_not_create_gmail_state():
    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        memory.security.create_user("alice")
        memory.security.grant("alice", "echo", "read")

        registry.register(
            Tool(
                "echo",
                lambda args: "hello",
                permission="read",
            ),
            enabled=True,
        )

        assistant = Assistant(
            memory,
            registry,
            Provider(),
            access=AccessContext("alice", memory.security),
        )

        response = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "message": "/tool echo {}",
                },
            )
        )

        assert response.ok, response.error

        assert memory.sessions.get_state(
            "alice",
            response.result["session_id"],
            "gmail.last_results",
        ) is None


def test_open_second_gmail_result_uses_saved_message_id():
    opened = []

    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        registry.register(
            Tool(
                "gmail.search",
                lambda args: json.dumps(
                    [
                        {"id": "msg-1"},
                        {"id": "msg-2"},
                        {"id": "msg-3"},
                    ]
                ),
                permission="read",
            ),
            enabled=True,
        )

        registry.register(
            Tool(
                "gmail.get_message",
                lambda args: (
                    opened.append(dict(args))
                    or json.dumps(
                        {
                            "id": args["message_id"],
                            "subject": "Selected message",
                            "body": "Full body",
                        }
                    )
                ),
                permission="read",
            ),
            enabled=True,
        )

        user = memory.security.create_user("alice")
        memory.security.grant(
            "alice",
            "gmail.search",
            "read",
        )
        memory.security.grant(
            "alice",
            "gmail.get_message",
            "read",
        )

        assistant = Assistant(
            memory,
            registry,
            Provider(),
            access=AccessContext(
                user.user_id,
                memory.security,
            ),
        )

        first = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "message": (
                        "\u041d\u0430\u0439\u0434\u0438 "
                        "\u043f\u0438\u0441\u044c\u043c\u0430 "
                        "\u043e\u0442 Google"
                    ),
                },
            )
        )

        assert first.ok, first.error

        second = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "session_id": first.result["session_id"],
                    "message": (
                        "\u041e\u0442\u043a\u0440\u043e\u0439 "
                        "\u0432\u0442\u043e\u0440\u043e\u0435 "
                        "\u043f\u0438\u0441\u044c\u043c\u043e"
                    ),
                },
            )
        )

        assert second.ok, second.error
        assert second.result["tool_used"] == "gmail.get_message"
        assert opened == [
            {"message_id": "msg-2"}
        ]


def test_open_numbered_gmail_result_works():
    opened = []

    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        registry.register(
            Tool(
                "gmail.search",
                lambda args: json.dumps(
                    [
                        {"id": "msg-1"},
                        {"id": "msg-2"},
                        {"id": "msg-3"},
                    ]
                ),
                permission="read",
            ),
            enabled=True,
        )

        registry.register(
            Tool(
                "gmail.get_message",
                lambda args: (
                    opened.append(dict(args))
                    or "{}"
                ),
                permission="read",
            ),
            enabled=True,
        )

        user = memory.security.create_user("alice")
        memory.security.grant(
            "alice",
            "gmail.search",
            "read",
        )
        memory.security.grant(
            "alice",
            "gmail.get_message",
            "read",
        )

        assistant = Assistant(
            memory,
            registry,
            Provider(),
            access=AccessContext(
                user.user_id,
                memory.security,
            ),
        )

        first = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "message": (
                        "\u041d\u0430\u0439\u0434\u0438 "
                        "\u043f\u0438\u0441\u044c\u043c\u0430 "
                        "\u043e\u0442 Google"
                    ),
                },
            )
        )

        second = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "session_id": first.result["session_id"],
                    "message": (
                        "\u041e\u0442\u043a\u0440\u043e\u0439 "
                        "3 "
                        "\u043f\u0438\u0441\u044c\u043c\u043e"
                    ),
                },
            )
        )

        assert second.ok, second.error
        assert opened == [
            {"message_id": "msg-3"}
        ]


def test_open_missing_gmail_result_is_rejected():
    opened = []

    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        registry.register(
            Tool(
                "gmail.search",
                lambda args: json.dumps(
                    [
                        {"id": "msg-1"},
                        {"id": "msg-2"},
                    ]
                ),
                permission="read",
            ),
            enabled=True,
        )

        registry.register(
            Tool(
                "gmail.get_message",
                lambda args: (
                    opened.append(dict(args))
                    or "{}"
                ),
                permission="read",
            ),
            enabled=True,
        )

        user = memory.security.create_user("alice")
        memory.security.grant(
            "alice",
            "gmail.search",
            "read",
        )
        memory.security.grant(
            "alice",
            "gmail.get_message",
            "read",
        )

        assistant = Assistant(
            memory,
            registry,
            Provider(),
            access=AccessContext(
                user.user_id,
                memory.security,
            ),
        )

        first = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "message": (
                        "\u041d\u0430\u0439\u0434\u0438 "
                        "\u043f\u0438\u0441\u044c\u043c\u0430 "
                        "\u043e\u0442 Google"
                    ),
                },
            )
        )

        second = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "session_id": first.result["session_id"],
                    "message": (
                        "\u041e\u0442\u043a\u0440\u043e\u0439 "
                        "\u0442\u0440\u0435\u0442\u044c\u0435 "
                        "\u043f\u0438\u0441\u044c\u043c\u043e"
                    ),
                },
            )
        )

        assert not second.ok
        assert "not available" in second.error
        assert opened == []


def test_open_last_gmail_result_uses_last_saved_message_id():
    opened = []

    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        registry.register(
            Tool(
                "gmail.search",
                lambda args: json.dumps(
                    [
                        {"id": "msg-1"},
                        {"id": "msg-2"},
                        {"id": "msg-3"},
                    ]
                ),
                permission="read",
            ),
            enabled=True,
        )

        registry.register(
            Tool(
                "gmail.get_message",
                lambda args: (
                    opened.append(dict(args))
                    or json.dumps(
                        {
                            "id": args["message_id"],
                            "subject": "Last message",
                        }
                    )
                ),
                permission="read",
            ),
            enabled=True,
        )

        user = memory.security.create_user("alice")

        memory.security.grant(
            "alice",
            "gmail.search",
            "read",
        )
        memory.security.grant(
            "alice",
            "gmail.get_message",
            "read",
        )

        assistant = Assistant(
            memory,
            registry,
            Provider(),
            access=AccessContext(
                user.user_id,
                memory.security,
            ),
        )

        first = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "message": (
                        "\u041d\u0430\u0439\u0434\u0438 "
                        "\u043f\u0438\u0441\u044c\u043c\u0430 "
                        "\u043e\u0442 Google"
                    ),
                },
            )
        )

        assert first.ok, first.error

        second = assistant.handle(
            Request(
                "dialogue",
                {
                    "user_id": "alice",
                    "session_id": first.result["session_id"],
                    "message": (
                        "\u041e\u0442\u043a\u0440\u043e\u0439 "
                        "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 "
                        "\u043f\u0438\u0441\u044c\u043c\u043e"
                    ),
                },
            )
        )

        assert second.ok, second.error
        assert second.result["tool_used"] == "gmail.get_message"

        assert opened == [
            {"message_id": "msg-3"}
        ]
