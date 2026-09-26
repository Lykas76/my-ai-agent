from assistant.core import Assistant
from assistant.models import Request
from memory.sqlite import SQLiteMemory
from security.store import AccessContext
from tools.registry import Tool, ToolRegistry


class Provider:
    def __init__(self, reply):
        self.reply = reply

    def generate(self, request):
        return self.reply


def _run(reply):
    with SQLiteMemory(":memory:") as memory:
        registry = ToolRegistry()

        user = memory.security.create_user("alice")
        memory.security.grant(
            "alice",
            "echo",
            "read",
        )

        registry.register(
            Tool(
                "echo",
                lambda args: "tool result",
                permission="read",
            ),
            enabled=True,
        )

        assistant = Assistant(
            memory,
            registry,
            Provider(reply),
            access=AccessContext(
                user.user_id,
                memory.security,
            ),
        )

        response = assistant.handle(
            Request(
                "dialogue",
                {
                    "message": "/tool echo {}",
                },
            )
        )

        assert response.ok, response.error
        assert response.result["tool_used"] == "echo"

        return response.result["reply"]


def test_removes_russian_would_you_like_offer():
    reply = _run(
        "\u0412\u043e\u0442 "
        "\u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442."
        "\n\n"
        "\u0425\u043e\u0442\u0438\u0442\u0435, "
        "\u0447\u0442\u043e\u0431\u044b \u044f "
        "\u0443\u0434\u0430\u043b\u0438\u043b "
        "\u043f\u0438\u0441\u044c\u043c\u043e?"
    )

    assert reply == (
        "\u0412\u043e\u0442 "
        "\u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442."
    )


def test_removes_russian_if_needed_offer():
    reply = _run(
        "\u041f\u0438\u0441\u044c\u043c\u043e "
        "\u043e\u0442\u043a\u0440\u044b\u0442\u043e."
        "\n\n"
        "\u0415\u0441\u043b\u0438 "
        "\u043d\u0443\u0436\u043d\u043e, "
        "\u043c\u043e\u0433\u0443 "
        "\u043f\u0435\u0440\u0435\u0441\u043b\u0430\u0442\u044c "
        "\u0435\u0433\u043e."
    )

    assert reply == (
        "\u041f\u0438\u0441\u044c\u043c\u043e "
        "\u043e\u0442\u043a\u0440\u044b\u0442\u043e."
    )


def test_keeps_normal_multi_paragraph_reply():
    original = (
        "\u041f\u0435\u0440\u0432\u044b\u0439 "
        "\u0430\u0431\u0437\u0430\u0446."
        "\n\n"
        "\u0412\u0442\u043e\u0440\u043e\u0439 "
        "\u0430\u0431\u0437\u0430\u0446 "
        "\u0441 \u0432\u0430\u0436\u043d\u043e\u0439 "
        "\u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u0435\u0439."
    )

    assert _run(original) == original
