"""Durable owner draft review, selection, preview, and projection-scan service."""

from __future__ import annotations

import unicodedata

from sqlalchemy.exc import SQLAlchemyError

from ..database import PrismDatabase, PrismUnitOfWork
from ..exceptions import DatabaseError, ValidationError
from ..models.draft import (
    DraftReview,
    DraftReviewResource,
    DraftReviewTurn,
    DraftSelectionRequest,
    DraftSnapshotPreview,
    DraftState,
)
from ..models.projection import ProjectionSelection
from ..projection import Finding, GateResult, check_gate
from ..projection.atoms import build_atoms
from ..projection.graph import (
    DependencyEdge,
    build_cue_phrase_edges,
    build_lexical_edges,
    build_provenance_edges,
)
from ..projection.provenance import TaintedEventRecord, ToolReadEventRecord
from .projection import ProjectionService
from .projection_scan import scan_draft


_MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")


class DraftService:
    def __init__(self, database: PrismDatabase) -> None:
        self._database = database
        self._database.initialize()

    def list(self) -> tuple[DraftState, ...]:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.drafts.list()
        except SQLAlchemyError as exc:
            raise DatabaseError("Durable drafts could not be listed") from exc

    def review(self, draft_id: str) -> DraftReview:
        try:
            with PrismUnitOfWork(self._database) as unit:
                draft = unit.drafts.get(draft_id)
                capture = unit.captures.load(draft.capture_id)
                review = ProjectionService.review_capture(capture)
        except SQLAlchemyError as exc:
            raise DatabaseError("The durable draft could not be read") from exc
        selected_turns = set(draft.selected_turn_ids)
        selected_resources = set(draft.selected_resource_ids)
        return DraftReview(
            draft=draft,
            title=review.title,
            turns=tuple(
                DraftReviewTurn(
                    turn=turn,
                    included=turn.turn_id in selected_turns,
                )
                for turn in review.turns
            ),
            resources=tuple(
                DraftReviewResource(
                    resource=resource,
                    included=resource.resource_id in selected_resources,
                )
                for resource in review.resources
            ),
        )

    def replace_selection(
        self,
        draft_id: str,
        request: DraftSelectionRequest,
    ) -> DraftState:
        try:
            with PrismUnitOfWork(self._database) as unit:
                state = unit.drafts.replace_selection(
                    draft_id,
                    request.expected_revision,
                    request.selected_turn_ids,
                    request.selected_resource_ids,
                )
                unit.commit()
                return state
        except SQLAlchemyError as exc:
            raise DatabaseError("The draft selection could not be saved") from exc

    def attach_text_resource(
        self,
        draft_id: str,
        expected_revision: int,
        display_name: str,
        content: str,
    ) -> DraftState:
        """Attach one owner-supplied UTF-8 text/Markdown file to the draft.

        Attaching is the explicit act of including it; nothing observed in the
        source conversation becomes a resource by itself. Bumps the revision, so
        the draft must be previewed again before it can be published.
        """

        name = " ".join(display_name.split())[:500]
        if not name:
            raise ValidationError("A resource name is required")
        if "\x00" in content:
            raise ValidationError("Only UTF-8 text files can be attached")
        media_type = (
            "text/markdown" if name.lower().endswith(_MARKDOWN_SUFFIXES) else "text/plain"
        )
        normalized = unicodedata.normalize(
            "NFC", content.replace("\r\n", "\n").replace("\r", "\n")
        )
        try:
            with PrismUnitOfWork(self._database) as unit:
                state = unit.drafts.add_attachment(
                    draft_id, expected_revision, name, media_type, normalized
                )
                unit.commit()
                return state
        except SQLAlchemyError as exc:
            raise DatabaseError("The resource could not be attached") from exc

    def remove_attachment(
        self,
        draft_id: str,
        expected_revision: int,
        attachment_id: str,
    ) -> DraftState:
        try:
            with PrismUnitOfWork(self._database) as unit:
                state = unit.drafts.remove_attachment(
                    draft_id, expected_revision, attachment_id
                )
                unit.commit()
                return state
        except SQLAlchemyError as exc:
            raise DatabaseError("The resource could not be removed") from exc

    def set_title(
        self,
        draft_id: str,
        expected_revision: int,
        title: str | None,
    ) -> DraftState:
        """Override the published title (``None`` reverts to the source title).

        The source title is never copied into the snapshot implicitly once a
        draft exists: the owner reviews and, if they choose, replaces it. A
        change bumps the revision, invalidating the current preview and any
        finding decisions tied to the old title text.
        """

        try:
            with PrismUnitOfWork(self._database) as unit:
                state = unit.drafts.set_title(draft_id, expected_revision, title)
                unit.commit()
                return state
        except SQLAlchemyError as exc:
            raise DatabaseError("The title could not be updated") from exc

    def scan(self, draft_id: str) -> tuple[Finding, ...]:
        """Run every projection detector, plus Tier 1 provenance findings,
        over the draft's current selection."""

        try:
            with PrismUnitOfWork(self._database) as unit:
                draft = unit.drafts.get(draft_id)
                capture = unit.captures.load(draft.capture_id)
                attachments = unit.drafts.projection_attachments(draft_id)
                return scan_draft(unit, draft, capture, attachments)
        except SQLAlchemyError as exc:
            raise DatabaseError("The draft could not be scanned") from exc

    def gate(self, draft_id: str) -> GateResult:
        """Findings combined with the owner's recorded decisions."""

        findings = self.scan(draft_id)
        try:
            with PrismUnitOfWork(self._database) as unit:
                decided = unit.finding_decisions.decided_ids(draft_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("Finding decisions could not be read") from exc
        return check_gate(findings, decided)

    def decided_finding_ids(self, draft_id: str) -> frozenset[str]:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.finding_decisions.decided_ids(draft_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("Finding decisions could not be read") from exc

    def provenance(self, draft_id: str) -> tuple[ToolReadEventRecord, ...]:
        """This draft's capture-time tool-call timeline (Phase 9)."""

        try:
            with PrismUnitOfWork(self._database) as unit:
                draft = unit.drafts.get(draft_id)
                return unit.tool_events.list_for_capture(draft.capture_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("The provenance timeline could not be read") from exc

    def tainted_events(self, draft_id: str) -> tuple[TaintedEventRecord, ...]:
        """All of this draft's current taint decisions."""

        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.tainted_events.list(draft_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("Taint decisions could not be read") from exc

    def taint(self, draft_id: str, event_id: str, reason: str) -> TaintedEventRecord:
        """Flag one capture-time tool-call event as a mistake.

        Every currently-included turn at or after this event, in the same
        capture, becomes an undecided BLOCK finding the next time this draft
        is scanned — see ``projection/taint.py``.
        """

        try:
            with PrismUnitOfWork(self._database) as unit:
                record = unit.tainted_events.taint(draft_id, event_id, reason)
                unit.commit()
                return record
        except SQLAlchemyError as exc:
            raise DatabaseError("The taint decision could not be saved") from exc

    def untaint(self, draft_id: str, event_id: str) -> None:
        try:
            with PrismUnitOfWork(self._database) as unit:
                unit.tainted_events.untaint(draft_id, event_id)
                unit.commit()
        except SQLAlchemyError as exc:
            raise DatabaseError("The taint decision could not be removed") from exc

    def dependency_graph(self, draft_id: str) -> tuple[DependencyEdge, ...]:
        """Tier 2 suggestions: what an included turn might be dragging in.

        Combines lexical (shared distinctive terms with excluded content),
        cue-phrase (backward-reference language), and provenance
        (chronologically after a tainted read) edges into one sorted list.
        Always WARN-equivalent — a suggestion for the owner to look at, never
        a blocking finding; only ``taint()`` produces something that blocks.
        """

        try:
            with PrismUnitOfWork(self._database) as unit:
                draft = unit.drafts.get(draft_id)
                capture = unit.captures.load(draft.capture_id)
                attachments = unit.drafts.projection_attachments(draft_id)
                tool_events = unit.tool_events.list_for_capture(draft.capture_id)
                tainted_ids = unit.tainted_events.tainted_event_ids(draft_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("The dependency graph could not be built") from exc
        atoms = build_atoms(
            capture,
            draft.selected_turn_ids,
            attachments,
            title_override=draft.title_override,
        )
        edges = list(build_lexical_edges(atoms))
        edges.extend(build_cue_phrase_edges(atoms))
        edges.extend(
            build_provenance_edges(capture, draft.selected_turn_ids, tool_events, tainted_ids)
        )
        edges.sort(key=lambda edge: (edge.source_ref, edge.target_ref, edge.kind))
        return tuple(edges)

    def allow_finding(
        self,
        draft_id: str,
        finding_id: str,
        finding_class: str,
        reason: str,
    ) -> None:
        """Owner explicitly allows one blocking finding through, with a reason.

        This does not change the content — it only removes that specific
        finding as an obstacle to publication. It is recorded in the
        snapshot's receipt as an override.
        """

        try:
            with PrismUnitOfWork(self._database) as unit:
                unit.finding_decisions.allow(draft_id, finding_id, finding_class, reason)
                unit.commit()
        except SQLAlchemyError as exc:
            raise DatabaseError("The finding decision could not be saved") from exc

    def revoke_finding_decision(self, draft_id: str, finding_id: str) -> None:
        try:
            with PrismUnitOfWork(self._database) as unit:
                unit.finding_decisions.revoke(draft_id, finding_id)
                unit.commit()
        except SQLAlchemyError as exc:
            raise DatabaseError("The finding decision could not be removed") from exc

    def preview(self, draft_id: str, expected_revision: int) -> DraftSnapshotPreview:
        try:
            with PrismUnitOfWork(self._database) as unit:
                draft = unit.drafts.get(draft_id)
                if draft.revision != expected_revision:
                    from ..exceptions import DraftChangedError

                    raise DraftChangedError(
                        "The draft changed after it was read; reload it before continuing"
                    )
                if not draft.selected_turn_ids:
                    raise ValidationError(
                        "At least one turn must be selected for a snapshot preview"
                    )
                capture = unit.captures.load(draft.capture_id)
                preview = ProjectionService.preview_capture(
                    capture,
                    ProjectionSelection(
                        selected_turn_ids=draft.selected_turn_ids,
                        selected_resource_ids=draft.selected_resource_ids,
                    ),
                    unit.drafts.projection_attachments(draft_id),
                    title_override=draft.title_override,
                )
                unit.drafts.record_preview(
                    draft_id,
                    expected_revision,
                    preview.preview_hash,
                )
                unit.commit()
        except SQLAlchemyError as exc:
            raise DatabaseError("The draft preview could not be recorded") from exc
        return DraftSnapshotPreview(
            draft_id=draft_id,
            draft_revision=expected_revision,
            snapshot=preview.snapshot,
            preview_hash=preview.preview_hash,
        )
