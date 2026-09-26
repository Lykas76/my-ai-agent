import http.client
import json
import threading
import unittest
from http.server import HTTPServer
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from api.server import make_handler
from assistant.core import Assistant
from memory.sqlite import SQLiteMemory
from tools.registry import ToolRegistry
from adapters.builtin import register_builtin


class VersionedApiTests(unittest.TestCase):
    def setUp(self):
        self.ready=threading.Event(); self.failures=[]
        def run():
            try:
                with SQLiteMemory(":memory:") as memory:
                    tools=ToolRegistry(); register_builtin(tools)
                    a=memory.security.create_user("a"); b=memory.security.create_user("b")
                    self.token=memory.security.issue_token("a").token
                    self.other=memory.security.issue_token("b").token
                    self.expired=memory.security.issue_token("a").token
                    with memory._connection:
                        memory._connection.execute("UPDATE api_tokens SET expires_at=? WHERE token_id=?",
                            ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),self.expired[:32]))
                    memory.security.grant("a","android.action","sensitive")
                    memory.security.grant("a","reminders.notify","write")
                    with HTTPServer(("127.0.0.1",0),make_handler(Assistant(memory,tools))) as server:
                        self.server=server; self.ready.set()
                        server.serve_forever(poll_interval=.01)
            except Exception as exc:
                self.failures.append(exc); self.ready.set()
        self.thread=threading.Thread(target=run,daemon=True); self.thread.start()
        self.assertTrue(self.ready.wait(10))
        if self.failures: raise self.failures[0]
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.thread.join(10)
        self.assertFalse(self.thread.is_alive())

    def request(self,method,path,body=None,token="default"):
        headers={}
        if token is not None: headers["Authorization"]="Bearer "+(self.token if token=="default" else token)
        if body is not None: headers["Content-Type"]="application/json"
        client=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        try:
            client.request(method,"/api/v1/"+path,json.dumps(body) if body is not None else None,headers)
            response=client.getresponse()
            return response.status,json.loads(response.read()),dict(response.getheaders())
        finally: client.close()

    def test_health_readiness_auth_expiry_headers(self):
        self.assertEqual(self.request("GET","health",token=None)[0],200)
        self.assertEqual(self.request("GET","ready",token=None)[0],200)
        for token in (None,self.expired,"invalid"):
            self.assertEqual(self.request("GET","sessions",token=token)[0],401)
        status,body,headers=self.request("POST","auth/login",{})
        self.assertEqual(status,200); self.assertEqual(body["result"]["user_id"],"a")
        self.assertEqual(headers["X-Content-Type-Options"],"nosniff")
        self.assertIsNotNone(body["result"]["token_expires_at"])

    def test_rotation_revokes_old_and_new_token_can_be_revoked(self):
        status,body,_=self.request("POST","auth/token",{})
        self.assertEqual(status,200)
        token=body["result"]["token"]
        self.assertEqual(self.request("GET","status")[0],401)
        self.assertEqual(self.request("GET","status",token=token)[0],200)
        self.assertEqual(self.request("POST","auth/revoke",{},token)[0],200)
        self.assertEqual(self.request("GET","status",token=token)[0],401)

    def test_chat_sessions_history_and_owner_isolation(self):
        status,body,_=self.request("POST","chat",{"message":"hello"})
        self.assertEqual(status,200)
        sid=body["result"]["session_id"]
        self.assertEqual(self.request("GET","sessions/"+sid+"/history")[1]["result"]["items"][0]["content"],"hello")
        self.assertEqual(self.request("GET","sessions/"+sid+"/history",token=self.other)[0],403)
        self.assertEqual(self.request("POST","chat",{"message":"hello","user_id":"b"})[0],400)
        self.assertEqual(self.request("POST","chat",{"message":"hello","session_id":sid},self.other)[0],403)

    def test_confirmation_api_binds_and_never_allows_replay(self):
        status,body,_=self.request("POST","chat",{"message":"/tool android.action {}"})
        self.assertEqual(status,200)
        cid=body["result"]["confirmation_id"]
        self.assertEqual(self.request("POST","confirmations/"+cid+"/execute",{})[0],403)
        self.assertEqual(self.request("POST","confirmations/"+cid+"/approve",{},self.other)[0],403)
        self.assertEqual(self.request("POST","confirmations/"+cid+"/approve",{"arguments":{}})[0],400)
        self.assertEqual(self.request("POST","confirmations/"+cid+"/approve",{})[0],200)
        self.assertEqual(self.request("POST","confirmations/"+cid+"/execute",{})[0],200)
        self.assertEqual(self.request("POST","confirmations/"+cid+"/execute",{})[0],403)
        self.assertEqual(self.request("GET","confirmations")[1]["result"]["items"],[])

    def test_reminders_tasks_and_private_inbox(self):
        status,body,_=self.request("POST","reminders",{"text":"tea","next_run_at":"2030-01-01T00:00:00Z"})
        self.assertEqual(status,200)
        task=body["result"]["task_id"]
        self.assertEqual(self.request("GET","tasks",token=self.other)[1]["result"]["items"],[])
        self.assertEqual(self.request("POST","tasks/"+task+"/enabled",{"enabled":False},self.other)[0],403)
        self.assertEqual(self.request("POST","tasks/"+task+"/enabled",{"enabled":False})[0],200)
        self.assertEqual(self.request("GET","tasks/"+task+"/runs")[0],200)
        self.assertEqual(self.request("GET","notifications")[1]["result"]["items"],[])
