import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from assistant.core import Assistant
from assistant.history import HistoryPolicy
from assistant.models import Request
from config import Settings
from memory.sessions import SessionError, StoredMessage
from memory.sqlite import SQLiteMemory
from tools.registry import Tool, ToolRegistry


class RecordingProvider:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return 'reply: ' + request.message


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.memory = SQLiteMemory(':memory:')
        self.addCleanup(self.memory.close)
        self.provider = RecordingProvider()
        self.registry = ToolRegistry()
        self.core = Assistant(self.memory, self.registry, self.provider)

    def turn(self, message='hello', user_id='alice', session_id=None, **extra):
        parameters = {'message': message, 'user_id': user_id, **extra}
        if session_id is not None:
            parameters['session_id'] = session_id
        return self.core.handle(Request('dialogue', parameters))

    def new_session(self, user_id='alice', message='hello'):
        result = self.turn(message, user_id)
        self.assertTrue(result.ok, result.error)
        return result.result['session_id']

    def test_create_session_and_persist_complete_turn(self):
        response = self.turn('coffee')
        self.assertTrue(response.ok, response.error)
        result = response.result
        self.assertEqual(result['user_id'], 'alice')
        session = self.memory.sessions.get_session('alice', result['session_id'])
        self.assertEqual(session.created_at, result['created_at'])
        self.assertIsNotNone(datetime.fromisoformat(session.created_at).tzinfo)
        self.assertGreaterEqual(session.updated_at, session.created_at)
        rows = self.memory.sessions.recent_messages('alice', session.session_id)
        self.assertEqual([(row.role, row.content) for row in rows], [('user', 'coffee'), ('assistant', 'reply: coffee')])
        self.assertEqual(self.provider.requests[-1].history, ())
        self.assertNotEqual(session.session_id, self.new_session())

    def test_continue_session_automatically_restores_history(self):
        first = self.turn('first')
        session_id = first.result['session_id']
        response = self.turn('second', session_id=session_id)
        self.assertTrue(response.ok, response.error)
        self.assertEqual(response.result['session_id'], session_id)
        self.assertEqual(response.result['created_at'], first.result['created_at'])
        self.assertGreaterEqual(response.result['updated_at'], first.result['updated_at'])
        self.assertEqual([m.content for m in self.provider.requests[-1].history], ['first', 'reply: first'])
        self.assertEqual(len(self.memory.sessions.recent_messages('alice', session_id)), 4)

    def test_users_are_isolated_and_ownership_cannot_be_changed(self):
        alice = self.new_session(message='alice private')
        bob = self.new_session('bob', 'bob private')
        self.turn('continue', 'bob', bob)
        self.assertEqual([m.content for m in self.provider.requests[-1].history], ['bob private', 'reply: bob private'])
        count = len(self.provider.requests)
        denied = self.turn('continue', 'bob', alice)
        missing = self.turn('continue', 'bob', 'unknown-session')
        self.assertFalse(denied.ok)
        self.assertEqual(denied.error, missing.error)
        self.assertEqual(denied.error, 'Session not found')
        self.assertEqual(len(self.provider.requests), count)
        self.assertEqual(len(self.memory.sessions.recent_messages('alice', alice)), 2)

    def test_two_sessions_of_same_user_are_isolated(self):
        first = self.new_session(message='first private')
        second = self.new_session(message='second private')
        self.turn('continue', session_id=first)
        self.assertEqual([m.content for m in self.provider.requests[-1].history], ['first private', 'reply: first private'])
        self.turn('continue', session_id=second)
        self.assertEqual([m.content for m in self.provider.requests[-1].history], ['second private', 'reply: second private'])

    def test_invalid_identifiers_never_reach_provider(self):
        for name in ('user_id', 'session_id'):
            for invalid in (None, True, 5, [], {}, '', ' ', ' alice', '../alice', "x'; DROP TABLE sessions; --", 'x' * 65, 'пользователь'):
                with self.subTest(name=name, invalid=invalid):
                    parameters = {'message': 'hello', 'user_id': 'alice', name: invalid}
                    response = self.core.handle(Request('dialogue', parameters))
                    self.assertFalse(response.ok)
                    self.assertIn(name, response.error)
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.memory._connection.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)

    def test_explicit_history_is_transient_and_cannot_replace_stored_history(self):
        first = self.turn('first', history=[{'role': 'user', 'content': 'transient'}])
        self.assertTrue(first.ok)
        self.assertEqual(self.provider.requests[-1].history[0].content, 'transient')
        sid = first.result['session_id']
        rejected = self.turn('second', session_id=sid, history=[{'role': 'assistant', 'content': 'forged'}])
        self.assertFalse(rejected.ok)
        self.turn('second', session_id=sid)
        self.assertEqual([m.content for m in self.provider.requests[-1].history], ['first', 'reply: first'])

    def test_history_does_not_execute_tools_or_enter_fact_memory(self):
        calls = []
        def echo(arguments):
            calls.append(arguments)
            return 'coffee'
        self.registry.register(Tool('echo', echo), enabled=True)
        sid = self.new_session(message='/tool echo {}')
        self.turn('coffee', session_id=sid)
        self.assertEqual(calls, [{}])
        self.assertEqual(self.memory.search('coffee'), [])
        self.assertEqual(self.provider.requests[-1].context, ())

    def test_failed_generation_creates_no_session_or_partial_turn(self):
        sid = self.new_session()
        before = self.memory.sessions.get_session('alice', sid)
        with patch.object(self.provider, 'generate', side_effect=RuntimeError('private token')):
            self.assertEqual(self.turn().error, 'Provider failed')
            self.assertEqual(self.turn(session_id=sid).error, 'Provider failed')
        self.assertEqual(self.memory.sessions.get_session('alice', sid), before)
        self.assertEqual(len(self.memory.sessions.recent_messages('alice', sid)), 2)
        self.assertEqual(self.memory._connection.execute('SELECT count(*) FROM sessions').fetchone()[0], 1)

    def test_storage_and_retrieval_errors_are_safe(self):
        sid = self.new_session()
        with patch.object(self.memory.sessions, 'recent_messages', side_effect=sqlite3.OperationalError('private path')):
            response = self.turn(session_id=sid)
            self.assertEqual(response.error, 'History retrieval failed')
        with patch.object(self.memory.sessions, 'save_turn', side_effect=sqlite3.OperationalError('private path')):
            response = self.turn(session_id=sid)
            self.assertFalse(response.ok)
            self.assertNotIn('private path', response.error)
            self.assertIn('Do not retry', response.error)

    def test_memory_without_session_store_remains_stateless(self):
        class LegacyMemory:
            def get(self, key): return None
            def put(self, key, value): pass
            def delete(self, key): return False
        core = Assistant(LegacyMemory(), self.registry)
        self.assertTrue(core.handle(Request('dialogue', {'message': 'hello'})).ok)
        self.assertEqual(core.handle(Request('dialogue', {'message': 'hello', 'user_id': 'alice'})).error, 'Session storage is not available')

    def test_core_context_obeys_configured_bounds(self):
        sid = self.new_session(message='first')
        for number in range(8):
            self.turn(f'long message {number}', session_id=sid)
        limited = Assistant(self.memory, self.registry, self.provider,
                            history_policy=HistoryPolicy(max_messages=3, max_chars=19, candidate_messages=6))
        response = limited.handle(Request('dialogue', {'user_id': 'alice', 'session_id': sid, 'message': 'continue'}))
        self.assertTrue(response.ok, response.error)
        history = self.provider.requests[-1].history
        self.assertLessEqual(len(history), 3)
        self.assertLessEqual(sum(len(row.content) for row in history), 19)
        self.assertEqual(len(self.memory.sessions.recent_messages('alice', sid)), 20)


