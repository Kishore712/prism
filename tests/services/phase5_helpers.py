from __future__ import annotations

from pathlib import Path

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.database.models import CaptureRow
from prism.models.draft import DraftSelectionRequest
from prism.models.publication import PublicationRequest, PublicationResult
from prism.services.drafts import DraftService
from prism.services.publication import PublicationService
from prism.storage import DatabaseCaptureStore

from tests.database.helpers import canonical_capture


def prepared_draft(
    root: Path,
    *,
    turn_number: int = 1,
    create_capture: bool = True,
) -> tuple[PrismDatabase, str, PublicationRequest]:
    database = PrismDatabase(root / "prism.db")
    if create_capture:
        result = DatabaseCaptureStore(database).save(canonical_capture())
        assert result.draft_id is not None
        draft_id = result.draft_id
    else:
        with PrismUnitOfWork(database) as unit:
            assert unit.session is not None
            capture_id = unit.session.query(CaptureRow.capture_id).scalar()
            assert capture_id is not None
            draft = unit.drafts.create_for_capture(capture_id)
            unit.commit()
        draft_id = draft.draft_id
    drafts = DraftService(database)
    review = drafts.review(draft_id)
    selected = drafts.replace_selection(
        draft_id,
        DraftSelectionRequest(
            expected_revision=review.draft.revision,
            selected_turn_ids=(review.turns[turn_number - 1].turn.turn_id,),
        ),
    )
    preview = drafts.preview(draft_id, selected.revision)
    return database, draft_id, PublicationRequest(
        draft_id=draft_id,
        expected_revision=selected.revision,
        expected_preview_hash=preview.preview_hash,
    )


def publish_prepared(
    root: Path,
    *,
    turn_number: int = 1,
    create_capture: bool = True,
) -> tuple[PrismDatabase, str, PublicationResult]:
    database, draft_id, request = prepared_draft(
        root,
        turn_number=turn_number,
        create_capture=create_capture,
    )
    return database, draft_id, PublicationService(database).publish(request)
