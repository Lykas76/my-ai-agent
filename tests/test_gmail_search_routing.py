from assistant.intent import Intent, IntentDetector


def test_search_mail_from_sender():
    decision = IntentDetector().detect(
        "\u041d\u0430\u0439\u0434\u0438 "
        "\u043f\u0438\u0441\u044c\u043c\u0430 "
        "\u043e\u0442 Google"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.search"
    assert decision.tool_call.arguments == {
        "query": "from:google",
        "max_results": 10,
    }


def test_search_last_five_mail_from_sender():
    decision = IntentDetector().detect(
        "\u041d\u0430\u0439\u0434\u0438 "
        "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 5 "
        "\u043f\u0438\u0441\u0435\u043c "
        "\u043e\u0442 GitHub"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.search"
    assert decision.tool_call.arguments == {
        "query": "from:github",
        "max_results": 5,
    }


def test_search_mail_by_subject():
    decision = IntentDetector().detect(
        "\u041d\u0430\u0439\u0434\u0438 "
        "\u043f\u0438\u0441\u044c\u043c\u0430 "
        "\u0441 \u0442\u0435\u043c\u043e\u0439 "
        "Security alert"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.search"
    assert decision.tool_call.arguments == {
        "query": 'subject:"security alert"',
        "max_results": 10,
    }


def test_search_mail_general_text():
    decision = IntentDetector().detect(
        "\u041f\u043e\u0438\u0449\u0438 "
        "\u0432 \u043f\u043e\u0447\u0442\u0435 Railway"
    )

    assert decision.intent == Intent.TOOL
    assert decision.tool_call.name == "gmail.search"
    assert decision.tool_call.arguments == {
        "query": "railway",
        "max_results": 10,
    }


def test_find_non_mail_still_recall():
    decision = IntentDetector().detect(
        "\u041d\u0430\u0439\u0434\u0438 "
        "\u043c\u043e\u044e "
        "\u0437\u0430\u043c\u0435\u0442\u043a\u0443"
    )

    assert decision.intent == Intent.RECALL
    assert decision.tool_call is None
