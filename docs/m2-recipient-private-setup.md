# M2 Order 4: Private OIDC and Tailnet Setup Checklist

This checklist prepares a private, authenticated HTTPS path for the named
remote-collaborator increment. It contains no account email, tenant DNS name,
IP address, client secret, certificate, subject claim, or invitation token.
Replace angle-bracket placeholders only in local owner files. Do not commit
those values or paste them into chat.

The checklist is preparation evidence, not pilot acceptance. It does not
authorize public deployment, a model call, a nonzero model allowance, or an
invitation to an unapproved recipient. Current deployment status is recorded
below; the detailed preparation table retains its earlier point-in-time scope.

## Current status and preparation history

**Latest owner-only OIDC checkpoint (2026-09-23):** The private service is
running from the new immutable 29-file release. Verified evidence includes
trusted HTTPS mode checks, the owner workspace route, a real Google owner
login in Chrome, cross-site denial checks, and the guarded Shields Up
lifecycle. One bounded synthetic Paired evidence-only owner answer used the
fixed model snapshot `gpt-5.4-mini-2026-03-17` with four model requests, three
tool calls, 8,898 input tokens, 488 output tokens, and 20 cents reserved from
the $1.00 aggregate reservation. The UI showed $0.80 remaining. A pending
handoff draft is under owner review with exactly three selected evidence files,
one answer excerpt, read-only capability, and no private notes; it has not been
approved. Recipient invitation/session, second-identity denial, and Kata
through this service remain unverified. The VM remains running with fixed
on-demand IAP SSH management and no public TCP/8443 ingress. `pilot_ready=false`.
See the [guest service guide](m2-recipient-guest-service.md), [sanitized owner
live-validation record](validation/m2-owner-live-validation.json), and
[sanitized live deployment checkpoint](validation/m2-recipient-private-deployment.txt).

The table below is a historical preparation snapshot from the earlier
app-install checkpoint. Its point-in-time statements are retained as history;
use the later live checkpoint above for current service, secret, login, and
network status.

