# Private guest identity-service preparation

**Current demo state (2026-09-23):** The deployed private service is running
with a test-recipient grant recorded with expiry 2026-09-24 03:35:54 PDT. The
earlier recorded live `json-check` run completed on `io.containerd.kata.v2` in 3.979
seconds with exit 0 and confirmed cleanup; `pilot_ready=false`. The aggregate
model reservation ceiling is temporarily $10.00 (1,000 cents). The earlier
$0.80 reservation remained recorded, leaving $9.20 at that checkpoint before later demo calls. Check the live interface for the current remainder. It stays in effect until
the user explicitly ends the demo; restoring the former ceiling requires an
explicit user request. This application reservation ceiling is not a hard
provider billing cap. The VM remains RUNNING with no automatic stop, the
existing grant remains active, and IAP SSH stays available on demand. Follow
the [end-to-end live demo SOP](live-demo-sop.md) for the owner-to-recipient
walkthrough. The sanitized record retains earlier validation checkpoints.

**Historical owner-path checkpoint (2026-09-23):** The owner-only private
OIDC service checkpoint passed on the new immutable 29-file release. The
release source-tar SHA-256 was verified; the original oneboot marker, manifest, and ledger remained
byte-identical. New-unit preflight, `systemd-analyze verify`, and manual start
passed. Shields Up is false only while the service is active; stop closed
inbound access and the subsequent guarded start reopened it. Mac trusted HTTPS
`/api/auth/mode` returned 200. Anonymous owner/review checks returned 401.
The owner workspace `/owner` route returned 200 over trusted HTTPS.
Chrome's real Google owner login now redirects directly to the owner workspace
without a reload after the narrow cross-site document-navigation fix. A
cross-site owner document navigation returned 200, while its API/auth requests
returned 403; an evil `Origin` returned 403. The earlier first systemd start
and initial dual-stack updater attempt both failed closed before retry or
publication; the updater fix was then installed as a new immutable release.
The later bounded second-identity invitation, scoped review, denial-path, model,
and revocation checks are recorded below and in the [live validation record](validation/m2-recipient-live-identity.json).

One bounded synthetic Paired evidence-only owner question was subsequently
completed through this service using the fixed snapshot
`gpt-5.4-mini-2026-03-17`. It used four model requests and three tool calls,
with 8,898 input tokens and 488 output tokens. Twenty cents was reserved from
the $1.00 aggregate reservation; the UI showed $0.80 remaining. The answer
cited `observations.csv` lines 1–9, `baseline.json` lines 1–13, and
`limitations.md` lines 1–6. The UI duplicated the question text, but no second
execution was requested; the one evidence-only answer did read evidence
through model tools. Private question text and owner identity are omitted.
This was one owner answer, not an approved share or completed handoff. A later
bounded live recipient validation exercised the approved synthetic Paired
snapshot with a distinct Google identity. One-time identity discovery created
no Prism session or grant, and replay was denied. The owner then invited that
verified identity for one hour to the exact approved snapshot. A wrong identity
could not redeem it and the invitation remained available. The intended
recipient received a fresh session with three approved files and the selected
context; reading `baseline.json` succeeded, while `private-notes.txt` evidence
and the owner API were denied. A focused question succeeded with a citation to
`limitations.md` lines 1–6. A broader question failed closed without partial
output. After the owner revoked the grant, the former recipient could no longer
read even previously available evidence. These checks are a bounded synthetic
flow, not a broad security proof. The local suite passed 174/174 tests with
targeted Ruff, Prettier and build checks. See the [sanitized live recipient
record](validation/m2-recipient-live-identity.json).

