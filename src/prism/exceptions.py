from __future__ import annotations

from uuid import uuid4


class PrismError(Exception):
    """Base error with a stable public code and safe diagnostic message."""

    code = "PRISM_ERROR"
    exit_code = 2

    def __init__(self, message: str, *, correlation_id: str | None = None) -> None:
        super().__init__(message)
        self.correlation_id = correlation_id or f"cor_{uuid4().hex[:20]}"

    def as_dict(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "correlation_id": self.correlation_id,
                "retryable": False,
            }
        }


class NotFoundError(PrismError):
    """Requested Prism object does not exist or is not visible."""

    code = "NOT_FOUND"


class AuthorizationError(PrismError):
    """The current grant does not permit the requested operation."""

    code = "ACCESS_DENIED"


class ValidationError(PrismError):
    """Input or source-session data is invalid."""

    code = "INVALID_INPUT"


class UnsafeArchiveError(PrismError):
    """The selected archive violates a Phase 2 safety limit."""

    code = "UNSAFE_ARCHIVE"
    exit_code = 3


class UnsupportedSourceFormatError(PrismError):
    """The source does not match a supported, tested export variant."""

    code = "UNSUPPORTED_SOURCE_FORMAT"
    exit_code = 4


class ConversationNotFoundError(PrismError):
    """The selected conversation reference is absent from this source."""

    code = "CONVERSATION_NOT_FOUND"
    exit_code = 5


class AmbiguousBranchError(PrismError):
    """A single active conversation branch cannot be proven."""

    code = "AMBIGUOUS_BRANCH"
    exit_code = 6


class UnsupportedContentError(PrismError):
    """Visible source content cannot be represented without losing fidelity."""

    code = "UNSUPPORTED_CONTENT"
    exit_code = 7


class CaptureValidationError(PrismError):
    """Adapter output does not satisfy the canonical capture contract."""

    code = "CAPTURE_VALIDATION_FAILED"
    exit_code = 8


class CaptureWriteError(PrismError):
    """A validated capture could not be persisted atomically."""

    code = "CAPTURE_WRITE_FAILED"
    exit_code = 9


class CaptureReadError(PrismError):
    """A canonical capture could not be safely loaded."""

    code = "CAPTURE_READ_FAILED"
    exit_code = 10


class RemoteCaptureError(PrismError):
    """A remote public capture source could not be retrieved safely."""

    code = "REMOTE_CAPTURE_FAILED"
    exit_code = 11


class PreviewChangedError(PrismError):
    """A confirmation does not match the exact candidate the owner reviewed."""

    code = "PREVIEW_CHANGED"
    exit_code = 12


class DraftChangedError(PrismError):
    """The caller attempted to update or preview a stale draft revision."""

    code = "DRAFT_CHANGED"
    exit_code = 13


class DatabaseError(PrismError):
    """Durable owner state could not be read, written, migrated, or backed up."""

    code = "DATABASE_ERROR"
    exit_code = 14


class PreviewRequiredError(PrismError):
    """Publication was requested before the current draft revision was previewed."""

    code = "PREVIEW_REQUIRED"
    exit_code = 15


class SnapshotCollisionError(PrismError):
    """A content hash resolved to non-identical canonical snapshot bytes."""

    code = "SNAPSHOT_COLLISION"
    exit_code = 16


class LifecycleError(PrismError):
    """An operation is not valid for the object's current lifecycle state."""

    code = "INVALID_STATE"
    exit_code = 17


class ResourceUnavailableError(PrismError):
    """A resource is not present in the caller's authorized snapshot."""

    code = "RESOURCE_NOT_AVAILABLE"
    exit_code = 18


class UnresolvedFindingsError(PrismError):
    """Publication was blocked by projection findings the owner has not allowed."""

    code = "UNRESOLVED_FINDINGS"
    exit_code = 19


class LeakDetectedError(PrismError):
    """The post-condition rescan found a problem in the built snapshot itself.

    This should never fire in normal operation (the pre-check already ran
    over the same content); it exists as a defense-in-depth backstop and
    always fails closed — no snapshot is created.
    """

    code = "LEAK_DETECTED"
    exit_code = 22