| Area | Required permission or decision | State at the earlier app-install checkpoint |
| --- | --- | --- |
| Google OIDC application | Owner may use the selected Google Cloud project and may create a web client | Google Auth Platform is configured for **External Testing**. Exactly one dedicated Web OAuth client has been created. The Console accepted the exact private HTTPS origin on port `8443` and both callbacks, `/auth/oidc/identify/callback` and `/auth/oidc/callback`, without a domain rejection. No client ID, secret, account, or private hostname is recorded here. The downloaded copy was removed after the one-time client JSON was saved locally at `~/.config/prism/identity-pilot/oidc/google-web-client.json`; its parent directory is mode `0700`, the file is mode `0600`, and a programmatic check matched the expected client shape and confirmed the client ID and secret fields are present. This confirms registration/configuration only, not use of the client in an app or browser flow. |
| Canonical origin | Owner selects the exact private HTTPS origin and port | The existing Apple tailnet MagicDNS name is selected; its value is intentionally omitted. Certificate issuance is recorded below. Reachability and service acceptance remain unverified. |
| Tailnet | Owner chooses a new account-specific tailnet or explicitly reviews the existing policy before changing it | A separate Free tailnet is confirmed under the selected account and now has two nodes. The saved policy allows only `admin` to assign `tag:prism-client` and `tag:prism-host`, and grants `tag:prism-client` to `tag:prism-host` on TCP 8443. Default allow-all was removed; SSH rules, auto-approver routes/services, and exit nodes are absent. The admin UI confirms `prism-pilot` with `tag:prism-host`. A later passive diagnostic of the stopped host reported the guest backend as `NeedsLogin`, with no node key, no auth URL, a self record present (`self_present=true`), `want_running=false`, `logged_out=true`, and approved preferences absent; it made no state change. The self record does not by itself establish an authenticated or usable identity. That diagnostic showed passive recovery could not proceed from the then-persisted state; why the guest entered that state remains unknown. The later passive reboot acceptance confirmed `Running`/`Online`, expected preferences, and the saved normalized-DNS hash. Current certificate configuration is confirmed, but host inbound traffic remains blocked by Shields Up and no private reachability has been established. The existing tailnet's two expired devices and policy remain unchanged. See the [passive diagnostic record](validation/m2-recipient-passive-diagnostic.txt) and the [private certificate record](validation/m2-recipient-private-cert.txt). |
| Mac/device enrollment | Owner explicitly authorizes connecting the intended host and collaborator devices | Latest Apple Connect reauthorization passed guest post-login validation (`Running`/`Online`, exact existing hostname/tag/unsafe preferences, `ShieldsUp=true`) and stopped on `old_admin_identity_reused`: the identity hash matched the saved old admin hash. The Apple tailnet still has exactly `prism-client` and one `prism-pilot`, with no duplicate; the pilot host tag is unchanged and last-seen refreshed. This is consistent with reauthorizing the same stable identity and does not establish that it is unsafe. Controller and independent GCP Console checks confirmed `STOPPED`, startup configuration absent, and the original four metadata controls. The subsequent passive reboot acceptance matched the exact saved device-ID and normalized-DNS hashes without another login or a duplicate device. See the [sanitized Apple reauthorization record](validation/m2-recipient-apple-reauth.txt) and [passive reboot acceptance](validation/m2-recipient-passive-acceptance.txt). Earlier diagnostics and the 33/33 Kata baseline remain separate evidence. |
| TLS certificate | Owner authorizes private certificate issuance and local owner-only key storage | User explicitly approved HTTPS enablement and Certificate Transparency disclosure. Tailscale HTTPS is enabled and refresh-confirmed. One bounded certificate issuance on the exact replacement VM succeeded: the exact SAN and certificate/key match passed, at least seven days of validity remained, and the guest-side key file was root-owned mode `0600`. Certificate and key remain on the retained guest disk; no key export occurred. The VM was cleaned up and independently confirmed stopped. This does not establish inbound reachability, Prism TLS, OIDC, or pilot readiness; Shields Up remains enabled. See the [sanitized certificate record](validation/m2-recipient-private-cert.txt). |
| Prism configuration | Owner supplies exact issuer, client, claims, origin, certificate and key out of band | App files are installed and independently hash-verified. No OIDC secret or live OIDC configuration is installed; the repository template remains secret-free. |
| Model and cloud use | Separate explicit authorization for each action | The target VM is RUNNING after the fixed install. A first scheduling update omitted the timeout fields but was ineffective; an explicit-null `setScheduling` followed by a fresh independent GET confirmed both absent. Temporary IAP access, OS Login key, dedicated firewall rule, network tag, and API enablement have all been revoked or removed. Original four metadata controls remain, no service account is attached, Shielded VM is enabled, and effective ingress is zero on both VMs. Model calls remain zero; no model, browser login, OIDC, or app-service run occurred. |

The latest bounded predicate diagnostic completed with `state=complete`,
`cleanup_verified=true`, and `mutation_outcome_uncertain=false`. The guest
reported `NeedsLogin`, an empty auth URL, a present self record, a missing node
key, logged-out and not-running state, nonmatching exact hostname/tag
predicates, and Shields Up disabled; status and preferences commands succeeded.
Independent GCP Console inspection confirmed `STOPPED`, blank startup
configuration, and the original four metadata keys. The strict recovery
preflight therefore failed on the missing key and nonmatching inactive
hostname/tags; this does not explain why the saved state became logged out. A
new recovery fix is pending. No Apple login occurred. See the [sanitized
predicate diagnostic](validation/m2-recipient-predicate-diagnostic.txt).

