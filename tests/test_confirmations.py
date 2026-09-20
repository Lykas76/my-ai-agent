import io
import json
import secrets
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from assistant.core import Assistant
from assistant.models import Request
from config import Settings
from memory.sqlite import SQLiteMemory
from security.confirmations import ConfirmationRejected, canonical_arguments
from security.store import AccessContext
from tools.registry import ConfirmationRequired, Tool, ToolRegistry

SCHEMA = {'target': ('draft', 'other'), 'count': int, 'options': {'enabled': bool}}


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.memory = SQLiteMemory(':memory:')
        self.addCleanup(self.memory.close)
        self.security = self.memory.security
        self.security.create_user('alice')
        self.security.create_user('bob')
        self.access = AccessContext('alice', self.security)
        self.store = self.security.confirmations
        self.registry = ToolRegistry()
        self.calls = []
        self.arguments = {'target': 'draft', 'count': 1, 'options': {'enabled': True}}
        def handler(arguments):
            self.calls.append(arguments)
            return 'done'
        for name in ('danger', 'other'):
            self.registry.register(Tool(name, handler, permission='sensitive', confirmation_schema=SCHEMA), enabled=True)
            self.security.grant('alice', name, 'sensitive')
            self.security.grant('bob', name, 'sensitive')

    def execute(self, confirmation_id=None, *, name='danger', arguments=None, session_id=None, access=None):
        return self.registry.execute(name, self.arguments if arguments is None else arguments,
                                     access=access or self.access, confirmation_id=confirmation_id, session_id=session_id)

    def create(self, **kwargs):
        with self.assertRaises(ConfirmationRequired) as raised:
            self.execute(**kwargs)
        return raised.exception.confirmation

    def approved(self, **kwargs):
        row = self.create(**kwargs)
        return self.store.approve('alice', row['confirmation_id'])

    def events(self):
        return [row[0] for row in self.memory._connection.execute('SELECT event FROM security_audit WHERE event IS NOT NULL ORDER BY audit_id')]

    def test_create_is_persistent_random_bound_and_does_not_execute(self):
        first = self.create()
        second = self.create()
        self.assertNotEqual(first['confirmation_id'], second['confirmation_id'])
        self.assertEqual(len(first['confirmation_id']), 43)
        self.assertEqual(first['status'], 'pending')
        self.assertEqual(first['user_id'], 'alice')
        self.assertEqual(first['permission'], 'sensitive')
        self.assertEqual(first['arguments'], self.arguments)
        self.assertEqual(first['arguments_hash'], canonical_arguments(self.arguments)[1])
        self.assertIsNone(first['approved_at'])
        self.assertIsNone(first['consumed_at'])
        self.assertEqual(self.store.get('alice', first['confirmation_id']), first)
        self.assertEqual(self.calls, [])

    def test_pending_cannot_execute_approve_is_explicit_and_consumed_once(self):
        row = self.create()
        cid = row['confirmation_id']
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid)
        approved = self.store.approve('alice', cid)
        self.assertEqual(approved['status'], 'approved')
        self.assertIsNotNone(approved['approved_at'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.execute(cid), 'done')
        consumed = self.store.get('alice', cid)
        self.assertEqual(consumed['status'], 'consumed')
        self.assertIsNotNone(consumed['consumed_at'])
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid)
        self.assertEqual(self.calls, [self.arguments])

    def test_other_user_cannot_get_approve_cancel_or_consume(self):
        cid = self.approved()['confirmation_id']
        for operation in ('get', 'approve', 'cancel'):
            with self.subTest(operation=operation), self.assertRaisesRegex(ConfirmationRejected, '^Confirmation rejected$'):
                getattr(self.store, operation)('bob', cid)
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid, access=AccessContext('bob', self.security))
        self.assertEqual(self.store.get('alice', cid)['status'], 'approved')
        self.assertEqual(self.calls, [])
        last = self.memory._connection.execute('SELECT tool, confirmation_id FROM security_audit ORDER BY audit_id DESC LIMIT 1').fetchone()
        self.assertEqual(last, ('<unknown>', None))

    def test_cancel_pending_and_approved_and_invalid_transitions(self):
        for approve in (False, True):
            row = self.approved() if approve else self.create()
            cid = row['confirmation_id']
            self.assertEqual(self.store.cancel('alice', cid)['status'], 'cancelled')
            for operation in ('approve', 'cancel'):
                with self.assertRaises(ConfirmationRejected):
                    getattr(self.store, operation)('alice', cid)
            with self.assertRaises(ConfirmationRejected):
                self.execute(cid)
        self.assertEqual(self.calls, [])

    def test_repeated_approve_and_changes_after_consumption_are_rejected(self):
        cid = self.approved()['confirmation_id']
        with self.assertRaises(ConfirmationRejected):
            self.store.approve('alice', cid)
        self.execute(cid)
        for operation in ('approve', 'cancel'):
            with self.assertRaises(ConfirmationRejected):
                getattr(self.store, operation)('alice', cid)
        self.assertEqual(self.store.get('alice', cid)['status'], 'consumed')

    def test_pending_and_approved_expire_at_exact_deadline(self):
        for approve in (False, True):
            row = self.approved() if approve else self.create()
            cid = row['confirmation_id']
            with patch('security.confirmations.utc_now', return_value=row['expires_at']):
                self.assertEqual(self.store.get('alice', cid)['status'], 'expired')
                for operation in ('approve', 'cancel'):
                    with self.assertRaises(ConfirmationRejected):
                        getattr(self.store, operation)('alice', cid)
                with self.assertRaises(ConfirmationRejected):
                    self.execute(cid)
                self.store.get('alice', cid)
        self.assertEqual(self.events().count('confirmation_expired'), 2)
        self.assertEqual(self.calls, [])

    def test_expiry_on_consume_without_polling(self):
        row = self.approved()
        with patch('security.confirmations.utc_now', return_value=row['expires_at']):
            with self.assertRaises(ConfirmationRejected):
                self.execute(row['confirmation_id'])
        self.assertEqual(self.store.get('alice', row['confirmation_id'])['status'], 'expired')

    def test_changed_arguments_tool_permission_and_session_are_rejected(self):
        session = self.memory.sessions.create_session('alice')
        other_session = self.memory.sessions.create_session('alice')
        cid = self.approved(session_id=session.session_id)['confirmation_id']
        attempts = (
            {'arguments': {**self.arguments, 'count': 2}, 'session_id': session.session_id},
            {'arguments': {**self.arguments, 'options': {'enabled': False}}, 'session_id': session.session_id},
            {'name': 'other', 'session_id': session.session_id},
            {'session_id': other_session.session_id},
            {},
        )
        for kwargs in attempts:
            with self.subTest(kwargs=kwargs), self.assertRaises(ConfirmationRejected):
                self.execute(cid, **kwargs)
        self.security.grant('alice', 'danger', 'write')
        original = self.registry._tools['danger']
        self.registry._tools['danger'] = replace(original, permission='write')
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid, session_id=session.session_id)
        self.registry._tools['danger'] = original
        self.assertEqual(self.calls, [])
        self.assertEqual(self.execute(cid, session_id=session.session_id), 'done')

    def test_canonical_object_order_does_not_change_arguments(self):
        cid = self.approved()['confirmation_id']
        reordered = {'options': {'enabled': True}, 'count': 1, 'target': 'draft'}
        self.assertEqual(self.execute(cid, arguments=reordered), 'done')
        self.assertEqual(self.calls, [self.arguments])
        self.assertNotEqual(canonical_arguments({'n': 1})[1], canonical_arguments({'n': 1.0})[1])

    def test_missing_unknown_and_malformed_confirmation_never_execute(self):
        self.approved()
        fresh = self.create()
        self.assertEqual(fresh['status'], 'pending')
        for cid in ('', 'unknown', secrets.token_urlsafe(32)):
            with self.assertRaises(ConfirmationRejected):
                self.execute(cid)
        self.assertEqual(self.calls, [])

    def test_foreign_session_cannot_create_confirmation(self):
        session = self.memory.sessions.create_session('bob')
        with self.assertRaises(ConfirmationRejected):
            self.execute(session_id=session.session_id)
        self.assertEqual(self.memory._connection.execute('SELECT count(*) FROM confirmations').fetchone()[0], 0)

    def test_revoked_permission_and_disabled_user_rechecked(self):
        cid = self.approved()['confirmation_id']
        self.security.revoke_permission('alice', 'danger', 'sensitive')
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid)
        self.security.grant('alice', 'danger', 'sensitive')
        self.security.set_status('alice', 'disabled')
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid)
        self.assertEqual(self.calls, [])

    def test_handler_failure_stays_consumed_and_never_retries(self):
        cid = self.approved()['confirmation_id']
        def fail(arguments):
            self.calls.append(arguments)
            raise RuntimeError('mock failure')
        self.registry._tools['danger'] = replace(self.registry._tools['danger'], handler=fail)
        with self.assertRaises(ValueError):
            self.execute(cid)
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid)
        self.assertEqual(self.store.get('alice', cid)['status'], 'consumed')
        self.assertEqual(len(self.calls), 1)

    def test_atomic_audit_failure_rolls_back_consume_and_prevents_action(self):
        cid = self.approved()['confirmation_id']
        real_audit = self.security._audit_in_transaction
        def audit(*args, **kwargs):
            if len(args) > 4 and args[4] == 'confirmation_consumed':
                raise sqlite3.OperationalError('mock audit failure')
            return real_audit(*args, **kwargs)
        with patch.object(self.security, '_audit_in_transaction', side_effect=audit):
            with self.assertRaises(ConfirmationRejected):
                self.execute(cid)
        self.assertEqual(self.store.get('alice', cid)['status'], 'approved')
        self.assertEqual(self.calls, [])
        self.execute(cid)
        self.assertEqual(len(self.calls), 1)

    def test_secrets_and_freeform_strings_not_persisted(self):
        issued = self.security.issue_token('alice')
        for arguments in ({'token': issued.token}, {'target': issued.token}, {'target': secrets.token_urlsafe(32)},
                          {'count': float('nan')}, {'count': True}, {'options': {'password': 'not-stored'}}):
            with self.subTest(keys=list(arguments)), self.assertRaises(ConfirmationRejected):
                self.execute(arguments=arguments)
        self.assertEqual(self.memory._connection.execute('SELECT count(*) FROM confirmations').fetchone()[0], 0)
        dump = '\n'.join(self.memory._connection.iterdump())
        self.assertNotIn(issued.token, dump)
        self.assertNotIn('not-stored', dump)
        self.assertEqual(self.calls, [])

    def test_dialogue_creates_bound_session_and_resumes_after_approval(self):
        core = Assistant(self.memory, self.registry).for_user(self.security.get_user('alice'))
        parameters = {'message': '/tool danger ' + json.dumps(self.arguments)}
        first = core.handle(Request('dialogue', parameters))
        self.assertTrue(first.ok, first.error)
        cid = first.result['confirmation_id']
        sid = first.result['session_id']
        self.assertIsNotNone(sid)
        self.assertEqual(self.memory.sessions.recent_messages('alice', sid), [])
        self.store.approve('alice', cid)
        response = core.handle(Request('dialogue', {**parameters, 'session_id': sid, 'confirmation_id': cid}))
        self.assertTrue(response.ok, response.error)
        self.assertEqual(len(self.memory.sessions.recent_messages('alice', sid)), 2)
        self.assertEqual(self.calls, [self.arguments])
        self.assertFalse(core.handle(Request('dialogue', {**parameters, 'session_id': sid, 'confirmation_id': cid})).ok)

    def test_audit_trail_records_all_six_events_without_arguments(self):
        cid = self.approved()['confirmation_id']
        self.execute(cid)
        with self.assertRaises(ConfirmationRejected):
            self.execute(cid)
        self.store.cancel('alice', self.create()['confirmation_id'])
        row = self.create()
        with patch('security.confirmations.utc_now', return_value=row['expires_at']):
            self.store.get('alice', row['confirmation_id'])
        self.assertTrue({'confirmation_created', 'confirmation_approved', 'confirmation_consumed',
                         'confirmation_rejected', 'confirmation_cancelled', 'confirmation_expired'} <= set(self.events()))
        audit = repr(self.memory._connection.execute('SELECT * FROM security_audit').fetchall())
        self.assertNotIn('draft', audit)
        self.assertNotIn('options', audit)

    def test_ttl_configuration(self):
        with patch.dict('os.environ', {'ASSISTANT_CONFIRMATION_TTL_SECONDS': '7'}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.confirmation_ttl_seconds, 7)
        self.registry = ToolRegistry(confirmation_ttl_seconds=7)
        self.registry.register(Tool('danger', lambda a: 'ok', permission='sensitive', confirmation_schema=SCHEMA), enabled=True)
        row = self.create()
        self.assertEqual((datetime.fromisoformat(row['expires_at']) - datetime.fromisoformat(row['created_at'])).total_seconds(), 7)
        for value in (0, 3601, True):
            with self.assertRaises(ValueError):
                ToolRegistry(confirmation_ttl_seconds=value)


