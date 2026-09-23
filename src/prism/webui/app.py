"""Prism owner web UI — a thin, server-rendered presentation layer.

Deliberately not a second implementation of anything: every route below
calls the exact same services the CLI calls (``CaptureService``,
``DraftService``, ``PublicationService``, ``SharingService``,
``DurableSharedLinkCaptureService``), against the same SQLite database. This
module owns HTTP plumbing and HTML rendering only — no projection, gate,
taint, or authorization logic lives here. See
``docs/phases/phase-10-owner-web-ui.md`` for the design rationale, most
importantly *why this has no authentication*: it is bound to loopback only
(``127.0.0.1``), by the same "owner administration stays local" principle
(D-009) the CLI already relies on. Never serve this over a tunnel.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from ..config import Settings
from ..database import PrismDatabase
from ..exceptions import DraftChangedError, PrismError, ValidationError
from ..models.capture import CaptureSelection, CaptureSource, RemoteCaptureSource
from ..models.draft import DraftSelectionRequest
from ..models.publication import PublicationRequest
from ..providers.chatgpt import ChatGPTSharedLinkAdapter
from ..providers.registry import built_in_capture_adapters
from ..services.capture import CaptureService
from ..services.drafts import DraftService
from ..services.durable_shared_link_capture import DurableSharedLinkCaptureService
from ..services.normalization import CaptureNormalizer
from ..services.publication import PublicationService
from ..services.sharing import SharingService
from ..storage import DatabaseCaptureStore

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True


# ---------------------------------------------------------------------------
# Service wiring — mirrors cli.py's `_draft_service` etc. exactly, so the UI
# and the CLI are two front ends over identical service construction.
# ---------------------------------------------------------------------------
def _capture_root(data_dir: Path | None) -> Path:
    return data_dir.expanduser().resolve() if data_dir else Settings.from_env().data_dir


def _database(request: Request) -> PrismDatabase:
    return PrismDatabase(_capture_root(request.app.state.data_dir) / "prism.db")


def _draft_service(request: Request) -> DraftService:
    return DraftService(_database(request))


def _publication_service(request: Request) -> PublicationService:
    return PublicationService(_database(request))


def _sharing_service(request: Request) -> SharingService:
    return SharingService(_database(request))


def _capture_service(request: Request) -> CaptureService:
    return CaptureService(
        adapters=built_in_capture_adapters(),
        normalizer=CaptureNormalizer(),
        store=DatabaseCaptureStore(_database(request)),
    )


def _shared_link_service(request: Request) -> DurableSharedLinkCaptureService:
    return DurableSharedLinkCaptureService(
        adapter=ChatGPTSharedLinkAdapter(),
        normalizer=CaptureNormalizer(),
        database=_database(request),
    )


# ---------------------------------------------------------------------------
# Small helpers shared by many routes
# ---------------------------------------------------------------------------
def _flash_url(path: str, *, ok: str | None = None, err: str | None = None) -> str:
    if ok:
        return f"{path}?ok={quote(ok)}"
    if err:
        return f"{path}?err={quote(err)}"
    return path


def _group_warnings(warnings) -> list[dict]:
    """Collapse WARN findings into one row per distinct finding_id.

    A finding's id is a fingerprint of (detector, class, normalized match)
    only — never the atom it was found in (see `projection/detectors.py`
    `_finding_id`) — specifically so that the *same* term appearing in
    several included messages is one decision, not several. That property
    already makes grouping for display safe: resolving the single grouped
    card resolves every occurrence, with no separate bookkeeping needed
    here.

    Ordered with the fewest-occurrence terms first — a term that shows up
    in only one or two places is far more likely to be a specific,
    identifying leak (a name) than one repeated across a dozen places (an
    ordinary shared-topic word); this is a plain occurrence-count heuristic,
    not a claim of measured precision, and is meant to help a human scan a
    long list, not to hide anything — every finding is still present.
    """

    groups: dict[str, dict] = {}
    for finding in warnings:
        group = groups.setdefault(finding.finding_id, {"finding": finding, "atom_refs": set()})
        group["atom_refs"].add(finding.atom_ref)
    ordered = sorted(groups.values(), key=lambda g: (len(g["atom_refs"]), g["finding"].finding_id))
    for group in ordered:
        group["occurrence_count"] = len(group["atom_refs"])
    return ordered


def _pending_grant_count(request: Request) -> int:
    grants = _sharing_service(request).list_grants()
    return sum(1 for g in grants if g.approval.value == "pending" and g.status.value == "active")


def _render(request: Request, template: str, context: dict) -> "TemplateResponse":  # type: ignore[name-defined]
    context = {
        **context,
        "request": request,
        "pending_badge": _pending_grant_count(request) or None,
    }
    return templates.TemplateResponse(request, template, context)


async def _form(request: Request) -> dict:
    form = await request.form()
    return {key: form.get(key) for key in form.keys()}


def _checked_ids(form: dict, prefix: str) -> tuple[str, ...]:
    """IDs from checkboxes named e.g. turn_<id>=on."""

    return tuple(
        key[len(prefix):] for key, value in form.items() if key.startswith(prefix) and value
    )


_DRAFT_ID_IN_PATH = re.compile(r"^/drafts/([^/?]+)")


def _draft_changed_message(request: Request, back: str, exc: DraftChangedError) -> str:
    """DRAFT_CHANGED covers two different situations the repository layer's
    one flat message doesn't distinguish (see `_require_editable_revision`
    in `repositories/drafts.py`): a genuine stale-revision race, where
    reloading the page and retrying is exactly the right fix — or a draft
    that was already published and is now permanently immutable *by
    design* (D-034: a published snapshot must stay traceable to the exact
    draft revision that was reviewed and hashed), where reloading will
    never help. Tell them apart by looking the draft back up, so the flash
    message actually says what to do next instead of a generic "reload".
    """

    match = _DRAFT_ID_IN_PATH.match(back)
    if match:
        try:
            state = _draft_service(request).review(match.group(1)).draft
            if state.status.value != "editing":
                return (
                    "This draft was already published and is now immutable — "
                    "capture the thread again to publish a different selection."
                )
        except PrismError:
            pass
    return str(exc)


async def _safe(request: Request, back: str, fn):
    """Run a mutating action; on a PrismError, redirect back with a flash."""

    try:
        return await fn()
    except DraftChangedError as exc:
        return RedirectResponse(
            _flash_url(back, err=_draft_changed_message(request, back, exc)), status_code=303
        )
    except PrismError as exc:
        return RedirectResponse(_flash_url(back, err=str(exc)), status_code=303)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
async def dashboard(request: Request):
    drafts = _draft_service(request).list()
    snapshots = _publication_service(request).list()
    grants = _sharing_service(request).list_grants()
    pending_grants = [g for g in grants if g.approval.value == "pending" and g.status.value == "active"]
    editing = [d for d in drafts if d.status.value == "editing"]
    return _render(
        request,
        "dashboard.html",
        {
            "draft_count": len(drafts),
            "editing_count": len(editing),
            "snapshot_count": len(snapshots),
            "pending_grants": pending_grants,
            "recent_drafts": sorted(drafts, key=lambda d: d.updated_at, reverse=True)[:6],
            "recent_snapshots": sorted(snapshots, key=lambda s: s.published_at, reverse=True)[:6],
        },
    )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
async def capture_new(request: Request):
    return _render(request, "capture_new.html", {"adapters": sorted(built_in_capture_adapters())})


async def capture_shared_link_inspect(request: Request):
    form = await _form(request)
    url = (form.get("url") or "").strip()

    async def go():
        preview = _shared_link_service(request).inspect(RemoteCaptureSource(url=url))
        return _render(request, "capture_shared_link_preview.html", {"preview": preview})

    return await _safe(request, "/capture/new", go)


async def capture_shared_link_confirm(request: Request):
    form = await _form(request)
    import_id = form["import_id"]
    preview_hash = form["preview_hash"]

    async def go():
        result = _shared_link_service(request).confirm(import_id, preview_hash)
        return RedirectResponse(
            _flash_url(f"/drafts/{result.draft_id}", ok="Captured. Everything starts excluded — choose what to share below."),
            status_code=303,
        )

    return await _safe(request, "/capture/new", go)


async def capture_local_inventory(request: Request):
    form = await _form(request)
    adapter = form["adapter"]
    path = (form.get("path") or "").strip()

    async def go():
        source = CaptureSource(path=Path(path))
        inventory = _capture_service(request).inventory(source, adapter)
        return _render(
            request,
            "capture_local_inventory.html",
            {"inventory": inventory, "adapter": adapter, "path": path},
        )

    return await _safe(request, "/capture/new", go)


async def capture_local_import(request: Request):
    form = await _form(request)
    adapter = form["adapter"]
    path = form["path"]
    conversation_ref = form["conversation_ref"]

    async def go():
        source = CaptureSource(path=Path(path))
        result = _capture_service(request).capture(
            source, adapter, CaptureSelection(conversation_ref=conversation_ref)
        )
        return RedirectResponse(
            _flash_url(f"/drafts/{result.draft_id}", ok="Captured. Everything starts excluded — choose what to share below."),
            status_code=303,
        )

    return await _safe(request, "/capture/new", go)


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
async def drafts_list(request: Request):
    drafts = _draft_service(request).list()
    return _render(request, "drafts_list.html", {"drafts": sorted(drafts, key=lambda d: d.updated_at, reverse=True)})


async def draft_review(request: Request):
    draft_id = request.path_params["draft_id"]
    drafts = _draft_service(request)

    def go():
        review = drafts.review(draft_id)
        gate = drafts.gate(draft_id)
        decided = drafts.decided_finding_ids(draft_id)
        provenance = drafts.provenance(draft_id)
        tainted = {t.event_id: t for t in drafts.tainted_events(draft_id)}
        graph_edges = drafts.dependency_graph(draft_id)
        included_turn_ids = {t.turn.turn_id for t in review.turns if t.included}
        can_edit = review.draft.status.value == "editing"
        # A published draft's preview_hash *is* its snapshot's content hash
        # (see repositories/snapshots.py `_snapshot_id`) — link straight to
        # it instead of just saying "go look at Snapshots".
        published_snapshot_id = None
        if not can_edit and review.draft.preview_hash:
            algorithm, _, digest = review.draft.preview_hash.partition(":")
            if algorithm == "sha256" and len(digest) == 64:
                published_snapshot_id = f"snp_{digest}"
        grouped_warnings = _group_warnings(gate.warnings)
        return _render(
            request,
            "draft_review.html",
            {
                "review": review,
                "gate": gate,
                "grouped_warnings": grouped_warnings,
                "decided": decided,
                "provenance": provenance,
                "tainted": tainted,
                "graph_edges": graph_edges,
                "included_turn_ids": included_turn_ids,
                "can_edit": can_edit,
                "published_snapshot_id": published_snapshot_id,
            },
        )

    try:
        return go()
    except PrismError as exc:
        return RedirectResponse(_flash_url("/drafts", err=str(exc)), status_code=303)


async def draft_select(request: Request):
    draft_id = request.path_params["draft_id"]
    form = await _form(request)
    back = f"/drafts/{draft_id}"

    async def go():
        selected = _checked_ids(form, "turn_")
        _draft_service(request).replace_selection(
            draft_id,
            DraftSelectionRequest(
                expected_revision=int(form["expected_revision"]),
                selected_turn_ids=selected,
                selected_resource_ids=(),
            ),
        )
        return RedirectResponse(_flash_url(back, ok=f"Saved — {len(selected)} turn(s) selected."), status_code=303)

    return await _safe(request, back, go)


async def draft_title(request: Request):
    draft_id = request.path_params["draft_id"]
    form = await _form(request)
    back = f"/drafts/{draft_id}"

    async def go():
        clear = bool(form.get("clear"))
        title = None if clear else (form.get("title") or "").strip() or None
        _draft_service(request).set_title(draft_id, int(form["expected_revision"]), title)
        return RedirectResponse(_flash_url(back, ok="Title updated."), status_code=303)

    return await _safe(request, back, go)


async def draft_resource_attach(request: Request):
    draft_id = request.path_params["draft_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        form = await request.form()
        upload = form.get("file")
        expected_revision = int(form.get("expected_revision"))
        if upload is None or not getattr(upload, "filename", None):
            raise ValidationError("Choose a text or Markdown file to attach")
        raw = await upload.read()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("Only UTF-8 text files can be attached") from exc
        _draft_service(request).attach_text_resource(draft_id, expected_revision, upload.filename, content)
        return RedirectResponse(_flash_url(back, ok=f"Attached {upload.filename}."), status_code=303)

    return await _safe(request, back, go)


async def draft_resource_remove(request: Request):
    draft_id = request.path_params["draft_id"]
    attachment_id = request.path_params["attachment_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        form = await _form(request)
        _draft_service(request).remove_attachment(draft_id, int(form["expected_revision"]), attachment_id)
        return RedirectResponse(_flash_url(back, ok="Resource removed."), status_code=303)

    return await _safe(request, back, go)


async def draft_finding_resolve(request: Request):
    draft_id = request.path_params["draft_id"]
    finding_id = request.path_params["finding_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        form = await _form(request)
        reason = (form.get("reason") or "").strip()
        if not reason:
            raise ValidationError("A reason is required to allow a finding through")
        _draft_service(request).allow_finding(draft_id, finding_id, form["finding_class"], reason)
        return RedirectResponse(_flash_url(back, ok="Finding allowed."), status_code=303)

    return await _safe(request, back, go)


async def draft_finding_unresolve(request: Request):
    draft_id = request.path_params["draft_id"]
    finding_id = request.path_params["finding_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        _draft_service(request).revoke_finding_decision(draft_id, finding_id)
        return RedirectResponse(_flash_url(back, ok="Decision removed."), status_code=303)

    return await _safe(request, back, go)


async def draft_taint(request: Request):
    draft_id = request.path_params["draft_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        form = await _form(request)
        reason = (form.get("reason") or "").strip()
        if not reason:
            raise ValidationError("A reason is required to flag a tool-call event")
        _draft_service(request).taint(draft_id, form["event_id"], reason)
        return RedirectResponse(_flash_url(back, ok="Event flagged. Turns at or after it now block publication."), status_code=303)

    return await _safe(request, back, go)


async def draft_untaint(request: Request):
    draft_id = request.path_params["draft_id"]
    event_id = request.path_params["event_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        _draft_service(request).untaint(draft_id, event_id)
        return RedirectResponse(_flash_url(back, ok="Flag removed."), status_code=303)

    return await _safe(request, back, go)


async def draft_preview(request: Request):
    draft_id = request.path_params["draft_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        form = await _form(request)
        preview = _draft_service(request).preview(draft_id, int(form["expected_revision"]))
        return _render(request, "draft_preview.html", {"draft_id": draft_id, "preview": preview})

    return await _safe(request, back, go)


async def draft_publish(request: Request):
    draft_id = request.path_params["draft_id"]
    back = f"/drafts/{draft_id}"

    async def go():
        form = await _form(request)
        result = _publication_service(request).publish(
            PublicationRequest(
                draft_id=draft_id,
                expected_revision=int(form["expected_revision"]),
                expected_preview_hash=form["expected_preview_hash"],
            )
        )
        return RedirectResponse(
            _flash_url(f"/snapshots/{result.snapshot_id}", ok="Published."), status_code=303
        )

    return await _safe(request, back, go)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------
async def snapshots_list(request: Request):
    snapshots = _publication_service(request).list()
    return _render(request, "snapshots_list.html", {"snapshots": sorted(snapshots, key=lambda s: s.published_at, reverse=True)})


async def snapshot_detail(request: Request):
    snapshot_id = request.path_params["snapshot_id"]
    service = _publication_service(request)

    def go():
        snapshot = service.get(snapshot_id)
        receipt = None
        valid = None
        try:
            receipt = service.get_receipt(snapshot_id)
            valid = service.verify_receipt(receipt)
        except PrismError:
            pass
        shares = [s for s in _sharing_service(request).list_shares() if any(v.snapshot_id == snapshot_id for v in s.versions)]
        return _render(
            request,
            "snapshot_detail.html",
            {"snapshot": snapshot, "receipt": receipt, "receipt_valid": valid, "shares": shares},
        )

    try:
        return go()
    except PrismError as exc:
        return RedirectResponse(_flash_url("/snapshots", err=str(exc)), status_code=303)


async def snapshot_revoke(request: Request):
    snapshot_id = request.path_params["snapshot_id"]
    back = f"/snapshots/{snapshot_id}"

    async def go():
        form = await _form(request)
        purge = bool(form.get("purge"))
        _publication_service(request).revoke(snapshot_id, purge=purge)
        return RedirectResponse(_flash_url(back, ok="Snapshot revoked." + (" Payload purged." if purge else "")), status_code=303)

    return await _safe(request, back, go)


async def snapshot_create_share(request: Request):
    snapshot_id = request.path_params["snapshot_id"]
    back = f"/snapshots/{snapshot_id}"

    async def go():
        form = await _form(request)
        name = (form.get("name") or "").strip() or None
        share = _sharing_service(request).create_share(snapshot_id, name)
        return RedirectResponse(_flash_url(f"/shares/{share.share.share_id}", ok="Share created."), status_code=303)

    return await _safe(request, back, go)


# ---------------------------------------------------------------------------
# Shares, invitations, grants
# ---------------------------------------------------------------------------
async def shares_list(request: Request):
    shares = _sharing_service(request).list_shares()
    return _render(request, "shares_list.html", {"shares": sorted(shares, key=lambda s: s.share.updated_at, reverse=True)})


async def share_detail(request: Request):
    share_id = request.path_params["share_id"]
    sharing = _sharing_service(request)

    def go():
        share = sharing.get_share(share_id)
        invitations = [i for i in sharing.list_invitations() if i.share_version_id in {v.share_version_id for v in share.versions}]
        grants = [g for g in sharing.list_grants() if g.share_version_id in {v.share_version_id for v in share.versions}]
        return _render(request, "share_detail.html", {"share": share, "invitations": invitations, "grants": grants})

    try:
        return go()
    except PrismError as exc:
        return RedirectResponse(_flash_url("/shares", err=str(exc)), status_code=303)


async def share_add_version(request: Request):
    share_id = request.path_params["share_id"]
    back = f"/shares/{share_id}"

    async def go():
        form = await _form(request)
        _sharing_service(request).add_version(share_id, form["snapshot_id"])
        return RedirectResponse(_flash_url(back, ok="Version added."), status_code=303)

    return await _safe(request, back, go)


async def share_revoke(request: Request):
    share_id = request.path_params["share_id"]
    back = f"/shares/{share_id}"

    async def go():
        _sharing_service(request).revoke_share(share_id)
        return RedirectResponse(_flash_url(back, ok="Share revoked."), status_code=303)

    return await _safe(request, back, go)


async def share_revoke_version(request: Request):
    share_id = request.path_params["share_id"]
    version = int(request.path_params["version"])
    back = f"/shares/{share_id}"

    async def go():
        _sharing_service(request).revoke_share_version(share_id, version)
        return RedirectResponse(_flash_url(back, ok=f"Version {version} revoked."), status_code=303)

    return await _safe(request, back, go)


async def share_create_invitation(request: Request):
    share_id = request.path_params["share_id"]
    back = f"/shares/{share_id}"

    async def go():
        form = await _form(request)
        expires_hours = int(form.get("expires_hours") or 24)
        grant_hours = int(form.get("grant_hours") or 168)
        version_raw = (form.get("version") or "").strip()
        now = datetime.now(timezone.utc)
        invitation = _sharing_service(request).create_invitation(
            share_id,
            version=int(version_raw) if version_raw else None,
            expires_at=(now + timedelta(hours=expires_hours)).isoformat().replace("+00:00", "Z"),
            grant_expires_at=(now + timedelta(hours=grant_hours)).isoformat().replace("+00:00", "Z"),
            require_approval=form.get("require_approval") != "off",
            recipient_hint=(form.get("recipient_hint") or "").strip() or None,
        )
        return _render(request, "invitation_created.html", {"invitation": invitation, "share_id": share_id})

    return await _safe(request, back, go)


async def invitation_revoke(request: Request):
    invitation_id = request.path_params["invitation_id"]
    back = request.query_params.get("back", "/shares")

    async def go():
        _sharing_service(request).revoke_invitation(invitation_id)
        return RedirectResponse(_flash_url(back, ok="Invitation revoked."), status_code=303)

    return await _safe(request, back, go)


async def grants_list(request: Request):
    grants = _sharing_service(request).list_grants()
    return _render(request, "grants_list.html", {"grants": sorted(grants, key=lambda g: g.created_at, reverse=True)})


async def grant_approve(request: Request):
    grant_id = request.path_params["grant_id"]
    back = request.query_params.get("back", "/grants")

    async def go():
        _sharing_service(request).approve_grant(grant_id)
        return RedirectResponse(_flash_url(back, ok="Grant approved."), status_code=303)

    return await _safe(request, back, go)


async def grant_deny(request: Request):
    grant_id = request.path_params["grant_id"]
    back = request.query_params.get("back", "/grants")

    async def go():
        _sharing_service(request).deny_grant(grant_id)
        return RedirectResponse(_flash_url(back, ok="Grant denied."), status_code=303)

    return await _safe(request, back, go)


async def grant_revoke(request: Request):
    grant_id = request.path_params["grant_id"]
    back = request.query_params.get("back", "/grants")

    async def go():
        _sharing_service(request).revoke_grant(grant_id)
        return RedirectResponse(_flash_url(back, ok="Grant revoked."), status_code=303)

    return await _safe(request, back, go)


async def audit_list(request: Request):
    events = _sharing_service(request).list_events(limit=200)
    return _render(request, "audit_list.html", {"events": events})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(data_dir: Path | None = None) -> Starlette:
    routes = [
        Route("/", dashboard),
        Route("/capture/new", capture_new),
        Route("/capture/shared-link/inspect", capture_shared_link_inspect, methods=["POST"]),
        Route("/capture/shared-link/confirm", capture_shared_link_confirm, methods=["POST"]),
        Route("/capture/local/inventory", capture_local_inventory, methods=["POST"]),
        Route("/capture/local/import", capture_local_import, methods=["POST"]),
        Route("/drafts", drafts_list),
        Route("/drafts/{draft_id}", draft_review),
        Route("/drafts/{draft_id}/select", draft_select, methods=["POST"]),
        Route("/drafts/{draft_id}/title", draft_title, methods=["POST"]),
        Route("/drafts/{draft_id}/resources/attach", draft_resource_attach, methods=["POST"]),
        Route("/drafts/{draft_id}/resources/{attachment_id}/remove", draft_resource_remove, methods=["POST"]),
        Route("/drafts/{draft_id}/findings/{finding_id}/resolve", draft_finding_resolve, methods=["POST"]),
        Route("/drafts/{draft_id}/findings/{finding_id}/unresolve", draft_finding_unresolve, methods=["POST"]),
        Route("/drafts/{draft_id}/taint", draft_taint, methods=["POST"]),
        Route("/drafts/{draft_id}/untaint/{event_id}", draft_untaint, methods=["POST"]),
        Route("/drafts/{draft_id}/preview", draft_preview, methods=["POST"]),
        Route("/drafts/{draft_id}/publish", draft_publish, methods=["POST"]),
        Route("/snapshots", snapshots_list),
        Route("/snapshots/{snapshot_id}", snapshot_detail),
        Route("/snapshots/{snapshot_id}/revoke", snapshot_revoke, methods=["POST"]),
        Route("/snapshots/{snapshot_id}/share", snapshot_create_share, methods=["POST"]),
        Route("/shares", shares_list),
        Route("/shares/{share_id}", share_detail),
        Route("/shares/{share_id}/versions", share_add_version, methods=["POST"]),
        Route("/shares/{share_id}/revoke", share_revoke, methods=["POST"]),
        Route("/shares/{share_id}/versions/{version}/revoke", share_revoke_version, methods=["POST"]),
        Route("/shares/{share_id}/invitations", share_create_invitation, methods=["POST"]),
        Route("/invitations/{invitation_id}/revoke", invitation_revoke, methods=["POST"]),
        Route("/grants", grants_list),
        Route("/grants/{grant_id}/approve", grant_approve, methods=["POST"]),
        Route("/grants/{grant_id}/deny", grant_deny, methods=["POST"]),
        Route("/grants/{grant_id}/revoke", grant_revoke, methods=["POST"]),
        Route("/audit", audit_list),
    ]
    app = Starlette(routes=routes)
    app.state.data_dir = data_dir

    from starlette.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return app


def run_web_ui(data_dir: Path | None, *, port: int) -> None:
    """Run the owner UI with Uvicorn, bound to loopback only.

    No ``host`` parameter on purpose — see the module docstring. Owner
    administration stays local (D-009); this reuses that principle rather
    than adding a second one for the UI.
    """

    import uvicorn

    uvicorn.run(create_app(data_dir), host="127.0.0.1", port=port, log_level="warning", access_log=False)