The subsequent bounded private HTTPS certificate run is recorded in the
[sanitized certificate record](validation/m2-recipient-private-cert.txt).
Tailscale HTTPS was enabled and confirmed after refresh, then one certificate
was issued on the exact replacement VM. The guest validated the expected SAN,
certificate/key match, at least seven days remaining validity, and a root-owned
mode-`0600` key file. The certificate and key stayed on the retained guest disk;
no key was exported. Controller cleanup reported `ISSUED_PROVISIONAL`,
`reason=null`, `cleanup_verified=true`, `cleanup_failed=false`, and
`mutation_outcome_uncertain=false`; cleanup passed with the VM terminated,
startup configuration absent, and the original four metadata controls. An
independent GCP Console check confirmed the VM stopped and startup configuration
blank. Preflight had found no project startup metadata and no effective
inbound-firewall rules. Shields Up remains enabled, so inbound tailnet traffic
is blocked. This evidence does not establish reachability, Prism TLS, OIDC,
application traffic, or pilot readiness. The public Certificate Transparency
disclosure was explicitly approved; certificate renewal will be required.

On 2026-09-22, Google Auth Platform was confirmed as **External Testing** and
exactly one dedicated Web OAuth client was created. The Console accepted the
exact private HTTPS origin on port `8443` and both registered callback paths
(`/auth/oidc/identify/callback` and `/auth/oidc/callback`) without domain
rejection. A one-time client JSON was saved in the local owner configuration
directory with directory mode `0700` and file mode `0600`; programmatic checks
confirmed the expected client structure and the presence of both client ID and
secret fields. The downloaded copy was removed. No identifier, secret,
hostname, account, or credential-bearing JSON is included in repository
documentation. This is registration and local-storage evidence only. The
secret still needs a secure transfer route to the VM, which has no SSH access
or service account. The identify-only bootstrap URL also needs a defined
private return channel to the owner. No VM, app, or browser OIDC test, model
call, or private reachability test was run; pilot readiness remains false.

The subsequent Apple Connect reauthorization passed the guest's post-login
recovery predicates: backend `Running`, server `Online`, exact existing
hostname and tag, approved unsafe preferences, and `ShieldsUp=true`. The run
then failed its script-level identity check with `old_admin_identity_reused`,
because the authenticated identity hash equaled the saved old admin hash. The
Apple tailnet retained exactly two nodes (`prism-client` and one
`prism-pilot`), without a duplicate; the existing pilot tag remained and its
last-seen time refreshed. This supports same-stable-identity reauthorization;
the script's new-identity assumption rejected it, which is not evidence that
the identity is unsafe. Controller and independent GCP Console checks
confirmed cleanup: VM `STOPPED`, startup configuration absent, and the original
four metadata controls. No certificate, HTTPS, DNS, OIDC, application, or
model validation occurred in that reauthorization attempt. Its later passive
reboot acceptance is recorded separately below; the earlier failed recovery
attempts remain historical evidence. See the [sanitized Apple reauthorization record](validation/m2-recipient-apple-reauth.txt).

The later [passive reboot acceptance](validation/m2-recipient-passive-acceptance.txt)
matched the exact saved existing admin device-ID and normalized-DNS hashes on
the replacement VM. The guest was accepted as `Running`/`Online`, with the
expected tag/preferences and `ShieldsUp=true`; routes, SSH, exit node, and
connectors remained absent or disabled. A read-only project-wide metadata
preflight found zero `startup-script` and zero `startup-script-url` keys. No
Tailscale login, `up`, reset, or logout command occurred. Controller
cleanup passed (`TERMINATED`, startup absent, four original metadata controls),
and independent GCP Console inspection showed `STOPPED`, blank startup
configuration, and those same controls. Only one existing `prism-pilot`
remains. No HTTPS/certificate/OIDC/app traffic or model call occurred, and
`pilot_ready=false`. Earlier failed recovery attempts remain historical; the
33/33 Kata baseline is separate and unchanged.

## 1. Choose the private network boundary

