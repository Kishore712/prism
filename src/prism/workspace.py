"""Session-owned text revisions, never a path into an owner's filesystem."""

import copy
import difflib
import io
import json
import re
import time
import zipfile

from prism.sharing import FILE_LIMIT, TOTAL_LIMIT, Denied, Source, digest, packed

EDIT_LIMIT = 20
LINE_LIMIT = 2000
FILE_ID = re.compile(r"^[0-9a-f]{24}$")


def checked_text(text):
    if not isinstance(text, str):
        raise Denied("Enter UTF-8 text for an approved editable file.", 400)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise Denied("Enter valid UTF-8 text.", 400) from None
    if (
        size > FILE_LIMIT
        or len(text.splitlines()) > LINE_LIMIT
        or Source.has_control(text)
    ):
        raise Denied("Workspace text exceeds its size, line or text-format limit.", 400)
    return size


def with_workspace(
    manifest, editable, *, check=False, python_entrypoint=None, python_outputs=None
):
    """Owner preparation only; rights are frozen into the approval digest."""
    if (
        not isinstance(editable, list)
        or not editable
        or any(not isinstance(n, str) for n in editable)
    ):
        raise Denied("Explicitly select at least one editable shared file.", 400)
    files = {file["name"]: file for file in manifest["files"]}
    if len(set(editable)) != len(editable) or any(
        name not in files for name in editable
    ):
        raise Denied("Editable files must be distinct selected shared files.", 400)
    for name in editable:
        checked_text(files[name]["text"])
    result = copy.deepcopy(manifest)
    action = result.get("action") if check else None
    if check and (not isinstance(action, dict) or action.get("id") != "json-check"):
        raise Denied(
            "Workspace validation requires the configured fixed JSON checker.", 400
        )
    result.update(schema=3, mode="continue", action=action)
    result["workspace"] = {
        "schema": 2 if check else 1,
        "editable": sorted(files[name]["id"] for name in editable),
    }
    if python_entrypoint is not None or python_outputs:
        from prism.computation import python_action

        if (
            check
            or python_entrypoint not in editable
            or any(n not in editable for n in (python_outputs or []))
        ):
            raise Denied(
                "Python execution requires an editable script and editable declared outputs; choose one execution capability.",
                400,
            )
        result["action"] = python_action(
            result["files"], python_entrypoint, python_outputs
        )
        result["workspace"]["schema"] = 3
    validate_manifest(result)
    return result


def validate_manifest(manifest):
    policy = manifest.get("workspace")
    if (
        manifest.get("mode") != "continue"
        or not isinstance(policy, dict)
        or set(policy) != {"schema", "editable"}
        or type(policy["schema"]) is not int
        or policy["schema"] not in (1, 2, 3)
        or not isinstance(policy["editable"], list)
        or not 1 <= len(policy["editable"]) <= 8
        or any(
            not isinstance(value, str) or not FILE_ID.fullmatch(value)
            for value in policy["editable"]
        )
        or len(set(policy["editable"])) != len(policy["editable"])
    ):
        raise Denied("The reviewed workspace policy is unavailable.", 409)
    if policy["schema"] == 1 and manifest.get("action") is not None:
        raise Denied("This editing policy grants no execution permission.", 409)
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise Denied("The reviewed workspace files are unavailable.", 409)
    ids, names, size = set(), set(), 0
    for file in files:
        if (
            not isinstance(file, dict)
            or not isinstance(file.get("id"), str)
            or not FILE_ID.fullmatch(file["id"])
        ):
            raise Denied("The reviewed workspace files are unavailable.", 409)
        Source.checked_name(file.get("name"))
        if file["id"] in ids or file["name"] in names:
            raise Denied("The reviewed workspace files are ambiguous.", 409)
        ids.add(file["id"])
        names.add(file["name"])
        text = file.get("text")
        if not isinstance(text, str) or digest(text) != file.get("sha256"):
            raise Denied("The reviewed workspace bytes do not match.", 409)
        size += len(text.encode("utf-8"))
        if len(text.encode("utf-8")) > FILE_LIMIT:
            raise Denied("The reviewed workspace file exceeds its limit.", 409)
        if file["id"] in policy["editable"]:
            checked_text(text)
    if not set(policy["editable"]).issubset(ids) or size > TOTAL_LIMIT:
        raise Denied("The reviewed workspace scope exceeds its limits.", 409)

    if policy["schema"] == 2:
        from prism.projects import json_check_action

        action = manifest.get("action")
        if not isinstance(action, dict) or packed(action) != packed(
            json_check_action(manifest.get("files", []), profile=action.get("profile"))
        ):
            raise Denied("The reviewed workspace checker policy is unavailable.", 409)
    if policy["schema"] == 3:
        from prism.computation import python_action

        action = manifest.get("action")
        if not isinstance(action, dict):
            raise Denied("The Python execution policy is unavailable.", 409)
        try:
            canonical = python_action(
                files,
                action.get("entrypoint_name"),
                [f["name"] for f in action.get("outputs", [])],
            )
        except (KeyError, TypeError, Denied):
            raise Denied("The Python execution policy is invalid.", 409) from None
        if packed(action) != packed(canonical) or not {
            action["entrypoint"],
            *[f["id"] for f in action["outputs"]],
        }.issubset(policy["editable"]):
            raise Denied("The reviewed Python execution policy changed.", 409)


