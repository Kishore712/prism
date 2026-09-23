# M2 Order 4: Named Remote Collaborator Invitations

**Status (2026-09-23):** The local identity-service implementation is present on
`prototype/m2-recipient-invitations`. It uses Authlib 1.8.0 and `joserfc` for a
fixed, provider-neutral OIDC authorization-code flow, exact issuer and subject
binding, recipient-bound invitations, server-side grants, and per-request
authorization checks. The current follow-up also provides a zero-default
identity-service allowance whose reservation state survives restart, a
secret-free local preflight, and one-shot OIDC identity discovery. Local test
and fake-IdP loopback TLS evidence are recorded. The private service has now
exercised a bounded synthetic recipient flow with a second Google identity:
one-time discovery, exact-version read-only invitation, fresh scoped session,
approved evidence reading, denied unauthorized access, one focused cited model
answer, and owner revocation. One broader model question failed closed without
partial output. A later Document release handoff also completed its approved
Kata `json-check` run; its scoped denial and post-revocation checks passed.
These bounded checks do not establish broad security, full order-4 acceptance,
or pilot readiness. See the [live
recipient record](validation/m2-recipient-live-identity.json), [local
validation record](validation/m2-recipient-invitations.json), and the [private
setup checklist](m2-recipient-private-setup.md).

## Scope and boundary

The supported path is:

`owner login → reviewed version → recipient-bound invitation → recipient OIDC login → fresh scoped session`

The invitation binds one exact OIDC issuer and `sub` claim to one approved
version, mode, action where applicable, expiry, and grant revision. The server
derives the authenticated principal from the validated ID token and checks the
current grant, session, version, expiry, action, and revision on each protected
API request. A client-supplied email, display name, role, history, or approval
object is not an identity or a grant.

The existing `prism demo` loopback mode remains unchanged and continues to use
ephemeral local capability links. Named identity mode is a separate
`identity-service` entry point with direct TLS. It does not trust proxy headers,
does not add a public deployment, and does not enable anonymous or password
fallback authentication. Use a separate `.prism-identity` data directory; this
increment does not read, migrate, or change the existing `.prism-demo` database
or its historical 300-cent ledger. The service starts Uvicorn with
`proxy_headers=False`.

## Configuration

Start from the repository [service OIDC configuration template](../examples/oidc-config.example.json)
or the separate [identify-only template](../examples/oidc-identify-config.example.json).
They contain no secret. The service file must contain only these fields:

| Field | Required value |
| --- | --- |
| `issuer` | The fixed HTTPS issuer of the selected OIDC tenant. |
| `authorization_endpoint` | The fixed HTTPS authorization endpoint. Dynamic discovery is not used. |
| `token_endpoint` | The fixed HTTPS token endpoint. |
| `jwks_uri` | The fixed HTTPS JWKS endpoint. |
| `client_id` | The client registered for this exact Prism service. |
| `redirect_uri` | Exactly `public_origin + /auth/oidc/callback`. |
| `public_origin` | The canonical HTTPS origin, without a path, query, or fragment. |
| `owner_subject` | The exact owner `sub` claim under this issuer. |
| `algorithms` | A non-empty fixed asymmetric set supported by the service, such as `["RS256"]`. |

The identify-only file has the same fixed HTTPS endpoint, client, origin and
algorithm fields but omits `owner_subject`; its `redirect_uri` is exactly
`public_origin + /auth/oidc/identify/callback`. Keep this configuration
separate from the service file and use it only with `identity-bootstrap`.

The owner must obtain the exact `iss` and `sub` values from the IdP's trusted
claims or administration surface. Do not derive `owner_subject` from an email,
display name, `preferred_username`, or a guessed account identifier. The
recipient's invitation uses the same configured issuer and the recipient's
exact `sub`; the owner should obtain it from the recipient's trusted IdP claim
or have the IdP administrator provide it. The service rejects an invitation
for a different issuer.

