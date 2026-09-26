import io
import json
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from api.limiter import RateLimiter
from api.v1 import V1API
from assistant.core import Assistant
from clients.telegram import TelegramClient, bind
from memory.sqlite import SQLiteMemory
from runtime.backup import backup
from runtime.logging import event
from security.store import AuthenticationError
from tools.registry import ToolRegistry
from adapters.builtin import register_builtin
from voice import UnavailableVoice


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.memory=SQLiteMemory(":memory:"); self.addCleanup(self.memory.close)
        self.memory.security.create_user("a"); self.memory.security.create_user("b")
        self.tools=ToolRegistry(); register_builtin(self.tools)
        self.client=TelegramClient(Assistant(self.memory,self.tools))
        bind(self.memory,101,"a"); bind(self.memory,202,"b")

    def update(self,number,text="/start",uid=101):
        return {"update_id":number,"message":{"text":text,"from":{"id":uid},"chat":{"id":uid,"type":"private"}}}

    def test_binding_session_persistence_and_duplicate_update(self):
        self.assertTrue(self.client.process(self.update(1)))
        sid=self.memory._connection.execute("SELECT session_id FROM telegram_links WHERE telegram_id=101").fetchone()[0]
        self.assertTrue(sid)
        self.assertEqual(self.client.process(self.update(1)),[])
        self.client.process(self.update(2,"hello"))
        self.assertEqual(len(self.memory.sessions.recent_messages("a",sid)),2)

    def test_group_unlinked_and_disabled_accounts(self):
        update=self.update(1); update["message"]["chat"]["type"]="group"
        self.assertEqual(self.client.process(update),[])
        self.assertIn("administrator",self.client.process(self.update(2,uid=303))[0]["text"])
        self.memory.security.set_status("a","disabled")
        self.assertEqual(self.client.process(self.update(3))[0]["text"],"Access denied.")

    def test_confirmation_buttons_owner_and_explicit_execution(self):
        self.memory.security.grant("a","android.action","sensitive")
        result=self.client.process(self.update(1,"/tool android.action {}"))
        callback=result[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        cid=callback.split(":")[1]
        def update(number,action,uid):
            return {"update_id":number,"callback_query":{"id":str(number),"data":action+":"+cid,
                "from":{"id":uid},"message":{"chat":{"id":uid,"type":"private"}}}}
        self.client.process(update(2,"approve",202))
        self.assertEqual(self.memory.security.confirmations.get("a",cid)["status"],"pending")
        self.client.process(update(3,"approve",101))
        self.assertEqual(self.memory.security.confirmations.get("a",cid)["status"],"approved")
        self.client.process(update(4,"execute",101))
        self.assertEqual(self.memory.security.confirmations.get("a",cid)["status"],"consumed")
        self.assertIn("failed",self.client.process(update(5,"execute",101))[0]["text"])

    def test_rebind_clears_old_session(self):
        self.client.process(self.update(1))
        bind(self.memory,101,"b")
        self.assertEqual(self.memory._connection.execute("SELECT user_id,session_id FROM telegram_links WHERE telegram_id=101").fetchone(),("b",None))


class RuntimeTests(unittest.TestCase):
    def test_rate_limiter_is_bounded_and_resets(self):
        limiter=RateLimiter(2,capacity=2)
        self.assertTrue(limiter.allow("x",0)); self.assertTrue(limiter.allow("x",1))
        self.assertFalse(limiter.allow("x",2)); self.assertTrue(limiter.allow("x",60))
        limiter.allow("y",60); limiter.allow("z",60)
        self.assertEqual(len(limiter.buckets),2)

    def test_logs_only_allowlisted_metadata(self):
        value=secrets.token_urlsafe(30)
        with self.assertLogs("assistant.runtime",level="INFO") as captured:
            event("http_response",status=200,token=value,text=value,error=value)
        self.assertNotIn(value,"".join(captured.output))
        self.assertIn('"status": 200',"".join(captured.output))

    def test_token_expiry_and_revocation_and_no_plaintext(self):
        with SQLiteMemory(":memory:") as memory:
            user=memory.security.create_user()
            issued=memory.security.issue_token(user.user_id,60)
            self.assertEqual(memory.security.authenticate(issued.token),user)
            self.assertNotIn(issued.token,"".join(memory._connection.iterdump()))
            with memory._connection:
                memory._connection.execute("UPDATE api_tokens SET expires_at=?",
                    ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
            with self.assertRaises(AuthenticationError): memory.security.authenticate(issued.token)

    def test_backup_reopen_migrations_preserve_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"a.sqlite3"; dest=Path(directory)/"backup.sqlite3"
            with SQLiteMemory(str(source)) as memory:
                memory.security.create_user("a"); memory.put("key","value")
                self.assertEqual(memory._connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],1)
                backup(source,dest)
            with SQLiteMemory(str(dest)) as memory:
                self.assertEqual(memory.get("key"),"value")
                self.assertIsNotNone(memory.security.get_user("a"))
            with self.assertRaises(ValueError): backup(source,dest)

    def test_legacy_token_migration_sets_finite_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"old.sqlite3")
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE api_tokens(token_id TEXT PRIMARY KEY,user_id TEXT,token_hash TEXT,created_at TEXT,revoked INTEGER DEFAULT 0)")
                connection.execute("INSERT INTO api_tokens VALUES('old','a','hash','2020-01-01T00:00:00+00:00',0)")
            connection.close()
            with SQLiteMemory(path) as memory:
                expiry=memory._connection.execute("SELECT expires_at FROM api_tokens").fetchone()[0]
                self.assertEqual(expiry,"2020-01-31T00:00:00+00:00")

    def test_voice_stub_explicitly_unavailable(self):
        with self.assertRaises(ValueError): UnavailableVoice().transcribe(b"","ru")
        with self.assertRaises(ValueError): UnavailableVoice().synthesize("hello","ru")
