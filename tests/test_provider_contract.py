import asyncio
import json
import unittest
from unittest.mock import patch
import httpx
from backend.investigator import Investigator, validate_report

class FixtureTools:
    def __init__(self):
        self.seen = {}

    def call(self, name, arguments):
        return []

class ProviderContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_rounds_and_report_requests_remain_separate(self):
        calls = []
        report = {"assessment": "inconclusive", "summary": "Evidence is insufficient.",
                  "origin": "unknown", "affected_services": [], "evidence_ids": [], "hypotheses": [],
                  "recommendations": ["Collect downstream observations."], "limitations": ["No decisive evidence."]}

        def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            if "tools" in body:
                message = {"role": "assistant", "content": None, "reasoning": "Fixture-only private field",
                           "tool_calls": [{"id": "tool-" + str(len(calls)), "type": "function",
                                           "function": {"name": "search_logs", "arguments": "{}"}}]}
            else:
                self.assertIn("response_format", body)
                self.assertNotIn("tool_choice", body)
                self.assertTrue(all("reasoning" not in message for message in body["messages"]))
                message = {"role": "assistant", "content": json.dumps(report)}
            return httpx.Response(200, json={"model": "fixture-model", "usage": {"total_tokens": 10},
                                             "choices": [{"message": message}]})

        activity = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch("backend.investigator.GROQ_KEY", "fixture-key"):
                tools = FixtureTools()
                result, usage = await Investigator(client).groq(tools, lambda *args: activity.append(args))
                validate_report(result, tools)
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(activity), 4)
        self.assertEqual(usage["requests"], 5)
        self.assertEqual(usage["total_tokens"], 50)
        self.assertIn("error", activity[1][2])

    async def test_rate_limit_is_visible_and_usage_is_retained(self):
        def respond(request):
            return httpx.Response(429, headers={"Retry-After": "60"}, json={"error": "limited"})
        usage = {}
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch("backend.investigator.GROQ_KEY", "fixture-key"):
                with self.assertRaisesRegex(ValueError, "rate limit"):
                    await Investigator(client).groq(FixtureTools(), lambda *args: None, usage)
        self.assertEqual(usage["requests"], 1)

