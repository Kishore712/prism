from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prism.database import PrismDatabase
from prism.exceptions import AuthorizationError, LifecycleError, ValidationError
from prism.models.publication import SnapshotStatus
from prism.models.sharing import (
    GrantStatus,
    InvitationStatus,
    ShareStatus,
    ShareVersionStatus,
)
from prism.services.publication import PublicationService
from prism.services.sharing import SharingService

from tests.services.phase5_helpers import publish_prepared


def _future(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace(
        "+00:00", "Z"
    )


class SharingServiceTests(unittest.TestCase):
    def _published(self, root: Path):
        database, _, published = publish_prepared(root)
        return database, published

    def _invitation(self, root: Path):
        database, published = self._published(root)
        service = SharingService(database)
        share = service.create_share(published.snapshot_id, "Research handoff")
        issued = service.create_invitation(
            share.share.share_id,
            version=None,
            expires_at=_future(2),
            grant_expires_at=_future(24),
        )
        return database, service, share, issued

    def test_share_invitation_redemption_and_grant_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, service, share, invitation = self._invitation(Path(directory))
            grant = service.redeem_invitation(invitation.invitation_token)
            restarted = SharingService(database)
            authorized = restarted.authorize_grant(grant.grant_token)

            self.assertEqual(invitation.invitation.status, InvitationStatus.PENDING)
            self.assertEqual(grant.grant.status, GrantStatus.ACTIVE)
            self.assertEqual(authorized.share.share_id, share.share.share_id)
            self.assertEqual(authorized.share_version.version, 1)
            self.assertEqual(
                authorized.snapshot.snapshot_id,
                share.versions[0].snapshot_id,
            )
            self.assertEqual(
                restarted.list_invitations()[0].invitation_id,
                invitation.invitation.invitation_id,
            )
            self.assertEqual(restarted.list_grants()[0].grant_id, grant.grant.grant_id)
            with closing(sqlite3.connect(database.path)) as connection:
                invitation_hash = connection.execute(
                    "SELECT token_hash FROM invitations"
                ).fetchone()[0]
                grant_hash = connection.execute(
                    "SELECT token_hash FROM grants"
                ).fetchone()[0]
                event_types = {
                    row[0]
                    for row in connection.execute(
                        "SELECT event_type FROM sharing_events"
                    )
                }
            self.assertNotEqual(invitation_hash, invitation.invitation_token)
            self.assertNotEqual(grant_hash, grant.grant_token)
            self.assertNotIn(invitation.invitation_token, invitation_hash)
            self.assertNotIn(grant.grant_token, grant_hash)
            self.assertTrue(
                {
                    "share_created",
                    "invitation_created",
                    "invitation_redeemed",
                    "grant_authorized",
                }.issubset(event_types)
            )

    def test_invitation_is_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, service, _, invitation = self._invitation(Path(directory))
            service.redeem_invitation(invitation.invitation_token)
            with self.assertRaises(AuthorizationError):
                service.redeem_invitation(invitation.invitation_token)

    def test_online_backup_restores_complete_phase5_authorization_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, service, share, invitation = self._invitation(root)
            grant = service.redeem_invitation(invitation.invitation_token)
            backup = database.backup(root / "restored")

            restored = SharingService(PrismDatabase(backup))
            authorization = restored.authorize_grant(grant.grant_token)

            self.assertEqual(authorization.share.share_id, share.share.share_id)
            self.assertEqual(
                authorization.snapshot.snapshot_id,
                share.versions[0].snapshot_id,
            )

    def test_unknown_or_malformed_tokens_return_generic_denials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, _, _ = self._invitation(Path(directory))
            service = SharingService(database)
            with self.assertRaisesRegex(AuthorizationError, "invalid, expired"):
                service.redeem_invitation("not-an-invitation-token")
            with self.assertRaisesRegex(AuthorizationError, "invalid, expired"):
                service.redeem_invitation("pinv_v1_" + "x" * 43)
            with self.assertRaisesRegex(AuthorizationError, "invalid, expired"):
                service.authorize_grant("pgrt_v1_" + "x" * 43)

    def test_expired_invitation_cannot_be_redeemed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, service, _, invitation = self._invitation(Path(directory))
            with closing(sqlite3.connect(database.path)) as connection:
                connection.execute(
                    "UPDATE invitations SET expires_at = ? WHERE invitation_id = ?",
                    ("2020-01-01T00:00:00Z", invitation.invitation.invitation_id),
                )
                connection.commit()
            with self.assertRaises(AuthorizationError):
                service.redeem_invitation(invitation.invitation_token)

    def test_expiry_contract_rejects_invalid_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, published = self._published(Path(directory))
            service = SharingService(database)
            share = service.create_share(published.snapshot_id)
            with self.assertRaises(ValidationError):
                service.create_invitation(
                    share.share.share_id,
                    version=None,
                    expires_at=_future(-1),
                    grant_expires_at=_future(10),
                )
            with self.assertRaises(ValidationError):
                service.create_invitation(
                    share.share.share_id,
                    version=None,
                    expires_at=_future(10),
                    grant_expires_at=_future(2),
                )

    def test_grant_revocation_takes_effect_on_next_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, service, _, invitation = self._invitation(Path(directory))
            grant = service.redeem_invitation(invitation.invitation_token)
            service.authorize_grant(grant.grant_token)
            revoked = service.revoke_grant(grant.grant.grant_id)

            self.assertEqual(revoked.status, GrantStatus.REVOKED)
            with self.assertRaises(AuthorizationError):
                service.authorize_grant(grant.grant_token)

    def test_expired_grant_fails_without_mutating_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, service, share, invitation = self._invitation(Path(directory))
            grant = service.redeem_invitation(invitation.invitation_token)
            with closing(sqlite3.connect(database.path)) as connection:
                connection.execute(
                    "UPDATE grants SET expires_at = ? WHERE grant_id = ?",
                    ("2020-01-01T00:00:00Z", grant.grant.grant_id),
                )
                connection.commit()

            with self.assertRaises(AuthorizationError):
                service.authorize_grant(grant.grant_token)
            snapshot = PublicationService(database).get(share.versions[0].snapshot_id)
            self.assertEqual(snapshot.summary.status, SnapshotStatus.ACTIVE)
            self.assertIsNotNone(snapshot.content)

    def test_pending_invitation_can_be_revoked_but_redeemed_one_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, service, _, invitation = self._invitation(Path(directory))
            revoked = service.revoke_invitation(invitation.invitation.invitation_id)
            self.assertEqual(revoked.status, InvitationStatus.REVOKED)
            with self.assertRaises(AuthorizationError):
                service.redeem_invitation(invitation.invitation_token)

        with tempfile.TemporaryDirectory() as directory:
            _, service, _, invitation = self._invitation(Path(directory))
            service.redeem_invitation(invitation.invitation_token)
            with self.assertRaises(LifecycleError):
                service.revoke_invitation(invitation.invitation.invitation_id)

    def test_share_revocation_cascades_to_version_invitation_and_grant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, service, share, invitation = self._invitation(Path(directory))
            grant = service.redeem_invitation(invitation.invitation_token)
            revoked = service.revoke_share(share.share.share_id)

            self.assertEqual(revoked.share.status, ShareStatus.REVOKED)
            self.assertTrue(
                all(
                    version.status is ShareVersionStatus.REVOKED
                    for version in revoked.versions
                )
            )
            with self.assertRaises(AuthorizationError):
                service.authorize_grant(grant.grant_token)
            with closing(sqlite3.connect(database.path)) as connection:
                status = connection.execute(
                    "SELECT status FROM grants WHERE grant_id = ?",
                    (grant.grant.grant_id,),
                ).fetchone()[0]
            self.assertEqual(status, GrantStatus.REVOKED.value)

    def test_invites_are_pinned_to_requested_share_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, first = self._published(root)
            _, _, second = publish_prepared(
                root,
                turn_number=2,
                create_capture=False,
            )
            service = SharingService(database)
            share = service.create_share(first.snapshot_id)
            share = service.add_version(share.share.share_id, second.snapshot_id)
            version_one_invite = service.create_invitation(
                share.share.share_id,
                version=1,
                expires_at=_future(2),
                grant_expires_at=_future(24),
            )
            latest_invite = service.create_invitation(
                share.share.share_id,
                version=None,
                expires_at=_future(2),
                grant_expires_at=_future(24),
            )
            first_auth = service.authorize_grant(
                service.redeem_invitation(version_one_invite.invitation_token).grant_token
            )
            latest_auth = service.authorize_grant(
                service.redeem_invitation(latest_invite.invitation_token).grant_token
            )

            self.assertEqual(first_auth.share_version.version, 1)
            self.assertEqual(first_auth.snapshot.snapshot_id, first.snapshot_id)
            self.assertEqual(latest_auth.share_version.version, 2)
            self.assertEqual(latest_auth.snapshot.snapshot_id, second.snapshot_id)

    def test_revoking_one_share_version_does_not_move_or_revoke_another(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, first = self._published(root)
            _, _, second = publish_prepared(
                root,
                turn_number=2,
                create_capture=False,
            )
            service = SharingService(database)
            share = service.create_share(first.snapshot_id)
            share = service.add_version(share.share.share_id, second.snapshot_id)
            first_invite = service.create_invitation(
                share.share.share_id,
                version=1,
                expires_at=_future(2),
                grant_expires_at=_future(24),
            )
            second_invite = service.create_invitation(
                share.share.share_id,
                version=2,
                expires_at=_future(2),
                grant_expires_at=_future(24),
            )
            first_grant = service.redeem_invitation(first_invite.invitation_token)
            second_grant = service.redeem_invitation(second_invite.invitation_token)

            state = service.revoke_share_version(share.share.share_id, 1)

            self.assertEqual(state.share.status, ShareStatus.ACTIVE)
            self.assertEqual(state.versions[0].status, ShareVersionStatus.REVOKED)
            self.assertEqual(state.versions[1].status, ShareVersionStatus.ACTIVE)
            with self.assertRaises(AuthorizationError):
                service.authorize_grant(first_grant.grant_token)
            authorized = service.authorize_grant(second_grant.grant_token)
            self.assertEqual(authorized.share_version.version, 2)
            self.assertEqual(authorized.snapshot.snapshot_id, second.snapshot_id)

    def test_snapshot_purge_revokes_every_dependent_access_and_keeps_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, service, share, invitation = self._invitation(root)
            grant = service.redeem_invitation(invitation.invitation_token)
            snapshot_id = share.versions[0].snapshot_id
            purged = PublicationService(database).revoke(snapshot_id, purge=True)

            self.assertEqual(purged.summary.status, SnapshotStatus.PURGED)
            self.assertIsNone(purged.content)
            self.assertEqual(purged.summary.byte_size, 0)
            with self.assertRaises(AuthorizationError):
                service.authorize_grant(grant.grant_token)
            with closing(sqlite3.connect(database.path)) as connection:
                snapshot_count = connection.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0]
                payload_count = connection.execute(
                    "SELECT COUNT(*) FROM snapshot_payloads WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0]
            self.assertEqual(snapshot_count, 1)
            self.assertEqual(payload_count, 0)

    def test_snapshot_revoke_preserves_payload_but_blocks_dependent_grant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, service, share, invitation = self._invitation(Path(directory))
            grant = service.redeem_invitation(invitation.invitation_token)
            snapshot_id = share.versions[0].snapshot_id
            revoked = PublicationService(database).revoke(snapshot_id)

            self.assertEqual(revoked.summary.status, SnapshotStatus.REVOKED)
            self.assertIsNotNone(revoked.content)
            self.assertGreater(revoked.summary.byte_size, 0)
            with self.assertRaises(AuthorizationError):
                service.authorize_grant(grant.grant_token)


if __name__ == "__main__":
    unittest.main()