The owner interface offers a one-time **Verify the account that opens this
link** flow. A signed-in owner creates the short-lived link from an approved
version, shares it privately with the intended recipient, and selects **Check
account** after the provider sign-in. The service validates the normal OIDC
code flow with its fixed issuer, PKCE, state, nonce, browser cookie, signature,
audience, and expiry. It records the verified issuer and `sub` for the owner to
review and fills the invitation fields. This proves which provider account
completed sign-in; it does not establish who controls that account or confirm
that it belongs to the intended person. The owner must confirm the recipient
through a separate trusted channel before issuing an invitation. An opaque
issuer/`sub` pair is a stable provider identity, not a human identity. This
check does **not** create a collaborator session, invitation, grant, or project
access. The link is usable once and expires after 15 minutes; the owner-only
result also expires then. The recipient sees only a completion notice, not the
`sub`. A fresh link is required after a failed or interrupted sign-in. The
owner must still approve the exact handoff version and create a separate
invitation.

If the client requires a secret, place it in a separate owner-only regular file
with mode `0600`; do not paste it into chat, the repository, the config JSON,
browser URLs, or logs. The service rejects symlinks, group/world-readable
secret files, and malformed secret files. TLS certificate and key inputs must
also be explicit regular files; the private TLS key must be an owner-only
`0600` single-link file. The service uses direct TLS and does not trust
`X-Forwarded-Proto` or another proxy header.

## Starting the service

Register the exact redirect URI with the IdP before a real login. The listening
port must be the port in `public_origin`; bind only to the intended private
interface. A representative invocation is:

```bash
uv run --no-editable prism identity-service \
  --data-dir .prism-identity \
  --bind-host <private-interface> \
  --port <canonical-origin-port> \
  --oidc-config /private/path/oidc-config.json \
  --oidc-client-secret-file /private/path/oidc-client-secret \
  --tls-cert-file /private/path/prism-cert.pem \
  --tls-key-file /private/path/prism-key.pem \
  --project "$PWD/examples/document-handoff/.prism-project.json" \
  --runtime-profile development
```

Omit `--oidc-client-secret-file` only when the registered client does not use
one. Use `reference-linux` only after the separate reference-host readiness
and runtime gates have been demonstrated; this guide does not claim those
gates. Identity mode exposes `--model-budget-cents` with a default of `0`.
Its reservation ledger is independent from `.prism-demo`; changing the ceiling
does not clear or rewrite existing reservations, and restarting the service
does not recharge the ledger. `--allow-openai --openai-key-file` may be
supplied only to configure the explicit provider route for a separately
authorized future check; it does not authorize or enable a model call in this
increment. Do not copy or modify the running `.prism-demo` ledger.

The service requires an explicit OIDC config, bind host, port, certificate, and
key. Missing or non-canonical values fail closed. The configured host, HTTPS
scheme, and same-origin JSON requests must match the canonical origin; forwarded
scheme headers do not substitute for direct TLS.

Before starting the service, use the local preflight to validate the exact
certificate/key pair, certificate SAN and expiry, owner-only key permissions,
canonical port, optional project manifests, and the selected runtime profile.
The report contains no secret paths or secret values. Keep the network probe off
for local-only preparation; `--probe-idp-tls` performs TLS handshakes to the
fixed configured IdP hosts and sends no HTTP, authorization, token, or JWKS
request:

```bash
uv run --no-editable prism identity-preflight \
  --bind-host <private-interface> \
  --port <canonical-origin-port> \
  --oidc-config /private/path/oidc-config.json \
  --tls-cert-file /private/path/prism-cert.pem \
  --tls-key-file /private/path/prism-key.pem \
  --project "$PWD/examples/document-handoff/.prism-project.json" \
  --runtime-profile development \
  --json
```

Add `--oidc-client-secret-file /private/path/oidc-client-secret` only when the
registered client requires one. Add `--probe-idp-tls` only when the operator
has intentionally authorized the fixed-host TLS check. The preflight does not
start the identity service, create a session, redeem an invitation, or call a
model.

The identify-only operator flow is separate from service startup. Its current
CLI is:

```bash
uv run --no-editable prism identity-bootstrap \
  --bind-host 127.0.0.1 \
  --port <canonical-origin-port> \
  --oidc-config /private/path/oidc-identify-config.json \
  --tls-cert-file /private/path/prism-cert.pem \
  --tls-key-file /private/path/prism-key.pem
```

