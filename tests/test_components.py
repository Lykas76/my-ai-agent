import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import HTTPServer
from unittest.mock import patch

from assistant.core import Assistant
from assistant.models import Request
from api.server import make_handler
from config import Settings
from memory.sqlite import SQLiteMemory
from tools.registry import Tool, ToolDenied, ToolRegistry


class MemoryTests(unittest.TestCase):
    def test_persistence_update_delete_and_sql_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'nested' / 'memory.sqlite3')
            key = "'; DROP TABLE memory; --"
            with SQLiteMemory(path) as memory:
                self.assertIsNone(memory.get(key))
                memory.put(key, 'first')
                memory.put(key, 'second')
            with SQLiteMemory(path) as memory:
                self.assertEqual(memory.get(key), 'second')
                self.assertTrue(memory.delete(key))
                self.assertFalse(memory.delete(key))
                self.assertIsNone(memory.get(key))


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.memory = SQLiteMemory(':memory:')
        self.addCleanup(self.memory.close)
        self.registry = ToolRegistry()
        self.core = Assistant(self.memory, self.registry)

    def test_routes(self):
        self.assertEqual(self.core.handle(Request('ping')).result, 'pong')
        self.assertTrue(self.core.handle(Request('memory.put', {'key': 'name', 'value': 'test'})).ok)
        self.assertEqual(self.core.handle(Request('memory.get', {'key': 'name'})).result, 'test')
        self.assertTrue(self.core.handle(Request('memory.delete', {'key': 'name'})).result)
        self.assertFalse(self.core.handle(Request('unknown')).ok)
        self.assertFalse(self.core.handle(Request('memory.put', {'key': 'name'})).ok)

    def test_tool_allowlist(self):
        called = []
        self.registry.register(Tool('disabled', lambda p: called.append(p)))
        for name in ('disabled', 'unknown'):
            with self.assertRaises(ToolDenied):
                self.registry.execute(name, {})
        self.assertEqual(called, [])
        self.registry.register(Tool('echo', lambda p: p['text']), enabled=True)
        self.assertEqual(self.core.handle(Request('tool.run', {'name': 'echo', 'arguments': {'text': 'hello'}})).result, 'hello')
        self.assertFalse(self.core.handle(Request('tool.run', {'name': 'disabled', 'enabled': True})).ok)
        with self.assertRaises(ValueError):
            self.registry.register(Tool('echo', str))

    def test_input_validation(self):
        for payload in (None, [], {}, {'action': 1}, {'action': 'ping', 'parameters': []}, {'action': 'ping', 'extra': True}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                Request.from_dict(payload)

    def test_settings(self):
        with patch.dict('os.environ', {'ASSISTANT_PORT': '', 'ASSISTANT_DB_PATH': ''}, clear=True):
            self.assertEqual(Settings.from_env(), Settings())
        with patch.dict('os.environ', {'ASSISTANT_PORT': '70000'}):
            with self.assertRaises(ValueError):
                Settings.from_env()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.ready = threading.Event()
        self.failures = []
        def run():
            try:
                with SQLiteMemory(':memory:') as memory:
                    with HTTPServer(('127.0.0.1', 0), make_handler(Assistant(memory, ToolRegistry()))) as server:
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

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_health_and_not_found(self):
        self.assertEqual(self.request('GET', '/health'), (200, {'status': 'ok'}))
        self.assertEqual(self.request('GET', '/missing')[0], 404)

    def test_memory_round_trip(self):
        headers = {'Content-Type': 'application/json'}
        for action, parameters, expected in [
            ('memory.put', {'key': 'city', 'value': 'Кишинёв'}, 'saved'),
            ('memory.get', {'key': 'city'}, 'Кишинёв'),
            ('memory.delete', {'key': 'city'}, True),
        ]:
            status, payload = self.request('POST', '/requests', json.dumps({'action': action, 'parameters': parameters}), headers)
            self.assertEqual(status, 200)
            self.assertEqual(payload['result'], expected)

    def test_rejected_requests(self):
        headers = {'Content-Type': 'application/json'}
        for body in ('{', '[]', '{"action":"unknown"}', '{"action":"tool.run","parameters":{"name":"android"}}'):
            self.assertEqual(self.request('POST', '/requests', body, headers)[0], 400)
        self.assertEqual(self.request('POST', '/requests', '{}')[0], 415)
        self.assertEqual(self.request('POST', '/requests', '{}', {**headers, 'Origin': 'https://example.com'})[0], 403)
        self.assertEqual(self.request('POST', '/requests', '{}', {**headers, 'Content-Length': '65537'})[0], 413)


if __name__ == '__main__':
    unittest.main()
