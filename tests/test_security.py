import hashlib
import hmac
import io
import json
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from api.admin import main as admin_main
from assistant.core import Assistant
from assistant.models import Request
from memory.sqlite import SQLiteMemory
from security.store import AccessContext, AuthenticationError
from tools.registry import ConfirmationRequired, Tool, ToolDenied, ToolRegistry


class SecurityStoreTests(unittest.TestCase):
    def setUp(self):
        self.memory = SQLiteMemory(':memory:')
        self.addCleanup(self.memory.close)
        self.store = self.memory.security
        self.user = self.store.create_user()
        self.issued = self.store.issue_token(self.user.user_id)

    def test_authentication_hash_and_constant_time_comparison(self):
        with patch('security.store.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
            user = self.store.authenticate(self.issued.token)
        self.assertEqual(user, self.user)
        compare.assert_called_once()
        row = self.memory._connection.execute('SELECT token_hash FROM api_tokens').fetchone()
        self.assertEqual(row[0], hashlib.sha256(self.issued.token.encode('ascii')).hexdigest())
        self.assertNotIn(self.issued.token, repr(self.issued))
        self.assertEqual(self.user.status, 'active')
        self.assertTrue(self.user.created_at)

    def test_wrong_unknown_revoked_and_disabled_credentials(self):
        wrong = self.issued.token[:33] + secrets.token_urlsafe(32)
        unknown = secrets.token_hex(16) + '.' + secrets.token_urlsafe(32)
        for token in (wrong, unknown, '', None, self.issued.token + 'extra'):
            with self.assertRaises(AuthenticationError):
                self.store.authenticate(token)
        self.store.set_status(self.user.user_id, 'disabled')
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(self.issued.token)
        with self.assertRaises(ValueError):
            self.store.issue_token(self.user.user_id)
        self.store.set_status(self.user.user_id, 'active')
        self.store.revoke_token(self.issued.token_id)
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(self.issued.token)

    def test_persistence_and_raw_token_absent_from_database_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.sqlite3'
            with SQLiteMemory(str(path)) as memory:
                user = memory.security.create_user()
                issued = memory.security.issue_token(user.user_id)
            self.assertNotIn(issued.token.encode(), path.read_bytes())
            self.assertNotIn(issued.token.split('.')[1].encode(), path.read_bytes())
            with SQLiteMemory(str(path)) as memory:
                self.assertEqual(memory.security.authenticate(issued.token).user_id, user.user_id)
                self.assertNotIn(issued.token, '\n'.join(memory._connection.iterdump()))

    def test_scoped_fact_memory_excludes_legacy_and_other_users(self):
        second = self.store.create_user()
        alice = self.memory.for_user(self.user.user_id)
        bob = self.memory.for_user(second.user_id)
        self.memory.put('coffee', 'legacy private')
        alice.put('coffee', 'alice private')
        self.assertIsNone(bob.get('coffee'))
        self.assertEqual(bob.search('coffee'), [])
        self.assertFalse(bob.delete('coffee'))
        bob.put('coffee', 'bob private')
        self.assertEqual(alice.get('coffee'), 'alice private')
        self.assertEqual(self.memory.get('coffee'), 'legacy private')
        self.assertEqual([row.value for row in bob.search('coffee')], ['bob private'])

    def test_admin_bootstrap_and_issue_token_are_local_and_one_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'admin.sqlite3')
            output = io.StringIO()
            with redirect_stdout(output):
                admin_main(['--database', path, 'create-user'])
            user = json.loads(output.getvalue())
            self.assertNotIn('token', user)
            output = io.StringIO()
            with redirect_stdout(output):
                admin_main(['--database', path, 'issue-token', '--user-id', user['user_id']])
            issued = json.loads(output.getvalue())
            with SQLiteMemory(path) as memory:
                self.assertEqual(memory.security.authenticate(issued['token']).user_id, user['user_id'])
                self.assertNotIn(issued['token'], '\n'.join(memory._connection.iterdump()))


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.memory = SQLiteMemory(':memory:')
        self.addCleanup(self.memory.close)
        self.store = self.memory.security
        self.user = self.store.create_user()
        self.access = AccessContext(self.user.user_id, self.store)
        self.registry = ToolRegistry()
        self.calls = []

    def register(self, name, permission, enabled=True):
        def handler(arguments):
            self.calls.append(arguments)
            return 'done'
        self.registry.register(Tool(name, handler, permission=permission), enabled=enabled)

    def execute(self, name, arguments=None):
        return self.registry.execute(name, arguments or {}, access=self.access)

    def decisions(self):
        return self.memory._connection.execute('SELECT tool, requested_permission, decision FROM security_audit ORDER BY audit_id').fetchall()

    def test_allow_read_and_write_with_exact_grants(self):
        for name, permission in (('read_data', 'read'), ('write_data', 'write')):
            self.register(name, permission)
            self.store.grant(self.user.user_id, name, permission)
            self.assertEqual(self.execute(name), 'done')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([row[2] for row in self.decisions()], ['allow', 'allow'])

    def test_default_deny_unknown_disabled_missing_and_wrong_permissions(self):
        self.register('no_grant', 'read')
        self.register('no_metadata', None)
        self.register('invalid_metadata', 'admin')
        self.register('disabled', 'read', enabled=False)
        self.register('wrong_level', 'write')
        for name in ('no_metadata', 'invalid_metadata', 'disabled', 'wrong_level'):
            self.store.grant(self.user.user_id, name, 'read')
        for name in ('unknown', 'no_grant', 'no_metadata', 'invalid_metadata', 'disabled', 'wrong_level'):
            with self.subTest(name=name), self.assertRaises(ToolDenied):
                self.execute(name, {'permission': 'sensitive', 'enabled': True})
        self.assertEqual(self.calls, [])
        self.assertEqual([row[2] for row in self.decisions()], ['deny'] * 6)

    def test_no_identity_is_denied(self):
        self.register('read_data', 'read')
        with self.assertRaises(ToolDenied):
            self.registry.execute('read_data', {})
        self.assertEqual(self.calls, [])

    def test_user_permission_isolation_disable_and_revoke(self):
        self.register('read_data', 'read')
        other = self.store.create_user()
        self.store.grant(other.user_id, 'read_data', 'read')
        with self.assertRaises(ToolDenied):
            self.execute('read_data')
        self.store.grant(self.user.user_id, 'read_data', 'read')
        self.store.set_status(self.user.user_id, 'disabled')
        with self.assertRaises(ToolDenied):
            self.execute('read_data')
        self.store.set_status(self.user.user_id, 'active')
        self.store.revoke_permission(self.user.user_id, 'read_data', 'read')
        with self.assertRaises(ToolDenied):
            self.execute('read_data')
        self.assertEqual(self.calls, [])

    def test_sensitive_never_executes_even_with_confirmed_argument(self):
        self.register('danger', 'sensitive')
        with self.assertRaises(ToolDenied):
            self.execute('danger')
        self.store.grant(self.user.user_id, 'danger', 'sensitive')
        with self.assertRaises(ConfirmationRequired):
            self.execute('danger', {'confirmed': True})
        core = Assistant(self.memory, self.registry, access=self.access)
        for request in (Request('tool.run', {'name': 'danger'}),
                        Request('dialogue', {'message': '/tool danger {"confirmed":true}'})):
            response = core.handle(request)
            self.assertTrue(response.ok)
            self.assertEqual(response.result['status'], 'confirmation_required')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.decisions()[-1], ('danger', 'sensitive', 'confirmation_required'))

    def test_audit_excludes_raw_tokens_arguments_and_unknown_names(self):
        issued = self.store.issue_token(self.user.user_id)
        self.register('read_data', 'read')
        self.store.grant(self.user.user_id, 'read_data', 'read')
        self.execute('read_data', {'private': issued.token})
        with self.assertRaises(ToolDenied):
            self.execute(issued.token)
        rows = self.memory._connection.execute('SELECT * FROM security_audit').fetchall()
        self.assertNotIn(issued.token, repr(rows))
        self.assertEqual(self.decisions()[-1][0], '<unknown>')
        self.assertNotIn(issued.token, '\n'.join(self.memory._connection.iterdump()))

    def test_audit_failure_prevents_action(self):
        self.register('read_data', 'read')
        self.store.grant(self.user.user_id, 'read_data', 'read')
        with patch.object(self.store, 'audit', side_effect=sqlite3.OperationalError('private path')):
            with self.assertRaisesRegex(ToolDenied, '^Tool execution denied$'):
                self.execute('read_data')
        self.assertEqual(self.calls, [])

    def test_direct_tool_errors_do_not_expose_secrets(self):
        marker = secrets.token_urlsafe(32)
        def fail(arguments):
            raise ValueError(marker)
        self.registry.register(Tool('failure', fail, permission='read'), enabled=True)
        self.store.grant(self.user.user_id, 'failure', 'read')
        response = Assistant(self.memory, self.registry, access=self.access).handle(Request('tool.run', {'name': 'failure'}))
        self.assertFalse(response.ok)
        self.assertNotIn(marker, response.error)


if __name__ == '__main__':
    unittest.main()