class ConfirmationPersistenceTests(unittest.TestCase):
    def prepare(self, path):
        with SQLiteMemory(path) as memory:
            memory.security.create_user('alice')
            memory.security.grant('alice', 'mock', 'sensitive')
            registry = ToolRegistry()
            registry.register(Tool('mock', lambda a: 'ok', permission='sensitive'), enabled=True)
            with self.assertRaises(ConfirmationRequired) as raised:
                registry.execute('mock', {}, access=AccessContext('alice', memory.security))
            cid = raised.exception.confirmation['confirmation_id']
            memory.security.confirmations.approve('alice', cid)
        return cid

    def test_reopen_and_competing_connections_invoke_handler_at_most_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'history.sqlite3')
            cid = self.prepare(path)
            barrier = threading.Barrier(2)
            results = []
            calls = []
            errors = []
            def worker():
                try:
                    with SQLiteMemory(path) as memory:
                        registry = ToolRegistry()
                        def handler(arguments):
                            calls.append(arguments)
                            return 'ok'
                        registry.register(Tool('mock', handler, permission='sensitive'), enabled=True)
                        barrier.wait(20)
                        try:
                            registry.execute('mock', {}, access=AccessContext('alice', memory.security), confirmation_id=cid)
                            results.append('consumed')
                        except ConfirmationRejected:
                            results.append('rejected')
                except Exception as exc:
                    errors.append(type(exc).__name__)
            workers = [threading.Thread(target=worker) for _ in range(2)]
            for worker_thread in workers:
                worker_thread.start()
            for worker_thread in workers:
                worker_thread.join(25)
                self.assertFalse(worker_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertCountEqual(results, ['consumed', 'rejected'])
            self.assertEqual(calls, [{}])
            with SQLiteMemory(path) as memory:
                self.assertEqual(memory.security.confirmations.get('alice', cid)['status'], 'consumed')
                events = memory._connection.execute("SELECT count(*) FROM security_audit WHERE event='confirmation_consumed'").fetchone()[0]
                self.assertEqual(events, 1)

    def test_additive_audit_migration_preserves_old_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'old.sqlite3')
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE security_audit (audit_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, user_id TEXT, tool TEXT NOT NULL, requested_permission TEXT, decision TEXT NOT NULL CHECK(decision IN ('allow','deny','confirmation_required')))")
                connection.execute("INSERT INTO security_audit(timestamp,user_id,tool,requested_permission,decision) VALUES ('old','legacy','mock','read','allow')")
            with SQLiteMemory(path) as memory:
                self.assertEqual(memory._connection.execute('SELECT decision, event, confirmation_id FROM security_audit').fetchone(), ('allow', None, None))


if __name__ == '__main__':
    unittest.main()
