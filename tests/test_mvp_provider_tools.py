import json
import secrets
import time
import unittest
from unittest.mock import patch
from assistant.openai_provider import OpenAICompatibleProvider
from assistant.providers import ProviderRequest, ProviderResponse, StructuredToolRequest, ToolResult
from assistant.intent import Intent
from assistant.core import Assistant
from assistant.models import Request
from memory.sqlite import SQLiteMemory
from security.store import AccessContext
from tools.registry import Tool, ToolRegistry, ConfirmationRequired
from tools.schema import object_schema, validate
from adapters.builtin import register_builtin
from runtime.http import NetworkError, validate_url
from runtime.errors import ProviderError


class ProviderTests(unittest.TestCase):
    def provider(self, transport, **kwargs):
        return OpenAICompatibleProvider(model="mock-model",api_key=secrets.token_urlsafe(24),transport=transport,**kwargs)

    def test_text_request_bounded_and_system_prompt(self):
        captured=[]
        provider=self.provider(lambda *args: (captured.append(args) or {"choices":[{"message":{"content":"reply"}}]}))
        response=provider.generate(ProviderRequest("hello",Intent.CHAT))
        self.assertEqual(response.text,"reply")
        self.assertEqual(captured[0][1]["messages"][0]["role"],"system")
        self.assertEqual(captured[0][3],20)

    def test_post_tool_context_lists_available_tools_without_second_tool_call(self):
        captured = []

        provider = self.provider(
            lambda *args: (
                captured.append(args)
                or {"choices": [{"message": {"content": "summary"}}]}
            )
        )

        request = ProviderRequest(
            "show mail",
            Intent.TOOL,
            tool_result=ToolResult("gmail.list_unread", "[]"),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "gmail.list_unread",
                        "description": "List unread mail",
                        "parameters": {"type": "object"},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "gmail.get_message",
                        "description": "Read one message",
                        "parameters": {"type": "object"},
                    },
                },
            ),
        )

        response = provider.generate(request)

        self.assertEqual(response.text, "summary")

        payload = captured[0][1]
        self.assertNotIn("tools", payload)

        user_payload = json.loads(payload["messages"][-1]["content"])

        self.assertEqual(
            user_payload["available_tools"],
            ["gmail.list_unread", "gmail.get_message"],
        )
        self.assertEqual(
            user_payload["tool_result"],
            "[]",
        )

    def test_structured_call_requires_registered_definition(self):
        answer={"choices":[{"message":{"tool_calls":[{"type":"function","function":{"name":"tool_0","arguments":'{"key":"x"}'}}]}}]}
        provider=self.provider(lambda *a: answer)
        request=ProviderRequest("x",Intent.CHAT,tools=({"type":"function","function":{"name":"notes.get"}},))
        self.assertEqual(provider.generate(request).tool_call.arguments,{"key":"x"})
        with self.assertRaises(ProviderError):
            provider.generate(ProviderRequest("x",Intent.CHAT))

    def test_invalid_json_multiple_calls_and_network_are_safe(self):
        for data in ({}, {"choices":[{"message":{"content":""}}]},
                     {"choices":[{"message":{"tool_calls":[{},{}]}}]}):
            with self.subTest(data=data), self.assertRaises(ProviderError):
                self.provider(lambda *a: data).generate(ProviderRequest("x",Intent.CHAT))
        def failure(*args): raise NetworkError()
        with self.assertRaises(ProviderError):
            self.provider(failure).generate(ProviderRequest("x",Intent.CHAT))

    def test_retry_only_explicit_transient_rejection_before_any_tool(self):
        calls=[]
        def failure(*args):
            calls.append(1)
            raise NetworkError(True)
        with self.assertRaises(ProviderError):
            self.provider(failure).generate(ProviderRequest("x",Intent.CHAT))
        self.assertEqual(len(calls),2)
        calls.clear()
        with self.assertRaises(ProviderError):
            self.provider(failure).generate(ProviderRequest("x",Intent.CHAT,tool_result=ToolResult("a","b")))
        self.assertEqual(len(calls),1)

    def test_endpoint_rejects_http_credentials_query_and_invalid_config(self):
        for url in ("http://remote.test","https://user:pass@remote.test","https://remote.test?key=x"):
            with self.assertRaises(ValueError): validate_url(url)
        with self.assertRaises(ValueError): self.provider(lambda *a: None,timeout=0)


