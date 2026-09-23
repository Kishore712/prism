"""Exact preview-to-publication workflow for immutable snapshots.

Publication is where the Sealed Projection gate actually gates: a pre-check
blocks on any undecided BLOCK-severity finding over the draft's current
selection, and a post-condition rescan re-runs the same detectors directly
against the bytes about to be published — a defense-in-depth backstop that
fails closed even if the pre-check were ever wrong. See
``docs/PROJECTION_LAYER_PROPOSAL.md`` §4.5-4.6.
"""

from __future__ import annotations

import hmac

from sqlalchemy.exc import SQLAlchemyError

from ..clock import utc_now
from ..database import PrismDatabase, PrismUnitOfWork
from ..exceptions import (
    DatabaseError,
    DraftChangedError,
    LeakDetectedError,
    PreviewChangedError,
    PreviewRequiredError,
    UnresolvedFindingsError,
)
from ..models.draft import DraftStatus
from ..models.projection import ProjectionSelection
from ..models.publication import (
    PublicationRequest,
    PublicationResult,
    PublishedSnapshot,
    SnapshotSummary,
)
from ..models.projection import SnapshotReceipt
from ..projection import DETECTOR_VERSIONS, check_gate, sign_receipt, verify_receipt
from ..projection.detectors import Atom, FindingSeverity, run_detectors
from ..projection.receipt import ReceiptKeyPair
from .projection import ProjectionService
from .projection_scan import scan_draft


def _describe(findings) -> str:
    shown = [f"{finding.finding_id} ({finding.finding_class.value}: {finding.detail})" for finding in findings[:5]]
    more = f" … and {len(findings) - 5} more" if len(findings) > 5 else ""
    return "; ".join(shown) + more


