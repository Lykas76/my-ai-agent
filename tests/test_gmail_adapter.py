import json
import unittest

from memory.sqlite import SQLiteMemory
from security.store import AccessContext
from tools.registry import ToolDenied, ToolRegistry
from adapters.gmail import register_gmail_readonly


class FakeGmail:
    def list_recent(self, max_results=10):
        return [{"id": "r1", "subject": "Recent", "max": max_results}]

    def list_unread(self, max_results=10):
        return [{"id": "u1", "subject": "Unread", "max": max_results}]

    def search(self, query, max_results=10):
        return [{"id": "s1", "query": query, "max": max_results}]

    def get_message(self, message_id):
        return {
            "id": message_id,
            "subject": "Message",
            "body": "hello",
        }


class GmailAdapterTests(unittest.TestCase):
    def setUp(self):
        self.memory = SQLiteMemory(":memory:")
        self.addCleanup(self.memory.close)

        self.user = self.memory.security.create_user("gmail-user")
        self.access = AccessContext(
            self.user.user_id,
            self.memory.security,
        )

        self.registry = ToolRegistry()
        register_gmail_readonly(
            self.registry,
            FakeGmail(),
        )

    def grant(self, name):
        self.memory.security.grant(
            self.user.user_id,
            name,
            "read",
        )

    def execute(self, name, args=None):
        return self.registry.execute(
            name,
            args or {},
            access=self.access,
        )

    def test_default_deny(self):
        with self.assertRaises(ToolDenied):
            self.execute("gmail.list_recent")

    def test_list_recent(self):
        self.grant("gmail.list_recent")
        result = json.loads(
            self.execute(
                "gmail.list_recent",
                {"max_results": 5},
            )
        )
        self.assertEqual(result[0]["id"], "r1")
        self.assertEqual(result[0]["max"], 5)

    def test_list_unread(self):
        self.grant("gmail.list_unread")
        result = json.loads(
            self.execute("gmail.list_unread")
        )
        self.assertEqual(result[0]["id"], "u1")

    def test_search(self):
        self.grant("gmail.search")
        result = json.loads(
            self.execute(
                "gmail.search",
                {
                    "query": "from:test@example.com",
                    "max_results": 3,
                },
            )
        )
        self.assertEqual(
            result[0]["query"],
            "from:test@example.com",
        )

    def test_get_message(self):
        self.grant("gmail.get_message")
        result = json.loads(
            self.execute(
                "gmail.get_message",
                {"message_id": "abc123"},
            )
        )
        self.assertEqual(result["id"], "abc123")

    def test_schema_rejects_unknown_argument(self):
        self.grant("gmail.list_recent")
        with self.assertRaises(ValueError):
            self.execute(
                "gmail.list_recent",
                {"unexpected": True},
            )

    def test_schema_limits_max_results(self):
        self.grant("gmail.list_recent")
        with self.assertRaises(ValueError):
            self.execute(
                "gmail.list_recent",
                {"max_results": 21},
            )


if __name__ == "__main__":
    unittest.main()