It may take an owner-only client-secret file when required. The command binds
the browser to a one-shot token, uses authorization-code PKCE, state, nonce,
and ID-token signature/claim validation, and prints only the verified `issuer`
and `subject`. It does not create a Prism session, invitation, grant, or
owner record. Keep the printed URL and any secret out of chat, logs, and the
repository; use the [private setup checklist](m2-recipient-private-setup.md)
for the operator sequence.

## Local fake-IdP and temporary-key validation

The deterministic local identity tests use a fake OIDC transport and a
temporary generated RSA key. They exercise claim validation, PKCE state binding,
single-use identity-bound invitations, grant revision checks, direct API
denials, and the configured service entry point without contacting a real IdP:

```bash
uv run --no-editable python -m unittest discover -s tests -p 'test_identity.py' -v
```

The confirmed local validation run used `PYTHONPATH=src uv run python -m
unittest discover -s tests` and passed all **146 tests** in **8.902 seconds**;
the focused identity module passed **17 tests** in **3.653 seconds**. The seven
changed Python files passed Ruff check and format check. Prettier
passed for `frontend/src/main.jsx` and `frontend/src/style.css`, and the
frontend production build passed. The identity coverage includes fake signed
RSA OIDC, exact subject and cookie session binding, grant/replay/revoke and
readiness denial, plus a `FunctionModel` cited-question check and a JSONJobs
runtime-double check. The zero-budget `GuardedTransport` rejected before
network access; `AgentTests::test_transport_destination_storage_size_and_atomic_prepaid_allowance`
left the `NoNetwork` delegate count unchanged, and the budget-increase check
preserved prior usage. Real loopback HTTPS fake-IdP/Prism servers using a
temporary client-trusted CA passed PKCE, nonce, RSA signature, exact issuer and
subject, wrong-hostname and untrusted-CA rejection, preflight checks, and the
identify-only bootstrap deadline/concurrency/cleanup checks. The test CA did
not modify the host trust store. Independent security review and parent code
review passed. The repository-wide Ruff baseline still has 11 failures in
unchanged files; this increment does not claim a full-repository lint pass.

For a local direct-TLS startup smoke check, create a short-lived certificate
and key outside the repository, use a matching private `public_origin`, and
remove them after the check. For example:

```bash
tmp_dir="$(mktemp -d)"
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$tmp_dir/prism-key.pem" \
  -out "$tmp_dir/prism-cert.pem" \
  -days 1 -subj "/CN=localhost"
chmod 600 "$tmp_dir/prism-key.pem"
```

This temporary certificate is an optional setup procedure for local file and
direct-TLS startup handling. It is not a trusted public certificate and does
not establish Google OIDC or private browser acceptance. The local validation
also exercised real loopback HTTPS fake-IdP and Prism servers with a temporary
client-trusted CA; the host trust store was unchanged. The complete local
evidence is recorded in the [validation record](validation/m2-recipient-invitations.json).

## Owner and recipient acceptance flow

1. The owner signs in at `/auth/oidc/owner`. The ID token must validate its
   fixed issuer, audience, asymmetric signature, `sub`, `exp`, `iat`, nonce,
   and authorization-code PKCE binding. The configured owner `sub` must match.
2. The owner selects and approves an immutable reviewed version using the
   existing owner workflow. The version and its action remain server-side
   records.
3. The owner creates an invitation for the exact recipient subject through
   `POST /api/owner/versions/{version}/invitations` with same-origin JSON:

   ```json
   {
     "recipient_issuer": "https://identity.example.com/tenant",
     "recipient_subject": "the-exact-recipient-sub",
     "mode": "inspect",
     "expires_in": 600
   }
   ```

   Use `verify` only for an approved version whose fixed action and runtime
   profile are ready. The lifetime is between five minutes and 24 hours. The
   returned invitation URL carries a one-time token in its fragment; keep it
   private and do not put it in chat or logs.
