from __future__ import annotations

import asyncio
import unittest

from prism.interfaces.mcp.ratelimit import RateLimitMiddleware, RateRule


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _call(app, path, *, headers=(), client=("10.0.0.1", 1234)):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {
        "type": "http",
        "path": path,
        "headers": list(headers),
        "client": client,
    }
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"], dict(sent[0]["headers"])


class RateLimitTests(unittest.TestCase):
    def _app(self, clock, **kwargs):
        rules = {
            "auth": RateRule(capacity=2, refill_per_second=1.0),
            "mcp": RateRule(capacity=3, refill_per_second=1.0),
        }
        return RateLimitMiddleware(_ok_app, rules=rules, clock=clock, **kwargs)

    def test_auth_endpoints_are_limited_per_address_and_recover(self) -> None:
        clock = _Clock()
        app = self._app(clock)
        self.assertEqual(_call(app, "/token")[0], 200)
        self.assertEqual(_call(app, "/authorize")[0], 200)
        status, headers = _call(app, "/prism/consent")
        self.assertEqual(status, 429)
        self.assertIn(b"retry-after", headers)
        self.assertEqual(_call(app, "/token", client=("10.0.0.2", 1))[0], 200)
        clock.now += 1.5
        self.assertEqual(_call(app, "/token")[0], 200)

    def test_mcp_calls_are_limited_per_bearer_token_not_per_address(self) -> None:
        clock = _Clock()
        app = self._app(clock)
        alice = ((b"authorization", b"Bearer alice"),)
        bob = ((b"authorization", b"Bearer bob"),)
        for _ in range(3):
            self.assertEqual(_call(app, "/mcp", headers=alice)[0], 200)
        self.assertEqual(_call(app, "/mcp", headers=alice)[0], 429)
        self.assertEqual(_call(app, "/mcp", headers=bob)[0], 200)

    def test_health_and_discovery_are_never_limited(self) -> None:
        app = self._app(_Clock())
        for _ in range(20):
            self.assertEqual(_call(app, "/healthz")[0], 200)
            self.assertEqual(_call(app, "/.well-known/oauth-authorization-server")[0], 200)

    def test_forwarded_for_is_ignored_unless_trusted(self) -> None:
        clock = _Clock()
        spoof = ((b"x-forwarded-for", b"1.2.3.4"),)
        untrusted = self._app(clock)
        for _ in range(2):
            _call(untrusted, "/token", headers=spoof)
        self.assertEqual(_call(untrusted, "/token", headers=((b"x-forwarded-for", b"9.9.9.9"),))[0], 429)

        trusted = self._app(clock, trust_forwarded_for=True)
        for _ in range(2):
            _call(trusted, "/token", headers=spoof)
        self.assertEqual(_call(trusted, "/token", headers=spoof)[0], 429)
        self.assertEqual(_call(trusted, "/token", headers=((b"x-forwarded-for", b"9.9.9.9"),))[0], 200)


if __name__ == "__main__":
    unittest.main()