class TypedToolsTests(unittest.TestCase):
    def setUp(self):
        self.memory=SQLiteMemory(":memory:")
        self.addCleanup(self.memory.close)
        self.user=self.memory.security.create_user("a")
        self.other=self.memory.security.create_user("b")
        self.access=AccessContext("a",self.memory.security)
        self.registry=ToolRegistry()
        register_builtin(self.registry)

    def grant(self,name,permission):
        self.memory.security.grant("a",name,permission)

    def test_types_required_extra_arguments(self):
        schema=object_schema({"count":{"type":"integer","minimum":1}},("count",))
        for args in ({},{"count":True},{"count":0},{"count":1,"extra":1}):
            with self.assertRaises(ValueError): validate(args,schema)
        validate({"count":1},schema)

    def test_notes_are_scoped_and_result_unified(self):
        self.grant("notes.put","write"); self.grant("notes.get","read")
        value=self.registry.execute_result("notes.put",{"key":"x","text":"value"},access=self.access)
        self.assertIsInstance(value,ToolResult)
        self.assertEqual(self.registry.execute("notes.get",{"key":"x"},access=self.access),"value")
        self.memory.security.grant("b","notes.get","read")
        self.assertEqual(self.registry.execute("notes.get",{"key":"x"},access=AccessContext("b",self.memory.security)),"Note not found")

    def test_stub_sensitive_requires_approval_and_is_one_use(self):
        self.grant("android.action","sensitive")
        with self.assertRaises(ConfirmationRequired) as exc:
            self.registry.execute("android.action",{},access=self.access)
        cid=exc.exception.confirmation["confirmation_id"]
        self.memory.security.confirmations.approve("a",cid)
        result=self.registry.execute("android.action",{},access=self.access,confirmation_id=cid)
        self.assertIn("not_connected",result)
        with self.assertRaises(ValueError): self.registry.execute("android.action",{},access=self.access,confirmation_id=cid)

    def test_context_deadline_and_handler_timeout(self):
        self.registry.register(Tool("slow",lambda args: (time.sleep(.02) or "done"),permission="read",timeout_seconds=.001),enabled=True)
        self.grant("slow","read")
        with self.assertRaisesRegex(ValueError,"Do not retry"):
            self.registry.execute("slow",{},access=self.access)

    def test_structured_provider_calls_registry_and_text_does_not(self):
        self.grant("notes.get","read")
        class Provider:
            def generate(self,request):
                return ProviderResponse(tool_call=StructuredToolRequest("notes.get",{"key":"x"}))
        core=Assistant(self.memory,self.registry,Provider()).for_user(self.user)
        response=core.handle(Request("dialogue",{"message":"find my note"}))
        self.assertTrue(response.ok)
        self.assertEqual(response.result["tool_used"],"notes.get")
        class TextProvider:
            def generate(self,request): return "/tool notes.put " + '{"key":"x","text":"unsafe"}'
        core=Assistant(self.memory,self.registry,TextProvider()).for_user(self.user)
        self.assertTrue(core.handle(Request("dialogue",{"message":"hello"})).ok)
        self.assertIsNone(self.memory.for_user("a").get("note:x"))

    def test_model_cannot_bypass_permission_or_confirmation(self):
        class Provider:
            def generate(self,request): return ProviderResponse(tool_call=StructuredToolRequest("android.action",{}))
        core=Assistant(self.memory,self.registry,Provider()).for_user(self.user)
        self.assertFalse(core.handle(Request("dialogue",{"message":"do it"})).ok)
        self.grant("android.action","sensitive")
        response=core.handle(Request("dialogue",{"message":"do it"}))
        self.assertEqual(response.result["status"],"confirmation_required")

    def test_secrets_rejected_by_note_adapter(self):
        self.grant("notes.put","write")
        token=secrets.token_hex(16)+"."+secrets.token_urlsafe(32)
        with self.assertRaises(ValueError):
            self.registry.execute("notes.put",{"key":"x","text":token},access=self.access)
        self.assertIsNone(self.memory.for_user("a").get("note:x"))
