"""Deterministic model doubles and transport tests; these are not live model evidence."""

import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx2
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from prism.conversation import (
    ENDPOINT,
    MODEL,
    Answer,
    Conversations,
    GuardedTransport,
    Scope,
    read_key,
)
from prism.jobs import Jobs
from prism.sharing import (
    DEMO_FILES,
    Denied,
    Source,
    Store,
    ident,
    prepare_source,
)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        prepare_source(self.root / "source")
        self.store = Store(self.root / "db.sqlite")
        self.jobs = Jobs(self.store)
        self.addCleanup(self.jobs.shutdown)
        candidate = self.store.candidate(
            Source(self.root / "source").freeze(
                [n for n in DEMO_FILES if n != "private-notes.txt"],
                "Review the paired evidence",
                "verify",
            )
        )
        self.store.approve(candidate["id"], candidate["digest"])
        self.session = self.store.new_session(candidate["id"], "reviewer")
        self.source = self.session["manifest"]["files"][0]
        self.answer = {
            "answer": "The baseline reports a mean difference of 0.0225.",
            "claims": [
                {"kind": "reported", "text": "The reported mean difference is 0.0225."}
            ],
            "citations": [
                {
                    "evidence_id": self.source["id"],
                    "start": 1,
                    "end": self.source["lines"],
                }
            ],
            "run_references": [],
            "limitations": ["These are synthetic observations."],
            "pending_request_id": None,
        }

    async def test_actual_agent_loop_tools_citations_followup_and_fresh_history(self):
        seen = []

        async def respond(messages, info):
            seen.append(messages)
            if len(seen) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "read_evidence",
                            {"evidence_id": self.source["id"], "start": 1, "end": 13},
                            tool_call_id="read1",
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name, self.answer, tool_call_id="answer1"
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        first = await service.ask(
            self.session["id"],
            "reviewer",
            "What is the baseline finding?",
            "question-001",
        )
        self.assertEqual(first["status"], "completed", first)
        self.assertEqual(
            first["answer"]["citations"][0]["version"], self.session["version"]
        )
        self.assertNotIn("PRISM_UNSHARED_ORCHID", repr(seen))
        self.assertTrue(any("0.022500" in repr(item) for item in seen))
        await service.ask(
            self.session["id"], "reviewer", "What limits that finding?", "question-002"
        )
        self.assertIn("What is the baseline finding?", repr(seen[-1]))
        two = self.store.new_session(self.session["version"], "reviewer")
        await service.ask(
            two["id"], "reviewer", "Fresh independent question", "question-003"
        )
        self.assertNotIn("What is the baseline finding?", repr(seen[-1]))

    async def test_bad_citations_and_forged_run_cannot_be_delivered(self):
        async def respond(messages, info):
            bad = {
                **self.answer,
                "citations": [{"evidence_id": "a" * 24, "start": 1, "end": 2}],
            }
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, bad)])

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            self.session["id"], "reviewer", "Invent evidence", "bad-citation"
        )
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["answer"])
        with self.assertRaises(Denied):
            service.validate_answer(
                Scope(service, self.session["id"], "reviewer", "fake"),
                Answer(**{**self.answer, "run_references": ["a" * 32]}),
            )
        with self.assertRaises(Denied):
            service.validate_answer(
                Scope(service, self.session["id"], "reviewer", "fake"),
                Answer(
                    **{
                        **self.answer,
                        "citations": [
                            {"evidence_id": self.source["id"], "start": 1, "end": 99}
                        ],
                    }
                ),
            )

    async def test_final_request_reserved_for_answer_without_more_tools(self):
        available = []

        async def respond(messages, info):
            available.append([t.name for t in info.function_tools])
            if len(available) < 4:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "read_evidence",
                            {"evidence_id": self.source["id"], "start": 1, "end": 13},
                            tool_call_id="read-" + str(len(available)),
                        )
                    ]
                )
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, self.answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            self.session["id"], "reviewer", "Review several sources", "bounded-final"
        )
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["usage"]["requests"], 4)
        self.assertTrue(all("read_evidence" in names for names in available[:3]))
        self.assertEqual(available[3], [])

    async def test_schema_correction_is_final_only_and_bounded(self):
        available = []

        async def respond(messages, info):
            available.append([tool.name for tool in info.function_tools])
            answer = (
                self.answer
                if len(available) == 2
                else {
                    key: value for key, value in self.answer.items() if key != "answer"
                }
            )
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            self.session["id"], "reviewer", "Correct the schema once.", "schema-repair"
        )
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["usage"]["requests"], 2)
        self.assertEqual(available[1], [])

    async def test_reference_correction_does_not_expand_four_request_limit(self):
        calls = []

        async def respond(messages, info):
            calls.append([tool.name for tool in info.function_tools])
            if len(calls) <= 3:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "read_evidence",
                            {"evidence_id": self.source["id"], "start": 1, "end": 1},
                            tool_call_id=f"read-{len(calls)}",
                        )
                    ]
                )
            invalid = {
                **self.answer,
                "citations": [],
                "claims": [{"kind": "reported", "text": "Unsupported."}],
            }
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, invalid)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            self.session["id"],
            "reviewer",
            "Use the full bounded request allowance.",
            "bounded-repair-limit",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[3], [])

    async def test_provider_and_budget_errors_do_not_expose_bodies(self):
        wrapped = RuntimeError("PRIVATE_ERROR_CANARY")
        wrapped.__cause__ = Denied("The transport's bounded denial.", 429)
        for number, error in enumerate(
            [
                UsageLimitExceeded("PRIVATE_ERROR_CANARY"),
                ModelHTTPError(401, MODEL, {"error": "PRIVATE_ERROR_CANARY"}),
                UnexpectedModelBehavior("PRIVATE_ERROR_CANARY", "PRIVATE_BODY_CANARY"),
                wrapped,
            ]
        ):

            async def respond(messages, info, expected=error):
                raise expected

            service = Conversations(
                self.store, self.jobs, test_model=FunctionModel(respond)
            )
            result = await service.ask(
                self.session["id"],
                "reviewer",
                "Check failure",
                "safe-error-" + str(number),
            )
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["answer"])
            self.assertNotIn("PRIVATE_ERROR_CANARY", str(result))
            self.assertIn(
                ["allowance", "credential", "structured answer", "bounded denial"][
                    number
                ],
                result["error"],
            )

    async def test_incomplete_provider_output_is_not_delivered(self):
        scope = self.running_scope()

        class Incomplete(httpx2.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return httpx2.Response(
                    200,
                    json={
                        "status": "incomplete",
                        "incomplete_details": {"reason": "max_output_tokens"},
                        "output": ["PRIVATE_PARTIAL_CANARY"],
                    },
                )

        transport = GuardedTransport(scope, Incomplete())
        with self.assertRaises(Denied) as error:
            await transport.handle_async_request(
                httpx2.Request(
                    "POST",
                    ENDPOINT,
                    json={
                        "model": MODEL,
                        "store": False,
                        "max_output_tokens": 1500,
                        "tools": [{"type": "function"}],
                    },
                )
            )
        self.assertIn("stopped before completing", str(error.exception))
        self.assertNotIn("PRIVATE_PARTIAL_CANARY", str(error.exception))
        self.assertEqual(scope.service.route()["reserved_cents"], 5)

    async def test_real_sdk_wire_contract_with_fake_http_response(self):
        captured = []
        answer = self.answer

        class FakeOpenAI(httpx2.AsyncBaseTransport):
            async def handle_async_request(self, request):
                body = json.loads(await request.aread())
                captured.append((body, dict(request.headers)))
                final = next(
                    t["name"] for t in body["tools"] if t["name"] == "final_result"
                )
                return httpx2.Response(
                    200,
                    json={
                        "id": "resp_synthetic",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "error": None,
                        "incomplete_details": None,
                        "instructions": None,
                        "model": MODEL,
                        "parallel_tool_calls": False,
                        "tool_choice": "auto",
                        "tools": [],
                        "output": [
                            {
                                "type": "function_call",
                                "id": "fc_synthetic",
                                "call_id": "call_synthetic",
                                "name": final,
                                "arguments": json.dumps(answer),
                                "status": "completed",
                            }
                        ],
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 100,
                            "total_tokens": 200,
                            "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens_details": {"reasoning_tokens": 0},
                        },
                    },
                )

        service = Conversations(self.store, self.jobs, key="sk-prism-test-only")
        with (
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "UNRELATED_KEY_CANARY",
                    "OPENAI_ADMIN_KEY": "UNRELATED_ADMIN_CANARY",
                    "OPENAI_CUSTOM_HEADERS": "Authorization: Bearer UNRELATED_HEADER_CANARY",
                },
            ),
            patch(
                "prism.conversation.httpx2.AsyncHTTPTransport",
                return_value=FakeOpenAI(),
            ),
        ):
            result = await service.ask(
                self.session["id"],
                "reviewer",
                "Which finding is supported?",
                "sdk-wire-test",
            )
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][1]["authorization"], "Bearer sk-prism-test-only")
        self.assertNotIn("UNRELATED_", repr(captured))
        self.assertNotIn("PRISM_UNSHARED_ORCHID", repr(captured))
        self.assertFalse(captured[0][0]["store"])
        self.assertEqual(service.route()["reserved_cents"], 5)

    async def test_pending_request_is_not_authority_and_inspect_tool_hidden(self):
        observer = self.store.new_session(self.session["version"], "observer")
        tools = []

        async def respond(messages, info):
            tools.extend(t.name for t in info.function_tools)
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, self.answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        request = service.request_access(
            observer["id"], "observer", "Please permit bootstrap verification."
        )
        self.assertEqual(request["status"], "pending")
        await service.ask(
            observer["id"], "observer", "Review only", "observer-question"
        )
        self.assertNotIn("submit_verification", tools)
        with self.assertRaises(Denied):
            self.jobs.submit(observer["id"], "observer", 7, "forged-approval")

    async def test_missing_route_and_duplicate_questions_do_not_call_model(self):
        service = Conversations(self.store, self.jobs)
        with self.assertRaises(Denied) as error:
            await service.ask(self.session["id"], "reviewer", "Test", "no-provider")
        self.assertEqual(error.exception.status, 503)
        calls = []

        async def respond(messages, info):
            calls.append(True)
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, self.answer)]
            )

        service.test_model = FunctionModel(respond)
        await service.ask(self.session["id"], "reviewer", "Test", "same-question")
        await service.ask(self.session["id"], "reviewer", "Test", "same-question")
        self.assertEqual(len(calls), 1)
        with self.assertRaises(Denied):
            await service.ask(
                self.session["id"], "reviewer", "Changed", "same-question"
            )

    async def test_serial_turns_and_revocation_discard_late_response(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(messages, info):
            entered.set()
            await release.wait()
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, self.answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        task = asyncio.create_task(
            service.ask(self.session["id"], "reviewer", "Wait", "waiting-question")
        )
        await asyncio.wait_for(entered.wait(), 3)
        with self.assertRaises(Denied):
            await service.ask(
                self.session["id"], "reviewer", "Concurrent", "second-question"
            )
        self.store.revoke(self.session["version"])
        release.set()
        with self.assertRaises(Denied):
            await task

    def running_scope(self, service=None):
        service = service or Conversations(self.store, self.jobs)
        turn = ident()
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO turns(id,session,request_key,question,status,created) VALUES(?,?,?,?,?,?)",
                (turn, self.session["id"], turn, "Synthetic test", "running", 0),
            )
        return Scope(service, self.session["id"], "reviewer", turn)

    async def test_transport_destination_storage_size_and_atomic_prepaid_allowance(
        self,
    ):
        scope = self.running_scope()
        sent = []

        class NoNetwork(httpx2.AsyncBaseTransport):
            async def handle_async_request(self, request):
                sent.append(True)
                return httpx2.Response(200, json={"test": "not a model response"})

        transport = GuardedTransport(scope, NoNetwork())
        payload = {
            "model": MODEL,
            "store": False,
            "max_output_tokens": 1500,
            "tools": [{"type": "function"}],
        }
        await transport.handle_async_request(
            httpx2.Request("POST", ENDPOINT, json=payload)
        )
        disabled = Conversations(
            self.store,
            self.jobs,
            key="synthetic-key",
            budget_cents=0,
            recover=False,
        )
        disabled_transport = GuardedTransport(self.running_scope(disabled), NoNetwork())
        with self.assertRaises(Denied):
            await disabled_transport.handle_async_request(
                httpx2.Request("POST", ENDPOINT, json=payload)
            )
        self.assertEqual(len(sent), 1)
        for url, data in [
            ("https://other.example/v1/responses", payload),
            (ENDPOINT, {**payload, "store": True}),
            (ENDPOINT, {**payload, "previous_response_id": "private"}),
            (ENDPOINT, {**payload, "tools": [{"type": "web_search"}]}),
            (ENDPOINT, {**payload, "input": "a" * 33000}),
        ]:
            with self.assertRaises(Denied):
                await transport.handle_async_request(
                    httpx2.Request("POST", url, json=data)
                )
        self.assertEqual(len(sent), 1)
        scopes = [self.running_scope(scope.service) for _ in range(40)]

        def reserve(item):
            try:
                item.service.reserve_dispatch(item)
                return 1
            except Denied:
                return 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            successes = sum(pool.map(reserve, scopes))
        self.assertEqual(successes, 19)
        self.assertEqual(scope.service.route()["reserved_cents"], 100)

    def test_explicit_key_file_rejects_links_and_broad_permissions(self):
        key = self.root / "prism.key"
        key.write_text("sk-synthetic-test-only-not-a-real-credential")
        key.chmod(0o600)
        self.assertTrue(read_key(key).startswith("sk-"))
        key.chmod(0o644)
        with self.assertRaises(ValueError):
            read_key(key)
        alias = self.root / "alias"
        alias.symlink_to(key)
        with self.assertRaises(OSError):
            read_key(alias)

    def test_explicit_budget_increase_preserves_usage_and_still_caps_requests(self):
        service = Conversations(self.store, self.jobs)
        for _ in range(20):
            service.reserve_dispatch(self.running_scope(service))
        with self.assertRaises(Denied):
            service.reserve_dispatch(self.running_scope(service))

        increased = Conversations(self.store, self.jobs, budget_cents=200)
        self.assertEqual(increased.route()["reserved_cents"], 100)
        self.assertEqual(increased.route()["budget_cents"], 200)
        scope = self.running_scope(increased)
        for _ in range(4):
            increased.reserve_dispatch(scope)
        with self.assertRaises(Denied):
            increased.reserve_dispatch(scope)
        for _ in range(16):
            increased.reserve_dispatch(self.running_scope(increased))
        with self.assertRaises(Denied):
            increased.reserve_dispatch(self.running_scope(increased))
        restarted = Conversations(self.store, self.jobs, budget_cents=200)
        self.assertEqual(restarted.route()["reserved_cents"], 200)
        with self.assertRaises(Denied):
            restarted.reserve_dispatch(self.running_scope(restarted))
        disabled = Conversations(
            self.store, self.jobs, key="synthetic-key", budget_cents=0
        )
        self.assertEqual(disabled.route()["status"], "blocked")
        self.assertFalse(disabled.route()["available"])
        with self.assertRaises(Denied):
            disabled.reserve_dispatch(self.running_scope(disabled))
        for invalid in (-1, 1, 4, 10001, 200.5, True):
            with self.assertRaises(ValueError):
                Conversations(self.store, self.jobs, budget_cents=invalid)


if __name__ == "__main__":
    unittest.main()
