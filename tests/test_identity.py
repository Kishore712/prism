import base64
import contextlib
import hashlib
import io
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch as mock_patch

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from prism.cli import main, parser
from prism.conversation import Conversations
from prism.demo import serve
from prism.identity import FixedOIDCTransport, OIDCAuth, OIDCConfig, read_client_secret
from prism.identity_bootstrap import (
    BootstrapIdentity,
    IdentifyOIDCConfig,
    create_identify_app,
    run_identity_bootstrap,
)
from prism.identity_preflight import identity_preflight, validate_tls_identity
from prism.jobs import Jobs
from prism.projects import ProjectSource
from prism.sharing import Denied, NamedPrincipal, Source, Store, digest, prepare_source
from prism.webapp import create_app


class FakeOIDC:
    def __init__(self):
        self.key = RSAKey.generate_key(2048, private=True, auto_kid=True)
        self.claims = None
        self.last_form = None

    def jwks(self, url):
        return {"keys": [self.key.as_dict(private=False)]}

    def token(self, url, form, *, client_id, client_secret=None):
        self.last_form = dict(form)
        return {
            "id_token": jwt.encode(
                {"alg": "RS256", "kid": self.key.kid},
                self.claims,
                self.key,
                algorithms=["RS256"],
            )
        }


class FakeRegistry:
    profile = "development"
    reference = None
    blocked = False

    def assert_ready(self, profile):
        if profile != "development":
            raise ValueError("unexpected profile")


