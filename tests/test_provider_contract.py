import json
import unittest
from unittest.mock import patch
import httpx
from backend.evidence import EvidenceTools, INPUT_BYTES, encoded
from backend.investigator import Investigator, validate_report
from backend.quota import GroqQuota, delay_seconds

REPORT = {"assessment": "inconclusive", "summary": "Evidence is insufficient.",
          "origin": "unknown", "affected_services": [], "evidence_ids": [], "hypotheses": [],
          "recommendations": ["Collect downstream observations."], "limitations": ["No decisive evidence."]}

class Clock:
    def __init__(self):
        self.now = 1800000000.0
        self.waits = []
    def __call__(self):
        return self.now
    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds

class FixtureTools(EvidenceTools):
    def __init__(self):
        super().__init__("fixture", "owner", groq=True)
    def call(self, name, args):
        return {"records": [], "omitted_groups": 0}

def success(body, tools=False):
    message = {"role": "assistant", "content": json.dumps(REPORT), "reasoning": "PRIVATE REASONING"}
    if tools:
        message["tool_calls"] = [{"id": "call", "type": "function", "function": {"name": "search_logs", "arguments": "{}"}}]
    return httpx.Response(200, json={"model": "fixture-model", "usage": {"total_tokens": 10, "prompt_tokens": 7, "completion_tokens": 3},
                                    "choices": [{"message": message, "finish_reason": "stop"}]})

