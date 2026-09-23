"""Small in-process token-bucket rate limiter for the public listener.

The recipient listener is reachable from the Internet while a tunnel is up.
Authorization endpoints are limited per client address; MCP calls are limited
per bearer token so one recipient cannot starve another.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass(frozen=True)
class RateRule:
    capacity: float
    refill_per_second: float


AUTH_PATHS = frozenset({"/authorize", "/token", "/register", "/revoke", "/prism/consent"})
DEFAULT_RULES = {
    "auth": RateRule(capacity=30, refill_per_second=0.5),
    "mcp": RateRule(capacity=60, refill_per_second=2.0),
}
MAX_TRACKED_KEYS = 10_000


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        rules: dict[str, RateRule] | None = None,
        trust_forwarded_for: bool = False,
        clock: Callable[[], float] = time.monotonic,
        mcp_path: str = "/mcp",
    ) -> None:
        self._app = app
        self._rules = rules or DEFAULT_RULES
        self._trust_forwarded = trust_forwarded_for
        self._clock = clock
        self._mcp_path = mcp_path
        self._buckets: dict[str, _Bucket] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in AUTH_PATHS:
            key, rule_name = f"auth:{self._address(scope)}", "auth"
        elif path == self._mcp_path or path.startswith(self._mcp_path + "/"):
            key, rule_name = f"mcp:{self._identity(scope)}", "mcp"
        else:
            await self._app(scope, receive, send)
            return
        retry_after = self._take(key, self._rules[rule_name])
        if retry_after is None:
            await self._app(scope, receive, send)
            return
        body = json.dumps(
            {"error": "rate_limited", "error_description": "Too many requests"}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(max(1, math.ceil(retry_after))).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _take(self, key: str, rule: RateRule) -> float | None:
        """Consume one token; return seconds to wait when the bucket is empty."""

        now = self._clock()
        if len(self._buckets) >= MAX_TRACKED_KEYS:
            self._evict(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = _Bucket(rule.capacity, now)
        bucket.tokens = min(
            rule.capacity,
            bucket.tokens + (now - bucket.updated) * rule.refill_per_second,
        )
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        return (1.0 - bucket.tokens) / rule.refill_per_second

    def _evict(self, now: float) -> None:
        # Drop the stalest half; a returning client simply gets a full bucket.
        stale = sorted(self._buckets.items(), key=lambda item: item[1].updated)
        for key, _ in stale[: len(stale) // 2]:
            del self._buckets[key]

    def _address(self, scope: Scope) -> str:
        if self._trust_forwarded:
            for name, value in scope.get("headers", []):
                if name == b"x-forwarded-for":
                    # The last hop is the one our own trusted proxy appended; earlier
                    # entries are client-controlled and spoofable.
                    last = value.decode("latin-1").split(",")[-1].strip()
                    if last:
                        return last
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _identity(self, scope: Scope) -> str:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                return "t:" + hashlib.sha256(value).hexdigest()[:32]
        return "a:" + self._address(scope)