class ImmediateJobs(Jobs):
    """Deterministic runtime double; it starts no process or container."""

    def _launch(
        self, run, session, actor, action, argument, program_hash, parameters, *rest
    ):
        result = {
            "action": action,
            "inputs": parameters,
            "output": {
                "action": action,
                "files": [
                    {"id": item["id"], "sha256": item["sha256"], "valid": True}
                    for item in parameters["files"]
                ],
            },
            "output_sha256": "0" * 64,
            "image_id": "sha256:" + "1" * 64,
            "image": "synthetic-runtime-double",
            "program_sha256": program_hash,
            "exit_code": 0,
            "elapsed_seconds": 0.01,
            "cleaned_up": True,
            "profile": "development",
        }
        with self.store.connect() as db:
            db.execute(
                "UPDATE runs SET status='completed',finished=?,result=?,error=NULL WHERE id=?",
                (time.time(), json.dumps(result, sort_keys=True), run),
            )
            self.store.event(db, "run_finished", actor, run, "completed")


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        prepare_source(root / "source")
        self.source = Source(root / "source")
        self.store = Store(root / "state.sqlite")
        self.inspect = self.store.candidate(
            self.source.freeze(["overview.md"], "Named review", "inspect")
        )
        self.store.approve(self.inspect["id"], self.inspect["digest"])
        self.verify = self.store.candidate(
            self.source.freeze(
                ["observations.csv", "baseline.json", "limitations.md"],
                "Named verification",
                "verify",
            )
        )
        self.store.approve(self.verify["id"], self.verify["digest"])
        self.now = [time.time()]
        self.config = OIDCConfig(
            issuer="https://idp.example/tenant",
            authorization_endpoint="https://idp.example/oauth/authorize",
            token_endpoint="https://idp.example/oauth/token",
            jwks_uri="https://idp.example/oauth/jwks",
            client_id="prism-client",
            redirect_uri="https://prism.example/auth/oidc/callback",
            public_origin="https://prism.example",
            owner_subject="owner-subject",
            algorithms=("RS256",),
        )
        self.transport = FakeOIDC()
        self.auth = OIDCAuth(
            self.store,
            self.config,
            transport=self.transport,
            clock=lambda: self.now[0],
            activation_check=lambda invitation: self.store.invitation_activation(
                invitation
            ),
        )

    def claims(self, subject, nonce, **patch):
        value = {
            "iss": self.config.issuer,
            "sub": subject,
            "aud": self.config.client_id,
            "exp": int(self.now[0] + 300),
            "iat": int(self.now[0]),
            "nonce": nonce,
        }
        value.update(patch)
        return value

    def callback(self, login, subject):
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(login["authorization_url"]).query
        )
        self.transport.claims = self.claims(subject, query["nonce"][0])
        return self.auth.callback(
            state=query["state"][0],
            code="authorization-code",
            binding=login["binding"],
        )

    def project_source(self, name="named-documents"):
        root = Path(self.temp.name).resolve() / name
        root.mkdir()
        (root / "README.md").write_text(
            "# Synthetic named review\nThe approved value is 7.\n"
        )
        (root / "valid.json").write_text('{"approved":7}\n')
        manifest = root / ".prism-project.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "id": name,
                    "title": "Synthetic named documents",
                    "files": ["README.md", "valid.json"],
                    "action": "json-check",
                }
            )
        )
        return ProjectSource.from_manifest(manifest)

    def tls_files(self, hostname="localhost"):
        if not shutil.which("openssl"):
            self.skipTest("openssl is required for the direct-TLS integration check")
        root = Path(self.temp.name) / ("tls-" + hostname.replace(".", "-"))
        root.mkdir()
        ca_key, ca_cert = root / "ca-key.pem", root / "ca.pem"
        key, csr, cert = root / "key.pem", root / "request.pem", root / "cert.pem"
        extension = root / "extension.cnf"
        extension.write_text(
            f"subjectAltName=DNS:{hostname}\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
        )
        commands = (
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=Prism test CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout",
                str(ca_key),
                "-out",
                str(ca_cert),
            ],
            [
                "openssl",
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                f"/CN={hostname}",
                "-keyout",
                str(key),
                "-out",
                str(csr),
            ],
            [
                "openssl",
                "x509",
                "-req",
                "-days",
                "1",
                "-in",
                str(csr),
                "-CA",
                str(ca_cert),
                "-CAkey",
                str(ca_key),
                "-CAcreateserial",
                "-extfile",
                str(extension),
                "-out",
                str(cert),
            ],
        )
        for command in commands:
            subprocess.run(command, check=True, capture_output=True)
        key.chmod(0o600)
        ca_key.chmod(0o600)
        return ca_cert, cert, key

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def tls_server(self, app, port, cert, key):
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                ssl_certfile=str(cert),
                ssl_keyfile=str(key),
                access_log=False,
                log_level="critical",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while not server.started and thread.is_alive() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(server.started)

        def stop():
            server.should_exit = True
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        self.addCleanup(stop)
        return server

    def named_session(self, version, subject, *, mode):
        principal = NamedPrincipal(self.config.issuer, subject)
        invitation = self.store.create_invitation(
            version, principal, mode=mode, expires_in=600
        )
        return principal, self.store.redeem_invitation(invitation["token"], principal)

    def test_strict_id_token_claims_signature_and_pkce(self):
        login = self.auth.begin(owner=True)
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(login["authorization_url"]).query
        )
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["openid"])
        self.transport.claims = self.claims("owner-subject", query["nonce"][0])
        result = self.auth.callback(
            state=query["state"][0], code="code", binding=login["binding"]
        )
        self.assertEqual(result["role"], "owner")
        self.assertEqual(self.transport.last_form["code_verifier"].count("."), 0)

        base = self.claims("recipient", "nonce")
        self.assertEqual(
            self.auth._identity(self._token(base), "nonce").subject, "recipient"
        )
        for patch in (
            {"iss": "https://other.example"},
            {"aud": "other-client"},
            {"azp": "other-client"},
            {"exp": int(self.now[0] - 31)},
            {"nonce": "wrong"},
            {"iat": int(self.now[0] - 601)},
        ):
            with self.subTest(patch=patch), self.assertRaises(Denied):
                self.auth._identity(self._token({**base, **patch}), "nonce")
        multi = {**base, "aud": [self.config.client_id, "api"]}
        with self.assertRaises(Denied):
            self.auth._identity(self._token(multi), "nonce")
        multi["azp"] = self.config.client_id
        self.assertEqual(
            self.auth._identity(self._token(multi), "nonce").subject, "recipient"
        )

        other_key = RSAKey.generate_key(2048, private=True, auto_kid=True)
        forged = jwt.encode(
            {"alg": "RS256", "kid": other_key.kid},
            base,
            other_key,
            algorithms=["RS256"],
        )
        with self.assertRaises(Denied):
            self.auth._identity(forged, "nonce")

    def _token(self, claims):
        return jwt.encode(
            {"alg": "RS256", "kid": self.transport.key.kid},
            claims,
            self.transport.key,
            algorithms=["RS256"],
        )

    def test_invitation_is_identity_bound_atomic_single_use_and_revocable(self):
        recipient = NamedPrincipal(self.config.issuer, "recipient-a")
        invitation = self.store.create_invitation(
            self.inspect["id"], recipient, mode="inspect", expires_in=600
        )
        self.assertNotIn(
            invitation["token"],
            Path(self.store.path).read_bytes().decode("latin1", errors="ignore"),
        )
        barrier = threading.Barrier(2)
        outcomes = []

        def redeem():
            try:
                barrier.wait()
                outcomes.append(
                    self.store.redeem_invitation(invitation["token"], recipient)
                )
            except Denied:
                outcomes.append("denied")

        threads = [threading.Thread(target=redeem) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        sessions = [value for value in outcomes if isinstance(value, dict)]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(outcomes.count("denied"), 1)
        session = sessions[0]
        with self.assertRaises(Denied):
            self.store.session(
                session["id"], NamedPrincipal(self.config.issuer, "recipient-b")
            )
        with self.assertRaises(Denied):
            self.store.session(
                session["id"], NamedPrincipal("https://other.example", "recipient-a")
            )
        with self.assertRaises(Denied):
            self.store.session(session["id"], "reviewer")
        with self.store.connect() as db:
            db.execute(
                "UPDATE sessions SET version=? WHERE id=?",
                (self.verify["id"], session["id"]),
            )
        with self.assertRaises(Denied):
            self.store.session(session["id"], recipient)
        with self.store.connect() as db:
            db.execute(
                "UPDATE sessions SET version=? WHERE id=?",
                (self.inspect["id"], session["id"]),
            )
        self.store.revoke_grant(session["grant_id"])
        with self.assertRaises(Denied):
            self.store.session(session["id"], recipient)

    def test_expired_version_revoked_and_action_mismatch_fail_closed(self):
        recipient = NamedPrincipal(self.config.issuer, "recipient")
        expired = self.store.create_invitation(
            self.inspect["id"], recipient, mode="inspect", expires_in=300
        )
        with self.store.connect() as db:
            db.execute("UPDATE invitations SET expires=0 WHERE id=?", (expired["id"],))
        with self.assertRaises(Denied):
            self.store.redeem_invitation(expired["token"], recipient)

        revoked = self.store.create_invitation(
            self.inspect["id"], recipient, mode="inspect", expires_in=300
        )
        self.store.revoke(self.inspect["id"])
        with self.assertRaises(Denied):
            self.store.redeem_invitation(revoked["token"], recipient)

        wrong_action = self.store.create_invitation(
            self.verify["id"], recipient, mode="verify", expires_in=300
        )
        with self.store.connect() as db:
            db.execute(
                "UPDATE invitations SET action='other' WHERE id=?",
                (wrong_action["id"],),
            )
        with self.assertRaises(Denied):
            self.store.redeem_invitation(wrong_action["token"], recipient)

    def test_https_api_owner_and_recipient_flow_denies_direct_and_cross_identity(self):
        project = self.project_source("oidc-imported")
        app = create_app(
            self.store, self.source, auth=self.auth, project_sources=[project]
        )
        with (
            TestClient(app, base_url="https://prism.example") as owner,
            TestClient(app, base_url="https://prism.example") as recipient,
        ):
            owner_start = owner.get("/auth/oidc/owner", follow_redirects=False)
            self.assertEqual(owner_start.status_code, 303)
            owner_login_cookie = owner_start.headers["set-cookie"]
            self.assertIn("__Host-prism_oidc_login=", owner_login_cookie)
            self.assertIn("Secure", owner_login_cookie)
            self.assertIn("HttpOnly", owner_login_cookie)
            self.assertIn("Path=/", owner_login_cookie)
            self.assertNotIn("Domain=", owner_login_cookie)
            owner_query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(owner_start.headers["location"]).query
            )
            self.transport.claims = self.claims(
                "owner-subject", owner_query["nonce"][0]
            )
            owner_callback = owner.get(
                "/auth/oidc/callback",
                params={"state": owner_query["state"][0], "code": "owner-code"},
                headers={
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "document",
                },
                follow_redirects=False,
            )
            self.assertEqual(owner_callback.status_code, 303)
            landing_headers = {
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            }
            self.assertEqual(owner_callback.headers["location"], "/owner")
            self.assertEqual(
                owner.get(
                    owner_callback.headers["location"], headers=landing_headers
                ).status_code,
                200,
            )
            self.assertEqual(
                owner.get("/api/owner/state", headers=landing_headers).status_code,
                403,
            )
            self.assertEqual(
                owner.get(
                    "/auth/oidc/owner", headers=landing_headers, follow_redirects=False
                ).status_code,
                403,
            )
            self.assertEqual(
                owner.get(
                    "/owner", headers={"Sec-Fetch-Site": "cross-site"}
                ).status_code,
                403,
            )
            owner_cookie = owner_callback.headers["set-cookie"]
            self.assertIn("__Host-prism_identity=", owner_cookie)
            self.assertIn("Secure", owner_cookie)
            self.assertIn("HttpOnly", owner_cookie)
            self.assertIn("Path=/", owner_cookie)
            self.assertNotIn("Domain=", owner_cookie)
            owner_state = owner.get("/api/owner/state").json()
            self.assertIn(
                "oidc-imported", [item["id"] for item in owner_state["projects"]]
            )
            self.assertEqual(
                owner.get("/api/owner/projects/oidc-imported/catalog").status_code,
                200,
            )
            headers = {
                "Origin": self.config.public_origin,
                "X-Prism-CSRF": owner_state["csrf"],
            }
            created = owner.post(
                f"/api/owner/versions/{self.verify['id']}/invitations",
                headers=headers,
                json={
                    "recipient_issuer": self.config.issuer,
                    "recipient_subject": "recipient-a",
                    "mode": "inspect",
                    "expires_in": 600,
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            token = urllib.parse.parse_qs(
                urllib.parse.urlsplit(created.json()["url"]).fragment
            )["token"][0]
            started = recipient.post(
                "/api/auth/oidc/invitation",
                headers={"Origin": self.config.public_origin},
                json={"token": token},
            )
            login_query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(started.json()["authorization_url"]).query
            )
            self.transport.claims = self.claims("recipient-a", login_query["nonce"][0])
            callback = recipient.get(
                "/auth/oidc/callback",
                params={"state": login_query["state"][0], "code": "recipient-code"},
                headers=landing_headers,
                follow_redirects=False,
            )
            self.assertEqual(callback.status_code, 303, callback.text)
            self.assertEqual(callback.headers["location"], "/review")
            self.assertEqual(
                recipient.get(
                    callback.headers["location"], headers=landing_headers
                ).status_code,
                200,
            )
            self.assertEqual(
                recipient.post(
                    "/api/auth/oidc/invitation",
                    headers={**landing_headers, "Origin": self.config.public_origin},
                    json={"token": "x" * 40},
                ).status_code,
                403,
            )
            state = recipient.get("/api/review/state").json()
            self.assertEqual([v["id"] for v in state["versions"]], [self.verify["id"]])
            self.assertTrue(state["identity_mode"])
            session = state["session"]
            review_headers = {
                "Origin": self.config.public_origin,
                "X-Prism-CSRF": state["csrf"],
            }
            self.assertEqual(
                recipient.post(
                    "/api/review/sessions",
                    headers=review_headers,
                    json={"version": self.verify["id"]},
                ).status_code,
                403,
            )
            self.assertEqual(
                recipient.get(f"/api/review/sessions/{session}").status_code, 200
            )
            self.assertEqual(
                recipient.get("/api/review/sessions/" + "0" * 32).status_code, 403
            )
            evidence_id = self.verify["manifest"]["files"][0]["id"]
            self.assertEqual(
                recipient.get(
                    f"/api/review/sessions/{session}/evidence/{evidence_id}"
                ).status_code,
                200,
            )
            self.assertEqual(
                recipient.get(
                    f"/api/review/sessions/{session}/evidence/" + "0" * 24
                ).status_code,
                403,
            )
            self.assertEqual(
                recipient.post(
                    f"/api/review/sessions/{session}/requests",
                    headers=review_headers,
                    json={"description": "Request a separately reviewed file."},
                ).status_code,
                200,
            )
            self.assertEqual(
                recipient.post(
                    f"/api/review/sessions/{session}/runs",
                    headers=review_headers,
                    json={"action": "json-check", "request_key": "inspect-cannot-run"},
                ).status_code,
                403,
            )
            self.assertEqual(
                recipient.post(
                    "/api/login",
                    headers={"Origin": self.config.public_origin},
                    json={"token": "x" * 40},
                ).status_code,
                404,
            )

            second = owner.post(
                f"/api/owner/versions/{self.verify['id']}/invitations",
                headers=headers,
                json={
                    "recipient_issuer": self.config.issuer,
                    "recipient_subject": "recipient-a",
                    "mode": "inspect",
                    "expires_in": 600,
                },
            )
            second_token = urllib.parse.parse_qs(
                urllib.parse.urlsplit(second.json()["url"]).fragment
            )["token"][0]
            with TestClient(app, base_url="https://prism.example") as recipient_two:
                second_start = recipient_two.post(
                    "/api/auth/oidc/invitation",
                    headers={"Origin": self.config.public_origin},
                    json={"token": second_token},
                )
                second_query = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(
                        second_start.json()["authorization_url"]
                    ).query
                )
                self.transport.claims = self.claims(
                    "recipient-a", second_query["nonce"][0]
                )
                self.assertEqual(
                    recipient_two.get(
                        "/auth/oidc/callback",
                        params={
                            "state": second_query["state"][0],
                            "code": "recipient-two-code",
                        },
                        follow_redirects=False,
                    ).status_code,
                    303,
                )
                session_two = recipient_two.get("/api/review/state").json()["session"]
                self.assertNotEqual(session, session_two)
                self.assertEqual(
                    recipient.get(f"/api/review/sessions/{session_two}").status_code,
                    403,
                )
                self.assertEqual(
                    recipient_two.get(f"/api/review/sessions/{session}").status_code,
                    403,
                )
            replay = recipient.get(
                "/auth/oidc/callback",
                params={"state": login_query["state"][0], "code": "recipient-code"},
                follow_redirects=False,
            )
            self.assertEqual(replay.status_code, 401)

            with self.store.connect() as db:
                grant = db.execute(
                    "SELECT grant_id FROM sessions WHERE id=?", (session,)
                ).fetchone()["grant_id"]
            self.assertEqual(
                owner.post(
                    f"/api/owner/grants/{grant}/revoke", headers=headers, json={}
                ).status_code,
                200,
            )
            self.assertEqual(
                recipient.get(f"/api/review/sessions/{session}").status_code, 403
            )

            plain = TestClient(app, base_url="http://prism.example")
            plain.cookies.update(recipient.cookies)
            response = plain.get(
                "/api/review/state", headers={"X-Forwarded-Proto": "https"}
            )
            self.assertEqual(response.status_code, 403)

    def test_owner_mediated_identity_discovery_is_one_use_and_grants_nothing(self):
        app = create_app(self.store, self.source, auth=self.auth)
        with (
            TestClient(app, base_url="https://prism.example") as owner,
            TestClient(app, base_url="https://prism.example") as recipient,
            TestClient(app, base_url="https://prism.example") as outsider,
        ):
            navigation = {
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            }
            for page in ("/identify", "/invite"):
                self.assertEqual(
                    recipient.get(page, headers=navigation).status_code, 200
                )
                self.assertEqual(
                    recipient.get(
                        page,
                        headers={
                            "Sec-Fetch-Site": "cross-site",
                            "Sec-Fetch-Mode": "cors",
                        },
                    ).status_code,
                    403,
                )
            self.assertEqual(
                recipient.get("/api/auth/mode", headers=navigation).status_code, 403
            )
            self.assertEqual(
                recipient.get(
                    "/auth/oidc/owner", headers=navigation, follow_redirects=False
                ).status_code,
                403,
            )
            started = owner.get("/auth/oidc/owner", follow_redirects=False)
            owner_query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(started.headers["location"]).query
            )
            self.transport.claims = self.claims(
                "owner-subject", owner_query["nonce"][0]
            )
            self.assertEqual(
                owner.get(
                    "/auth/oidc/callback",
                    params={"state": owner_query["state"][0], "code": "owner"},
                    follow_redirects=False,
                ).status_code,
                303,
            )
            self.assertEqual(
                outsider.post(
                    "/api/owner/identity-discoveries",
                    json={},
                    headers={"Origin": self.config.public_origin},
                ).status_code,
                401,
            )
            self.assertEqual(
                owner.post(
                    "/api/owner/identity-discoveries",
                    json={},
                    headers={"Origin": self.config.public_origin},
                ).status_code,
                403,
            )
            csrf = owner.get("/api/owner/state").json()["csrf"]
            headers = {"Origin": self.config.public_origin, "X-Prism-CSRF": csrf}
            created = owner.post(
                "/api/owner/identity-discoveries", json={}, headers=headers
            )
            self.assertEqual(created.status_code, 200, created.text)
            discovery = created.json()
            self.assertIn("/identify#token=", discovery["url"])
            token = discovery["url"].split("#token=", 1)[1]
            path = "/api/owner/identity-discoveries/" + discovery["id"]
            self.assertEqual(outsider.get(path).status_code, 401)
            self.assertEqual(owner.get(path).json()["status"], "created")
            start = recipient.post(
                "/api/auth/oidc/discovery",
                json={"token": token},
                headers={"Origin": self.config.public_origin},
            )
            self.assertEqual(start.status_code, 200, start.text)
            self.assertEqual(
                outsider.post(
                    "/api/auth/oidc/discovery",
                    json={"token": token},
                    headers={"Origin": self.config.public_origin},
                ).status_code,
                401,
            )
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(start.json()["authorization_url"]).query
            )
            self.transport.claims = self.claims("verified-recipient", query["nonce"][0])
            self.assertEqual(
                outsider.get(
                    "/auth/oidc/callback",
                    params={"state": query["state"][0], "code": "stolen"},
                    follow_redirects=False,
                ).status_code,
                401,
            )
            completed = recipient.get(
                "/auth/oidc/callback",
                params={"state": query["state"][0], "code": "recipient"},
                follow_redirects=False,
            )
            self.assertEqual(completed.status_code, 303, completed.text)
            self.assertEqual(completed.headers["location"], "/identify/completed")
            self.assertNotIn(
                "__Host-prism_identity", completed.headers.get("set-cookie", "")
            )
            self.assertEqual(recipient.get("/api/review/state").status_code, 401)
            self.assertEqual(outsider.get(path).status_code, 401)
            verified = owner.get(path).json()
            self.assertEqual(
                (verified["issuer"], verified["subject"]),
                (self.config.issuer, "verified-recipient"),
            )
            self.assertEqual(
                recipient.get(
                    "/auth/oidc/callback",
                    params={"state": query["state"][0], "code": "recipient"},
                    follow_redirects=False,
                ).status_code,
                401,
            )
            self.assertEqual(self.store.invitations(), [])

    def test_identity_discovery_expiry_and_failed_token_validation(self):
        discovery = self.auth.create_discovery()
        login = self.auth.begin(discovery_token=discovery["token"])
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(login["authorization_url"]).query
        )
        self.transport.claims = self.claims("recipient", "wrong-nonce")
        with self.assertRaises(Denied):
            self.auth.callback(
                state=query["state"][0], code="code", binding=login["binding"]
            )
        self.assertEqual(self.auth.discovery(discovery["id"])["status"], "started")
        self.now[0] += 901
        with self.assertRaises(Denied):
            self.auth.discovery(discovery["id"])
        expired = self.auth.create_discovery()
        self.now[0] += 901
        with self.assertRaises(Denied):
            self.auth.begin(discovery_token=expired["token"])

    def test_expired_completed_identity_is_deleted_on_read_and_startup(self):
        def complete():
            created = self.auth.create_discovery()
            login = self.auth.begin(discovery_token=created["token"])
            self.assertEqual(self.callback(login, "recipient")["role"], "discovery")
            return created["id"]

        first = complete()
        self.now[0] += 901
        with self.assertRaises(Denied):
            self.auth.discovery(first)
        with self.store.connect() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM oidc_discoveries WHERE id=?", (first,)
                ).fetchone()
            )

        second = complete()
        self.now[0] += 901
        OIDCAuth(
            self.store,
            self.config,
            transport=self.transport,
            clock=lambda: self.now[0],
        )
        with self.store.connect() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM oidc_discoveries WHERE id=?", (second,)
                ).fetchone()
            )

    def test_pending_state_expires_and_success_rotates_existing_session(self):
        protected = self.auth.begin(owner=True)
        protected_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(protected["authorization_url"]).query
        )
        self.transport.claims = self.claims(
            "owner-subject", protected_query["nonce"][0]
        )
        with self.assertRaises(Denied):
            self.auth.callback(
                state=protected_query["state"][0],
                code="wrong-binding-code",
                binding="wrong-browser-binding",
            )
        self.assertEqual(
            self.auth.callback(
                state=protected_query["state"][0],
                code="correct-binding-code",
                binding=protected["binding"],
            )["role"],
            "owner",
        )

        first = self.auth.begin(owner=True)
        one = self.callback(first, "owner-subject")
        second = self.auth.begin(owner=True)
        two_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(second["authorization_url"]).query
        )
        self.transport.claims = self.claims("owner-subject", two_query["nonce"][0])
        two = self.auth.callback(
            state=two_query["state"][0],
            code="second-code",
            binding=second["binding"],
            old_cookie=one["cookie"],
        )
        with self.store.connect() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM identity_sessions WHERE cookie_hash=?",
                    (digest(one["cookie"]),),
                ).fetchone()
            )
        self.assertNotEqual(one["cookie"], two["cookie"])

        expired = self.auth.begin(owner=True)
        self.now[0] += 301
        with self.assertRaises(Denied):
            self.callback(expired, "owner-subject")

    def test_runtime_readiness_blocks_redeem_without_consuming_invitation(self):
        invitation = self.store.create_invitation(
            self.verify["id"],
            NamedPrincipal(self.config.issuer, "recipient"),
            mode="verify",
            expires_in=600,
        )
        login = self.auth.begin(invitation_token=invitation["token"])

        def unavailable(invitation_id):
            self.store.invitation_activation(invitation_id)
            raise Denied("The approved runtime profile is not ready on this host.", 503)

        self.auth.activation_check = unavailable
        with self.assertRaises(Denied):
            self.callback(login, "recipient")
        self.assertEqual(
            self.store.invitation(invitation["token"])["id"], invitation["id"]
        )

    def test_wrong_recipient_does_not_consume_invitation(self):
        invitation = self.store.create_invitation(
            self.inspect["id"],
            NamedPrincipal(self.config.issuer, "expected"),
            mode="inspect",
            expires_in=600,
        )
        login = self.auth.begin(invitation_token=invitation["token"])
        with self.assertRaises(Denied):
            self.callback(login, "other")
        login = self.auth.begin(invitation_token=invitation["token"])
        self.assertEqual(self.callback(login, "expected")["role"], "review")

    def test_config_rejects_noncanonical_or_insecure_urls(self):
        base = self.config.__dict__.copy()
        for field, value in (
            ("public_origin", "https://prism.example/path"),
            ("public_origin", "http://prism.example"),
            ("authorization_endpoint", "https://idp.example/auth?tenant=x"),
            ("token_endpoint", "https://user@idp.example/token"),
            ("jwks_uri", "https://idp.example/jwks#fragment"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                OIDCConfig(**{**base, field: value})

    def test_config_template_cli_entry_and_owner_only_secret(self):
        template = Path("examples/oidc-config.example.json")
        loaded = OIDCConfig.from_file(template)
        self.assertEqual(loaded.algorithms, ("RS256",))
        self.assertEqual(loaded.public_origin, "https://prism.example.com:8443")
        args = parser().parse_args(
            [
                "identity-service",
                "--bind-host",
                "127.0.0.1",
                "--port",
                "8443",
                "--oidc-config",
                str(template),
                "--tls-cert-file",
                "cert.pem",
                "--tls-key-file",
                "key.pem",
            ]
        )
        self.assertEqual(args.command, "identity-service")
        self.assertFalse(args.allow_openai)
        self.assertEqual(args.model_budget_cents, 0)

        secret = Path(self.temp.name) / "oidc-secret"
        secret.write_text("synthetic-client-secret-value")
        secret.chmod(0o600)
        self.assertEqual(read_client_secret(secret), "synthetic-client-secret-value")
        secret.chmod(0o644)
        with self.assertRaises(ValueError):
            read_client_secret(secret)

        _, certificate, tls_key = self.tls_files()
        tls_key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            serve(
                Path(self.temp.name) / "identity-state",
                8443,
                oidc_config=template,
                bind_host="127.0.0.1",
                tls_cert_file=certificate,
                tls_key_file=tls_key,
            )

    def test_client_secret_basic_encoding_is_form_safe(self):
        transport = FixedOIDCTransport()
        captured = []
        transport._read_json = lambda request: captured.append(request) or {}
        transport.token(
            "https://idp.example/token",
            {"code": "synthetic"},
            client_id="client:id",
            client_secret="secret/value",
        )
        authorization = captured[0].headers["Authorization"]
        decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode()
        self.assertEqual(decoded, "client%3Aid:secret%2Fvalue")

    def test_identity_cli_constructs_zero_allowance_service_without_dispatch(self):
        _, certificate, tls_key = self.tls_files("prism.example.com")
        model_key = Path(self.temp.name) / "model-key"
        model_key.write_text("sk-" + "x" * 32)
        tls_key.chmod(0o600)
        model_key.chmod(0o600)
        with mock_patch("prism.demo.uvicorn.run") as run:
            self.assertEqual(
                main(
                    [
                        "identity-service",
                        "--data-dir",
                        str(Path(self.temp.name) / "identity-service"),
                        "--bind-host",
                        "127.0.0.1",
                        "--port",
                        "8443",
                        "--oidc-config",
                        "examples/oidc-config.example.json",
                        "--tls-cert-file",
                        str(certificate),
                        "--tls-key-file",
                        str(tls_key),
                        "--allow-openai",
                        "--openai-key-file",
                        str(model_key),
                        "--model-budget-cents",
                        "0",
                    ]
                ),
                0,
            )
        app = run.call_args.args[0]
        with TestClient(app, base_url="https://prism.example.com:8443") as client:
            self.assertEqual(client.get("/api/auth/mode").json()["identity_mode"], True)
        self.assertEqual(run.call_args.kwargs["proxy_headers"], False)

    def test_identity_preflight_is_local_secret_free_and_checks_tls_pair(self):
        _, certificate, tls_key = self.tls_files("prism.example.com")
        report = identity_preflight(
            oidc_config="examples/oidc-config.example.json",
            bind_host="127.0.0.1",
            port=8443,
            tls_cert_file=certificate,
            tls_key_file=tls_key,
        )
        self.assertTrue(report["local_inputs_valid"])
        self.assertFalse(report["network_probe_enabled"])
        self.assertEqual(
            next(c for c in report["checks"] if c["id"] == "idp_tls_reachability")[
                "status"
            ],
            "not_checked",
        )
        rendered = json.dumps(report)
        self.assertNotIn(str(certificate), rendered)
        self.assertNotIn(str(tls_key), rendered)
        _, other_cert, _ = self.tls_files("other.example.com")
        with self.assertRaises(ValueError):
            validate_tls_identity(other_cert, tls_key, "other.example.com")
        with self.assertRaises(ValueError):
            validate_tls_identity(certificate, tls_key, "wrong.example.com")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "identity-preflight",
                        "--bind-host",
                        "127.0.0.1",
                        "--port",
                        "8443",
                        "--oidc-config",
                        str(Path(self.temp.name) / "missing-config.json"),
                        "--tls-cert-file",
                        str(Path(self.temp.name) / "private-cert.pem"),
                        "--tls-key-file",
                        str(Path(self.temp.name) / "private-key.pem"),
                        "--json",
                    ]
                ),
                2,
            )
        failure = json.loads(output.getvalue())
        self.assertEqual(failure["pilot_ready"], False)
        self.assertNotIn(self.temp.name, output.getvalue())

    def test_identity_factory_defaults_zero_and_explicit_budget_is_a_ceiling(self):
        with mock_patch("prism.webapp.Conversations") as conversations:
            configured = conversations.return_value
            configured.key = None
            configured.test_model = None
            configured.budget_cents = 0
            create_app(self.store, self.source, auth=self.auth, key="synthetic-key")
        self.assertEqual(conversations.call_args.kwargs["budget_cents"], 0)
        args = parser().parse_args(
            [
                "identity-service",
                "--bind-host",
                "127.0.0.1",
                "--port",
                "8443",
                "--oidc-config",
                "examples/oidc-config.example.json",
                "--tls-cert-file",
                "cert.pem",
                "--tls-key-file",
                "key.pem",
                "--model-budget-cents",
                "25",
            ]
        )
        self.assertEqual(args.model_budget_cents, 25)
        invalid_root = Path(self.temp.name) / "invalid-budget-state"
        with self.assertRaisesRegex(ValueError, "explicit allowance"):
            serve(
                invalid_root,
                8443,
                model_budget_cents=1,
                oidc_config="examples/oidc-config.example.json",
                tls_cert_file="unused-cert.pem",
                tls_key_file="unused-key.pem",
            )
        self.assertFalse(invalid_root.exists())

    def test_identify_bootstrap_binds_browser_and_returns_only_verified_identity(self):
        config = IdentifyOIDCConfig(
            issuer=self.config.issuer,
            authorization_endpoint=self.config.authorization_endpoint,
            token_endpoint=self.config.token_endpoint,
            jwks_uri=self.config.jwks_uri,
            client_id=self.config.client_id,
            redirect_uri="https://prism.example/auth/oidc/identify/callback",
            public_origin=self.config.public_origin,
        )
        bootstrap = BootstrapIdentity(
            config, transport=self.transport, clock=lambda: self.now[0]
        )
        authorization_url, binding = bootstrap.begin(bootstrap.start_token)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(authorization_url).query)
        with self.assertRaises(Denied):
            bootstrap.callback(
                state=query["state"][0], code="code", binding="wrong-binding"
            )
        self.assertIsNotNone(bootstrap.pending)
        self.transport.claims = self.claims(
            "verified-bootstrap-subject", query["nonce"][0]
        )
        result = bootstrap.callback(
            state=query["state"][0], code="code", binding=binding
        )
        self.assertEqual(
            result,
            {
                "issuer": self.config.issuer,
                "subject": "verified-bootstrap-subject",
            },
        )
        self.assertIsNone(bootstrap.pending)
        with self.assertRaises(Denied):
            bootstrap.begin(bootstrap.start_token)

        competing = BootstrapIdentity(
            config, transport=self.transport, clock=lambda: self.now[0]
        )
        outcomes = []
        gate = threading.Barrier(8)

        def attempt():
            gate.wait()
            try:
                competing.begin(competing.start_token)
                outcomes.append("started")
            except Denied:
                outcomes.append("denied")

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("started"), 1)
        self.assertEqual(outcomes.count("denied"), 7)

        class DeadlineTransport(FakeOIDC):
            def token(inner_self, *args, **kwargs):
                value = super().token(*args, **kwargs)
                self.now[0] += 301
                return value

        deadline_transport = DeadlineTransport()
        deadline = BootstrapIdentity(
            config, transport=deadline_transport, clock=lambda: self.now[0]
        )
        authorization_url, binding = deadline.begin(deadline.start_token)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(authorization_url).query)
        deadline_transport.claims = self.claims(
            "late-bootstrap-subject", query["nonce"][0], exp=int(self.now[0] + 600)
        )
        with self.assertRaises(Denied):
            deadline.callback(
                state=query["state"][0], code="late-code", binding=binding
            )
        self.assertIsNone(deadline.result)
        self.assertIsNone(deadline.pending)
        with self.assertRaises(Denied):
            deadline.begin(deadline.start_token)

    def test_identify_bootstrap_deadline_stops_listener_without_identity(self):
        _, certificate, tls_key = self.tls_files("localhost")
        port = self.free_port()
        origin = f"https://localhost:{port}"
        config = Path(self.temp.name) / "identify.json"
        config.write_text(
            json.dumps(
                {
                    "issuer": "https://idp.example",
                    "authorization_endpoint": "https://idp.example/authorize",
                    "token_endpoint": "https://idp.example/token",
                    "jwks_uri": "https://idp.example/jwks",
                    "client_id": "bounded-client",
                    "redirect_uri": origin + "/auth/oidc/identify/callback",
                    "public_origin": origin,
                    "algorithms": ["RS256"],
                }
            )
        )
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(ValueError, "without a verified identity"),
        ):
            run_identity_bootstrap(
                oidc_config=config,
                bind_host="127.0.0.1",
                port=port,
                tls_cert_file=certificate,
                tls_key_file=tls_key,
                lifetime_seconds=0.2,
            )
        self.assertIn("/identify#token=", output.getvalue())
        with socket.socket() as probe:
            probe.settimeout(0.5)
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    def test_real_loopback_tls_code_flow_uses_pkce_ca_and_exact_identity(self):
        ca, certificate, tls_key = self.tls_files("localhost")
        idp_port, prism_port = self.free_port(), self.free_port()
        while prism_port == idp_port:
            prism_port = self.free_port()
        issuer = f"https://localhost:{idp_port}"
        origin = f"https://localhost:{prism_port}"
        signing_key = RSAKey.generate_key(2048, private=True, auto_kid=True)
        issued = {}
        next_subject = ["owner-subject"]
        observations = {"token_requests": 0, "pkce_matches": 0}
        idp = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

        @idp.get("/authorize")
        async def authorize(request: Request):
            query = request.query_params
            code = f"code-{len(issued) + 1}"
            issued[code] = {
                "subject": next_subject[0],
                "nonce": query["nonce"],
                "challenge": query["code_challenge"],
                "redirect_uri": query["redirect_uri"],
            }
            return RedirectResponse(
                query["redirect_uri"]
                + "?"
                + urllib.parse.urlencode({"state": query["state"], "code": code}),
                status_code=303,
            )

        @idp.post("/token")
        async def token(request: Request):
            form = urllib.parse.parse_qs((await request.body()).decode())
            record = issued.pop(form.get("code", [""])[0], None)
            if (
                record is None
                or form.get("redirect_uri", [""])[0] != record["redirect_uri"]
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            verifier = form.get("code_verifier", [""])[0]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            if challenge != record["challenge"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            observations["token_requests"] += 1
            observations["pkce_matches"] += 1
            now = int(time.time())
            claims = {
                "iss": issuer,
                "sub": record["subject"],
                "aud": "loopback-client",
                "exp": now + 300,
                "iat": now,
                "nonce": record["nonce"],
            }
            encoded = jwt.encode(
                {"alg": "RS256", "kid": signing_key.kid},
                claims,
                signing_key,
                algorithms=["RS256"],
            )
            return {"id_token": encoded}

        @idp.get("/jwks")
        async def jwks():
            return {"keys": [signing_key.as_dict(private=False)]}

        self.tls_server(idp, idp_port, certificate, tls_key)
        config = OIDCConfig(
            issuer=issuer,
            authorization_endpoint=issuer + "/authorize",
            token_endpoint=issuer + "/token",
            jwks_uri=issuer + "/jwks",
            client_id="loopback-client",
            redirect_uri=origin + "/auth/oidc/callback",
            public_origin=origin,
            owner_subject="owner-subject",
        )
        trusted = ssl.create_default_context(cafile=str(ca))
        auth = OIDCAuth(
            self.store,
            config,
            transport=FixedOIDCTransport(ssl_context=trusted),
            activation_check=lambda invitation: self.store.invitation_activation(
                invitation
            ),
        )
        app = create_app(self.store, self.source, port=prism_port, auth=auth)
        self.tls_server(app, prism_port, certificate, tls_key)

        with self.assertRaises(httpx.TransportError):
            httpx.get(origin + "/api/auth/mode", timeout=2)
        with self.assertRaises(httpx.TransportError):
            httpx.get(
                f"https://127.0.0.1:{prism_port}/api/auth/mode",
                verify=ssl.create_default_context(cafile=str(ca)),
                timeout=2,
            )

        with (
            httpx.Client(
                base_url=origin,
                verify=ssl.create_default_context(cafile=str(ca)),
                follow_redirects=False,
            ) as owner,
            httpx.Client(
                base_url=origin,
                verify=ssl.create_default_context(cafile=str(ca)),
                follow_redirects=False,
            ) as recipient,
            httpx.Client(
                verify=ssl.create_default_context(cafile=str(ca)),
                follow_redirects=False,
            ) as browser,
        ):
            start = owner.get("/auth/oidc/owner")
            provider = browser.get(start.headers["location"])
            callback = owner.get(provider.headers["location"])
            self.assertEqual(callback.status_code, 303)
            owner_state = owner.get("/api/owner/state").json()
            headers = {
                "Origin": origin,
                "X-Prism-CSRF": owner_state["csrf"],
            }
            invitation = owner.post(
                f"/api/owner/versions/{self.inspect['id']}/invitations",
                headers=headers,
                json={
                    "recipient_issuer": issuer,
                    "recipient_subject": "recipient-a",
                    "mode": "inspect",
                    "expires_in": 600,
                },
            ).json()
            invite_token = urllib.parse.parse_qs(
                urllib.parse.urlsplit(invitation["url"]).fragment
            )["token"][0]

            next_subject[0] = "wrong-recipient"
            wrong = recipient.post(
                "/api/auth/oidc/invitation",
                headers={"Origin": origin},
                json={"token": invite_token},
            )
            wrong_provider = browser.get(wrong.json()["authorization_url"])
            self.assertEqual(
                recipient.get(wrong_provider.headers["location"]).status_code, 403
            )

            next_subject[0] = "recipient-a"
            started = recipient.post(
                "/api/auth/oidc/invitation",
                headers={"Origin": origin},
                json={"token": invite_token},
            )
            provider = browser.get(started.json()["authorization_url"])
            self.assertEqual(
                recipient.get(provider.headers["location"]).status_code, 303
            )
            state = recipient.get("/api/review/state").json()
            self.assertEqual(
                [item["id"] for item in state["versions"]], [self.inspect["id"]]
            )
            self.assertEqual(
                recipient.get("/api/review/sessions/" + "0" * 32).status_code,
                403,
            )
            replay = recipient.post(
                "/api/auth/oidc/invitation",
                headers={"Origin": origin},
                json={"token": invite_token},
            )
            self.assertEqual(replay.status_code, 404)

        identify_port = self.free_port()
        identify_origin = f"https://localhost:{identify_port}"
        identify_config = IdentifyOIDCConfig(
            issuer=issuer,
            authorization_endpoint=issuer + "/authorize",
            token_endpoint=issuer + "/token",
            jwks_uri=issuer + "/jwks",
            client_id="loopback-client",
            redirect_uri=identify_origin + "/auth/oidc/identify/callback",
            public_origin=identify_origin,
        )
        identify = BootstrapIdentity(
            identify_config,
            transport=FixedOIDCTransport(ssl_context=trusted),
        )
        identified = []
        identify_app = create_identify_app(identify, on_success=identified.append)
        self.tls_server(identify_app, identify_port, certificate, tls_key)
        next_subject[0] = "bootstrap-subject"
        client_context = ssl.create_default_context(cafile=str(ca))
        with (
            httpx.Client(
                base_url=identify_origin,
                verify=client_context,
                follow_redirects=False,
            ) as identify_client,
            httpx.Client(
                verify=ssl.create_default_context(cafile=str(ca)),
                follow_redirects=False,
            ) as browser,
        ):
            self.assertEqual(
                identify_client.post(
                    "/api/identify", json={"token": identify.start_token}
                ).status_code,
                403,
            )
            start = identify_client.post(
                "/api/identify",
                headers={"Origin": identify_origin},
                json={"token": identify.start_token},
            )
            self.assertEqual(start.status_code, 200)
            cookie = start.headers["set-cookie"]
            self.assertIn("__Host-prism_identify=", cookie)
            self.assertIn("Path=/", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn("HttpOnly", cookie)
            provider = browser.get(start.json()["authorization_url"])
            callback = identify_client.get(provider.headers["location"])
            self.assertEqual(callback.status_code, 200)
            self.assertEqual(
                identified, [{"issuer": issuer, "subject": "bootstrap-subject"}]
            )
            self.assertNotIn("bootstrap-subject", callback.text)
        self.assertEqual(observations, {"token_requests": 4, "pkce_matches": 4})

    def test_named_conversation_and_json_job_cross_full_authorization_stack(self):
        project = self.project_source()
        candidate = self.store.candidate(
            project.freeze(
                ["README.md", "valid.json"],
                "Review the approved named documents.",
                "verify",
            )
        )
        self.store.approve(candidate["id"], candidate["digest"])
        principal, session = self.named_session(
            candidate["id"], "recipient-a", mode="verify"
        )
        jobs = ImmediateJobs(self.store, registry=FakeRegistry())
        run = jobs.submit_json_check(session["id"], principal, "named-json-check")
        completed = jobs.get(session["id"], principal, run["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["result"]["cleaned_up"])

        with self.assertRaises(Denied):
            jobs.get(
                session["id"],
                NamedPrincipal(self.config.issuer, "recipient-b"),
                run["id"],
            )
        observer, inspect_session = self.named_session(
            candidate["id"], "recipient-b", mode="inspect"
        )
        with self.assertRaises(Denied):
            jobs.submit_json_check(
                inspect_session["id"], observer, "inspect-direct-job"
            )

        evidence = session["manifest"]["files"][0]
        answer = {
            "answer": "The approved source reports the value 7.",
            "claims": [{"kind": "reported", "text": "The approved value is 7."}],
            "citations": [{"evidence_id": evidence["id"], "start": 1, "end": 2}],
            "run_references": [],
            "historical_run_references": [],
            "context_references": [],
            "limitations": ["This is synthetic test evidence."],
            "pending_request_id": None,
        }
        calls = []

        async def respond(messages, info):
            calls.append(messages)
            if len(calls) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "read_evidence",
                            {"evidence_id": evidence["id"], "start": 1, "end": 2},
                            tool_call_id="read-named",
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name, answer, tool_call_id="answer-named"
                    )
                ]
            )

        conversations = Conversations(
            self.store,
            jobs,
            test_model=FunctionModel(respond),
            budget_cents=100,
        )
        turn = asyncio.run(
            conversations.ask(
                session["id"],
                principal,
                "What value does the source report?",
                "named-question",
            )
        )
        self.assertEqual(turn["status"], "completed", turn)
        self.assertEqual(turn["answer"]["citations"][0]["version"], candidate["id"])
        with self.store.connect() as db:
            actors = {row[0] for row in db.execute("SELECT actor FROM events")}
        self.assertIn(principal.key, actors)
        self.assertNotIn(principal.subject, actors)

        async def revoke_while_answering(messages, info):
            self.store.revoke_grant(session["grant_id"])
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, answer)]
            )

        conversations.test_model = FunctionModel(revoke_while_answering)
        with self.assertRaises(Denied):
            asyncio.run(
                conversations.ask(
                    session["id"],
                    principal,
                    "Try after revocation.",
                    "revoked-question",
                )
            )


if __name__ == "__main__":
    unittest.main()
import asyncio
import json