1. The selected boundary is a new account-specific Free tailnet for Prism. It
   avoids inheriting the existing tailnet's devices and policy; the existing
   tailnet remains reserved and unchanged.
2. Review the saved policy for the new tailnet. The first save attempt had a
   syntax rejection; that error was corrected and the restricted policy was
   saved successfully. Its control-plane checks accept client-to-host TCP
   8443, deny client-to-host TCP 22 and 443, deny UDP 8443, and deny
   host-to-client TCP 8443. These are policy-evaluation checks only: the
   connected client does not prove host enrollment, Prism TLS, OAuth, or real
   application traffic.
3. Select one stable, non-sensitive machine name and the private HTTPS origin
   it produces. Do not use an email address, account name, or other sensitive
   label in a machine name. Do not publish the origin in this repository.
4. Decide exactly which owner and recipient devices may reach the Prism host.
   Keep the VM stopped and do not connect the collaborator device until the
   selected tailnet and access policy are reviewed.
5. Verify that the intended clients can resolve and reach the origin through
   the private network. This is a reachability check only; it is not a browser
   OIDC acceptance run.

The current controller evidence is account-side only. The earlier run ended
`connect_incomplete` before authorization (summary recorded at
`2026-09-22T00:26:26.109749+00:00`; SHA-256
`dd48a0e99ba74a02a2ed0fd9711ad73076e8a3822820ac288a112de5a6ddefde`), with
`cleanup_failed=false` and `verified=true`; cleanup confirmed `TERMINATED`, the
original four metadata keys, and no startup script. Server-online and
preferences verification remain pending. Device expiry is disabled, so the
retained disk may contain machine identity; revocation must be handled through
Machines. No private email, FQDN, IP address, or credential is recorded.

The subsequent one-shot passive diagnostic completed with `reason=null`,
`cleanup_verified=true`, and `mutation_outcome_uncertain=false`. It reported
backend `NeedsLogin`, `have_node_key=false`, `auth_url_present=false`,
`self_present=true`, `want_running=false`, `logged_out=true`,
`approved_prefs=false`, `state_changed=false`, and `daemon_active=true`. An
independent CloudShell describe confirmed the VM was `TERMINATED`, retained
exactly the original four metadata keys, and had no startup script. These
observations establish that passive recovery cannot proceed from the currently
persisted guest state; they do not establish why it became logged out. The
earlier timeout-before-Connect chronology is verified, but causation remains
unknown. The report is transcribed from the CloudShell UI/operator report, not
from downloaded JSON. No reinstall, login/reset/logout cycle, new node, ACL
change, application or certificate action, or model call was performed. See the
[sanitized diagnostic record](validation/m2-recipient-passive-diagnostic.txt).

The later bounded predicate diagnostic is recorded separately. It found a
missing node key, nonmatching exact inactive hostname/tag predicates, and
Shields Up disabled, while status and preferences commands succeeded. The
strict preflight did not pass; the new recovery fix remains pending, with no
Apple login. See the [predicate diagnostic record](validation/m2-recipient-predicate-diagnostic.txt).

On 2026-09-22, one approved live recovery run failed with guest-side
`state_mismatch` before an authorization URL was produced. No Apple Connect,
authentication, or new node occurred. The controller reported
`state=fail`, `cleanup_verified=true`, `cleanup_failed=false`,
`mutation_outcome_uncertain=false`, `app_started=false`, and
`cert_requested=false`. Controller cleanup passed with the VM
`TERMINATED`, no startup script, and the original four metadata keys;
independent GCP Console inspection confirmed `STOPPED`, startup configuration
blank, and the original controls intact. A temporary CloudShell token-refresh
error occurred during cleanup, but final controller and independent Console
checks confirmed clean state. The cause of `state_mismatch` is not yet known;
targeted diagnostics are pending. See the [sanitized live recovery record](validation/m2-recipient-live-recovery.txt).