A separate bounded flow used a synthetic Document release handoff approved as
version `b61a601cb23845a492de9e0f71923fc0`, with `README.md`,
`docs/release.md`, and `config/release.json`. A second Google identity whose
exact issuer and subject were verified received a one-hour invitation and a
fresh scoped session. A live question used four internal model dispatches and
reserved $0.20 from the existing $1.00 aggregate allowance. Its permitted run
`de722cb9bd594af09f15ea0c4558d6bf` completed the fixed `json-check` in
`io.containerd.kata.v2`: guest kernel 6.18.35, exit 0, 3.992 seconds,
`cleaned_up=true`, and valid JSON object output. The fixture was installed with
the pinned source SHA recorded in the validation record; all six source hashes
matched, and the Kata namespace had no remaining containers or tasks.

The recipient's direct request for `unlisted-canary.txt`, owner API access,
and evidence from an unrelated session were denied. Revoking this Document
release grant did not affect the earlier Paired grant. After revocation,
`config/release.json` and the exact run endpoint returned unavailable, the
refreshed review workspace disappeared, and Sources and Run were inaccessible;
a read-only database check confirmed the grant was revoked. No additional
model call or run occurred after revocation. Together, the two live flows have
reserved $0.70 of the $1.00 aggregate allowance, leaving $0.30. This remains
bounded evidence rather than a broad security proof or full order-4 acceptance;
`pilot_ready=false`. See the [sanitized live recipient record](validation/m2-recipient-live-identity.json).

The historical live flows described above were performed under a $1.00
aggregate ceiling and left $0.30 available at that checkpoint. The current
ceiling and reservation state are reported in the current demo-state paragraph
at the top of this guide; raising the ceiling did not reset the ledger. These
bounded checks do not establish broad runtime assurance, full order-4
acceptance, or pilot readiness (`pilot_ready=false`).

The [guest helper](../scripts/gcp/identity-service-guest.py) provides
`render` for owner-only OIDC configuration with fixed Google endpoints,
`preflight` for Prism's local checks, and `serve` for starting the direct-TLS
service. Its model allowance defaults to zero; the deployed unit currently
sets the temporary ceiling to 1,000 cents. Earlier live-validation results
were produced under a 100-cent setting. `selfcheck` makes one local HTTPS
request to `/api/auth/mode`, validates the certificate and hostname using the
system trust store, and requires identity mode. A `project-inventory` check
verifies the installed fixture digest. `preflight`, `selfcheck`, and
`project-inventory` perform no login, OAuth exchange, model call, or Kata job.
`serve` handles service requests under its configured policy. The helper never
takes the client secret, subject, or login URL as a command-line value or
environment variable. It strips the inherited environment before invoking
Prism. `serve` suppresses Prism's startup stdout because Prism currently
prints the canonical origin.

## Provision the pinned synthetic project fixture

The current Document release handoff uses a fixed synthetic project fixture.
Package it to a private absolute path in the working environment:

```bash
python3 scripts/gcp/identity-project-package.py package \
  <private-absolute-path>/project.tar
```

Transfer the archive through the existing pinned private IAP path. On the
guest, as root, run the reviewed helper with the recorded transfer digest:

```bash
python3 /usr/local/libexec/prism-identity-project-package.py install \
  <private-transfer-path>/project.tar \
  --transfer-sha256 <recorded-transfer-sha256>
```

Installation creates the root-only immutable directory
`/var/lib/prism-identity/project`. The expected project inventory digest is
`737348aaef852355c5a16381fafc96f0c6df0931edafddc93a77906d064db525`.

Add `--enable-project` when rendering the documented lifecycle unit. It pins
that exact inventory digest; the unit's preflight, prepare, serve, and open
steps check the pin, and the service passes the project to Prism with
`--project`. This path provisions only the fixed synthetic fixture. It does
not provision arbitrary user-supplied projects. Do not put secrets or private
project contents in this guide.

## Phase 1: identify the exact owner subject

After the dedicated Web client ID and secret have been transferred through the
separately reviewed private channel, place the ID alone in an owner-only
single-link file. Use the exact selected tailnet DNS name and a new output path:

```bash
python3 /usr/local/libexec/prism-identity-service-guest.py render \
  --phase identify --hostname <private-tailnet-dns-name> \
  --client-id-file /var/lib/prism-identity/client-id \
  --output /var/lib/prism-identity/oidc-identify-config.json
```

