from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.exceptions import (
    AuthorizationError,
    LifecycleError,
    ResourceUnavailableError,
    ValidationError,
)
from prism.models.capture import MessageRole
from prism.models.projection import (
    SnapshotContent,
    SnapshotMessage,
    SnapshotResource,
)
from prism.models.recipient import UNTRUSTED_CONTENT_NOTICE
from prism.models.sharing import GrantApproval
from prism.services.projection import ProjectionService
from prism.services.publication import PublicationService
from prism.services.recipient_access import RecipientAccessService
from prism.services.sharing import SharingService

from tests.services.phase5_helpers import publish_prepared


def _future(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace(
        "+00:00", "Z"
    )


class RecipientAccessServiceTests(unittest.TestCase):
    def _invitation(
        self,
        root: Path,
        *,
        turn_number: int = 1,
        require_approval: bool = True,
    ):
        database, _, published = publish_prepared(root, turn_number=turn_number)
        sharing = SharingService(database)
        share = sharing.create_share(published.snapshot_id, "Research handoff")
        invitation = sharing.create_invitation(
            share.share.share_id,
            version=None,
            expires_at=_future(2),
            grant_expires_at=_future(24),
            require_approval=require_approval,
            recipient_hint="Alice from the lab",
        )
        return database, sharing, share, invitation

    def _approved(self, root: Path, *, turn_number: int = 1):
        database, sharing, share, invitation = self._invitation(
            root, turn_number=turn_number
        )
        service = RecipientAccessService(database, owner_label="Kishore")
        redeemed = service.redeem_invitation(invitation.invitation_token, "Alice")
        sharing.approve_grant(redeemed.grant_id)
        return database, sharing, share, service, redeemed

    def _resource_invitation(self, root: Path):
        database = PrismDatabase(root / "prism.db")
        database.initialize()
        content = SnapshotContent(
            schema_version="prism.snapshot.v1",
            title="Resource test",
            messages=(
                SnapshotMessage(
                    message_id="pubmsg_resource_user_00001",
                    turn_id="pubturn_resource_00001",
                    ordinal=1,
                    role=MessageRole.USER,
                    content="What does the resource say?",
                ),
                SnapshotMessage(
                    message_id="pubmsg_resource_assistant_01",
                    turn_id="pubturn_resource_00001",
                    ordinal=2,
                    role=MessageRole.ASSISTANT,
                    content="Use the published notes.",
                ),
            ),
            resources=(
                SnapshotResource(
                    resource_id="pubres_research_notes_00001",
                    display_name="research-notes.md",
                    media_type="text/markdown",
                    content="privacy boundary " + "abcdef" * 3_000,
                ),
            ),
        )
        canonical = ProjectionService.canonical_snapshot_bytes(content)
        content_hash = ProjectionService.snapshot_hash(content)
        with PrismUnitOfWork(database) as unit:
            snapshot, _ = unit.snapshots.create_or_reuse(
                content,
                content_hash,
                canonical,
            )
            unit.commit()
        sharing = SharingService(database)
        share = sharing.create_share(snapshot.summary.snapshot_id)
        invitation = sharing.create_invitation(
            share.share.share_id,
            version=None,
            expires_at=_future(2),
            grant_expires_at=_future(24),
            require_approval=False,
        )
        service = RecipientAccessService(database)
        redeemed = service.redeem_invitation(invitation.invitation_token, "Bob")
        return database, sharing, service, redeemed, content

    # Redemption and approval --------------------------------------------

    def test_redemption_binds_a_principal_and_waits_for_owner_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, sharing, share, invitation = self._invitation(Path(directory))
            service = RecipientAccessService(database, owner_label="Kishore")

            redeemed = service.redeem_invitation(invitation.invitation_token, "  Alice\x00 ")

            self.assertEqual(redeemed.approval, GrantApproval.PENDING)
            self.assertTrue(redeemed.principal_id.startswith("rcp_"))
            with self.assertRaises(AuthorizationError):
                service.get_manifest(redeemed.grant_id)
            grants = sharing.list_grants()
            self.assertEqual(grants[0].recipient_label, "Alice")
            self.assertEqual(grants[0].principal_id, redeemed.principal_id)
            self.assertEqual(grants[0].approval, GrantApproval.PENDING)

            sharing.approve_grant(redeemed.grant_id)
            manifest = service.get_manifest(redeemed.grant_id)
            self.assertEqual(manifest.version, 1)
            self.assertEqual(len(manifest.messages), 2)
            self.assertEqual(manifest.provenance.shared_by, "Kishore")
            self.assertEqual(manifest.provenance.notice, UNTRUSTED_CONTENT_NOTICE)
            self.assertEqual(
                manifest.provenance.content_kind, "untrusted_shared_transcript"
            )

    def test_invitation_replay_and_invalid_tokens_fail_generically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _, invitation = self._invitation(Path(directory))
            service = RecipientAccessService(database)
            service.redeem_invitation(invitation.invitation_token, "Alice")
            for token in (invitation.invitation_token, "pinv_v1_" + "x" * 43, "nope"):
                with self.subTest(token=token[:12]):
                    with self.assertRaises(AuthorizationError) as caught:
                        service.redeem_invitation(token, "Mallory")
                    self.assertIn("invalid, expired, or already used", str(caught.exception))
            with self.assertRaises(ValidationError):
                service.redeem_invitation(invitation.invitation_token, "\x00\x01")

    def test_no_approval_invitation_is_active_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _, invitation = self._invitation(
                Path(directory), require_approval=False
            )
            service = RecipientAccessService(database)
            redeemed = service.redeem_invitation(invitation.invitation_token, "Alice")
            self.assertEqual(redeemed.approval, GrantApproval.APPROVED)
            self.assertEqual(len(service.get_manifest(redeemed.grant_id).messages), 2)

    def test_denied_grant_never_becomes_usable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, sharing, _, invitation = self._invitation(Path(directory))
            service = RecipientAccessService(database)
            redeemed = service.redeem_invitation(invitation.invitation_token, "Mallory")
            sharing.deny_grant(redeemed.grant_id)
            with self.assertRaises(AuthorizationError):
                service.get_manifest(redeemed.grant_id)
            with self.assertRaises(LifecycleError):
                sharing.approve_grant(redeemed.grant_id)

    def test_unknown_grant_ids_are_denied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _, _ = self._invitation(Path(directory))
            service = RecipientAccessService(database)
            with self.assertRaises(AuthorizationError):
                service.get_manifest("grt_does_not_exist_000000")

    # Reads ---------------------------------------------------------------

    def test_query_is_deterministic_bounded_pinned_and_never_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _, service, redeemed = self._approved(Path(directory))

            first = service.query_share(
                redeemed.grant_id,
                "First NEVER_PERSIST_THIS_QUERY",
                max_results=1,
            )
            second = service.query_share(
                redeemed.grant_id,
                "First NEVER_PERSIST_THIS_QUERY",
                max_results=1,
            )
            outside = service.query_share(redeemed.grant_id, "Second", max_results=5)

            self.assertEqual(first, second)
            self.assertEqual(len(first.context_blocks), 1)
            self.assertEqual(outside.context_blocks, ())
            self.assertEqual(first.provenance.shared_by, "Kishore")
            with closing(sqlite3.connect(database.path)) as connection:
                dump = "\n".join(connection.iterdump())
                events = connection.execute(
                    "SELECT event_type, detail FROM sharing_events "
                    "WHERE event_type = 'recipient_access'"
                ).fetchall()
            self.assertNotIn("NEVER_PERSIST_THIS_QUERY", dump)
            self.assertIn(("recipient_access", "query"), events)

    def test_stopwords_are_ignored_unless_they_are_all_that_is_left(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, service, redeemed = self._approved(Path(directory))
            focused = service.query_share(redeemed.grant_id, "the of First and")
            self.assertEqual(focused.context_blocks[0].score, 1.0)
            # Only function words: falls back to them instead of raising.
            fallback = service.query_share(redeemed.grant_id, "what is the")
            self.assertIsNotNone(fallback.provenance)

    def test_query_validation_rejects_empty_oversized_and_unsearchable_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, service, redeemed = self._approved(Path(directory))
            for query in ("", "x" * 501, "___"):
                with self.subTest(query=query[:20]):
                    with self.assertRaises(ValidationError):
                        service.query_share(redeemed.grant_id, query)
            with self.assertRaises(ValidationError):
                service.query_share(redeemed.grant_id, "prototype", max_results=11)

    def test_read_message_pages_and_rejects_unknown_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, service, redeemed = self._approved(Path(directory))
            manifest = service.get_manifest(redeemed.grant_id)
            first = manifest.messages[0]
            page = service.read_message(
                redeemed.grant_id, first.message_id, max_characters=5
            )
            self.assertEqual(len(page.content), 5)
            self.assertEqual(page.next_cursor, 5)
            rest = service.read_message(
                redeemed.grant_id, first.message_id, cursor=page.next_cursor or 0
            )
            self.assertEqual(
                page.content + rest.content,
                service.read_message(redeemed.grant_id, first.message_id).content,
            )
            with self.assertRaises(ResourceUnavailableError):
                service.read_message(redeemed.grant_id, "pubmsg_not_in_this_share")
            with self.assertRaises(ValidationError):
                service.read_message(redeemed.grant_id, first.message_id, cursor=10_000)

    def test_resource_reads_are_bounded_and_hash_the_complete_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, _, service, redeemed, snapshot = self._resource_invitation(
                Path(directory)
            )
            manifest = service.get_manifest(redeemed.grant_id)
            self.assertIn("resource:read", manifest.capabilities)
            resource_id = snapshot.resources[0].resource_id

            first = service.read_resource(
                redeemed.grant_id, resource_id, max_characters=100
            )
            second = service.read_resource(
                redeemed.grant_id,
                resource_id,
                cursor=first.next_cursor or 0,
                max_characters=100,
            )

            self.assertEqual(len(first.content), 100)
            self.assertEqual(len(second.content), 100)
            self.assertEqual(
                first.content_hash,
                "sha256:"
                + hashlib.sha256(snapshot.resources[0].content.encode("utf-8")).hexdigest(),
            )
            with self.assertRaises(ResourceUnavailableError):
                service.read_resource(redeemed.grant_id, "pubres_not_in_authorized_share")
            with self.assertRaises(ValidationError):
                service.read_resource(redeemed.grant_id, resource_id, max_characters=12_001)

    # Revocation, expiry, audit ------------------------------------------------

    def test_grant_and_snapshot_revocation_fail_on_the_next_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, sharing, share, service, redeemed = self._approved(Path(directory))
            self.assertEqual(len(service.get_manifest(redeemed.grant_id).messages), 2)
            sharing.revoke_grant(redeemed.grant_id)
            with self.assertRaises(AuthorizationError):
                service.get_manifest(redeemed.grant_id)

            second = sharing.create_invitation(
                share.share.share_id,
                version=None,
                expires_at=_future(2),
                grant_expires_at=_future(24),
                require_approval=False,
            )
            other = service.redeem_invitation(second.invitation_token, "Carol")
            service.get_manifest(other.grant_id)
            PublicationService(database).revoke(share.versions[0].snapshot_id)
            with self.assertRaises(AuthorizationError):
                service.get_manifest(other.grant_id)

    def test_grants_are_isolated_from_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, sharing, share, service, alice = self._approved(Path(directory))
            second = sharing.create_invitation(
                share.share.share_id,
                version=None,
                expires_at=_future(2),
                grant_expires_at=_future(24),
                require_approval=False,
            )
            bob = service.redeem_invitation(second.invitation_token, "Bob")
            self.assertNotEqual(alice.principal_id, bob.principal_id)
            sharing.revoke_grant(alice.grant_id)
            with self.assertRaises(AuthorizationError):
                service.get_manifest(alice.grant_id)
            self.assertEqual(len(service.get_manifest(bob.grant_id).messages), 2)

    def test_expired_grant_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _, service, redeemed = self._approved(Path(directory))
            with closing(sqlite3.connect(database.path)) as connection:
                connection.execute(
                    "UPDATE grants SET expires_at = ? WHERE grant_id = ?",
                    ("2020-01-01T00:00:00Z", redeemed.grant_id),
                )
                connection.commit()
            with self.assertRaises(AuthorizationError):
                service.get_manifest(redeemed.grant_id)

    def test_audit_events_are_metadata_only_and_list_by_grant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, sharing, _, service, redeemed = self._approved(Path(directory))
            service.get_manifest(redeemed.grant_id)
            service.query_share(redeemed.grant_id, "first")
            events = sharing.list_events(grant_id=redeemed.grant_id)
            kinds = [(event.event_type, event.detail) for event in events]
            self.assertIn(("recipient_access", "manifest"), kinds)
            self.assertIn(("recipient_access", "query"), kinds)
            self.assertIn(("grant_approved", None), kinds)
            self.assertIn(("recipient_redeemed", "pending"), kinds)

    def test_online_backup_preserves_the_identity_bound_grant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, _, _, _, redeemed = self._approved(root)
            backup = database.backup(root / "restore")

            restored = RecipientAccessService(PrismDatabase(backup))
            self.assertEqual(len(restored.get_manifest(redeemed.grant_id).messages), 2)


if __name__ == "__main__":
    unittest.main()