The authorized capacity-recovery path preserves the original VM and disk,
because the source boot disk is configured to auto-delete with its VM. The new
resource must use an explicit no-auto-delete disk setting. The target zone was
first attempted in `us-central1-c` and then `us-central1-f`; both lacked
capacity and left no VM or disk residue. The same-spec replacement
`prism-m0-kvm-b` was then configured in `us-central1-b` from the existing 30
GiB `READY` disk snapshot, with no additional snapshot created, and intentionally
stopped; final cleanup later independently confirmed `TERMINATED`. Configuration checks confirmed nested virtualization, four-hour
STOP, auto-restart disabled, secure boot/vTPM/integrity enabled, no service
account, and boot-disk auto-delete disabled. The exact four metadata keys are
the existing SSH/endpoint/OS-login/serial-port controls; startup metadata was
removed. A basic replacement-host guard passed inventory/prerequisite checks.
During the earlier malformed RESET attempt, the command was rejected and never
executed; that earlier attempt did not run the full 33-check workload. The
later controller run executed the successor workload and the fresh replacement-
host Kata baseline passed 33/33 checks across 10 cleaned, distinct boots, with
helpers absent and the approved program hash unchanged; it does not establish
Prism application integration. HTTPS, OIDC, and host-tailnet verification still
require evidence before Prism setup. The original VM/disk remain preserved. This is a disk snapshot,
not an in-memory snapshot; retained snapshot and disk storage can incur cost
while stopped. Results from the old host do not transfer to this host. The
sanitized prerequisite evidence is in the [replacement-host guard record](validation/m2-recipient-host-migration-guard.txt),
the fresh [Kata revalidation record](validation/m2-recipient-host-kata-revalidation.json),
and the [cleanup note](validation/m2-recipient-host-cleanup.txt).