class Workspaces:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_returns (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
                    revision INTEGER NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspaces (
                    session TEXT PRIMARY KEY REFERENCES sessions(id),
                    revision INTEGER NOT NULL, contents TEXT NOT NULL);
            """)

    def _state(self, db, session, actor):
        state = self.store.authorized(db, session, actor)
        if state["mode"] != "continue" or state["manifest"].get("schema") != 3:
            raise Denied("This session has no permission to edit workspace copies.")
        row = db.execute(
            "SELECT revision,contents FROM workspaces WHERE session=?", (session,)
        ).fetchone()
        contents = json.loads(row["contents"]) if row else {}
        return state, row["revision"] if row else 0, contents

    @staticmethod
    def _files(state, contents):
        return [
            {**file, "text": contents.get(file["id"], file["text"])}
            for file in state["manifest"]["files"]
        ]

    def state(self, session, actor):
        with self.store.connect() as db:
            state, revision, contents = self._state(db, session, actor)
            return {
                "session": session,
                "version": state["version"],
                "revision": revision,
                "matching_check": self._matching_check(db, state, revision, contents),
                "matching_computation": self._matching_computation(
                    db, state, revision, contents
                ),
                "validator": state["manifest"].get("action"),
                "edit_limit": EDIT_LIMIT,
                "file_limit_bytes": FILE_LIMIT,
                "files": [
                    {
                        "id": file["id"],
                        "name": file["name"],
                        "sha256": digest(file["text"]),
                        "bytes": len(file["text"].encode()),
                        "editable": file["id"]
                        in state["manifest"]["workspace"]["editable"],
                        "changed": file["id"] in contents,
                    }
                    for file in self._files(state, contents)
                ],
            }

    def read(self, session, actor, file_id):
        with self.store.connect() as db:
            state, revision, contents = self._state(db, session, actor)
            for file in self._files(state, contents):
                if file["id"] == file_id:
                    return {
                        "id": file_id,
                        "name": file["name"],
                        "text": file["text"],
                        "sha256": digest(file["text"]),
                        "revision": revision,
                        "editable": file_id
                        in state["manifest"]["workspace"]["editable"],
                    }
            raise Denied()

    def edit(self, session, actor, file_id, text, expected_revision):
        return self.edit_many(
            session, actor, [{"id": file_id, "text": text}], expected_revision
        )

    def edit_many(self, session, actor, updates, expected_revision):
        """One atomic save: no partial edits if any member is denied."""
        with self.store.connect() as db:
            state, revision, contents = self._state(db, session, actor)
            if type(expected_revision) is not int or expected_revision != revision:
                raise Denied(
                    "The workspace changed. Reload and review before saving.", 409
                )
            if (
                not isinstance(updates, list)
                or not 1 <= len(updates) <= 3
                or any(
                    not isinstance(u, dict)
                    or set(u) != {"id", "text"}
                    or not isinstance(u["id"], str)
                    for u in updates
                )
                or len({u["id"] for u in updates}) != len(updates)
            ):
                raise Denied("Provide one to three distinct file edits.", 400)
            originals = {file["id"]: file for file in state["manifest"]["files"]}
            changed = []
            for update in updates:
                key, text = update["id"], update["text"]
                if key not in state["manifest"]["workspace"]["editable"]:
                    raise Denied("This file is not approved for workspace editing.")
                checked_text(text)
                original = originals[key]["text"]
                if text == contents.get(key, original):
                    continue
                changed.append(key)
                if text == original:
                    contents.pop(key, None)
                else:
                    contents[key] = text
            if not changed:
                return {"revision": revision, "changed": False}
            if revision >= EDIT_LIMIT:
                raise Denied("This workspace's 20-save allowance is exhausted.", 429)
            if (
                sum(len(file["text"].encode()) for file in self._files(state, contents))
                > TOTAL_LIMIT
            ):
                raise Denied("This workspace exceeds its total text allowance.", 400)
            db.execute(
                "INSERT INTO workspaces(session,revision,contents) VALUES(?,?,?) "
                "ON CONFLICT(session) DO UPDATE SET revision=excluded.revision,contents=excluded.contents",
                (session, revision + 1, packed(contents)),
            )
            for key in changed:
                self.store.event(db, "workspace_edited", actor, session + ":" + key)
            return {
                "revision": revision + 1,
                "changed": True,
                "workspace_references": [
                    {
                        "file_id": update["id"],
                        "revision": revision + 1,
                        "sha256": digest(
                            contents.get(update["id"], originals[update["id"]]["text"])
                        ),
                    }
                    for update in updates
                ],
            }

    def inspect(self, session, actor, file_ids):
        """Bound reads separately from the maximum stored file size."""
        with self.store.connect() as db:
            state, revision, contents = self._state(db, session, actor)
            files = {f["id"]: f for f in self._files(state, contents)}
            if (
                not isinstance(file_ids, list)
                or not 1 <= len(file_ids) <= 3
                or any(not isinstance(key, str) or key not in files for key in file_ids)
                or len(set(file_ids)) != len(file_ids)
            ):
                raise Denied(
                    "Select one to three distinct approved working-copy IDs.", 400
                )
            selected = [files[key] for key in file_ids]
            if sum(len(f["text"].encode()) for f in selected) > 12 * 1024:
                raise Denied(
                    "This read exceeds the bounded agent text allowance. Use the manual editor.",
                    400,
                )
            return {
                "revision": revision,
                "files": [
                    {
                        "id": f["id"],
                        "name": f["name"],
                        "text": f["text"],
                        "sha256": digest(f["text"]),
                        "editable": f["id"]
                        in state["manifest"]["workspace"]["editable"],
                    }
                    for f in selected
                ],
            }

    def check_inputs(self, db, session, actor, expected_revision):
        from prism.projects import json_check_action, json_check_payload

        state, revision, contents = self._state(db, session, actor)
        action = state["manifest"].get("action")
        if state["manifest"]["workspace"]["schema"] != 2 or not action:
            raise Denied("The owner did not authorize working-copy validation.")
        if type(expected_revision) is not int or revision != expected_revision:
            raise Denied(
                "The workspace changed. Check the current revision explicitly.", 409
            )
        if packed(action) != packed(
            json_check_action(state["manifest"]["files"], profile=action.get("profile"))
        ):
            raise Denied("The checker policy changed. Review a new version.", 409)
        current = {f["name"]: f for f in self._files(state, contents)}
        selected = [
            {**current[name], "sha256": digest(current[name]["text"])}
            for name in action["required_inputs"]
        ]
        return {
            "workspace_revision": revision,
            "files": [{"id": f["id"], "sha256": f["sha256"]} for f in selected],
        }, json_check_payload(selected)

    def returns(self, session, actor):
        with self.store.connect() as db:
            self._state(db, session, actor)
            return [
                self._return_metadata(row)
                for row in db.execute(
                    "SELECT * FROM workspace_returns WHERE session=? ORDER BY created DESC",
                    (session,),
                )
            ]

    def computation_inputs(self, db, session, actor, entrypoint, expected_revision):
        from prism.computation import payload

        state, revision, contents = self._state(db, session, actor)
        action = state["manifest"].get("action")
        if (
            state["manifest"]["workspace"]["schema"] != 3
            or not action
            or entrypoint != action["entrypoint"]
        ):
            raise Denied("The owner did not approve execution of this Python script.")
        if type(expected_revision) is not int or expected_revision != revision:
            raise Denied(
                "The workspace changed. Run the explicitly reviewed current revision.",
                409,
            )
        parameters, argument = payload(action, self._files(state, contents))
        return {
            "workspace_revision": revision,
            "entrypoint": entrypoint,
            **parameters,
        }, argument

    def apply_computation(self, session, actor, run_id, expected_revision):
        from prism.computation import accepted_output

        with self.store.connect() as db:
            state, _revision, _contents = self._state(db, session, actor)
            row = db.execute(
                "SELECT * FROM runs WHERE id=? AND session=? AND status='completed'",
                (run_id, session),
            ).fetchone()
            if not row:
                raise Denied("A completed computation in this session is required.")
            parameters, _ = self.computation_inputs(
                db,
                session,
                actor,
                state["manifest"].get("action", {}).get("entrypoint"),
                expected_revision,
            )
            result = json.loads(row["result"] or "null")
            if (
                row["action"] != "python-workspace"
                or json.loads(row["parameters"]) != parameters
                or not self._trusted_computation(
                    result, parameters, state["manifest"]["action"]
                )
            ):
                raise Denied("This result does not apply to the current revision.", 409)
            actual = accepted_output(result["output"], parameters)
            # All outputs commit together; total/edit guards also cover generated files.
            updates = [{"id": f["id"], "text": f["text"]} for f in actual["files"]]
        return self.edit_many(session, actor, updates, expected_revision)

    @staticmethod
    def _trusted_computation(result, parameters, action):
        return (
            isinstance(result, dict)
            and result.get("action") == "python-workspace"
            and result.get("inputs") == parameters
            and result.get("profile") == "development"
            and result.get("image") == action["image"]
            and result.get("program_sha256") == action["program_sha256"]
            and type(result.get("exit_code")) is int
            and result["exit_code"] == 0
            and result.get("cleaned_up") is True
        )

    def _matching_computation(self, db, state, revision, contents):
        from prism.computation import accepted_output, payload

        action = state["manifest"].get("action")
        if (
            not action
            or action.get("id") != "python-workspace"
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
        ):
            return None
        current = self._files(state, contents)
        expected, _ = payload(action, current)
        for row in db.execute(
            "SELECT * FROM runs WHERE session=? AND action='python-workspace' AND status='completed' ORDER BY created DESC",
            (state["id"],),
        ):
            parameters = json.loads(row["parameters"] or "null")
            result = json.loads(row["result"] or "null")
            if (
                not isinstance(parameters, dict)
                or {k: parameters.get(k) for k in expected} != expected
                or type(parameters.get("workspace_revision")) is not int
                or not 0 <= parameters["workspace_revision"] <= revision
                or parameters.get("entrypoint") != action["entrypoint"]
                or not self._trusted_computation(result, parameters, action)
            ):
                continue
            try:
                output = accepted_output(result["output"], parameters)
            except (ValueError, KeyError, TypeError, Denied):
                continue
            if all(
                any(
                    f["id"] == o["id"] and digest(f["text"]) == o["sha256"]
                    for f in current
                )
                for o in output["files"]
            ):
                return {
                    "id": row["id"],
                    "computed_revision": parameters["workspace_revision"],
                    "applies_to_revision": revision,
                }
        return None

    def matching_check(self, session, actor, expected_revision):
        """Read-only reuse proof for exact approved JSON bytes in this session."""
        with self.store.connect() as db:
            state, revision, contents = self._state(db, session, actor)
            self.check_inputs(db, session, actor, expected_revision)
            return self._matching_check(db, state, revision, contents)

    def _matching_check(self, db, state, revision, contents):
        action = state["manifest"].get("action")
        if (
            not action
            or state["manifest"]["workspace"]["schema"] != 2
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
        ):
            return None
        from prism.projects import json_check_action

        if packed(action) != packed(
            json_check_action(state["manifest"]["files"], profile=action.get("profile"))
        ):
            raise Denied("The checker policy changed. Review a new version.", 409)
        files = self._files(state, contents)
        expected = [
            {"id": f["id"], "sha256": digest(f["text"])}
            for name in action["required_inputs"]
            for f in files
            if f["name"] == name
        ]
        for row in db.execute(
            "SELECT * FROM runs WHERE session=? AND status='completed' ORDER BY created DESC",
            (state["id"],),
        ):
            parameters = json.loads(row["parameters"] or "null")
            result = json.loads(row["result"] or "null")
            if not isinstance(parameters, dict) or not isinstance(result, dict):
                continue
            checked_revision = parameters.get("workspace_revision")
            output = result.get("output")
            if not isinstance(output, dict) or not isinstance(
                output.get("files"), list
            ):
                continue
            checked_files = output["files"]
            if (
                row["action"] == "json-check"
                and row["runtime_profile"] == action["profile"]
                and type(checked_revision) is int
                and 0 <= checked_revision <= revision
                and set(parameters) == {"workspace_revision", "files"}
                and parameters["files"] == expected
                and result.get("inputs") == parameters
                and result.get("profile") == action["profile"]
                and result.get("program_sha256") == action["program_sha256"]
                and result.get("image") == action["image"]
                and type(result.get("exit_code")) is int
                and result.get("exit_code") == 0
                and result.get("cleaned_up") is True
                and output.get("action") == "json-check"
                and len(checked_files) == len(expected)
                and all(
                    isinstance(f, dict)
                    and {"id": f.get("id"), "sha256": f.get("sha256")} == e
                    and type(f.get("valid")) is bool
                    for f, e in zip(checked_files, expected)
                )
            ):
                return {
                    "id": row["id"],
                    "checked_revision": checked_revision,
                    "applies_to_revision": revision,
                    "input_hashes": expected,
                    "reused": checked_revision != revision,
                    "validated": all(f["valid"] for f in checked_files),
                }
        return None

    @staticmethod
    def _return_metadata(row):
        payload = json.loads(row["payload"])
        return {
            "id": row["id"],
            "revision": row["revision"],
            "created": row["created"],
            "sha256": digest(row["payload"]),
            "workspace_references": [
                {"file_id": f["id"], "revision": row["revision"], "sha256": f["sha256"]}
                for f in payload["files"]
            ],
            "check_completed": bool(payload["verification"]),
            "validated": bool(payload["verification"])
            and all(
                f.get("valid") is True
                for f in payload["verification"]["result"]["output"]["files"]
            ),
            "run_id": payload["verification"]["id"]
            if payload["verification"]
            else None,
            "checked_revision": payload["verification"]["parameters"][
                "workspace_revision"
            ]
            if payload["verification"]
            else None,
            "reused_check": payload["verification"].get("reused", False)
            if payload["verification"]
            else False,
            "computation_run": payload.get("computation", {}).get("id")
            if payload.get("computation")
            else None,
        }

    def prepare_return(self, session, actor, expected_revision):
        from prism.sharing import ident

        with self.store.connect() as db:
            state, revision, contents = self._state(db, session, actor)
            if type(expected_revision) is not int or revision != expected_revision:
                raise Denied(
                    "The workspace changed. Review the current revision before returning it.",
                    409,
                )
            files = [
                {**f, "sha256": digest(f["text"])} for f in self._files(state, contents)
            ]
            # Only completed checks of the exact approved JSON bytes can support
            # a return. The original check revision is never relabelled as new.
            verification = None
            match = self._matching_check(db, state, revision, contents)
            if match:
                row = db.execute(
                    "SELECT * FROM runs WHERE id=?", (match["id"],)
                ).fetchone()
                verification = {
                    "id": row["id"],
                    "parameters": json.loads(row["parameters"]),
                    "result": json.loads(row["result"]),
                    "applies_to_revision": revision,
                    "input_hashes_match": True,
                    "reused": match["reused"],
                }
            payload = {
                "schema": 1,
                "version": state["version"],
                "revision": revision,
                "files": [
                    {k: f[k] for k in ("id", "name", "text", "sha256")} for f in files
                ],
                "verification": verification,
                "changes": self._diff(state, revision, contents)["changes"],
                "source_modified": False,
                "notice": "JSON syntax only; semantic correctness and owner acceptance are not established.",
            }
            if state["manifest"]["workspace"]["schema"] == 3:
                match = self._matching_computation(db, state, revision, contents)
                payload["computation"] = None
                if match:
                    row = db.execute(
                        "SELECT * FROM runs WHERE id=?", (match["id"],)
                    ).fetchone()
                    payload["computation"] = {
                        **match,
                        "parameters": json.loads(row["parameters"]),
                        "result": json.loads(row["result"]),
                    }
                payload["notice"] = (
                    "Python computation provenance is not mathematical validation or owner approval."
                )
            for existing in db.execute(
                "SELECT * FROM workspace_returns WHERE session=? AND revision=?",
                (session, revision),
            ):
                if existing["payload"] == packed(payload):
                    return self._return_metadata(existing)
            if (
                db.execute(
                    "SELECT count(*) FROM workspace_returns WHERE session=?", (session,)
                ).fetchone()[0]
                >= 6
            ):
                raise Denied("The session's six-return allowance is exhausted.", 429)
            key = ident()
            db.execute(
                "INSERT INTO workspace_returns VALUES(?,?,?,?,?)",
                (key, session, revision, time.time(), packed(payload)),
            )
            self.store.event(db, "workspace_returned", actor, key)
            row = db.execute(
                "SELECT * FROM workspace_returns WHERE id=?", (key,)
            ).fetchone()
            return self._return_metadata(row)

    def get_return(self, session, actor, return_id):
        with self.store.connect() as db:
            self._state(db, session, actor)
            row = db.execute(
                "SELECT * FROM workspace_returns WHERE id=? AND session=?",
                (return_id, session),
            ).fetchone()
            if row is None:
                raise Denied()
            return {**self._return_metadata(row), "payload": json.loads(row["payload"])}

    def download(self, session, actor, return_id):
        data = self.get_return(session, actor, return_id)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            payload = data["payload"]
            entries = [("prism-return.json", packed(payload).encode("utf-8"))]
            for file in payload["files"]:
                Source.checked_name(file["name"])
                entries.append(("files/" + file["name"], file["text"].encode("utf-8")))
            for name, content in entries:
                # Stable transport bytes: do not stamp each download with the
                # current time. The immutable manifest remains the content ID.
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, content)
        return stream.getvalue()

    def diff(self, session, actor):
        with self.store.connect() as db:
            state, revision, contents = self._state(db, session, actor)
            return self._diff(state, revision, contents)

    @staticmethod
    def _diff(state, revision, contents):
        changes = []
        for file in state["manifest"]["files"]:
            if file["id"] not in contents:
                continue
            after = contents[file["id"]]
            # splitlines plus explicit end-of-file information avoids hiding
            # an added/removed final newline in an otherwise empty diff.
            lines = list(
                difflib.unified_diff(
                    file["text"].splitlines(),
                    after.splitlines(),
                    fromfile="approved/" + file["name"],
                    tofile="workspace/" + file["name"],
                    lineterm="",
                )
            )
            changes.append(
                {
                    "id": file["id"],
                    "name": file["name"],
                    "before_sha256": file["sha256"],
                    "after_sha256": digest(after),
                    "before_final_newline": file["text"].endswith("\n"),
                    "after_final_newline": after.endswith("\n"),
                    "line_endings_only": file["text"].splitlines()
                    == after.splitlines(),
                    "diff": "\n".join(lines),
                }
            )
        return {
            "version": state["version"],
            "revision": revision,
            "changes": changes,
            "validated": False,
            "source_modified": False,
        }