class ProviderContractTests(unittest.IsolatedAsyncioTestCase):
    async def run_provider(self, respond, usage=None, tools=None, quota=None, progress=None):
        self.clock = Clock() if quota is None else None
        quota = quota or GroqQuota(clock=self.clock, sleep=self.clock.sleep)
        self.calls, self.activity, self.states = [], [], []
        def handler(request):
            body = json.loads(request.content)
            self.calls.append(body)
            self.assertLessEqual(len(request.content), INPUT_BYTES)
            self.assertNotIn("PRIVATE REASONING", request.content.decode())
            self.assertEqual(body["reasoning_effort"], "low")
            self.assertEqual(body["max_completion_tokens"], 1024 if "tools" in body else 2048)
            return respond(request, body, len(self.calls))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("backend.investigator.GROQ_KEY", "SECRET_KEY"):
                return await Investigator(client, quota).groq(tools or FixtureTools(), lambda *a: self.activity.append(a), usage,
                                                               progress or self.states.append)

    async def test_tool_rounds_report_separation_and_duplicate_queries(self):
        result, usage = await self.run_provider(lambda r, b, n: success(b, tools="tools" in b))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.activity), 5)
        self.assertEqual(usage["total_tokens"], 20)
        self.assertIn("error", self.activity[4][2])
        self.assertTrue(self.calls[-1]["response_format"]["json_schema"]["strict"])
        self.assertNotIn("tools", self.calls[-1])
        self.assertEqual(len(self.calls[-1]["messages"]), 2)
        validate_report(result, FixtureTools())

    async def test_429_waits_then_resumes_same_request(self):
        usage = {}
        await self.run_provider(lambda r, b, n: httpx.Response(429, headers={"Retry-After": "7"}) if n == 1 else success(b), usage)
        self.assertIn("quota_waiting", self.states)
        self.assertEqual(self.calls[0], self.calls[1])
        self.assertEqual(usage["quota_wait_seconds"], 7)
        self.assertEqual(usage["requests"], 3)
        self.assertEqual(usage["total_tokens"], 20)
        self.assertNotIn("SECRET_KEY", json.dumps(usage))

    async def test_missing_headers_uses_fallback_once(self):
        usage = {}
        with self.assertRaisesRegex(ValueError, "rate limit"):
            await self.run_provider(lambda *a: httpx.Response(429, json={"error": "SECRET_KEY"}), usage)
        self.assertEqual(usage["quota_wait_seconds"], 60)
        self.assertEqual(usage["requests"], 2)
        self.assertNotIn("SECRET_KEY", json.dumps(usage))

    async def test_daily_quota_fails_without_long_sleep(self):
        usage = {}
        with self.assertRaisesRegex(ValueError, "120-second"):
            await self.run_provider(lambda *a: httpx.Response(429, headers={"Retry-After": "86400"},
                json={"error": {"message": "Limit reached (TPD); SECRET_KEY"}}), usage)
        self.assertEqual(usage["requests"], 1)
        self.assertEqual(usage["quota_wait_seconds"], 0)
        self.assertEqual(usage["diagnostics"][0]["limit_category"], "TPD")
        self.assertIn("retry_at", usage)

    async def test_daily_token_quota_does_not_use_minute_reset_as_retry_timing(self):
        usage = {}
        with self.assertRaisesRegex(ValueError, "TPD"):
            await self.run_provider(lambda *a: httpx.Response(429, headers={
                "x-ratelimit-remaining-tokens": "7000", "x-ratelimit-reset-tokens": "1s"},
                json={"error": {"message": "Daily tokens exceeded (TPD)"}}), usage)
        self.assertEqual(usage["quota_wait_seconds"], 60)
        self.assertEqual(usage["requests"], 2)

    async def test_prior_success_headers_schedule_following_report(self):
        def respond(r, b, n):
            response = success(b)
            if n == 1:
                response.headers.update({"x-ratelimit-limit-tokens": "20000", "x-ratelimit-remaining-tokens": "0",
                                         "x-ratelimit-reset-tokens": "2m"})
            return response
        _, usage = await self.run_provider(respond)
        self.assertEqual(usage["quota_wait_seconds"], 120)
        self.assertEqual(usage["requests"], 2)

    async def test_retry_after_and_applicable_reset_use_later_time(self):
        def respond(r, b, n):
            if n == 1:
                return httpx.Response(429, headers={"Retry-After": "2", "x-ratelimit-remaining-tokens": "0",
                    "x-ratelimit-reset-tokens": "12s"}, json={"error": {"message": "Tokens per minute (TPM)"}})
            return success(b)
        _, usage = await self.run_provider(respond)
        self.assertEqual(usage["quota_wait_seconds"], 12)

    async def test_wait_budget_is_total_across_requests(self):
        usage = {}
        def respond(r, b, n):
            return httpx.Response(429, headers={"Retry-After": "70"}) if n in {1, 3} else success(b)
        with self.assertRaisesRegex(ValueError, "120-second"):
            await self.run_provider(respond, usage)
        self.assertEqual(usage["quota_wait_seconds"], 70)
        self.assertEqual(usage["requests"], 3)

    async def test_exploration_retry_reserves_two_report_attempts(self):
        class NewSources(FixtureTools):
            def call(self, name, args):
                if not args:
                    return {"records": []}
                row = {"id": str(args["limit"]), "service": "gateway", "kind": "log", "timestamp": "2026-11-02T00:00:00Z", "message": str(args["limit"])}
                self.seen[row["id"]] = row
                return {"records": [row]}
        def respond(r, b, n):
            if n in {1, 5}:
                return httpx.Response(503)
            response = success(b, tools="tools" in b)
            if "tools" in b:
                value = response.json()
                value["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps({"limit": n})
                return httpx.Response(200, json=value)
            return response
        _, usage = await self.run_provider(respond, tools=NewSources())
        self.assertEqual(usage["requests"], 6)
        self.assertEqual(sum("tools" in c for c in self.calls), 4)
        self.assertEqual(len(self.activity), 7)
        self.assertEqual(sum("response_format" in c for c in self.calls), 2)

    async def test_connection_retry_once(self):
        usage = {}
        def respond(r, b, n):
            raise httpx.ConnectError("SECRET_KEY", request=r)
        with self.assertRaisesRegex(ValueError, "connection failure"):
            await self.run_provider(respond, usage)
        self.assertEqual(usage["requests"], 2)
        self.assertNotIn("SECRET_KEY", json.dumps(usage))
        self.assertEqual([entry["connection_error"] for entry in usage["diagnostics"]], ["connect_error", "connect_error"])

    async def test_permanent_errors_are_specific_and_not_retried(self):
        for status, error in [(401, "authentication"), (403, "authentication"), (413, "oversized"), (400, "schema")]:
            with self.subTest(status=status):
                usage = {}
                with self.assertRaisesRegex(ValueError, error):
                    await self.run_provider(lambda *a: httpx.Response(status, text="SECRET_KEY"), usage)
                self.assertEqual(usage["requests"], 1)

    async def test_invalid_and_truncated_output_not_retried(self):
        for result in [[], {"usage": None}, {}, {"choices": [{"message": {}, "finish_reason": "length"}]}]:
            with self.assertRaisesRegex(ValueError, "invalid or truncated"):
                await self.run_provider(lambda *a: httpx.Response(200, json=result))
            self.assertEqual(len(self.calls), 1)

    async def test_fabricated_citation_fails_report(self):
        def respond(r, b, n):
            response = success(b)
            if "tools" not in b:
                value = response.json()
                value["choices"][0]["message"]["content"] = json.dumps({**REPORT, "evidence_ids": ["fabricated"]})
                return httpx.Response(200, json=value)
            return response
        with self.assertRaisesRegex(ValueError, "citation"):
            await self.run_provider(respond)
        self.assertEqual(len(self.calls), 2)

    async def test_tiny_full_allowance_never_resends_oversized_request(self):
        for category in ("TPM", "TPD"):
            usage = {}
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, "allowance"):
                await self.run_provider(lambda *a: httpx.Response(429, headers={"Retry-After": "1"},
                    json={"error": {"message": f"Rate limit ({category}): Limit 100, Requested 9000"}}), usage)
            self.assertEqual(usage["requests"], 1)

    async def test_quota_state_is_shared_between_investigations(self):
        clock = Clock()
        quota = GroqQuota(clock=clock, sleep=clock.sleep)
        def respond(r, b, n):
            response = success(b)
            if "tools" not in b:
                response.headers.update({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "15s"})
            return response
        await self.run_provider(respond, quota=quota)
        _, usage = await self.run_provider(lambda r, b, n: success(b), quota=quota)
        self.assertEqual(usage["quota_wait_seconds"], 15)

    async def test_deadline_excludes_queue_and_bounds_execution(self):
        clock = Clock()
        quota = GroqQuota(clock=clock, sleep=clock.sleep)
        def respond(r, b, n):
            clock.now += 301
            return success(b)
        with self.assertRaisesRegex(ValueError, "five-minute"):
            await self.run_provider(respond, quota=quota)
        self.assertEqual(len(self.calls), 1)

    def test_header_time_formats(self):
        self.assertEqual(delay_seconds("1m2.5s", 0), 62.5)
        self.assertEqual(delay_seconds("100ms", 0), .1)
        self.assertEqual(delay_seconds("Thu, 01 Jan 1970 00:01:00 GMT", 0), 60)
        self.assertIsNone(delay_seconds("invalid", 0))
        self.assertIsNone(delay_seconds("NaN", 0))
