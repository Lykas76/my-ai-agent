"""One dialogue turn: validate, classify, retrieve, execute, generate."""
import json
import re
from dataclasses import asdict, dataclass

from assistant.intent import Intent, IntentDecision, IntentDetector, ToolCall
from assistant.history import HistoryPolicy
from assistant.providers import AIProvider, LocalProvider, Message, ProviderRequest, ToolResult, ProviderResponse
from memory.base import Memory
from memory.sessions import SessionError, SessionStore, StoredMessage, validate_id
from memory.search import MemoryEntry, SearchableMemory
from tools.registry import ConfirmationRequired, ToolDenied, ToolRegistry
from security.store import AccessContext
from security.confirmations import ConfirmationRejected
from runtime.credentials import reject_credentials

MAX_MESSAGE = 4000
MAX_HISTORY = 20
MAX_CONTEXT = 5
MAX_CONTEXT_KEY = 200
MAX_CONTEXT_VALUE = 1000
MAX_TOOL_RESULT = 4000

GMAIL_RESULT_TOOLS = {
    "gmail.list_recent",
    "gmail.list_unread",
    "gmail.search",
}
GMAIL_LAST_RESULTS_KEY = "gmail.last_results"


class DialogueError(ValueError):
    """A safe public error message for a failed dialogue turn."""


@dataclass(frozen=True)
class DialogueResponse:
    reply: str
    intent: str
    context_keys: tuple[str, ...]
    tool_used: str | None = None