class PublicationService:
    def __init__(self, database: PrismDatabase) -> None:
        self._database = database
        self._database.initialize()

    def publish(self, request: PublicationRequest) -> PublicationResult:
        try:
            with PrismUnitOfWork(self._database) as unit:
                draft = unit.drafts.get(request.draft_id)
                if draft.revision != request.expected_revision:
                    raise DraftChangedError(
                        "The draft changed after it was previewed; reload it before publishing"
                    )
                if draft.status not in {DraftStatus.EDITING, DraftStatus.PUBLISHED}:
                    raise DraftChangedError("The draft can no longer be published")
                if (
                    draft.previewed_revision != request.expected_revision
                    or draft.preview_hash is None
                ):
                    raise PreviewRequiredError(
                        "The current draft revision must be previewed before publication"
                    )
                if not hmac.compare_digest(
                    draft.preview_hash,
                    request.expected_preview_hash,
                ):
                    raise PreviewChangedError(
                        "The supplied hash does not match the recorded preview"
                    )
                capture = unit.captures.load(draft.capture_id)
                attachments = unit.drafts.projection_attachments(draft.draft_id)

                # --- Sealed Projection: pre-check ---------------------------------
                # Includes Tier 1 provenance-derived findings (Phase 9): the
                # post-condition rescan below deliberately does not, since
                # they are not a property of the built bytes — see
                # `services/projection_scan.py`.
                findings = scan_draft(unit, draft, capture, attachments)
                decided = unit.finding_decisions.decided_ids(draft.draft_id)
                gate = check_gate(findings, decided)
                if not gate.ok:
                    raise UnresolvedFindingsError(
                        f"{len(gate.blocking)} finding(s) must be reviewed before "
                        f"publishing (see `prism draft findings`): {_describe(gate.blocking)}"
                    )

                rebuilt = ProjectionService.preview_capture(
                    capture,
                    ProjectionSelection(
                        selected_turn_ids=draft.selected_turn_ids,
                        selected_resource_ids=draft.selected_resource_ids,
                    ),
                    attachments,
                    title_override=draft.title_override,
                )
                if not hmac.compare_digest(
                    rebuilt.preview_hash,
                    request.expected_preview_hash,
                ):
                    raise PreviewChangedError(
                        "The durable selection no longer rebuilds the reviewed preview"
                    )

                # --- Sealed Projection: post-condition rescan ----------------------
                # Re-derive BLOCK-class findings directly from the bytes about to be
                # published, independent of the pre-check: a defense-in-depth
                # backstop against any bug between "what was scanned" and "what was
                # actually built". Any BLOCK finding not covered by a decision
                # aborts with no snapshot created.
                self._post_condition_check(rebuilt.snapshot, decided)

                canonical = ProjectionService.canonical_snapshot_bytes(rebuilt.snapshot)
                snapshot, created = unit.snapshots.create_or_reuse(
                    rebuilt.snapshot,
                    rebuilt.preview_hash,
                    canonical,
                )
                event = unit.publications.record(
                    snapshot.summary.snapshot_id,
                    draft.capture_id,
                    draft.draft_id,
                    draft.revision,
                    request.expected_preview_hash,
                )
                unit.drafts.mark_published(draft.draft_id, draft.revision)
                override_count = len(gate.overridden)
                keypair = ReceiptKeyPair.load_or_create(self._database.path.parent)
                receipt = sign_receipt(
                    keypair,
                    capture_hash=capture.capture_hash,
                    snapshot_hash=snapshot.summary.content_hash,
                    detector_versions=DETECTOR_VERSIONS,
                    finding_count=len(findings),
                    override_count=override_count,
                    created_at=utc_now(),
                )
                unit.receipts.save(snapshot.summary.snapshot_id, receipt)
                unit.commit()
                return PublicationResult(
                    publication_id=event.publication_id,
                    snapshot_id=snapshot.summary.snapshot_id,
                    content_hash=snapshot.summary.content_hash,
                    draft_id=draft.draft_id,
                    draft_revision=draft.revision,
                    published_at=event.published_at,
                    message_count=len(rebuilt.snapshot.messages),
                    resource_count=len(rebuilt.snapshot.resources),
                    created=created,
                    finding_count=len(findings),
                    override_count=override_count,
                )
        except SQLAlchemyError as exc:
            raise DatabaseError("The snapshot publication transaction failed") from exc

    @staticmethod
    def _post_condition_check(snapshot, decided: frozenset[str]) -> None:
        published_atoms = [Atom(kind="title", ref="title", text=snapshot.title)]
        published_atoms += [
            Atom(kind="message", ref=message.message_id, text=message.content)
            for message in snapshot.messages
        ]
        published_atoms += [
            Atom(kind="resource", ref=resource.resource_id, text=resource.content)
            for resource in snapshot.resources
        ]

        # Only the precise, regex-based classes (secret / url-secret /
        # pii-direct / env-identifier) re-block here. exclusion-derived is
        # deliberately excluded from this hard gate: measured against real
        # fixtures it false-positives on ordinary reused words ("question",
        # "answer") often enough that an unoverridable block on it would make
        # publication unusable, not safer. It remains a WARN in both the
        # pre-check and here — always computed, always shown, never silently
        # dropped — but the owner's own turn-exclusion choice is the actual
        # enforcement mechanism for G1; this class is a nudge on top of it,
        # not a second gate. Tightening this (IDF weighting, an owner
        # ignore-list) is tracked as follow-up work, not silently deferred.
        block_findings = [
            finding
            for finding in run_detectors(published_atoms)
            if finding.severity is FindingSeverity.BLOCK
        ]
        unresolved = [f for f in block_findings if f.finding_id not in decided]
        if unresolved:
            raise LeakDetectedError(
                "Publication aborted: the built snapshot itself still contains "
                f"undecided findings ({_describe(unresolved)}). No snapshot was created."
            )

    def get(self, snapshot_id: str) -> PublishedSnapshot:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.snapshots.get(snapshot_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("The published snapshot could not be read") from exc

    def list(self) -> tuple[SnapshotSummary, ...]:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.snapshots.list()
        except SQLAlchemyError as exc:
            raise DatabaseError("Published snapshots could not be listed") from exc

    def get_receipt(self, snapshot_id: str) -> SnapshotReceipt:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.receipts.get(snapshot_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("The receipt could not be read") from exc

    @staticmethod
    def verify_receipt(receipt: SnapshotReceipt) -> bool:
        return verify_receipt(receipt)

    def revoke(self, snapshot_id: str, *, purge: bool = False) -> PublishedSnapshot:
        try:
            with PrismUnitOfWork(self._database) as unit:
                snapshot = unit.snapshots.revoke(snapshot_id, purge=purge)
                unit.shares.revoke_versions_for_snapshot(snapshot_id)
                unit.sharing_events.record(
                    "snapshot_purged" if purge else "snapshot_revoked",
                    snapshot_id=snapshot_id,
                )
                unit.commit()
                return snapshot
        except SQLAlchemyError as exc:
            raise DatabaseError("The snapshot lifecycle update failed") from exc
