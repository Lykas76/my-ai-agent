import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from memory.sqlite import SQLiteMemory
from scheduler.store import Scheduler, timestamp
from tools.registry import Tool, ToolRegistry
from tools.schema import object_schema

NOW="2030-01-01T00:00:00.000000+00:00"


def registry(handler=lambda a: "ok", permission="read", retry_safe=True):
    tools=ToolRegistry()
    tools.register(Tool("work",handler,permission=permission,schema=object_schema(),confirmation_schema={},retry_safe=retry_safe),enabled=True)
    return tools


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.memory=SQLiteMemory(":memory:"); self.addCleanup(self.memory.close)
        self.memory.security.create_user("a"); self.memory.security.create_user("b")
        self.memory.security.grant("a","work","read")
        self.calls=[]
        self.tools=registry(lambda a: (self.calls.append(1) or "ok"))
        self.scheduler=Scheduler(self.memory,self.tools)

    def create(self,**kwargs):
        return self.scheduler.create("a",{"tool":"work","next_run_at":NOW,**kwargs})

    def test_one_shot_claim_and_no_double_execution(self):
        task=self.create()
        self.assertEqual(self.scheduler.run_one(NOW)["status"],"success")
        self.assertIsNone(self.scheduler.run_one(NOW))
        self.assertEqual(self.calls,[1])
        self.assertFalse(self.scheduler.get("a",task["task_id"])["enabled"])
        self.assertEqual(self.scheduler.runs("a",task["task_id"])[0]["status"],"success")

    def test_recurring_disable_enable_and_timezone(self):
        task=self.create(interval_seconds=60)
        self.scheduler.enable("a",task["task_id"],False)
        self.assertIsNone(self.scheduler.run_one(NOW))
        self.scheduler.enable("a",task["task_id"],True)
        self.scheduler.run_one(NOW)
        self.assertIsNone(self.scheduler.run_one(NOW))
        self.assertEqual(self.scheduler.run_one("2030-01-01T00:01:00+00:00")["status"],"success")
        self.assertEqual(len(self.calls),2)
        self.assertEqual(timestamp("2030-01-01T02:00:00+02:00"),"2030-01-01T00:00:00+00:00")

    def test_isolation_and_validation(self):
        task=self.create()
        for action in (lambda:self.scheduler.get("b",task["task_id"]),
                       lambda:self.scheduler.runs("b",task["task_id"]),
                       lambda:self.scheduler.enable("b",task["task_id"],False),
                       lambda:self.create(interval_seconds=1),
                       lambda:self.create(next_run_at="2030-01-01"),
                       lambda:self.create(arguments={"extra":1}),
                       lambda:self.create(user_id="b")):
            with self.assertRaises(ValueError): action()
        self.assertEqual(self.scheduler.list("b"),[])

    def test_crashed_claim_is_quarantined(self):
        task=self.create()
        claimed=self.scheduler.claim(NOW)
        self.assertIsNotNone(claimed["running_run"])
        self.assertIsNone(self.scheduler.run_one("2030-01-02T00:00:00+00:00"))
        self.assertEqual(self.scheduler.runs("a",task["task_id"])[0]["status"],"running")

    def test_retry_only_safe_read_and_bounded(self):
        def fail(args): raise RuntimeError("private details")
        self.scheduler.registry=registry(fail)
        task=self.create(retry_limit=1)
        self.assertEqual(self.scheduler.run_one(NOW)["status"],"failed")
        self.assertTrue(self.scheduler.get("a",task["task_id"])["enabled"])
        self.scheduler.run_one("2030-01-01T00:00:30+00:00")
        self.assertFalse(self.scheduler.get("a",task["task_id"])["enabled"])
        self.scheduler.registry=registry(permission="write")
        self.memory.security.grant("a","work","write")
        with self.assertRaises(ValueError): self.create(retry_limit=1)

    def test_permission_revocation_blocks_run(self):
        self.create()
        self.memory.security.revoke_permission("a","work","read")
        self.assertEqual(self.scheduler.run_one(NOW)["status"],"failed")
        self.assertEqual(self.calls,[])

    def test_sensitive_task_pauses_with_confirmation_no_execution(self):
        self.scheduler.registry=registry(lambda a: (self.calls.append(1) or "ok"),permission="sensitive",retry_safe=False)
        self.memory.security.grant("a","work","sensitive")
        task=self.create(interval_seconds=60)
        result=self.scheduler.run_one(NOW)
        self.assertEqual(result["status"],"confirmation_required")
        self.assertFalse(self.scheduler.get("a",task["task_id"])["enabled"])
        self.assertEqual(self.calls,[])
        self.assertIsNotNone(result["confirmation_id"])

    def test_persistence_and_two_connections_claim_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"tasks.sqlite3")
            with SQLiteMemory(path) as memory:
                memory.security.create_user("a"); memory.security.grant("a","work","read")
                Scheduler(memory,registry()).create("a",{"tool":"work","next_run_at":NOW})
            barrier=threading.Barrier(2); results=[]; failures=[]; calls=[]
            def consume():
                try:
                    with SQLiteMemory(path) as memory:
                        scheduler=Scheduler(memory,registry(lambda a: (calls.append(1) or "ok")))
                        barrier.wait(5)
                        results.append(scheduler.run_one(NOW))
                except Exception as exc: failures.append(exc)
            threads=[threading.Thread(target=consume) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures,[])
            self.assertEqual(len([item for item in results if item]),1)
            self.assertEqual(calls,[1])
