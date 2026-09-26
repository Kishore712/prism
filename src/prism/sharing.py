"""Explicit synthetic preparation and immutable reviewed versions.

This module is owner-side code. The model adapter never receives a Source or a
source path; its tools address version-bound evidence IDs instead.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from prism.engine import IMAGE
from prism.runtime import probe_program

FILE_LIMIT = 32 * 1024
TOTAL_LIMIT = 96 * 1024
CONFIG_LIMIT = 16 * 1024
DEMO_FILES = {
    "overview.md": "# Synthetic paired evaluation\nCompare two methods on eight invented paired observations.\nThe mean difference is 0.0225 in favour of method B.\nThese data are synthetic and do not establish scientific significance.\nUse the recorded seed-7 baseline and inspect a new seed for stability.\nThe evaluation uses 300 bootstrap resamples, not a trained model.\n",
    "observations.csv": "pair,method_b_minus_a\n1,0.03\n2,-0.01\n3,0.07\n4,0.02\n5,0.00\n6,0.05\n7,-0.02\n8,0.04\n",
    "baseline.json": json.dumps(
        {
            "kind": "recorded_synthetic_baseline",
            "source": "Prism M0 real development-runtime validation, 2026-09-17",
            "seed": 7,
            "samples": 8,
            "mean_difference": 0.022500000000000003,
            "bootstrap_interval": [0.004999999999999999, 0.0425],
            "resamples": 300,
            "synthetic": True,
        },
        indent=2,
    )
    + "\n",
    "limitations.md": "# Limitations\nEight invented observations are too few for a research conclusion.\nThe interval uses sorted bootstrap positions 7 and 292 of 300 samples.\nChanging the seed changes the resampling interval, not the observed mean.\nThe fixed evaluator embeds the eight differences shown in observations.csv.\nNo private dataset, training pipeline or unseen experiment is included.\n",
    "private-notes.txt": "Synthetic excluded canary: PRISM_UNSHARED_ORCHID_5927\nThis is an owner's preparation note. Leave it unselected to test exclusion.\n",
}


class Denied(Exception):
    def __init__(
        self,
        message="This resource is unavailable under the current access.",
        status=403,
    ):
        self.message, self.status = message, status
        super().__init__(message)


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def ident():
    return uuid.uuid4().hex


@dataclass(frozen=True)
class NamedPrincipal:
    """An immutable OIDC subject. Display claims never participate in authority."""

    issuer: str
    subject: str

    @property
    def key(self):
        return "oidc:" + digest(packed([self.issuer, self.subject]))


class Source:
    source_kind = "synthetic"

    def __init__(self, root: Path, *, names=None, strict_ancestors=False):
        self.root = Path(root)
        self.names = tuple(DEMO_FILES) if names is None else tuple(names)
        self.strict_ancestors = strict_ancestors

    @staticmethod
    def has_control(value):
        return any(
            unicodedata.category(char).startswith("C") and char not in "\t\n\r"
            for char in value
        )

    @classmethod
    def checked_name(cls, name):
        if not isinstance(name, str):
            raise Denied("Select a listed regular project file.", 400)
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or str(path) != name
            or len(name) > 240
            or len(path.parts) > 8
            or any(
                p in ("..", ".")
                or any(unicodedata.category(char).startswith("C") for char in p)
                for p in path.parts
            )
            or "\\" in name
            or "\x00" in name
        ):
            raise Denied("Select a listed regular project file.", 400)
        return path

    def _root_descriptor(self):
        if not self.strict_ancestors:
            return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if not self.root.is_absolute():
            raise Denied("The configured project root must be absolute.", 400)
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                os.close(current)
                current = next_fd
            return current
        except BaseException:
            os.close(current)
            raise

    def read_bytes(self, name: str, limit=FILE_LIMIT):
        path = self.checked_name(name)
        descriptors = []
        try:
            current = self._root_descriptor()
            descriptors.append(current)
            for component in path.parts[:-1]:
                current = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                descriptors.append(current)
            fd = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current
            )
            descriptors.append(fd)
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size > limit
            ):
                raise Denied(
                    "Links, special files and oversized files are not supported.", 400
                )
            chunks = bytearray()
            while len(chunks) <= limit:
                block = os.read(fd, min(8192, limit + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
            after = os.fstat(fd)
            if len(chunks) > limit or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_nlink,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
                raise Denied(
                    "The selected file changed while copying. Review a new candidate.",
                    409,
                )
            return bytes(chunks)
        except Denied:
            raise
        except (OSError, ValueError) as exc:
            raise Denied(
                "The selected file is unavailable or unsupported.", 400
            ) from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def read(self, name: str):
        try:
            chunks = self.read_bytes(name)
            text = chunks.decode("utf-8")
            if "\x00" in text or self.has_control(text):
                raise Denied("Only UTF-8 text evidence is supported.", 400)
            return {
                "id": digest(name)[:24],
                "name": name,
                "text": text,
                "sha256": digest(text),
                "bytes": len(chunks),
                "lines": len(text.splitlines()),
            }
        except Denied:
            raise
        except (UnicodeError, ValueError) as exc:
            raise Denied(
                "The selected file is unavailable or unsupported.", 400
            ) from exc

    def catalog(self):
        # Deliberately not a recursive filesystem browser or a private index.
        result = []
        for name in self.names:
            result.append(self.read(name))
        return result

    def freeze_files(self, names):
        if (
            not isinstance(names, list)
            or not names
            or len(names) > 8
            or len(set(names)) != len(names)
            or any(n not in self.names for n in names)
        ):
            raise Denied("Choose one or more listed files, without duplicates.", 400)
        evidence = [self.read(name) for name in sorted(names)]
        if sum(f["bytes"] for f in evidence) > TOTAL_LIMIT:
            raise Denied("The selected content exceeds the 96 KiB version limit.", 400)
        return evidence

    def freeze(self, names, purpose, mode):
        evidence = self.freeze_files(names)
        action = None
        if mode == "verify":
            # The synthetic evaluator has fixed embedded inputs. Its full readable
            # program is reviewed too; no editable project code runs on the host.
            if not {"observations.csv", "baseline.json", "limitations.md"}.issubset(
                names
            ):
                raise Denied(
                    "Verification requires observations, baseline and limitations.", 400
                )
            if any(
                f["text"] != DEMO_FILES[f["name"]]
                for f in evidence
                if f["name"] in {"observations.csv", "baseline.json", "limitations.md"}
            ):
                raise Denied(
                    "The fixed verification action requires its original synthetic inputs.",
                    400,
                )
            action = bootstrap_action()
        return {
            "schema": 1,
            "project": "Paired evaluation · synthetic",
            "purpose": purpose,
            "mode": mode,
            "files": evidence,
            "action": action,
            "source_kind": self.source_kind,
        }


def bootstrap_action():
    program = probe_program()
    return {
        "id": "bootstrap",
        "label": "Rerun synthetic bootstrap",
        "seed_min": 0,
        "seed_max": 1000,
        "required_inputs": sorted(
            {"observations.csv", "baseline.json", "limitations.md"}
        ),
        "program": program,
        "program_sha256": digest(program),
        "image": IMAGE,
        "profile": "development",
        "timeout_seconds": 10,
        "network": "none",
        "memory_mib": 128,
        "cpu": 0.5,
        "processes": 32,
        "scratch_mib": 8,
        "output_kib": 64,
        "runs_per_session": 6,
    }


def prepare_source(root: Path):
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, text in DEMO_FILES.items():
        with (root / name).open("x", encoding="utf-8") as out:
            out.write(text)


class Store:
    def __init__(self, path: Path, *, measurements=False):
        self.path, self.measurements = path, measurements
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, manifest TEXT NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_version BEFORE UPDATE OF digest,manifest ON versions
                    BEGIN SELECT RAISE(ABORT, 'Immutable version'); END;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, version TEXT NOT NULL, recipient TEXT NOT NULL,
                    mode TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL,
                    actor TEXT NOT NULL, resource TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL, value REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                    version TEXT NOT NULL, recipient_issuer TEXT NOT NULL,
                    recipient_subject TEXT NOT NULL, mode TEXT NOT NULL,
                    action TEXT, expires REAL NOT NULL, created REAL NOT NULL,
                    redeemed REAL, grant_id TEXT);
                CREATE TABLE IF NOT EXISTS grants (
                    id TEXT PRIMARY KEY, version TEXT NOT NULL,
                    recipient_issuer TEXT NOT NULL, recipient_subject TEXT NOT NULL,
                    mode TEXT NOT NULL, action TEXT, revision INTEGER NOT NULL,
                    expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS version_provenance (
                    version TEXT PRIMARY KEY, project_id TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_version_provenance
                    BEFORE UPDATE ON version_provenance
                    BEGIN SELECT RAISE(ABORT, 'Immutable version provenance'); END;
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            if "grant_id" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN grant_id TEXT")
            if "grant_revision" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN grant_revision INTEGER")
            invitation_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(invitations)")
            }
            if "origin_request" not in invitation_columns:
                db.execute("ALTER TABLE invitations ADD COLUMN origin_request TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, kind, actor, resource, outcome="allowed"):
        if isinstance(actor, NamedPrincipal):
            actor = actor.key
        db.execute(
            "INSERT INTO events(at,kind,actor,resource,outcome) VALUES(?,?,?,?,?)",
            (time.time(), kind, actor, resource, outcome),
        )
        db.execute("DELETE FROM events WHERE id <= (SELECT max(id)-1000 FROM events)")

    def measure(self, kind, value):
        if self.measurements:
            with self.connect() as db:
                db.execute(
                    "INSERT INTO measurements(at,kind,value) VALUES(?,?,?)",
                    (time.time(), kind, value),
                )
                db.execute(
                    "DELETE FROM measurements WHERE id <= (SELECT max(id)-2000 FROM measurements)"
                )

    def candidate(self, manifest, *, project_id=None):
        with self.connect() as db:
            version = self.insert_candidate(db, manifest, project_id=project_id)
        return self.owner_version(version)

    def insert_candidate(self, db, manifest, *, project_id=None):
        """Insert frozen bytes in the caller's transaction, including source mapping."""
        body, version = packed(manifest), ident()
        if db.execute("SELECT count(*) FROM versions").fetchone()[0] >= 24:
            raise Denied("The demo's 24-version limit is reached.", 429)
        db.execute(
            "INSERT INTO versions(id,digest,manifest,created) VALUES(?,?,?,?)",
            (version, digest(body), body, time.time()),
        )
        if project_id is not None:
            db.execute(
                "INSERT INTO version_provenance(version,project_id) VALUES(?,?)",
                (version, project_id),
            )
        self.event(db, "candidate_created", "owner", version)
        return version

    def owner_version(self, version):
        with self.connect() as db:
            row = db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone()
            if row is None:
                raise Denied()
            return {
                **dict(row),
                "manifest": json.loads(row["manifest"]),
                "project_id": self.version_project(db, version),
            }

    @staticmethod
    def version_project(db, version):
        row = db.execute(
            "SELECT project_id FROM version_provenance WHERE version=?", (version,)
        ).fetchone()
        if row is not None:
            return row["project_id"]
        # Older contextual handoffs already have an immutable source mapping.
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='owner_handoff_sources'"
        ).fetchone():
            row = db.execute(
                "SELECT project FROM owner_handoff_sources WHERE version=?",
                (version,),
            ).fetchone()
            if row is not None:
                return row["project"]
        return None

    def approve(self, version, expected_digest):
        with self.connect() as db:
            row = db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone()
            if (
                row is None
                or row["revoked"]
                or row["digest"] != expected_digest
                or digest(row["manifest"]) != expected_digest
            ):
                raise Denied("Approval does not match the frozen candidate.", 409)
            db.execute("UPDATE versions SET approved=1 WHERE id=?", (version,))
            self.event(db, "version_approved", "owner", version)
        return self.owner_version(version)

    def versions(self, *, owner=False):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM versions ORDER BY created DESC").fetchall()
            return [
                {
                    "id": r["id"],
                    "digest": r["digest"],
                    "approved": bool(r["approved"]),
                    "revoked": bool(r["revoked"]),
                    "created": r["created"],
                    "project": json.loads(r["manifest"])["project"],
                    "project_id": self.version_project(db, r["id"]) if owner else None,
                    "purpose": json.loads(r["manifest"])["purpose"],
                    "mode": json.loads(r["manifest"])["mode"],
                    "schema": json.loads(r["manifest"])["schema"],
                }
                for r in rows
                if owner
                or (
                    r["approved"]
                    and not r["revoked"]
                    and json.loads(r["manifest"])["schema"] in (1, 2)
                )
            ]

    def new_session(self, version, actor):
        if isinstance(actor, NamedPrincipal):
            raise Denied("A named collaborator must redeem a current invitation.", 401)
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM versions WHERE id=? AND approved=1 AND revoked=0",
                (version,),
            ).fetchone()
            if row is None or actor not in ("reviewer", "observer"):
                raise Denied()
            self.checked_manifest(row)
            if db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= 48:
                raise Denied("The demo's session limit is reached.", 429)
            session = ident()
            mode = (
                "inspect"
                if actor == "observer"
                else json.loads(row["manifest"])["mode"]
            )
            db.execute(
                "INSERT INTO sessions(id,version,recipient,mode,expires,created) VALUES(?,?,?,?,?,?)",
                (session, version, actor, mode, time.time() + 3600, time.time()),
            )
            self.event(db, "session_created", actor, session)
        return self.session(session, actor)

    def authorized(self, db, session, actor):
        recipient = actor.key if isinstance(actor, NamedPrincipal) else actor
        row = db.execute(
            "SELECT s.*,v.manifest,v.digest,g.recipient_issuer AS grant_issuer,"
            "g.recipient_subject AS grant_subject,g.mode AS grant_mode,"
            "g.version AS grant_version,g.action AS grant_action,"
            "g.revision AS current_grant_revision,"
            "g.expires AS grant_expires,g.revoked AS grant_revoked "
            "FROM sessions s JOIN versions v ON v.id=s.version "
            "LEFT JOIN grants g ON g.id=s.grant_id "
            "WHERE s.id=? AND s.recipient=? AND s.expires>? "
            "AND v.approved=1 AND v.revoked=0",
            (session, recipient, time.time()),
        ).fetchone()
        if row is None:
            raise Denied()
        manifest = self.checked_manifest(row)
        if isinstance(actor, NamedPrincipal):
            expected_action = self.grant_action(manifest, row["mode"])
            if (
                row["grant_id"] is None
                or row["grant_version"] != row["version"]
                or row["grant_issuer"] != actor.issuer
                or row["grant_subject"] != actor.subject
                or row["grant_mode"] != row["mode"]
                or row["grant_action"] != expected_action
                or row["grant_expires"] <= time.time()
                or row["grant_revoked"]
                or row["current_grant_revision"] != row["grant_revision"]
            ):
                raise Denied()
        elif row["grant_id"] is not None:
            raise Denied()
        return {**dict(row), "manifest": manifest}

    def create_invitation(
        self, version, recipient: NamedPrincipal, *, mode, expires_in
    ):
        if not isinstance(recipient, NamedPrincipal):
            raise Denied("Choose a named OIDC recipient.", 400)
        if (
            mode not in ("inspect", "verify")
            or type(expires_in) is not int
            or not 300 <= expires_in <= 86400
        ):
            raise Denied(
                "Choose an invitation lifetime from 5 minutes to 24 hours.", 400
            )
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM invitations").fetchone()[0] >= 48:
                raise Denied("The invitation limit is reached.", 429)
            row = db.execute(
                "SELECT * FROM versions WHERE id=? AND approved=1 AND revoked=0",
                (version,),
            ).fetchone()
            if row is None:
                raise Denied()
            manifest = self.checked_manifest(row)
            version_mode = manifest.get("mode")
            if mode == "verify" and version_mode != "verify":
                raise Denied("This reviewed version does not permit verification.", 409)
            action = self.grant_action(manifest, mode)
            invitation = ident()
            db.execute(
                "INSERT INTO invitations(id,token_hash,version,recipient_issuer,"
                "recipient_subject,mode,action,expires,created,redeemed,grant_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    invitation,
                    digest(token),
                    version,
                    recipient.issuer,
                    recipient.subject,
                    mode,
                    action,
                    now + expires_in,
                    now,
                    None,
                    None,
                ),
            )
            self.event(db, "invitation_created", "owner", invitation)
        return {"id": invitation, "token": token, "expires": now + expires_in}

    def invitation(self, token):
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            raise Denied("This invitation is unavailable.", 404)
        with self.connect() as db:
            row = db.execute(
                "SELECT id,recipient_issuer,recipient_subject,expires,redeemed "
                "FROM invitations WHERE token_hash=?",
                (digest(token),),
            ).fetchone()
            if (
                row is None
                or row["redeemed"] is not None
                or row["expires"] <= time.time()
            ):
                raise Denied("This invitation is unavailable.", 404)
            return dict(row)

    def invitation_activation(self, invitation):
        with self.connect() as db:
            row = db.execute(
                "SELECT i.mode,i.action,i.origin_request,v.manifest,v.digest FROM invitations i "
                "JOIN versions v ON v.id=i.version WHERE i.id=? AND i.redeemed IS NULL "
                "AND i.expires>? AND v.approved=1 AND v.revoked=0",
                (invitation, time.time()),
            ).fetchone()
            if row is None:
                raise Denied("This invitation cannot be activated.", 403)
            self._check_request_invitation(db, row["origin_request"], invitation)
            manifest = self.checked_manifest(row)
            return {
                "mode": row["mode"],
                "action": manifest.get("action") if row["mode"] == "verify" else None,
            }

    def redeem_invitation(self, token, recipient: NamedPrincipal):
        if not isinstance(token, str):
            raise Denied("This invitation cannot be redeemed.", 403)
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM invitations WHERE token_hash=?", (digest(token),)
            ).fetchone()
        if row is None:
            raise Denied("This invitation cannot be redeemed.", 403)
        return self.redeem_invitation_id(row["id"], recipient)

    def redeem_invitation_id(self, invitation, recipient: NamedPrincipal):
        if not isinstance(recipient, NamedPrincipal):
            raise Denied("A verified named identity is required.", 401)
        now = time.time()
        with self.connect() as db:
            if (
                db.execute("SELECT count(*) FROM grants").fetchone()[0] >= 48
                or db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= 48
            ):
                raise Denied("The named-session limit is reached.", 429)
            row = db.execute(
                "SELECT i.*,v.manifest,v.digest,v.approved,v.revoked AS version_revoked "
                "FROM invitations i JOIN versions v ON v.id=i.version "
                "WHERE i.id=?",
                (invitation,),
            ).fetchone()
            if (
                row is None
                or row["redeemed"] is not None
                or row["expires"] <= now
                or not row["approved"]
                or row["version_revoked"]
                or row["recipient_issuer"] != recipient.issuer
                or row["recipient_subject"] != recipient.subject
            ):
                raise Denied("This invitation cannot be redeemed.", 403)
            self._check_request_invitation(db, row["origin_request"], invitation)
            manifest = self.checked_manifest(row)
            expected_action = self.grant_action(manifest, row["mode"])
            if row["action"] != expected_action:
                raise Denied(
                    "The invitation no longer matches its reviewed action.", 409
                )
            grant, session = ident(), ident()
            if not db.execute(
                "UPDATE invitations SET redeemed=?,grant_id=? "
                "WHERE id=? AND redeemed IS NULL AND expires>?",
                (now, grant, row["id"], now),
            ).rowcount:
                raise Denied("This invitation was already redeemed.", 409)
            db.execute(
                "INSERT INTO grants VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    grant,
                    row["version"],
                    recipient.issuer,
                    recipient.subject,
                    row["mode"],
                    row["action"],
                    1,
                    row["expires"],
                    0,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO sessions(id,version,recipient,mode,expires,created,grant_id,grant_revision) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    session,
                    row["version"],
                    recipient.key,
                    row["mode"],
                    row["expires"],
                    now,
                    grant,
                    1,
                ),
            )
            self.event(db, "invitation_redeemed", recipient.key, row["id"])
        return self.session(session, recipient)

    def invitations(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT i.id,i.version,i.recipient_issuer,i.recipient_subject,"
                    "i.mode,i.action,i.expires,i.created,i.redeemed,i.grant_id,"
                    "coalesce(g.revoked,0) AS revoked,g.revision AS grant_revision "
                    "FROM invitations i LEFT JOIN grants g ON g.id=i.grant_id "
                    "ORDER BY i.created DESC LIMIT 48"
                )
            ]

    def active_grant_run(self):
        """Look up the one active fixed job and its named grant, if any."""
        with self.connect() as db:
            row = db.execute(
                "SELECT r.id,r.status,r.runtime_profile,r.runtime_resource,"
                "r.runtime_token,s.grant_id FROM runs r "
                "LEFT JOIN sessions s ON s.id=r.session "
                "WHERE r.status IN ('queued','running') "
                "ORDER BY r.created DESC LIMIT 1",
            ).fetchone()
        return dict(row) if row and row["grant_id"] else None

    def _check_request_invitation(self, db, request_id, invitation):
        if request_id is None:
            return
        origin = db.execute(
            "SELECT r.status,r.session,r.recipient_issuer,r.recipient_subject,"
            "r.source_grant,r.source_revision,r.decision_invitation,"
            "g.revision,g.expires,g.revoked FROM access_requests r "
            "JOIN grants g ON g.id=r.source_grant WHERE r.id=?",
            (request_id,),
        ).fetchone()
        if (
            origin is None
            or origin["status"] != "approved"
            or origin["decision_invitation"] != invitation
            or origin["source_revision"] != origin["revision"]
            or origin["revoked"]
            or origin["expires"] <= time.time()
        ):
            raise Denied("The invitation's originating grant is no longer active.", 403)
        self.authorized(
            db,
            origin["session"],
            NamedPrincipal(origin["recipient_issuer"], origin["recipient_subject"]),
        )

    def decide_access_request(
        self, request_id, *, decision, version=None, mode=None, expires_in=None
    ):
        if decision not in ("deny", "approve"):
            raise Denied("Choose an access decision.", 400)
        now = time.time()
        with self.connect() as db:
            request = db.execute(
                "SELECT r.*,s.version AS session_version,s.grant_id AS session_grant,"
                "s.grant_revision AS session_revision,g.recipient_issuer AS grant_issuer,"
                "g.recipient_subject AS grant_subject,g.revision AS grant_revision,"
                "g.expires AS grant_expires,g.revoked AS grant_revoked,"
                "v.manifest AS source_manifest,v.digest AS source_digest "
                "FROM access_requests r JOIN sessions s ON s.id=r.session "
                "JOIN grants g ON g.id=s.grant_id "
                "JOIN versions v ON v.id=s.version WHERE r.id=?",
                (request_id,),
            ).fetchone()
            if request is None or request["status"] != "pending":
                raise Denied(
                    "This access request is unavailable or already decided.", 409
                )
            if (
                request["source_grant"] != request["session_grant"]
                or request["source_revision"] != request["session_revision"]
                or request["source_version"] != request["session_version"]
                or request["recipient_issuer"] != request["grant_issuer"]
                or request["recipient_subject"] != request["grant_subject"]
            ):
                raise Denied("This access request no longer matches its source.", 409)
            if decision == "deny":
                if version is not None or mode is not None or expires_in is not None:
                    raise Denied("A denial does not select new access.", 400)
                db.execute(
                    "UPDATE access_requests SET status='denied',decided=? WHERE id=?",
                    (now, request_id),
                )
                self.event(db, "access_decided", "owner", request_id, "denied")
                return {"id": request_id, "status": "denied"}
            if (
                not isinstance(version, str)
                or mode not in ("inspect", "verify")
                or type(expires_in) is not int
                or not 300 <= expires_in <= 86400
            ):
                raise Denied(
                    "Choose an approved version, mode and 5-minute to 24-hour lifetime.",
                    400,
                )
            if (
                request["grant_revoked"]
                or request["grant_revision"] != request["source_revision"]
                or request["grant_expires"] <= now + expires_in
            ):
                raise Denied(
                    "The originating grant cannot cover this invitation lifetime.", 409
                )
            # Recheck the full original session/version boundary in this same
            # transaction. A revoked version or expired session cannot sponsor
            # a new invitation even if its grant row still looks current.
            self.authorized(
                db,
                request["session"],
                NamedPrincipal(
                    request["recipient_issuer"], request["recipient_subject"]
                ),
            )
            self.checked_manifest(
                {
                    "manifest": request["source_manifest"],
                    "digest": request["source_digest"],
                }
            )
            target = db.execute(
                "SELECT * FROM versions WHERE id=? AND approved=1 AND revoked=0",
                (version,),
            ).fetchone()
            if target is None:
                raise Denied("Choose a separately approved version.", 409)
            manifest = self.checked_manifest(target)
            source_project = self.version_project(db, request["source_version"])
            target_project = self.version_project(db, version)
            if version != request["source_version"] and (
                source_project is None
                or target_project is None
                or source_project != target_project
            ):
                raise Denied("The selected version belongs to another project.", 409)
            if mode == "verify" and manifest["mode"] != "verify":
                raise Denied("This reviewed version does not permit verification.", 409)
            action = self.grant_action(manifest, mode)
            if db.execute("SELECT count(*) FROM invitations").fetchone()[0] >= 48:
                raise Denied("The invitation limit is reached.", 429)
            invitation, token = ident(), secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO invitations(id,token_hash,version,recipient_issuer,"
                "recipient_subject,mode,action,expires,created,redeemed,grant_id,origin_request) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    invitation,
                    digest(token),
                    version,
                    request["recipient_issuer"],
                    request["recipient_subject"],
                    mode,
                    action,
                    now + expires_in,
                    now,
                    None,
                    None,
                    request_id,
                ),
            )
            db.execute(
                "UPDATE access_requests SET status='approved',decision_version=?,"
                "decision_mode=?,decision_action=?,decision_expires=?,"
                "decision_invitation=?,decided=? WHERE id=?",
                (version, mode, action, now + expires_in, invitation, now, request_id),
            )
            self.event(db, "access_decided", "owner", request_id, "approved")
            self.event(db, "invitation_created", "owner", invitation)
        return {
            "id": request_id,
            "status": "approved",
            "version": version,
            "mode": mode,
            "action": action,
            "expires": now + expires_in,
            "invitation": invitation,
            "token": token,
        }

    def revoke_grant(self, grant):
        with self.connect() as db:
            if not db.execute(
                "UPDATE grants SET revoked=1,revision=revision+1 WHERE id=? AND revoked=0",
                (grant,),
            ).rowcount:
                raise Denied()
            self.event(db, "grant_revoked", "owner", grant)

    @staticmethod
    def checked_manifest(row):
        manifest = json.loads(row["manifest"])
        if digest(row["manifest"]) != row["digest"]:
            raise Denied("The approved version failed its integrity check.", 409)
        if manifest.get("schema") not in (1, 2) or (
            manifest["schema"] == 2 and manifest.get("context", {}).get("schema") != 1
        ):
            raise Denied("This handoff format is not supported.", 409)
        return manifest

    @staticmethod
    def grant_action(manifest, mode):
        if mode == "inspect":
            return None
        action = manifest.get("action")
        if (
            mode != "verify"
            or not isinstance(action, dict)
            or not isinstance(action.get("id"), str)
        ):
            raise Denied("The reviewed action is unavailable.", 409)
        return action["id"]

    def background(self, session, actor):
        state = self.session(session, actor)
        return {
            "version": state["version"],
            "context": state["manifest"].get("context"),
        }

    def historical_run(self, session, actor, run_id):
        context = self.background(session, actor)["context"] or {}
        for run in context.get("runs", []):
            if run["id"] == run_id:
                return run
        raise Denied()

    def session(self, session, actor):
        with self.connect() as db:
            return self.authorized(db, session, actor)

    def evidence(self, session, actor, evidence_id, start=1, end=120):
        state = self.session(session, actor)
        if (
            type(start) is not int
            or type(end) is not int
            or start < 1
            or end < start
            or end - start >= 120
        ):
            raise Denied("Choose up to 120 numbered lines.", 400)
        for file in state["manifest"]["files"]:
            if file["id"] == evidence_id:
                lines = file["text"].splitlines()
                if start > len(lines):
                    raise Denied("That line range is unavailable.", 400)
                return {
                    "id": file["id"],
                    "name": file["name"],
                    "version": state["version"],
                    "sha256": file["sha256"],
                    "start": start,
                    "end": min(end, len(lines)),
                    "text": "\n".join(lines[start - 1 : end]),
                }
        raise Denied()

    def search(self, session, actor, query):
        if not isinstance(query, str) or not 1 <= len(query) <= 160:
            raise Denied("Enter a search query of 1 to 160 characters.", 400)
        state = self.session(session, actor)
        hits = []
        for file in state["manifest"]["files"]:
            for i, line in enumerate(file["text"].splitlines(), 1):
                if query.casefold() in line.casefold():
                    hits.append(
                        {
                            "id": file["id"],
                            "name": file["name"],
                            "line": i,
                            "text": line[:500],
                            "sha256": file["sha256"],
                            "version": state["version"],
                        }
                    )
                    if len(hits) == 12:
                        return hits
        return hits

    def revoke(self, version):
        with self.connect() as db:
            if not db.execute(
                "UPDATE versions SET revoked=1 WHERE id=?", (version,)
            ).rowcount:
                raise Denied()
            self.event(db, "version_revoked", "owner", version)

    def activity(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 200")
            ]

    def owner_sessions(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,version,recipient,mode,created FROM sessions ORDER BY created DESC LIMIT 48"
                )
            ]