Tailscale's [HTTPS documentation](https://tailscale.com/docs/how-to/set-up-https-certificates)
requires MagicDNS and HTTPS certificates to be enabled for certificate
provisioning. It also explains that certificate names are published in the
Certificate Transparency ledger and that file-based certificates require
operator renewal. Review that public-name disclosure before enabling HTTPS.
The [Tailscale security guidance](https://tailscale.com/docs/reference/best-practices/security)
also recommends HTTPS for services available inside a tailnet.

## 2. Register the Google OIDC client

Use the selected HTTPS origin before creating the client. Google documents the
server flow and ID-token validation in its [OpenID Connect guide](https://developers.google.com/identity/openid-connect/openid-connect)
and lists endpoint and claim requirements in the [OIDC API reference](https://developers.google.com/identity/openid-connect/reference).

1. In the selected Google Cloud project, keep the consent screen in the
   approved external-testing state and restrict test users to the intended
   owner and recipient accounts. Do not place those email addresses in this
   repository.
2. Create the OAuth web client only after the canonical origin is final. Add
   the exact HTTPS redirect URI for the service:
   `https://<private-origin>/auth/oidc/callback`.
3. Register the identify-only bootstrap callback for the separate bounded
   identify configuration:
   `https://<private-origin>/auth/oidc/identify/callback`.
   Keep the two redirect purposes distinct if the provider configuration uses
   separate clients.
4. Store any client secret in an explicit owner-only regular file with mode
   `0600`. Never place it in the JSON configuration, a URL, browser history,
   logs, chat, or Git.
5. Obtain `iss` and `sub` from a verified provider result. Do not substitute
   email, display name, or a guessed account identifier for `sub`. The Prism
   configuration must keep the issuer, audience/client ID, exact subject and
   fixed HTTPS endpoints consistent.

Google's redirect URI rules require HTTPS for web clients and exact matching;
the Prism service additionally requires the redirect URI to equal the
configured canonical origin plus `/auth/oidc/callback`. The identify-only
configuration uses the `/auth/oidc/identify/callback` suffix shown above.

## 3. Issue and inspect the TLS material

After the origin is final, provision the certificate using the selected private
network procedure. Check its SAN covers the exact hostname, its validity window
includes the test time, and its private key is a single-link regular file owned
by the operator with mode `0600`. Keep the certificate and key outside the
repository. Do not alter the host trust store for this preparation.

Run the secret-free local preflight before starting Prism:

```bash
uv run --no-editable prism identity-preflight \
  --bind-host <private-interface> \
  --port <canonical-origin-port> \
  --oidc-config /private/path/oidc-config.json \
  --oidc-client-secret-file /private/path/oidc-client-secret \
  --tls-cert-file /private/path/prism-cert.pem \
  --tls-key-file /private/path/prism-key.pem \
  --runtime-profile development \
  --json
```

Add each explicitly selected project manifest with a repeatable `--project`
option. Add `--probe-idp-tls` only for a fixed-host TLS-handshake check. That
option does not send HTTP, authorization, token, or JWKS requests. A successful
preflight does not prove browser login or private reachability.

## 4. Verify the owner identity without creating a Prism session

Use a bounded identify-only configuration whose redirect URI is the
`/auth/oidc/identify/callback` value above:

```bash
uv run --no-editable prism identity-bootstrap \
  --bind-host 127.0.0.1 \
  --port <canonical-origin-port> \
  --oidc-config /private/path/oidc-identify-config.json \
  --oidc-client-secret-file /private/path/oidc-client-secret \
  --tls-cert-file /private/path/prism-cert.pem \
  --tls-key-file /private/path/prism-key.pem
```

Open the one-shot URL only in the intended browser. The flow binds the browser
to a short-lived token and uses authorization-code PKCE, state, nonce, and
signature/claim validation. It prints only the verified issuer and subject,
then exits; it does not create a Prism session, invitation, grant, or owner
record. Treat the URL and terminal output as private transient credentials.

## 5. Historical pre-live acceptance checklist (2026-09-22)

The following table and next-action note record the state before the later
owner-only OIDC deployment. They are retained as dated preparation history;
they do not describe the current live service state above.

Record the following in the local, owner-controlled validation notes, using
placeholders or hashes where possible:

| Evidence | Required result | State at the pre-live checkpoint |
| --- | --- | --- |
| Google client and redirect registration | Exact service and identify callbacks accepted by the selected client configuration | Complete for one dedicated Web OAuth client; exact private HTTPS `:8443` origin and both callbacks accepted. No app/browser use tested. |
| TLS identity | Key pair matches; SAN and expiry pass; key is owner-only | Certificate issuance and guest key checks passed as recorded above; private reachability and Prism TLS remain unverified. |
| IdP TLS probe | Fixed configured hosts complete trusted TLS handshakes, if explicitly run | Not run against a real provider |
| Owner identify-only flow | Browser returns a verified `iss`/`sub`; no Prism session/grant appears | Local loopback TLS fake-IdP/bootstrap flow passed; real Google/browser flow pending |
| Tailnet reachability | Intended owner and recipient devices reach the exact HTTPS origin | Not tested; Shields Up blocks inbound traffic. |
| Recipient invitation | Exact recipient `sub` receives one invitation and a fresh scoped session | Local fake-IdP evidence only |
| Denial paths | Wrong subject, replay, revoked grant and direct unauthorized API calls fail | Local evidence exists; real browser evidence pending |
| Model/Kata path | Separately authorized finite allowance and supported runtime pass | Not authorized or run in this increment |

**Historical next action at that checkpoint:** Deploy the OIDC configuration
and HTTPS service after separate review, then run real browser identity checks.
Model and Kata integration checks also required separate review. At that time,
`service_started=false`, no OIDC secret or live login was present, and
`model_calls=0`; that state was superseded by the owner-only deployment
checkpoint above. `pilot_ready=false` remains current. Do not mark the order-4
guide complete or pilot-ready from the app-install checkpoint. Record final
counts and completion statuses in the [machine-readable validation
record](validation/m2-recipient-invitations.json) only after those checks finish.
