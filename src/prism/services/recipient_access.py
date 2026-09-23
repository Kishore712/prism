"""Recipient-only access to one immutable, authorized share version.

Authorization is anchored to a *grant* bound to an authenticated recipient
principal. The grant identifier is resolved from a verified OAuth access token
by the MCP adapter; it is never a model-supplied argument and never derived
from transport session state. Every read re-verifies the complete chain
grant -> approval -> share version -> share -> snapshot.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from ..clock import utc_now
from ..database import PrismDatabase, PrismUnitOfWork
from ..exceptions import (
    AuthorizationError,
    DatabaseError,
    LifecycleError,
    NotFoundError,
    ResourceUnavailableError,
    ValidationError,
)
from ..models.projection import SnapshotContent
from ..models.publication import SnapshotStatus
from ..models.recipient import (
    ContextBlock,
    ContextSourceType,
    ManifestMessage,
    ManifestResource,
    MessagePage,
    Provenance,
    RedeemedInvitation,
    ResourcePage,
    ShareManifest,
    ShareQueryResult,
)
from ..models.sharing import (
    GrantApproval,
    GrantRecord,
    GrantStatus,
    InvitationStatus,
    ShareRecord,
    ShareStatus,
    ShareVersionRecord,
    ShareVersionStatus,
)
from ..security import capability_hash, is_capability, new_capability


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_MAX_QUERY_LENGTH = 500
_MAX_QUERY_RESULTS = 10
_MAX_CONTEXT_CHARACTERS = 4_000
_MAX_PAGE_CHARACTERS = 12_000
_MAX_LABEL_CHARACTERS = 80

# Function words that would otherwise match nearly every message.
_STOPWORDS = frozenset(
    """a about after all also an and any are as at be been but by can could did do
    does for from had has have how i if in into is it its just may me more most my
    no not of on one or our out over should so some than that the their them then
    there these they this to up us was we were what when where which who why will
    with would you your""".split()
)


@dataclass(frozen=True)
class _AuthorizedShare:
    grant: GrantRecord
    share: ShareRecord
    version: ShareVersionRecord
    content: SnapshotContent


class RecipientAccessService:
    """Serve one pinned snapshot to one approved, authenticated recipient."""

    def __init__(
        self,
        database: PrismDatabase,
        *,
        owner_label: str = "the Prism owner",
    ) -> None:
        label = self.clean_label(owner_label, limit=200)
        if not label:
            raise ValueError("owner_label must not be empty")
        self._database = database
        self._owner_label = label
        self._database.initialize()

    @property
    def owner_label(self) -> str:
        return self._owner_label

    # Redemption ----------------------------------------------------------

    def redeem_invitation(
        self,
        invitation_token: str,
        display_name: str,
    ) -> RedeemedInvitation:
        """Consume a one-time invitation into a grant for a new principal.

        The grant starts ``pending`` when the invitation requires owner
        approval; it is unusable until the owner approves it.
        """

        label = self.clean_label(display_name, limit=_MAX_LABEL_CHARACTERS)
        if not label:
            raise ValidationError("A recipient name of 1 to 80 characters is required")
        if not is_capability(invitation_token, "pinv_v1"):
            raise self._invite_denied()
        now_text = utc_now()
        discarded_grant_secret = new_capability("pgrt_v1")
        principal_id = f"rcp_{uuid4().hex[:26]}"
        try:
            with PrismUnitOfWork(self._database) as unit:
                invitation = unit.invitations.find_by_hash(
                    capability_hash("Invitation", invitation_token)
                )
                if (
                    invitation is None
                    or invitation.status is not InvitationStatus.PENDING
                    or invitation.expires_at <= now_text
                    or invitation.grant_expires_at <= now_text
                ):
                    raise self._invite_denied()
                version = unit.shares.get_version_by_id(invitation.share_version_id)
                share = unit.shares.get(version.share_id)
                if (
                    share.share.status is not ShareStatus.ACTIVE
                    or version.status is not ShareVersionStatus.ACTIVE
                ):
                    raise self._invite_denied()
                unit.snapshots.require_active(version.snapshot_id)
                redeemed = unit.invitations.mark_redeemed(
                    invitation.invitation_id,
                    now_text,
                )
                approval = (
                    GrantApproval.PENDING
                    if redeemed.require_approval
                    else GrantApproval.APPROVED
                )
                grant = unit.grants.create(
                    redeemed.invitation_id,
                    redeemed.share_version_id,
                    capability_hash("Grant", discarded_grant_secret),
                    discarded_grant_secret[-8:],
                    redeemed.grant_expires_at,
                    approval=approval,
                    principal_id=principal_id,
                    recipient_label=label,
                )
                unit.sharing_events.record(
                    "recipient_redeemed",
                    snapshot_id=version.snapshot_id,
                    share_id=version.share_id,
                    share_version_id=version.share_version_id,
                    invitation_id=redeemed.invitation_id,
                    grant_id=grant.grant_id,
                    detail=approval.value,
                )
                unit.commit()
                return RedeemedInvitation(
                    grant_id=grant.grant_id,
                    principal_id=principal_id,
                    approval=approval,
                    expires_at=grant.expires_at,
                )
        except SQLAlchemyError as exc:
            raise DatabaseError("The invitation could not be redeemed") from exc
        except (LifecycleError, NotFoundError) as exc:
            raise self._invite_denied() from exc

    def grant_approval(self, grant_id: str) -> tuple[GrantApproval, GrantStatus]:
        """Return the owner-approval and lifecycle state of a redeemed grant."""

        try:
            with PrismUnitOfWork(self._database) as unit:
                grant = unit.grants.get(grant_id)
                return grant.approval, grant.status
        except SQLAlchemyError as exc:
            raise DatabaseError("The grant state could not be read") from exc
        except NotFoundError as exc:
            raise AuthorizationError("This Prism share is unavailable") from exc

    def verify_grant(self, grant_id: str) -> GrantRecord:
        """Fail closed unless the entire chain for ``grant_id`` is currently valid."""

        return self._authorize(grant_id, None).grant

    # Recipient reads --------------------------------------------------------

    def get_manifest(self, grant_id: str) -> ShareManifest:
        authorized = self._authorize(grant_id, "manifest")
        content = authorized.content
        capabilities = ["manifest:read", "message:read", "context:query"]
        if content.resources:
            capabilities.append("resource:read")
        return ShareManifest(
            provenance=self._provenance(authorized),
            title=content.title,
            version=authorized.version.version,
            capabilities=tuple(capabilities),
            message_count=len(content.messages),
            resource_count=len(content.resources),
            messages=tuple(
                ManifestMessage(
                    message_id=message.message_id,
                    ordinal=message.ordinal,
                    role=message.role,
                    preview=self._preview(message.content),
                    character_count=len(message.content),
                )
                for message in content.messages
            ),
            resources=tuple(
                ManifestResource(
                    resource_id=resource.resource_id,
                    display_name=resource.display_name,
                    media_type=resource.media_type,
                )
                for resource in content.resources
            ),
            expires_at=authorized.grant.expires_at,
        )

    def query_share(
        self,
        grant_id: str,
        query: str,
        *,
        max_results: int = 5,
    ) -> ShareQueryResult:
        query = query.strip()
        if not query or len(query) > _MAX_QUERY_LENGTH:
            raise ValidationError("Query must contain 1 to 500 characters")
        if max_results < 1 or max_results > _MAX_QUERY_RESULTS:
            raise ValidationError("max_results must be between 1 and 10")
        all_tokens = tuple(dict.fromkeys(self._tokens(query)))
        if not all_tokens:
            raise ValidationError("Query must contain at least one searchable term")
        # Drop function words unless nothing else is left.
        query_tokens = (
            tuple(token for token in all_tokens if token not in _STOPWORDS) or all_tokens
        )
        authorized = self._authorize(grant_id, "query")
        candidates: list[tuple[float, int, ContextBlock]] = []
        for message in authorized.content.messages:
            score = self._score(query, query_tokens, message.content)
            if score > 0:
                candidates.append(
                    (
                        score,
                        message.ordinal,
                        ContextBlock(
                            block_id=self._block_id("message", message.message_id),
                            source_type=ContextSourceType.MESSAGE,
                            source_id=message.message_id,
                            content=self._excerpt(message.content, query_tokens),
                            score=score,
                            role=message.role,
                        ),
                    )
                )
        message_count = len(authorized.content.messages)
        for resource_index, resource in enumerate(authorized.content.resources, start=1):
            score = self._score(query, query_tokens, resource.content)
            if score > 0:
                candidates.append(
                    (
                        score,
                        message_count + resource_index,
                        ContextBlock(
                            block_id=self._block_id("resource", resource.resource_id),
                            source_type=ContextSourceType.RESOURCE,
                            source_id=resource.resource_id,
                            content=self._excerpt(resource.content, query_tokens),
                            score=score,
                            display_name=resource.display_name,
                        ),
                    )
                )
        candidates.sort(key=lambda item: (-item[0], item[1], item[2].source_id))
        selected = candidates[:max_results]
        return ShareQueryResult(
            provenance=self._provenance(authorized),
            context_blocks=tuple(item[2] for item in selected),
            truncated=len(candidates) > max_results,
        )

    def read_message(
        self,
        grant_id: str,
        message_id: str,
        *,
        cursor: int = 0,
        max_characters: int = _MAX_PAGE_CHARACTERS,
    ) -> MessagePage:
        self._validate_page(cursor, max_characters)
        authorized = self._authorize(grant_id, "message")
        message = next(
            (item for item in authorized.content.messages if item.message_id == message_id),
            None,
        )
        if message is None:
            raise ResourceUnavailableError(
                "The requested message is not available in this Prism share"
            )
        if cursor > len(message.content):
            raise ValidationError("cursor is outside the message")
        end = min(len(message.content), cursor + max_characters)
        return MessagePage(
            provenance=self._provenance(authorized),
            message_id=message.message_id,
            ordinal=message.ordinal,
            role=message.role,
            cursor=cursor,
            content=message.content[cursor:end],
            next_cursor=end if end < len(message.content) else None,
        )

    def read_resource(
        self,
        grant_id: str,
        resource_id: str,
        *,
        cursor: int = 0,
        max_characters: int = _MAX_PAGE_CHARACTERS,
    ) -> ResourcePage:
        self._validate_page(cursor, max_characters)
        authorized = self._authorize(grant_id, "resource")
        resource = next(
            (
                item
                for item in authorized.content.resources
                if item.resource_id == resource_id
            ),
            None,
        )
        if resource is None:
            raise ResourceUnavailableError(
                "The requested resource is not available in this Prism share"
            )
        if cursor > len(resource.content):
            raise ValidationError("cursor is outside the resource")
        end = min(len(resource.content), cursor + max_characters)
        return ResourcePage(
            provenance=self._provenance(authorized),
            resource_id=resource.resource_id,
            display_name=resource.display_name,
            media_type=resource.media_type,
            cursor=cursor,
            content=resource.content[cursor:end],
            next_cursor=end if end < len(resource.content) else None,
            content_hash="sha256:"
            + hashlib.sha256(resource.content.encode("utf-8")).hexdigest(),
        )

    # Internals ---------------------------------------------------------------

    def _authorize(self, grant_id: str, tool: str | None) -> _AuthorizedShare:
        denied = AuthorizationError("This Prism share is unavailable or has expired")
        now = utc_now()
        try:
            with PrismUnitOfWork(self._database) as unit:
                grant = unit.grants.get(grant_id)
                if (
                    grant.status is not GrantStatus.ACTIVE
                    or grant.approval is not GrantApproval.APPROVED
                    or grant.expires_at <= now
                ):
                    raise denied
                version = unit.shares.get_version_by_id(grant.share_version_id)
                share = unit.shares.get(version.share_id)
                if (
                    share.share.status is not ShareStatus.ACTIVE
                    or version.status is not ShareVersionStatus.ACTIVE
                ):
                    raise denied
                snapshot = unit.snapshots.get(version.snapshot_id)
                if (
                    snapshot.summary.status is not SnapshotStatus.ACTIVE
                    or snapshot.content is None
                ):
                    raise denied
                if tool is not None:
                    # Metadata only: which tool, for which grant. Never the query,
                    # the result, or any snapshot text.
                    unit.sharing_events.record(
                        "recipient_access",
                        snapshot_id=version.snapshot_id,
                        share_id=version.share_id,
                        share_version_id=version.share_version_id,
                        grant_id=grant.grant_id,
                        detail=tool,
                    )
                    unit.commit()
                return _AuthorizedShare(
                    grant=grant,
                    share=share.share,
                    version=version,
                    content=snapshot.content,
                )
        except SQLAlchemyError as exc:
            raise DatabaseError("The recipient authorization check failed") from exc
        except (LifecycleError, NotFoundError) as exc:
            raise denied from exc

    def _provenance(self, authorized: _AuthorizedShare) -> Provenance:
        return Provenance(
            shared_by=self._owner_label,
            share_title=authorized.content.title,
            share_version=authorized.version.version,
        )

    @staticmethod
    def clean_label(value: str, *, limit: int) -> str:
        """Normalize a display label: NFC, no control characters, bounded."""

        normalized = unicodedata.normalize("NFC", value)
        collapsed = " ".join(_CONTROL.sub(" ", normalized).split())
        return collapsed[:limit]

    @staticmethod
    def _validate_page(cursor: int, max_characters: int) -> None:
        if cursor < 0:
            raise ValidationError("cursor must be zero or greater")
        if max_characters < 1 or max_characters > _MAX_PAGE_CHARACTERS:
            raise ValidationError("max_characters must be between 1 and 12000")

    @staticmethod
    def _invite_denied() -> AuthorizationError:
        return AuthorizationError("The invitation is invalid, expired, or already used")

    @staticmethod
    def _preview(content: str) -> str:
        collapsed = " ".join(content.split())
        return collapsed if len(collapsed) <= 200 else collapsed[:199] + "…"

    @staticmethod
    def _tokens(value: str) -> tuple[str, ...]:
        return tuple(token.casefold() for token in _WORD.findall(value))

    @classmethod
    def _score(
        cls,
        query: str,
        query_tokens: tuple[str, ...],
        content: str,
    ) -> float:
        content_tokens = set(cls._tokens(content))
        overlap = sum(token in content_tokens for token in query_tokens)
        if overlap == 0:
            return 0.0
        base = overlap / len(query_tokens)
        phrase_bonus = 0.25 if query.casefold() in content.casefold() else 0.0
        return round(min(2.0, base + phrase_bonus), 6)

    @classmethod
    def _excerpt(cls, content: str, query_tokens: tuple[str, ...]) -> str:
        if len(content) <= _MAX_CONTEXT_CHARACTERS:
            return content
        folded = content.casefold()
        positions = [folded.find(token) for token in query_tokens]
        positions = [position for position in positions if position >= 0]
        center = min(positions) if positions else 0
        start = max(0, center - _MAX_CONTEXT_CHARACTERS // 4)
        end = min(len(content), start + _MAX_CONTEXT_CHARACTERS)
        start = max(0, end - _MAX_CONTEXT_CHARACTERS)
        excerpt = content[start:end]
        if start:
            excerpt = "…" + excerpt[1:]
        if end < len(content):
            excerpt = excerpt[:-1] + "…"
        return excerpt

    @staticmethod
    def _block_id(source_type: str, source_id: str) -> str:
        digest = hashlib.sha256(
            b"PrismContextBlock-v1\0"
            + source_type.encode("ascii")
            + b"\0"
            + source_id.encode("utf-8")
        ).hexdigest()[:26]
        return f"blk_{digest}"