The output has the identify callback and no `owner_subject`. Use only the
separately reviewed, bounded identify-only guest flow for browser sign-in. Its
one-time URL and verified result must travel through its dedicated private
return channel, and its Shields Up restore must pass. Do not run the bare
`prism identity-bootstrap` command in a terminal or log that exposes the URL.
The issuer and subject must come from its verified result; an email or display
name cannot substitute for `sub`.

## Phase 2: configure and start the service

Place the verified subject alone in an owner-only single-link file. Render a
separate service configuration at a new path. The helper rejects overwrite,
symlinks, nonprivate input files, unexpected DNS names, and missing subjects.

```bash
python3 /usr/local/libexec/prism-identity-service-guest.py render \
  --phase service --hostname <private-tailnet-dns-name> \
  --client-id-file /var/lib/prism-identity/client-id \
  --owner-subject-file /var/lib/prism-identity/owner-subject \
  --output /var/lib/prism-identity/oidc-service-config.json
```

Run `preflight` first with the exact installed release and one current tailnet
IPv4 address. It validates the config, client-secret file, TLS key/certificate,
port, and reference-Linux profile locally. It does not contact Google unless
the operator separately invokes Prism's optional network probe. Keep the
service data directory distinct from the historical `.prism-demo` state.

```bash
python3 /usr/local/libexec/prism-identity-service-guest.py preflight \
  --hostname <private-tailnet-dns-name> --bind-host <tailnet-ipv4> \
  --release /var/lib/prism/identity-pilot/app-releases/<verified-release> \
  --oidc-config /var/lib/prism-identity/oidc-service-config.json \
  --client-secret-file /var/lib/prism-identity/oidc-client-secret \
  --tls-cert-file /var/lib/prism-identity/tls/cert.pem \
  --tls-key-file /var/lib/prism-identity/tls/key.pem
```

Use the same arguments with `serve` in place of `preflight`, plus
`--data-dir /var/lib/prism-identity/service-state`. It stays in the foreground
for an explicitly reviewed supervisor. It does not enable an external model
route, import a project, or change any historical database. Preserve the
separately reviewed tailnet guard; do not lower Shields Up until local TLS and
service checks pass. From the guest, use:

```bash
python3 /usr/local/libexec/prism-identity-service-guest.py selfcheck \
  --hostname <private-tailnet-dns-name> --bind-host <tailnet-ipv4>
```

The local selfcheck is narrower than private reachability or a real browser
OIDC test. Those checks need a separately reviewed network exposure window,
the intended client device, and denial-path evidence. This preparation alone
does not establish pilot readiness.

## Manual service lifecycle

The [lifecycle helper](../scripts/gcp/identity-service-lifecycle.py) renders
one root-only systemd unit for the current installed release, fixed service
configuration, and one explicit tailnet IPv4 address. It reads the guest's
fixed `/var/lib/prism/identity-pilot/oneboot/installed.json` and matching
manifest/ledger, validates the bundle hash and run ID, then pins the derived
release path and identifiers in the unit. Before startup and service execution,
it rechecks the records, the exact 29-file manifest set, and every installed
source hash while rejecting symlink parents and nonregular files. The unit has
no `[Install]` section and `Restart=no`; it
is intended for a manual `systemctl start`, not boot enablement. Copy both
reviewed helpers to the documented `/usr/local/libexec/` paths before
rendering. Rendering uses the fixed service config to obtain the private DNS
name and does not display it:

```bash
python3 /usr/local/libexec/prism-identity-service-lifecycle.py render \
  --bind-host <tailnet-ipv4> \
  --output /etc/systemd/system/prism-identity-service.service
```

Review the root-only rendered unit locally, run `systemd-analyze verify`, then
`systemctl daemon-reload` and manually start it. Installation and starting are
separate operator actions; this repository script does neither. The unit
requires the already installed `prism-identify-boot-restore.service` and binds
its lifetime to `tailscaled.service`. It does not replace or disable either
existing guard.