class DialogueHandler:
    def __init__(self, memory: Memory, tools: ToolRegistry, provider: AIProvider | None = None,
                 *, sessions: SessionStore | None = None, history_policy: HistoryPolicy | None = None,
                 access: AccessContext | None = None):
        self.memory = memory
        self.tools = tools
        self.access = access
        self.provider = provider if provider is not None else LocalProvider()
        self.detector = IntentDetector()
        self.sessions = sessions if sessions is not None else getattr(memory, "sessions", None)
        self.history_policy = history_policy if history_policy is not None else HistoryPolicy()

    def handle(self, parameters: dict) -> dict:
        if not isinstance(parameters, dict) or set(parameters) - {"message", "history", "user_id", "session_id", "confirmation_id"}:
            raise ValueError("Expected message, optional history, user_id and session_id")
        message = self._text(parameters.get("message"))
        history = self._history(parameters.get("history", []))
        decision = self.detector.detect(message)
        if "confirmation_id" in parameters and (not isinstance(parameters["confirmation_id"], str) or decision.tool_call is None):
            raise ConfirmationRejected()
        persistent = "user_id" in parameters or "session_id" in parameters
        user_id = session_id = None
        if persistent:
            user_id = validate_id(parameters.get("user_id", "local"), "user_id")
            if "session_id" in parameters:
                session_id = validate_id(parameters["session_id"], "session_id")
            if self.sessions is None:
                raise DialogueError("Session storage is not available")
            if session_id is not None and history:
                raise ValueError("Existing sessions use stored history; omit explicit history")
            try:
                if session_id is not None:
                    rows = self.sessions.recent_messages(user_id, session_id, self.history_policy.candidate_messages)
                else:
                    # Legacy client-supplied history is transient context for a new session.
                    rows = [StoredMessage(i, item.role, item.content, "") for i, item in enumerate(history)]
                history = self.history_policy.select(rows, message)
            except SessionError:
                raise
            except Exception as exc:
                raise DialogueError("History retrieval failed") from exc
        followup = self._gmail_followup_decision(
            message,
            user_id,
            session_id,
        )

        if followup is not None:
            decision = followup

        context = ()
        # A greeting or tool invocation does not need to disclose stored memories.
        if decision.intent in {Intent.CHAT, Intent.RECALL} and isinstance(self.memory, SearchableMemory):
            try:
                entries = self.memory.search(message, limit=MAX_CONTEXT)
                context = tuple(
                    MemoryEntry(entry.key[:MAX_CONTEXT_KEY], entry.value[:MAX_CONTEXT_VALUE])
                    for entry in entries[:MAX_CONTEXT]
                )
            except Exception as exc:
                raise DialogueError("Memory retrieval failed") from exc
        result = None
        pending_session_state = None

        if decision.tool_call is not None:
            call = decision.tool_call
            try:
                value = self.tools.execute(call.name, call.arguments, access=self.access,
                                           confirmation_id=parameters.get("confirmation_id"), session_id=session_id,
                                           new_session=persistent and session_id is None)
                if not isinstance(value, str):
                    raise TypeError("Tool must return text")

                pending_session_state = self._gmail_result_state(
                    call.name,
                    value,
                )

                result = ToolResult(call.name, value[:MAX_TOOL_RESULT])
            except (ToolDenied, ConfirmationRequired, ConfirmationRejected):
                raise
            except Exception as exc:
                raise DialogueError("Tool execution failed; it may have produced side effects. Do not retry automatically.") from exc
        request = ProviderRequest(message, decision.intent, context, history, result, self.tools.definitions(self.access))
        try:
            reply = self.provider.generate(request)
            if isinstance(reply, ProviderResponse):
                if reply.tool_call is not None:
                    if result is not None:
                        raise ValueError("Only one tool per turn")
                    call = reply.tool_call
                    value = self.tools.execute(call.name, call.arguments, access=self.access,
                                               session_id=session_id, new_session=persistent and session_id is None)
                    pending_session_state = self._gmail_result_state(
                        call.name,
                        value,
                    )
                    result = ToolResult(call.name, value[:MAX_TOOL_RESULT])
                    # Bounded one-call workflow; no second model execution or automatic retry.
                    reply = result.value
                else:
                    reply = reply.text
            if not isinstance(reply, str) or not reply.strip() or len(reply) > 8000:
                raise TypeError("Provider must return bounded nonempty text")
            reject_credentials(reply)
        except (ToolDenied, ConfirmationRequired, ConfirmationRejected):
            raise
        except Exception as exc:
            detail = "Provider failed after tool execution; do not retry automatically" if result else "Provider failed"
            raise DialogueError(detail) from exc
        response = asdict(DialogueResponse(
            reply, decision.intent.value, tuple(entry.key for entry in context), result.name if result else None
        ))
        if persistent:
            try:
                session = self.sessions.save_turn(
                    user_id,
                    session_id,
                    message,
                    reply,
                )

                if pending_session_state is not None:
                    self.sessions.set_state(
                        user_id,
                        session.session_id,
                        GMAIL_LAST_RESULTS_KEY,
                        pending_session_state,
                    )

            except Exception as exc:
                raise DialogueError(
                    "History storage failed; actions may have completed. "
                    "Do not retry automatically."
                ) from exc

            response.update(
                user_id=session.user_id,
                session_id=session.session_id,
                created_at=session.created_at,
                updated_at=session.updated_at,
            )
        return response

    def _gmail_followup_decision(
        self,
        message: str,
        user_id: str | None,
        session_id: str | None,
    ) -> IntentDecision | None:
        normalized = (
            message.casefold()
            .replace("\u0451", "\u0435")
            .strip(" .!?\n\t")
        )

        mail_terms = (
            "\u043f\u0438\u0441\u044c\u043c",
            "\u043f\u0438\u0441\u0435\u043c",
            "email",
            "e-mail",
            "mail",
            "message",
        )

        open_terms = (
            "\u043e\u0442\u043a\u0440\u043e\u0439",
            "\u043e\u0442\u043a\u0440\u044b\u0442\u044c",
            "\u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0439",
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e",
            "open",
            "read",
        )

        if not any(term in normalized for term in mail_terms):
            return None

        if not any(term in normalized for term in open_terms):
            return None

        position = None

        digit = re.search(r"\b(\d{1,2})\b", normalized)
        if digit:
            number = int(digit.group(1))
            if 1 <= number <= 20:
                position = number

        if position is None:
            ordinals = (
                ("\u043f\u0435\u0440\u0432", 1),
                ("\u0432\u0442\u043e\u0440", 2),
                ("\u0442\u0440\u0435\u0442", 3),
                ("\u0447\u0435\u0442\u0432\u0435\u0440\u0442", 4),
                ("\u043f\u044f\u0442", 5),
                ("\u0448\u0435\u0441\u0442", 6),
                ("\u0441\u0435\u0434\u044c\u043c", 7),
                ("\u0432\u043e\u0441\u044c\u043c", 8),
                ("\u0434\u0435\u0432\u044f\u0442", 9),
                ("\u0434\u0435\u0441\u044f\u0442", 10),
            )

            for stem, number in ordinals:
                if stem in normalized:
                    position = number
                    break

        # This is not a positional Gmail follow-up.
        if position is None:
            return None

        if (
            self.sessions is None
            or user_id is None
            or session_id is None
        ):
            raise DialogueError(
                "Gmail follow-up requires an existing session"
            )

        raw = self.sessions.get_state(
            user_id,
            session_id,
            GMAIL_LAST_RESULTS_KEY,
        )

        if raw is None:
            raise DialogueError(
                "No Gmail results are available in this session"
            )

        try:
            state = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise DialogueError(
                "Saved Gmail results are invalid"
            ) from exc

        if not isinstance(state, dict):
            raise DialogueError(
                "Saved Gmail results are invalid"
            )

        message_ids = state.get("message_ids")

        if not isinstance(message_ids, list):
            raise DialogueError(
                "Saved Gmail results are invalid"
            )

        index = position - 1

        if index < 0 or index >= len(message_ids):
            raise DialogueError(
                "Requested Gmail message is not available "
                "in the last results"
            )

        message_id = message_ids[index]

        if (
            not isinstance(message_id, str)
            or not 1 <= len(message_id) <= 200
        ):
            raise DialogueError(
                "Saved Gmail results are invalid"
            )

        return IntentDecision(
            Intent.TOOL,
            ToolCall(
                "gmail.get_message",
                {"message_id": message_id},
            ),
        )

    @staticmethod
    def _gmail_result_state(tool_name: str, value: str) -> str | None:
        if tool_name not in GMAIL_RESULT_TOOLS:
            return None

        try:
            rows = json.loads(value)
        except (TypeError, ValueError):
            return None

        if not isinstance(rows, list):
            return None

        message_ids = []

        for row in rows[:20]:
            if not isinstance(row, dict):
                continue

            message_id = row.get("id")

            if (
                isinstance(message_id, str)
                and 1 <= len(message_id) <= 200
            ):
                message_ids.append(message_id)

        return json.dumps(
            {
                "source": tool_name,
                "message_ids": message_ids,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_MESSAGE:
            raise ValueError("Message must contain 1..4000 characters and cannot be blank")
        reject_credentials(value)
        return value.strip()

    @classmethod
    def _history(cls, value):
        if not isinstance(value, list) or len(value) > MAX_HISTORY:
            raise ValueError("history must be a list of at most 20 messages")
        history = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {"role", "content"}:
                raise ValueError("History messages require role and content")
            if item["role"] not in ("user", "assistant"):
                raise ValueError("History role must be user or assistant")
            history.append(Message(item["role"], cls._text(item["content"])))
        return tuple(history)
