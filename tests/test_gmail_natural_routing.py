from assistant.intent import Intent, IntentDetector


def test_routes_russian_unread_gmail():
    decision = IntentDetector().detect(
        "\u041f\u043e\u043a\u0430\u0436\u0438 "
        "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 3 "
        "\u043d\u0435\u043f\u0440\u043e\u0447\u0438\u0442\u0430\u043d\u043d\u044b\u0445 "
        "\u043f\u0438\u0441\u044c\u043c\u0430"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.list_unread"
    assert decision.tool_call.arguments == {"max_results": 3}


def test_routes_unread_gmail_default_count():
    decision = IntentDetector().detect(
        "\u041f\u043e\u043a\u0430\u0436\u0438 "
        "\u043d\u0435\u043f\u0440\u043e\u0447\u0438\u0442\u0430\u043d\u043d\u044b\u0435 "
        "\u043f\u0438\u0441\u044c\u043c\u0430"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.list_unread"
    assert decision.tool_call.arguments == {"max_results": 10}


def test_routes_recent_gmail():
    decision = IntentDetector().detect(
        "\u041f\u043e\u043a\u0430\u0436\u0438 "
        "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 5 "
        "\u043f\u0438\u0441\u0435\u043c"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.list_recent"
    assert decision.tool_call.arguments == {"max_results": 5}


def test_routes_english_unread_gmail():
    decision = IntentDetector().detect(
        "Show me 4 unread emails"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.list_unread"
    assert decision.tool_call.arguments == {"max_results": 4}


def test_does_not_route_unrelated_chat():
    decision = IntentDetector().detect(
        "\u0420\u0430\u0441\u0441\u043a\u0430\u0436\u0438 "
        "\u043c\u043d\u0435 \u043e "
        "\u043f\u043e\u0433\u043e\u0434\u0435"
    )

    assert decision.intent == Intent.CHAT
    assert decision.tool_call is None


def test_greeting_still_has_priority():
    decision = IntentDetector().detect(
        "\u041f\u0440\u0438\u0432\u0435\u0442"
    )

    assert decision.intent == Intent.GREETING
    assert decision.tool_call is None
