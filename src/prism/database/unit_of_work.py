"""Transaction boundary shared by owner-state repositories."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session

from ..repositories import (
    CaptureCandidateRepository,
    CaptureRepository,
    DraftFindingDecisionRepository,
    DraftRepository,
    GrantRepository,
    InvitationRepository,
    OAuthRepository,
    PublicationEventRepository,
    ReceiptRepository,
    ShareRepository,
    SharingEventRepository,
    SnapshotRepository,
    TaintedEventRepository,
    ToolEventRepository,
)
from .engine import PrismDatabase


class PrismUnitOfWork:
    def __init__(self, database: PrismDatabase) -> None:
        self._database = database
        self.session: Session | None = None
        self.candidates: CaptureCandidateRepository
        self.captures: CaptureRepository
        self.drafts: DraftRepository
        self.finding_decisions: DraftFindingDecisionRepository
        self.snapshots: SnapshotRepository
        self.publications: PublicationEventRepository
        self.receipts: ReceiptRepository
        self.oauth: OAuthRepository
        self.shares: ShareRepository
        self.invitations: InvitationRepository
        self.grants: GrantRepository
        self.sharing_events: SharingEventRepository
        self.tool_events: ToolEventRepository
        self.tainted_events: TaintedEventRepository
        self._committed = False

    def __enter__(self) -> "PrismUnitOfWork":
        self.session = self._database.session()
        self.candidates = CaptureCandidateRepository(self.session)
        self.captures = CaptureRepository(self.session)
        self.drafts = DraftRepository(self.session)
        self.finding_decisions = DraftFindingDecisionRepository(self.session)
        self.snapshots = SnapshotRepository(self.session)
        self.publications = PublicationEventRepository(self.session)
        self.receipts = ReceiptRepository(self.session)
        self.oauth = OAuthRepository(self.session)
        self.shares = ShareRepository(self.session)
        self.invitations = InvitationRepository(self.session)
        self.grants = GrantRepository(self.session)
        self.sharing_events = SharingEventRepository(self.session)
        self.tool_events = ToolEventRepository(self.session)
        self.tainted_events = TaintedEventRepository(self.session)
        self._committed = False
        return self

    def commit(self) -> None:
        assert self.session is not None
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self.session is not None
        try:
            if exception_type is not None or not self._committed:
                self.session.rollback()
        finally:
            self.session.close()
