import http.client
import io
import json
import secrets
import threading
import unittest
from contextlib import redirect_stderr
from http.server import HTTPServer

from api.server import make_handler
from assistant.core import Assistant
from memory.sqlite import SQLiteMemory
from tools.registry import Tool, ToolRegistry


class SecurityApiTests(unittest.TestCase):
    def setUp(self):
        self.ready = threading.Event()
        self.failures = []
        self.calls = []
        def run():
            try:
                with SQLiteMemory(':memory:') as memory:
                    store = memory.security
                    self.tokens = {}
                    for name in ('alice', 'bob', 'disabled'):
                        store.create_user(name)
                        self.tokens[name] = store.issue_token(name).token
                    store.set_status('disabled', 'disabled')
                    memory.put('legacy', 'shared before authentication')
                    self.legacy_session = memory.sessions.save_turn('alice', None, 'old history', 'old reply').session_id
                    registry = ToolRegistry()
                    def handler(arguments):
                        self.calls.append(arguments)
                        return 'completed'
                    for name, permission in (('reader', 'read'), ('writer', 'write'), ('danger', 'sensitive'), ('no_permission', None)):
                        registry.register(Tool(name, handler, permission=permission), enabled=True)
                        store.grant('alice', name, 'read' if permission is None else permission)
                    self.core = Assistant(memory, registry)
                    with HTTPServer(('127.0.0.1', 0), make_handler(self.core)) as server:
                        self.server = server
                        self.ready.set()
                        server.serve_forever(poll_interval=0.01)
            except Exception as exc:
                self.failures.append(exc)
                self.ready.set()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.assertTrue(self.ready.wait(5))
        if self.failures:
            raise self.failures[0]
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, payload=None, user='alice', headers=None, method='POST', path='/requests'):
        request_headers = {'Content-Type': 'application/json'}
        if user is not None:
            request_headers['Authorization'] = 'Bearer ' + self.tokens[user]
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, json.dumps(payload) if payload is not None else None, request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def test_authentication_required_and_public_health(self):
        status, body, _ = self.request({'action': 'ping'})
        self.assertEqual((status, body['result']), (200, 'pong'))
        for user in (None, 'disabled'):
            status, body, headers = self.request({'action': 'ping'}, user=user)
            self.assertEqual(status, 401)
            self.assertEqual(body, {'error': 'Unauthorized'})
            self.assertEqual(headers['WWW-Authenticate'], 'Bearer')
        status, body, _ = self.request(user=None, method='GET', path='/health')
        self.assertEqual((status, body), (200, {'status': 'ok'}))
        for action in ('dialogue', 'memory.get', 'tool.run'):
            self.assertEqual(self.request({'action': action}, user=None)[0], 401)

    def test_invalid_token_headers_and_no_token_in_logs_or_responses(self):
        wrong = self.tokens['alice'][:33] + secrets.token_urlsafe(32)
        logs = io.StringIO()
        with redirect_stderr(logs):
            for value in ('Bearer ' + wrong, 'Basic ' + self.tokens['alice'], 'Bearer', 'Bearer ' + self.tokens['alice'] + ' extra'):
                status, body, _ = self.request({'action': 'ping'}, user=None, headers={'Authorization': value})
                self.assertEqual(status, 401)
                self.assertNotIn(self.tokens['alice'], json.dumps(body))
                self.assertNotIn(wrong, json.dumps(body))
        self.assertNotIn(self.tokens['alice'], logs.getvalue())
        self.assertNotIn(wrong, logs.getvalue())
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest('POST', '/requests')
            connection.putheader('Authorization', 'Bearer ' + self.tokens['alice'])
            connection.putheader('Authorization', 'Bearer ' + self.tokens['bob'])
            connection.putheader('Content-Length', '0')
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            connection.close()

    def test_identity_is_server_owned_and_other_session_is_inaccessible(self):
        status, body, _ = self.request({'action': 'dialogue', 'parameters': {'message': 'hello'}})
        self.assertEqual(status, 200)
        result = body['result']
        self.assertEqual(result['user_id'], 'alice')
        sid = result['session_id']
        for payload in (
            {'action': 'dialogue', 'user_id': 'alice', 'session_id': sid, 'parameters': {'message': 'hello'}},
            {'action': 'dialogue', 'parameters': {'user_id': 'alice', 'session_id': sid, 'message': 'hello'}},
        ):
            self.assertEqual(self.request(payload, user='bob')[0], 403)
        status, denied, _ = self.request({'action': 'dialogue', 'session_id': sid, 'parameters': {'message': 'hello'}}, user='bob')
        status2, missing, _ = self.request({'action': 'dialogue', 'session_id': 'missing', 'parameters': {'message': 'hello'}}, user='bob')
        self.assertEqual((status, denied['error']), (status2, missing['error']))
        self.assertEqual(denied['error'], 'Session not found')
        status, own, _ = self.request({'action': 'dialogue', 'session_id': sid, 'parameters': {'message': 'next'}})
        self.assertEqual(status, 200)
        self.assertEqual(own['result']['session_id'], sid)
        # Existing block 3 sessions are accessible only after explicit local user adoption.
        status, old, _ = self.request({'action': 'dialogue', 'session_id': self.legacy_session, 'parameters': {'message': 'next'}})
        self.assertEqual(status, 200)
        self.assertEqual(old['result']['user_id'], 'alice')

    def test_registry_permissions_and_sensitive_confirmation_over_http(self):
        for name in ('reader', 'writer'):
            self.assertEqual(self.request({'action': 'tool.run', 'parameters': {'name': name}})[0], 200)
        for name in ('reader', 'unknown', 'no_permission'):
            user = 'bob' if name == 'reader' else 'alice'
            self.assertEqual(self.request({'action': 'tool.run', 'parameters': {'name': name}}, user=user)[0], 403)
        for payload in (
            {'action': 'tool.run', 'parameters': {'name': 'danger', 'arguments': {'confirmed': True}}},
            {'action': 'dialogue', 'parameters': {'message': '/tool danger {"confirmed":true}'}},
        ):
            status, body, _ = self.request(payload)
            self.assertEqual(status, 200)
            self.assertEqual(body['result']['status'], 'confirmation_required')
        self.assertEqual(len(self.calls), 2)

    def test_authenticated_memory_is_private(self):
        self.assertEqual(self.request({'action': 'memory.put', 'parameters': {'key': 'coffee', 'value': 'alice fact'}})[0], 200)
        self.assertIsNone(self.request({'action': 'memory.get', 'parameters': {'key': 'coffee'}}, user='bob')[1]['result'])
        self.assertFalse(self.request({'action': 'memory.delete', 'parameters': {'key': 'coffee'}}, user='bob')[1]['result'])
        self.assertEqual(self.request({'action': 'memory.get', 'parameters': {'key': 'coffee'}})[1]['result'], 'alice fact')
        self.assertIsNone(self.request({'action': 'memory.get', 'parameters': {'key': 'legacy'}})[1]['result'])
        status, body, _ = self.request({'action': 'dialogue', 'parameters': {'message': 'recall coffee'}}, user='bob')
        self.assertEqual(body['result']['context_keys'], [])
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main()