The start sequence verifies the tailscaled `ExecStartPost` restore hook, boot
restore service, exact DNS/IP/tag, and closed tailnet state. It refuses to open
while any previous `prism-identify-restore-<run-id>.timer` is active; that
timer must be stopped and checked explicitly before starting. It then invokes
Prism's local preflight and foreground service with `reference-linux`, the
fixed tailnet IP, direct TLS, and a zero-cent model allowance. After repeated
local HTTPS selfchecks pass, it lowers Shields Up, rechecks tailnet state and
HTTPS, and otherwise immediately restores Shields Up. On stop, startup failure,
or tailscaled shutdown/restart, `ExecStopPost` invokes the existing restore
guard and confirms Shields Up. If both the guard and direct Shields Up setting
fail, the bounded fallback kills the tailscaled main process and queues a
nonblocking stop, then verifies either Shields Up or no daemon main process.
It does not synchronously wait for a dependency stop from inside
`ExecStopPost`. The unit allows 360 seconds for startup and 240 seconds for
stop, covering the bounded checks and recovery paths. A daemon restart cannot leave a running Prism
unit with inbound traffic open; service restoration requires another manual
start and the same checks. The local check does not authenticate a real user.

## Immutable service update for the OIDC landing fix

For the recipient identity discovery update, version 3 packages exactly
`src/prism/identity.py`, `src/prism/webapp.py`, and `src/prism/static/app.js`.
Use a fresh private absolute path and retain all four hashes printed by
`package`:

```bash
python3 scripts/gcp/identity-service-release.py package \
  --identity src/prism/identity.py \
  --webapp src/prism/webapp.py \
  --app-js src/prism/static/app.js \
  --output <new-private-absolute-path>/identity-v3.tar

python3 /usr/local/libexec/prism-identity-service-release.py install \
  --transfer-tar <private-absolute-path>/identity-v3.tar \
  --transfer-tar-sha256 <recorded-tar-sha256> \
  --identity-sha256 <recorded-identity-sha256> \
  --webapp-sha256 <recorded-webapp-sha256> \
  --app-js-sha256 <recorded-app-js-sha256>
```

The identity hash selects the strict v3 manifest and requires both other
patch hashes. The transfer must be a canonical tar with precisely these three
bounded regular files. The updater copies the other 26 baseline source bytes
unchanged into a new immutable release. It requires the stopped service,
Shields Up, a private transfer file, and the updated lifecycle helper before
installation. Version 1 and 2 packages and records remain supported.

A version 2 update can carry exactly the reviewed `src/prism/webapp.py` and
`src/prism/static/app.js` together. The original single-file command and
`prism_service_update_v1` records remain valid. For the two-file update, use
these commands with a new private absolute transfer path and the three hashes
printed by `package`:

```bash
python3 scripts/gcp/identity-service-release.py package \
  --webapp src/prism/webapp.py \
  --app-js src/prism/static/app.js \
  --output <new-private-absolute-path>/identity-v2.tar

python3 /usr/local/libexec/prism-identity-service-release.py install \
  --transfer-tar <private-absolute-path>/identity-v2.tar \
  --transfer-tar-sha256 <recorded-tar-sha256> \
  --webapp-sha256 <recorded-webapp-sha256> \
  --app-js-sha256 <recorded-app-js-sha256>
```

The `--app-js-sha256` option selects the strict version 2 record. The transfer
must be a canonical tar with precisely those two regular files, each within
its fixed size limit. The updater copies the other 27 original source bytes,
preserves the one-boot record and ledger, and validates both patched hashes in
the new immutable release. Apply the same stopped-service, Shields Up, private
transfer, and lifecycle-helper prerequisites described below.

