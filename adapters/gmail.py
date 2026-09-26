"""Read-only Gmail adapter.

Google dependencies are imported lazily so the base assistant can still run
without the optional Gmail package set installed.
"""
from __future__ import annotations

import base64
import json
import os
from html.parser import HTMLParser
from pathlib import Path

from tools.registry import Tool
from tools.schema import object_schema


SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _path_from_env(name: str, default: str) -> Path:
    value = os.getenv(name) or default
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())

    def text(self):
        return " ".join(self.parts)


def _decode(data: str) -> str:
    if not data:
        return ""
    try:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _headers(payload):
    result = {}
    for item in payload.get("headers", ()):
        name = str(item.get("name", "")).lower()
        if name in {"subject", "from", "to", "date"}:
            result[name] = item.get("value", "")
    return result


def _body(payload):
    plain = []
    html = []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            value = _decode(data)
            if mime == "text/plain":
                plain.append(value)
            elif mime == "text/html":
                html.append(value)

        for child in part.get("parts", ()):
            walk(child)

    walk(payload)

    if plain:
        return "\n".join(plain).strip()

    if html:
        parser = _HTMLText()
        parser.feed("\n".join(html))
        return parser.text().strip()

    return ""


class GmailReadOnlyClient:
    def __init__(
        self,
        credentials_file=None,
        token_file=None,
        timeout_seconds=None,
    ):
        self.credentials_file = Path(credentials_file) if credentials_file else _path_from_env(
            "ASSISTANT_GMAIL_CREDENTIALS", "credentials.json"
        )
        self.token_file = Path(token_file) if token_file else _path_from_env(
            "ASSISTANT_GMAIL_TOKEN", "token.json"
        )
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("ASSISTANT_GMAIL_TIMEOUT_SECONDS") or "10"
        )

        if not 1 <= self.timeout_seconds <= 60:
            raise ValueError("Invalid Gmail timeout")

    def _credentials(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise RuntimeError(
                "Gmail dependencies are not installed; install requirements-gmail.txt"
            ) from exc

        if not self.token_file.exists():
            raise RuntimeError(
                "Gmail OAuth token not found; complete OAuth bootstrap first"
            )

        creds = Credentials.from_authorized_user_file(
            str(self.token_file),
            list(SCOPES),
        )

        if hasattr(creds, "has_scopes") and not creds.has_scopes(SCOPES):
            raise RuntimeError("Gmail token does not have required read-only scope")

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

            temporary = self.token_file.with_suffix(
                self.token_file.suffix + ".tmp"
            )
            temporary.write_text(
                creds.to_json(),
                encoding="utf-8",
            )
            os.replace(temporary, self.token_file)

        if not creds.valid:
            raise RuntimeError("Gmail OAuth credentials are not valid")

        return creds

    def _service(self):
        try:
            import httplib2
            from google_auth_httplib2 import AuthorizedHttp
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Gmail dependencies are not installed; install requirements-gmail.txt"
            ) from exc

        creds = self._credentials()
        http = AuthorizedHttp(
            creds,
            http=httplib2.Http(timeout=self.timeout_seconds),
        )

        return build(
            "gmail",
            "v1",
            http=http,
            cache_discovery=False,
        )

    @staticmethod
    def _summary(message):
        payload = message.get("payload") or {}
        headers = _headers(payload)

        return {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
            "snippet": message.get("snippet", ""),
            "labels": message.get("labelIds", []),
        }

    def _metadata(self, service, message_id):
        message = service.users().messages().get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()

        return self._summary(message)

    def _list(self, *, max_results, label_ids=None, query=None):
        service = self._service()

        kwargs = {
            "userId": "me",
            "maxResults": max_results,
        }

        if label_ids:
            kwargs["labelIds"] = list(label_ids)

        if query:
            kwargs["q"] = query

        response = service.users().messages().list(**kwargs).execute()
        messages = response.get("messages", [])

        return [
            self._metadata(service, item["id"])
            for item in messages
        ]

    def list_recent(self, max_results=10):
        return self._list(
            max_results=max_results,
            label_ids=("INBOX",),
        )

    def list_unread(self, max_results=10):
        return self._list(
            max_results=max_results,
            label_ids=("INBOX", "UNREAD"),
        )

    def search(self, query, max_results=10):
        return self._list(
            max_results=max_results,
            query=query,
        )

    def get_message(self, message_id):
        service = self._service()

        message = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        payload = message.get("payload") or {}
        headers = _headers(payload)

        body = _body(payload)
        if not body:
            body = message.get("snippet", "")

        # Prevent an email from flooding the model context.
        body = body[:12000]

        return {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "snippet": message.get("snippet", ""),
            "body": body,
            "labels": message.get("labelIds", []),
        }


class GmailReadOnlyAdapter:
    def __init__(self, client=None):
        self.client = client or GmailReadOnlyClient()

    @staticmethod
    def _max(args):
        return args.get("max_results", 10)

    def list_recent(self, args, context):
        context.remaining()
        result = self.client.list_recent(self._max(args))
        context.remaining()
        return json.dumps(result, ensure_ascii=False)

    def list_unread(self, args, context):
        context.remaining()
        result = self.client.list_unread(self._max(args))
        context.remaining()
        return json.dumps(result, ensure_ascii=False)

    def search(self, args, context):
        context.remaining()
        result = self.client.search(
            args["query"],
            self._max(args),
        )
        context.remaining()
        return json.dumps(result, ensure_ascii=False)

    def get_message(self, args, context):
        context.remaining()
        result = self.client.get_message(args["message_id"])
        context.remaining()
        return json.dumps(result, ensure_ascii=False)


def register_gmail_readonly(registry, client=None):
    adapter = GmailReadOnlyAdapter(client)

    maximum = {
        "type": "integer",
        "minimum": 1,
        "maximum": 20,
    }

    query = {
        "type": "string",
        "minLength": 1,
        "maxLength": 500,
    }

    message_id = {
        "type": "string",
        "minLength": 1,
        "maxLength": 200,
    }

    registry.register(
        Tool(
            "gmail.list_recent",
            adapter.list_recent,
            "List recent messages from the Gmail inbox. Read-only.",
            "read",
            schema=object_schema({"max_results": maximum}),
            contextual=True,
            retry_safe=True,
        ),
        enabled=True,
    )

    registry.register(
        Tool(
            "gmail.list_unread",
            adapter.list_unread,
            "List unread messages from the Gmail inbox. Read-only.",
            "read",
            schema=object_schema({"max_results": maximum}),
            contextual=True,
            retry_safe=True,
        ),
        enabled=True,
    )

    registry.register(
        Tool(
            "gmail.search",
            adapter.search,
            "Search Gmail using a Gmail search query. Read-only.",
            "read",
            schema=object_schema(
                {
                    "query": query,
                    "max_results": maximum,
                },
                ("query",),
            ),
            contextual=True,
            retry_safe=True,
        ),
        enabled=True,
    )

    registry.register(
        Tool(
            "gmail.get_message",
            adapter.get_message,
            "Read one Gmail message by message ID. Read-only.",
            "read",
            schema=object_schema(
                {"message_id": message_id},
                ("message_id",),
            ),
            contextual=True,
            retry_safe=True,
        ),
        enabled=True,
    )
