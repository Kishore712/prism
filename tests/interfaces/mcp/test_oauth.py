from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from starlette.testclient import TestClient

from prism.database import PrismDatabase
from prism.interfaces.mcp.oauth import MAX_CONSENT_ATTEMPTS
from prism.interfaces.mcp.server import create_mcp_application
from prism.services.sharing import SharingService

from tests.services.phase5_helpers import publish_prepared


BASE = "http://127.0.0.1:8766"
RESOURCE = f"{BASE}/mcp"
REDIRECT = "http://localhost:9999/callback"


def _future(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace(
        "+00:00", "Z"
    )


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


class OAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.database, _, published = publish_prepared(root)
        self.sharing = SharingService(self.database)
        self.share = self.sharing.create_share(published.snapshot_id, "OAuth test")
        app = create_mcp_application(self.database, public_url=BASE, owner_label="Kishore")
        self.client = TestClient(app, base_url=BASE)
        self.addCleanup(self.client.close)

    # helpers ---------------------------------------------------------------

    def _invitation(self, *, require_approval: bool = True) -> str:
        return self.sharing.create_invitation(
            self.share.share.share_id,
            version=None,
            expires_at=_future(2),
            grant_expires_at=_future(24),
            require_approval=require_approval,
        ).invitation_token

    def _register(self, name: str = "Test Client") -> str:
        response = self.client.post(
            "/register",
            json={
                "redirect_uris": [REDIRECT],
                "client_name": name,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "prism:read",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["client_id"]

    def _authorize(self, client_id: str, challenge: str, **overrides) -> str:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st-123",
            "scope": "prism:read",
            "resource": RESOURCE,
        }
        params.update(overrides)
        response = self.client.get("/authorize", params=params, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.text)
        location = response.headers["location"]
        self.assertTrue(location.startswith(f"{BASE}/prism/consent?ticket="), location)
        return parse_qs(urlsplit(location).query)["ticket"][0]

    def _consent(self, ticket: str, token: str, name: str = "Alice"):
        return self.client.post(
            "/prism/consent",
            content=f"ticket={ticket}&invitation_token={token}&display_name={name}",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )

    def _check(self, ticket: str):
        return self.client.post(
            "/prism/consent",
            content=f"ticket={ticket}",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )

    @staticmethod
    def _code_from(response) -> tuple[str, dict[str, list[str]]]:
        query = parse_qs(urlsplit(response.headers["location"]).query)
        return query["code"][0], query

    def _exchange(self, client_id: str, code: str, verifier: str, **overrides):
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": RESOURCE,
        }
        data.update(overrides)
        return self.client.post("/token", data=data)

    def _connect(self, *, require_approval: bool = False):
        client_id = self._register()
        verifier, challenge = _pkce()
        ticket = self._authorize(client_id, challenge)
        response = self._consent(ticket, self._invitation(require_approval=require_approval))
        return client_id, verifier, ticket, response

    # discovery --------------------------------------------------------------

    def test_unauthenticated_mcp_call_gets_a_discoverable_401(self) -> None:
        response = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(response.status_code, 401)
        challenge = response.headers["www-authenticate"]
        self.assertIn("resource_metadata=", challenge)
        self.assertIn(".well-known/oauth-protected-resource", challenge)

    def test_protected_resource_and_authorization_server_metadata(self) -> None:
        resource = self.client.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(resource["resource"], RESOURCE)
        self.assertEqual(resource["authorization_servers"], [BASE])
        server = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(server["issuer"], BASE)
        self.assertIn("S256", server["code_challenge_methods_supported"])
        self.assertIn("registration_endpoint", server)

    # happy path ---------------------------------------------------------------

    def test_full_flow_with_owner_approval_issues_hashed_tokens(self) -> None:
        client_id = self._register()
        verifier, challenge = _pkce()
        ticket = self._authorize(client_id, challenge)

        page = self.client.get("/prism/consent", params={"ticket": ticket})
        self.assertEqual(page.status_code, 200)
        self.assertIn("Test Client", page.text)
        self.assertIn("localhost:9999", page.text)  # where the result will be sent
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertEqual(page.headers["cache-control"], "no-store")

        invitation = self._invitation(require_approval=True)
        waiting = self._consent(ticket, invitation)
        self.assertEqual(waiting.status_code, 202)
        self.assertIn("Kishore", waiting.text)

        still_waiting = self._check(ticket)
        self.assertEqual(still_waiting.status_code, 202)

        grant = self.sharing.list_grants()[0]
        self.assertEqual(grant.recipient_label, "Alice")
        self.sharing.approve_grant(grant.grant_id)

        redirected = self._check(ticket)
        self.assertEqual(redirected.status_code, 302)
        code, query = self._code_from(redirected)
        self.assertTrue(redirected.headers["location"].startswith(REDIRECT))
        self.assertEqual(query["state"], ["st-123"])
        self.assertEqual(query["iss"], [BASE])

        issued = self._exchange(client_id, code, verifier)
        self.assertEqual(issued.status_code, 200, issued.text)
        body = issued.json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["scope"], "prism:read")
        self.assertIn("refresh_token", body)

        unauthenticated = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(unauthenticated.status_code, 401)

        with closing(sqlite3.connect(self.database.path)) as connection:
            dump = "\n".join(connection.iterdump())
        for secret in (body["access_token"], body["refresh_token"], code, ticket):
            self.assertNotIn(secret, dump)

    def test_no_approval_invitation_completes_in_one_step(self) -> None:
        client_id, verifier, _, response = self._connect(require_approval=False)
        self.assertEqual(response.status_code, 302)
        code, _ = self._code_from(response)
        self.assertEqual(self._exchange(client_id, code, verifier).status_code, 200)

    # failure paths ------------------------------------------------------------------

    def test_authorization_code_is_single_use_and_pkce_bound(self) -> None:
        client_id, verifier, _, response = self._connect()
        code, _ = self._code_from(response)

        wrong = self._exchange(client_id, code, _pkce()[0])
        self.assertEqual(wrong.status_code, 400)

        first = self._exchange(client_id, code, verifier)
        self.assertEqual(first.status_code, 200)
        replay = self._exchange(client_id, code, verifier)
        self.assertEqual(replay.status_code, 400)

    def test_other_client_cannot_redeem_the_code(self) -> None:
        client_id, verifier, _, response = self._connect()
        code, _ = self._code_from(response)
        attacker = self._register("Attacker")
        stolen = self._exchange(attacker, code, verifier)
        self.assertEqual(stolen.status_code, 400)

    def test_wrong_invitation_attempts_lock_the_ticket(self) -> None:
        client_id = self._register()
        _, challenge = _pkce()
        ticket = self._authorize(client_id, challenge)
        bad = "pinv_v1_" + "z" * 43
        for _ in range(MAX_CONSENT_ATTEMPTS - 1):
            self.assertEqual(self._consent(ticket, bad).status_code, 400)
        locked = self._consent(ticket, bad)
        self.assertEqual(locked.status_code, 429)
        good = self._consent(ticket, self._invitation(require_approval=False))
        self.assertEqual(good.status_code, 400)  # ticket is dead; start over
        self.assertEqual(
            self.client.get("/prism/consent", params={"ticket": ticket}).status_code, 400
        )

    def test_denied_grant_redirects_with_access_denied(self) -> None:
        client_id = self._register()
        _, challenge = _pkce()
        ticket = self._authorize(client_id, challenge)
        self._consent(ticket, self._invitation(require_approval=True))
        self.sharing.deny_grant(self.sharing.list_grants()[0].grant_id)
        denied = self._check(ticket)
        self.assertEqual(denied.status_code, 302)
        query = parse_qs(urlsplit(denied.headers["location"]).query)
        self.assertEqual(query["error"], ["access_denied"])
        self.assertEqual(query["state"], ["st-123"])
        self.assertNotIn("code", query)

    def test_authorize_rejects_foreign_resource_and_unknown_scope(self) -> None:
        client_id = self._register()
        _, challenge = _pkce()
        for override in ({"resource": "https://evil.example/mcp"}, {"scope": "admin"}):
            response = self.client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": REDIRECT,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": "s",
                    **{"scope": "prism:read", "resource": RESOURCE, **override},
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)
            self.assertNotIn(f"{BASE}/prism/consent", response.headers["location"], override)
            self.assertIn("error=", response.headers["location"])

    def test_registration_rejects_non_loopback_http_redirects(self) -> None:
        response = self.client.post(
            "/register",
            json={
                "redirect_uris": ["http://evil.example/cb"],
                "client_name": "Evil",
                "token_endpoint_auth_method": "none",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_client_name_is_escaped_on_the_consent_page(self) -> None:
        client_id = self._register('<script>alert(1)</script>')
        _, challenge = _pkce()
        ticket = self._authorize(client_id, challenge)
        page = self.client.get("/prism/consent", params={"ticket": ticket})
        self.assertNotIn("<script>alert(1)</script>", page.text)
        self.assertIn("&lt;script&gt;", page.text)

    def test_unknown_ticket_and_oversized_forms_are_rejected(self) -> None:
        self.assertEqual(
            self.client.get("/prism/consent", params={"ticket": "nope"}).status_code, 400
        )
        oversized = self.client.post(
            "/prism/consent",
            content="ticket=" + "a" * 20_000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        self.assertIn(oversized.status_code, {400, 413})

    # refresh --------------------------------------------------------------------------

    def test_refresh_rotates_and_reuse_revokes_the_family(self) -> None:
        client_id, verifier, _, response = self._connect()
        code, _ = self._code_from(response)
        first = self._exchange(client_id, code, verifier).json()

        refreshed = self.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": client_id,
            },
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        second = refreshed.json()
        self.assertNotEqual(second["refresh_token"], first["refresh_token"])

        replay = self.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": client_id,
            },
        )
        self.assertEqual(replay.status_code, 400)
        after_reuse = self.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": second["refresh_token"],
                "client_id": client_id,
            },
        )
        self.assertEqual(after_reuse.status_code, 400)

    def test_refresh_fails_once_the_grant_is_revoked(self) -> None:
        client_id, verifier, _, response = self._connect()
        code, _ = self._code_from(response)
        issued = self._exchange(client_id, code, verifier).json()
        self.sharing.revoke_grant(self.sharing.list_grants()[0].grant_id)
        refreshed = self.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": issued["refresh_token"],
                "client_id": client_id,
            },
        )
        self.assertEqual(refreshed.status_code, 400)


if __name__ == "__main__":
    unittest.main()
