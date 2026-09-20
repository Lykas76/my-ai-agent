import http.client
import json
import threading
import unittest
from http.server import HTTPServer

from api.server import make_handler
from assistant.core import Assistant
from memory.sqlite import SQLiteMemory
from tools.registry import ToolRegistry


class HistoryEchoProvider:
    def generate(self, request):
        return json.dumps([message.content for message in request.history])


class SessionApiTests(unittest.TestCase):
    def setUp(self):
        self.ready = threading.Event()
        self.failures = []
        def run():
            try:
                with SQLiteMemory(':memory:') as memory:
                    core = Assistant(memory, ToolRegistry(), HistoryEchoProvider())
                    with HTTPServer(('127.0.0.1', 0), make_handler(core)) as server:
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

    def request(self, payload):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('POST', '/requests', json.dumps(payload), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_old_request_creates_default_session_and_can_continue(self):
        payload = {'action': 'dialogue', 'parameters': {'message': 'first'}}
        status, first = self.request(payload)
        self.assertEqual(status, 200)
        result = first['result']
        self.assertEqual(result['user_id'], 'local')
        status, second = self.request({'action': 'dialogue', 'session_id': result['session_id'], 'parameters': {'message': 'second'}})
        self.assertEqual(status, 200)
        self.assertEqual(second['result']['session_id'], result['session_id'])
        self.assertEqual(json.loads(second['result']['reply']), ['first', '[]'])
        status, fresh = self.request(payload)
        self.assertNotEqual(fresh['result']['session_id'], result['session_id'])

    def test_top_level_and_parameter_ids_and_user_isolation(self):
        status, first = self.request({'action': 'dialogue', 'user_id': 'alice', 'parameters': {'message': 'private'}})
        self.assertEqual(status, 200)
        sid = first['result']['session_id']
        status, continuation = self.request({'action': 'dialogue', 'parameters': {'user_id': 'alice', 'session_id': sid, 'message': 'next'}})
        self.assertEqual(status, 200)
        self.assertIn('private', json.loads(continuation['result']['reply']))
        status, denied = self.request({'action': 'dialogue', 'user_id': 'bob', 'session_id': sid, 'parameters': {'message': 'next'}})
        self.assertEqual(status, 400)
        self.assertEqual(denied['error'], 'Session not found')
        status, bob = self.request({'action': 'dialogue', 'user_id': 'bob', 'parameters': {'message': 'next'}})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(bob['result']['reply']), [])

    def test_invalid_and_duplicate_ids_have_safe_errors(self):
        for payload in (
            {'action': 'dialogue', 'user_id': None, 'parameters': {'message': 'x'}},
            {'action': 'dialogue', 'session_id': '', 'parameters': {'message': 'x'}},
            {'action': 'dialogue', 'user_id': 'a', 'parameters': {'user_id': 'b', 'message': 'x'}},
            {'action': 'ping', 'user_id': 'a'},
            {'action': 'dialogue', 'session_id': 'unknown', 'parameters': {'message': 'x'}},
        ):
            with self.subTest(payload=payload):
                status, result = self.request(payload)
                self.assertEqual(status, 400)
                self.assertIn('error', result)


if __name__ == '__main__':
    unittest.main()