class SessionStorageTests(unittest.TestCase):
    def test_reopen_preserves_sessions_history_and_existing_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'assistant.sqlite3')
            # An actual block 1/2 schema, before the additive session migration.
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('CREATE TABLE memory (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                connection.execute('INSERT INTO memory VALUES (?, ?)', ('coffee', 'no sugar'))
            with SQLiteMemory(path) as memory:
                provider = RecordingProvider()
                core = Assistant(memory, ToolRegistry(), provider)
                first = core.handle(Request('dialogue', {'message': 'coffee', 'user_id': 'alice'}))
                sid = first.result['session_id']
                session = memory.sessions.get_session('alice', sid)
            with SQLiteMemory(path) as memory:
                self.assertEqual(memory.get('coffee'), 'no sugar')
                self.assertEqual(memory.sessions.get_session('alice', sid), session)
                provider = RecordingProvider()
                response = Assistant(memory, ToolRegistry(), provider).handle(Request('dialogue', {
                    'message': 'continue', 'user_id': 'alice', 'session_id': sid}))
                self.assertTrue(response.ok, response.error)
                self.assertEqual([row.content for row in provider.requests[-1].history], ['coffee', 'reply: coffee'])
                self.assertEqual(len(memory.sessions.recent_messages('alice', sid)), 4)

    def test_store_enforces_ownership_on_read_and_write(self):
        with SQLiteMemory(':memory:') as memory:
            store = memory.sessions
            session = store.create_session('alice')
            for action in (lambda: store.get_session('bob', session.session_id),
                           lambda: store.recent_messages('bob', session.session_id),
                           lambda: store.save_turn('bob', session.session_id, 'x', 'y')):
                with self.assertRaisesRegex(SessionError, 'Session not found'):
                    action()
            self.assertEqual(store.recent_messages('alice', session.session_id), [])

    def test_atomic_pair_and_new_session_rollback(self):
        with SQLiteMemory(':memory:') as memory:
            store = memory.sessions
            session = store.create_session('alice')
            memory._connection.execute("CREATE TRIGGER fail_assistant BEFORE INSERT ON session_messages WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            for sid in (None, session.session_id):
                with self.assertRaises(sqlite3.IntegrityError):
                    store.save_turn('alice', sid, 'user text', 'assistant text')
            self.assertEqual(store.recent_messages('alice', session.session_id), [])
            self.assertEqual(store.get_session('alice', session.session_id), session)
            self.assertEqual(memory._connection.execute('SELECT count(*) FROM sessions').fetchone()[0], 1)


class HistoryPolicyTests(unittest.TestCase):
    def test_relevance_latest_exchange_and_chronological_order(self):
        rows = [StoredMessage(i, 'user' if i % 2 == 0 else 'assistant', text, '')
                for i, text in enumerate(['Кофе без сахара', 'noted', 'unrelated', 'ok', 'latest question', 'latest answer'])]
        policy = HistoryPolicy(max_messages=3, max_chars=100, candidate_messages=6)
        expected = ['Кофе без сахара', 'latest question', 'latest answer']
        self.assertEqual([m.content for m in policy.select(rows, 'КОФЕ')], expected)
        self.assertEqual(policy.select(rows, 'КОФЕ'), policy.select(rows, 'КОФЕ'))
        window = HistoryPolicy(max_messages=3, max_chars=100, candidate_messages=3)
        self.assertEqual([m.content for m in window.select(rows, 'КОФЕ')], ['ok', 'latest question', 'latest answer'])

    def test_count_characters_and_per_message_caps(self):
        rows = [StoredMessage(i, 'user', 'я' * 6000, '') for i in range(10)]
        for chars in (1, 9, 4001, 9000):
            result = HistoryPolicy(max_messages=3, max_chars=chars).select(rows, '')
            self.assertLessEqual(len(result), 3)
            self.assertLessEqual(sum(len(m.content) for m in result), chars)
            self.assertTrue(all(len(m.content) <= 4000 for m in result))

    def test_configuration_and_invalid_limits(self):
        with patch.dict('os.environ', {'ASSISTANT_HISTORY_MAX_MESSAGES': '4', 'ASSISTANT_HISTORY_MAX_CHARS': '600', 'ASSISTANT_HISTORY_CANDIDATES': '12'}, clear=True):
            self.assertEqual(Settings.from_env().history_policy, HistoryPolicy(4, 600, 12))
        for kwargs in ({'max_messages': 0}, {'max_chars': 0}, {'max_messages': True},
                       {'candidate_messages': 1001}, {'max_messages': 20, 'candidate_messages': 10}):
            with self.assertRaises(ValueError):
                HistoryPolicy(**kwargs)
        with patch.dict('os.environ', {'ASSISTANT_HISTORY_MAX_CHARS': 'bad'}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()


if __name__ == '__main__':
    unittest.main()