4. The recipient opens the private invitation URL. The client starts
   `POST /api/auth/oidc/invitation`, follows the provider authorization code
   flow, and returns to `/auth/oidc/callback`. The first valid exact `sub`
   redeems the invitation atomically and creates the fresh review session and
   grant. A second redemption, a different subject, a changed version, a
   revoked grant, or an expired invitation is denied.
5. The recipient checks `/api/review/state` and reads only the invited version.
   A sourced question requires a separately authorized finite model allowance.
   The default/local identity-service configuration has an external allowance
   of zero. Separately, the private owner deployment has since completed one
   bounded model-backed evidence-only owner answer; this is recorded in the
   [live validation record](validation/m2-owner-live-validation.json) and does
   not validate a recipient question. Deterministic
   `FunctionModel` or runtime-double pipeline checks remain local evidence and
   are not live model or Kata acceptance. A permitted fixed run is likewise
   pending the relevant runtime gate. Direct access to another session,
   evidence ID, run, or owner endpoint must be denied. In identity mode, the
   legacy `/api/login` capability endpoint is unavailable.
6. The owner revokes the returned grant through
   `POST /api/owner/grants/{grant}/revoke`. Subsequent recipient reads,
   requests, runs, and result delivery must fail authorization. Revocation
   cannot recall information already delivered to the recipient.

## Session cookies and re-login

The pending OIDC login uses `__Host-prism_oidc_login`; the authenticated session
uses `__Host-prism_identity`. Both are Secure, HttpOnly, SameSite=Lax,
Path=/ cookies without a Domain attribute. Pending login state expires after
five minutes. An owner identity session lasts one hour. A recipient identity
cookie and database session last until the invitation's fixed grant expiry, so
the cookie does not impose a shorter one-hour limit on a still-valid recipient
grant. Losing or expiring a cookie, or revoking the grant, does not replay a
consumed invitation; the owner must issue a new invitation. There is no
multi-grant recovery path in this increment.

## What remains unverified

At the local validation checkpoint, the fake-IdP and temporary-key checks were
local evidence only. The later private Google OIDC/Tailscale HTTPS browser flow
and one bounded second-identity recipient flow have since completed. The live
flow covered exact-version invitation, a fresh scoped session, denied private
evidence and owner API access, model-backed evidence lookup, and revocation.
Kata execution through this service, full order-4 acceptance, and pilot gates
remain outstanding. Keep private credentials out of repository documentation
and chat; see the [sanitized live recipient record](validation/m2-recipient-live-identity.json).

The local follow-up includes a real loopback-TLS fake-IdP path that trusts a
temporary test CA inside the test process. That trust is test-local and does
not modify the host trust store. This local path is recorded as passed; it is
separate from the subsequent owner and recipient Google browser flows. Kata
execution through the service remains unverified.

The local validation below records no real IdP, HTTPS browser flow, model
route, Kata execution, cloud deployment, or private-pilot release at its
checkpoint. Later owner and bounded recipient evidence is recorded separately
in the [live recipient record](validation/m2-recipient-live-identity.json),
including one permitted Kata run for the Document release handoff. This does
not establish full order-4 acceptance or pilot readiness.
The M2.3 reference-Linux 46/46 evidence remains a separate preceding record
and does not itself transfer acceptance to order 4. See the [order 4 local
validation record](validation/m2-recipient-invitations.json) for local
machine-readable test evidence.

The identity service's provider flags do not migrate or share the existing
`.prism-demo` model ledger. Its default/local external model allowance starts
at zero; provider flags only configure a possible route. For the later private
owner and recipient validations, a separate bounded $1.00 aggregate reservation
was authorized: $0.20 for the owner answer, $0.20 for the failed broad recipient
question, $0.10 for the successful Paired focused question, and $0.20 for the
Document release question, leaving $0.30. These checks did not change the
`.prism-demo` data, key, or 300-cent ledger. The
model-call-free and no-cloud statements in the local validation history
describe that earlier checkpoint. Changing the identity ceiling or restarting
its service does not reset or recharge reservations. The single successful
recipient question does not establish general model reliability.

The identity implementation follows the fixed Authlib dependency and its
[official HTTP client guidance](https://docs.authlib.org/en/stable/oauth2/client/http/index.html);
dependency-change details remain outside this guide.
