# Private identity service model route

The guest identity service defaults to a zero-cent model allowance. To opt in,
place a Prism-only OpenAI key in the guest file
`/var/lib/prism-identity/openai-model-key`. It must be a root-owned, single-link
regular file with owner-only permissions (mode `0600`) and a valid `sk-` value.
Keep the value out of command arguments, environment variables, logs, and the
repository.

The deployed private service currently has its external model route enabled
with a $10.00 (1,000-cent) aggregate reservation ceiling. The previous $0.80
reservation remained recorded at the time of the change, leaving $9.20 before new demo calls. This temporary ceiling remains
until the user explicitly ends the demo; restoring the former ceiling
requires an explicit user request. It is an application reservation ceiling,
not a hard provider billing cap. The owner must first install a root-owned,
single-link model key as described above, then replace the service unit
through the guarded sequence below when changing the configured allowance.
The guest wrapper accepts 0, 100, or 1,000 cents; absent an explicit option,
the rendered unit keeps the zero-cent default. A missing or unsafe key file
prevents startup.

## Guarded service-unit replacement

The renderer creates its destination with exclusive file creation (`O_EXCL`)
and refuses to overwrite the existing unit. Never rerender directly over the
active or saved unit. Replace it only with this sequence, using a new private
backup path that does not already exist:

1. Stop `prism-identity-service.service`. Confirm it is `inactive/dead` and
   `MainPID=0` and `ControlPID=0` with `systemctl show`.
2. Confirm Shields Up is enabled (`ShieldsUp: true` in
   `tailscale debug prefs`). If either check fails, stop here and keep the old
   unit in place.
3. Preserve the existing unit byte-for-byte at the private backup path, then
   move it out of `/etc/systemd/system/prism-identity-service.service`. Keep
   the backup for rollback.
4. Render a new unit at the fixed service path with the intended finite budget
   and the same verified installed-release record and bind address. The
   renderer's exclusive-create guard must succeed because the destination is
   now absent.
5. Run `systemd-analyze verify` on the new unit. If verification fails, do not
   reload or start it; restore the preserved unit as described below.
6. Run `systemctl daemon-reload`, then start the service manually with
   `systemctl start prism-identity-service.service`.
7. Verify the service is active and the owner HTTPS route and model status
   show the intended finite allowance. This checks configuration only; a real
   provider request still requires separate explicit authorization.

To roll back, stop the service, again verify it is `inactive/dead` with both
process IDs zero and Shields Up enabled, remove or move aside only the failed
new unit, restore the preserved unit to the fixed service path, run
`systemctl daemon-reload`, and manually start the restored service. Verify its
owner HTTPS route and prior model status. Keep both unit copies until rollback
or replacement is verified.

The existing application ledger reserves model cost before each provider
request and persists reservations across restarts in the dedicated identity
state directory. Changing the unit does not clear those reservations. The
route uses the application's fixed OpenAI Responses endpoint and model policy;
it does not expand collaborator permissions or task-network access. Questions,
current-session history, selected project evidence, and permitted tool results
may be sent to OpenAI when model use is enabled. A bounded live recipient
question has used the external route; this is real provider inference. See the
[live demo SOP](live-demo-sop.md) and [sanitized live validation
record](validation/m2-recipient-live-identity.json) for the current outcome
and limits. This does not establish pilot readiness.