The [release updater](../scripts/gcp/identity-service-release.py) installs the
reviewed `webapp.py` change as a separate release. It does not edit the
one-boot marker, its ledger or manifest, the old release, or service data. The
operator must keep the service stopped and Shields Up throughout installation.
The updater checks both conditions before creating the new release.

On the trusted workstation, package the reviewed source into a single-file
canonical tar and record both SHA-256 values printed by the command:

```bash
python3 scripts/gcp/identity-service-release.py package \
  --webapp src/prism/webapp.py --output <new-private-absolute-path>/webapp.tar
```

Transfer that tar and the reviewed updater through the existing private
administrative channel. Stage the tar as a single-link root-owned `0600`
regular file inside a root-owned private directory with no symlink ancestors.
On the guest, first stop the Prism unit and confirm
Shields Up. Install the **new** lifecycle helper at
`/usr/local/libexec/prism-identity-service-lifecycle.py` before invoking the
updater: the update's final validation requires its service-update record
support. Keep the previous unit backed up. Then run:

```bash
python3 /usr/local/libexec/prism-identity-service-release.py install \
  --transfer-tar <private-absolute-path>/webapp.tar \
  --transfer-tar-sha256 <recorded-tar-sha256> \
  --webapp-sha256 <recorded-webapp-sha256>
```

The updater verifies the transfer tar and patch hash before mutation, requires
the unit to be exactly `inactive/dead` with both process IDs zero, and checks
Shields Up. It repeats the service-state and Shields Up checks immediately
before publishing the new release and record. It checks the original 29-file
installation, copies the other 28 exact source files and
the contained Python runtime, and rejects unsafe links or extra source files.
The tailnet check permits an additional IPv6 address but requires exactly one
IPv4 address in `100.64.0.0/10` matching the fixed private DNS identity.
It writes a new root-only `source.tar` containing all 29 sources, hashes its
actual bytes, and writes a separate root-only update marker and manifest. It
also verifies imports resolve inside the new release. A failed installation
removes only the newly staged update. Record its printed `update_record` and
`source_tar_sha256`; do not substitute the transfer tar hash for the full
source tar hash.

Move the old unit to a private backup path, then render a new unit with the
same fixed tailnet IPv4 and the returned update marker:

```bash
python3 /usr/local/libexec/prism-identity-service-lifecycle.py render \
  --installed-record <printed-update-record> \
  --bind-host <same-tailnet-ipv4> \
  --output /etc/systemd/system/prism-identity-service.service
```

Review the unit, run `systemd-analyze verify`, reload systemd, and start the
service manually. Confirm the private HTTPS and owner login path, its
`reference-linux` profile, zero model allowance, and separate service data
directory. To roll back, stop the service and confirm Shields Up, restore the
backed-up old unit, reload systemd, and manually start the baseline release.
Leave both immutable releases and records available for audit; no historical
data migration is involved.

## Recorded immutable-release and owner-path result

The currently deployed service uses a new immutable 29-file release. The
release source-tar SHA-256 passed verification, and independent byte comparison
confirmed the original oneboot marker, manifest, and ledger were unchanged.
The first updater attempt rejected simultaneous IPv4/IPv6 addresses before
publication; after the updater fix, the release installed and the new unit
passed preflight, systemd verification, and start. Stop/restart tests confirmed
Shields Up closes access while stopped and opens it only for an active guarded
service. The Mac and Chrome checks and their limits are summarized above and
in the [sanitized evidence](validation/m2-recipient-private-deployment.txt).

The retained IAP SSH path is an explicitly requested administrative
capability, separate from the application service. It uses a VM-dedicated
firewall rule and tag for IAP TCP/22 only; public HTTPS ingress is not opened.
An OS Login key expires after one hour and is renewed for each connection. The
stable local key remains available for those on-demand sessions.

On the configured Mac, run `prism-ssh --check` to verify the connection or
`prism-ssh` to open an interactive shell. Exiting the shell closes that local
IAP tunnel; the fixed configuration remains available for the next connection.
The command does not start a stopped VM, and Google account authorization may
need renewal separately.
