"""Owner-reviewed context materialization. No model calls or recipient activation."""

import copy
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from prism.owner import OwnerIdentity
from prism.projects import json_check_action
from prism.sharing import Denied, digest, ident, packed

REQUIRED_INPUTS = {"observations.csv", "baseline.json", "limitations.md"}
CONTEXT_LIMIT = 16 * 1024


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExcerptSelection(Selection):
    turn: str = Field(pattern=r"^[0-9a-f]{32}$")
    part: Literal["question", "answer"]
    edited_text: str | None = Field(default=None, min_length=1, max_length=5000)
    omit_references: bool = False


class HandoffSelection(Selection):
    checkpoint: str = Field(pattern=r"^[0-9a-f]{32}$")
    purpose: str = Field(min_length=5, max_length=1000)
    summary: str = Field(min_length=1, max_length=2000)
    open_questions: str = Field(max_length=1500)
    files: list[str] = Field(min_length=1, max_length=8)
    runs: list[str] = Field(max_length=6)
    excerpts: list[ExcerptSelection] = Field(min_length=1, max_length=24)
    mode: Literal["inspect", "verify"]


def message_text(turn, part):
    if part == "question":
        return turn["question"]
    answer = json.loads(turn["answer"])
    text = answer["answer"]
    if answer.get("limitations"):
        text += "\n\nLimitations:\n" + "\n".join(answer["limitations"])
    return text


class Handoffs:
    def __init__(self, workspace):
        self.workspace = workspace
        self.store = workspace.base
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS owner_handoff_sources (
                    version TEXT PRIMARY KEY REFERENCES versions(id),
                    owner TEXT NOT NULL, project TEXT NOT NULL,
                    conversation TEXT NOT NULL, checkpoint TEXT NOT NULL,
                    mapping TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_handoff_source
                    BEFORE UPDATE ON owner_handoff_sources
                    BEGIN SELECT RAISE(ABORT, 'Immutable handoff source'); END;
                CREATE TABLE IF NOT EXISTS handoff_schema_versions (
                    version INTEGER PRIMARY KEY, applied REAL NOT NULL);
            """)
            db.execute(
                "INSERT OR IGNORE INTO handoff_schema_versions VALUES(1,?)",
                (time.time(),),
            )

    def source(self, session, actor):
        with self.store.connect() as db:
            state = self.workspace.authorized(db, session, actor)
            turns = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM turns WHERE session=? ORDER BY created,id",
                    (session,),
                )
            ]
            runs = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM runs WHERE session=? ORDER BY created,id",
                    (session,),
                )
            ]
        action = copy.deepcopy(state["manifest"].get("action"))
        if action and action.get("id") == "bootstrap" and "required_inputs" not in action:
            action["required_inputs"] = sorted(REQUIRED_INPUTS)

        def public_run(run):
            action_id = run.get("action") or "bootstrap"
            parameters = (
                json.loads(run["parameters"])
                if run.get("parameters")
                else {"seed": run["seed"]}
            )
            record = {
                "id": run["id"],
                "action": action_id,
                "parameters": parameters,
                "status": run["status"],
                "finished": run["finished"],
                "result": json.loads(run["result"]) if run["result"] else None,
            }
            if action_id == "bootstrap":
                record["seed"] = parameters["seed"]
            return record

        return {
            "revision": state["version"],
            "files": state["manifest"]["files"],
            "action": action,
            "source_kind": state["manifest"].get("source_kind", "synthetic"),
            "turns": [
                {
                    "id": t["id"],
                    "created": t["created"],
                    "finished": t["finished"],
                    "question": t["question"],
                    "answer": message_text(t, "answer"),
                    "citations": json.loads(t["answer"]).get("citations", []),
                    "run_references": json.loads(t["answer"]).get("run_references", []),
                }
                for t in turns
                if t["status"] == "completed" and t["answer"]
            ],
            "runs": [
                public_run(r)
                for r in runs
                if r["status"] == "completed" and r["result"]
            ],
            "incomplete_turns": sum(t["status"] != "completed" for t in turns),
        }

    def draft(self, version, owner):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM owner_handoff_sources WHERE version=? AND owner=?",
                (version, owner),
            ).fetchone()
            if row is None:
                raise Denied()
            self.workspace.authorized(
                db, row["conversation"], OwnerIdentity(owner, row["project"])
            )
            return {
                "project": row["project"],
                "conversation": row["conversation"],
                "selection": json.loads(row["mapping"])["selection"],
            }

    def freeze(self, session, actor, selection: HandoffSelection):
        if not selection.purpose.strip() or not selection.summary.strip():
            raise Denied("Enter a purpose and summary with visible text.", 400)
        with self.store.connect() as db:
            state = self.workspace.authorized(db, session, actor)
            turns = {
                r["id"]: dict(r)
                for r in db.execute(
                    "SELECT * FROM turns WHERE session=? ORDER BY created,id",
                    (session,),
                )
            }
            checkpoint = turns.get(selection.checkpoint)
            if (
                not checkpoint
                or checkpoint["status"] != "completed"
                or not checkpoint["finished"]
            ):
                raise Denied("Choose a completed checkpoint in this conversation.", 400)
            eligible = {
                k: t
                for k, t in turns.items()
                if t["status"] == "completed"
                and t["answer"]
                and t["finished"]
                and (t["created"], t["id"]) <= (checkpoint["created"], checkpoint["id"])
                and t["finished"] <= checkpoint["finished"]
            }
            all_runs = {
                r["id"]: dict(r)
                for r in db.execute("SELECT * FROM runs WHERE session=?", (session,))
            }
            files = {f["id"]: f for f in state["manifest"]["files"]}
            if len(set(selection.files)) != len(selection.files) or any(
                k not in files for k in selection.files
            ):
                raise Denied(
                    "Select distinct files from this conversation's resource revision.",
                    400,
                )
            if len(set(selection.runs)) != len(selection.runs):
                raise Denied("Select each historical run once.", 400)
            names = {files[k]["name"] for k in selection.files}
            source_action = state["manifest"].get("action")
            if selection.mode == "verify" and source_action is None:
                raise Denied("This project has no approved verification action.", 400)
            if selection.runs and source_action is None:
                raise Denied(
                    "Historical runs are unavailable without an approved action.", 400
                )
            if source_action and source_action.get("id") == "bootstrap":
                required = set(source_action.get("required_inputs", REQUIRED_INPUTS))
                if (
                    selection.mode == "verify" or selection.runs
                ) and not required.issubset(names):
                    raise Denied(
                        "Verification and historical run evidence require every action input. Select them explicitly.",
                        400,
                    )
            file_map = {k: ident()[:24] for k in selection.files}
            run_map = {k: ident() for k in selection.runs}
            selected_files = [
                {**copy.deepcopy(files[k]), "id": file_map[k]}
                for k in sorted(selection.files, key=lambda k: files[k]["name"])
            ]
            run_records = []
            for key in selection.runs:
                run = all_runs.get(key)
                if (
                    not run
                    or run["status"] != "completed"
                    or not run["finished"]
                    or run["finished"] > checkpoint["finished"]
                    or not run["result"]
                ):
                    raise Denied(
                        "Choose completed runs at or before the checkpoint from this conversation.",
                        400,
                    )
                result = json.loads(run["result"])
                if result.get("program_sha256") != source_action["program_sha256"]:
                    raise Denied(
                        "The historical run does not match this resource revision.", 409
                    )
                run_action = run.get("action") or "bootstrap"
                parameters = (
                    json.loads(run["parameters"])
                    if run.get("parameters")
                    else {"seed": run["seed"]}
                )
                if run_action != source_action.get("id"):
                    raise Denied("The historical run uses another action.", 409)
                if run_action == "json-check":
                    inputs = []
                    for item in parameters.get("files", []):
                        if item.get("id") not in file_map:
                            raise Denied(
                                "Select every file used by a historical JSON check.",
                                400,
                            )
                        inputs.append(
                            {"id": file_map[item["id"]], "sha256": item["sha256"]}
                        )
                else:
                    inputs = [
                        {"id": f["id"], "sha256": f["sha256"]}
                        for f in selected_files
                        if f["name"]
                        in set(source_action.get("required_inputs", REQUIRED_INPUTS))
                    ]
                shared_parameters = (
                    {"files": inputs} if run_action == "json-check" else parameters
                )
                shared_output = copy.deepcopy(result["output"])
                if run_action == "json-check":
                    for item in shared_output.get("files", []):
                        if item.get("id") not in file_map:
                            raise Denied(
                                "The historical JSON result has unavailable inputs.",
                                409,
                            )
                        item["id"] = file_map[item["id"]]
                record = {
                    "id": run_map[key],
                    "kind": "historical_owner_run",
                    "action": run_action,
                    "parameters": shared_parameters,
                    "inputs": inputs,
                    "result": {
                        "output": shared_output,
                        "output_sha256": (
                            digest(packed(shared_output))
                            if run_action == "json-check"
                            else result["output_sha256"]
                        ),
                        **{
                            k: result[k]
                            for k in (
                                "image",
                                "image_id",
                                "program_sha256",
                                "exit_code",
                                "elapsed_seconds",
                                "cleaned_up",
                                "profile",
                                "runtime_handler",
                                "guest_kernel",
                                "guest_boot_id",
                            )
                            if k in result
                        },
                    },
                }
                if run_action == "bootstrap":
                    record["seed"] = parameters["seed"]
                run_records.append(record)
            excerpts, seen, private_mapping = [], set(), []
            ordered = sorted(
                selection.excerpts,
                key=lambda e: (
                    eligible.get(e.turn, {}).get("created", 0),
                    e.turn,
                    e.part != "question",
                ),
            )
            for chosen in ordered:
                key = (chosen.turn, chosen.part)
                if chosen.turn not in eligible or key in seen:
                    raise Denied(
                        "Select distinct messages completed by this checkpoint.", 400
                    )
                seen.add(key)
                turn = eligible[chosen.turn]
                original = message_text(turn, chosen.part)
                text = original if chosen.edited_text is None else chosen.edited_text
                if not text.strip():
                    raise Denied("An excerpt cannot be blank.", 400)
                answer = json.loads(turn["answer"]) if chosen.part == "answer" else {}
                citations, references = [], []
                if not chosen.omit_references:
                    for cite in answer.get("citations", []):
                        f = files.get(cite["id"])
                        if (
                            not f
                            or cite["id"] not in file_map
                            or cite.get("version") != state["version"]
                            or cite.get("sha256") != f["sha256"]
                            or not 1 <= cite["start"] <= cite["end"] <= f["lines"]
                        ):
                            raise Denied(
                                "An excerpt cites evidence outside your selection. Include it or explicitly omit references and label the evidence gap.",
                                400,
                            )
                        citations.append(
                            {
                                "id": file_map[cite["id"]],
                                "start": cite["start"],
                                "end": cite["end"],
                            }
                        )
                    for reference in answer.get("run_references", []):
                        if reference not in run_map:
                            raise Denied(
                                "An excerpt cites an unselected run. Include it or explicitly omit references and label the evidence gap.",
                                400,
                            )
                        references.append(run_map[reference])
                excerpt_id = ident()
                excerpts.append(
                    {
                        "id": excerpt_id,
                        "speaker": "owner" if chosen.part == "question" else "agent",
                        "origin": "verbatim" if text == original else "owner_edited",
                        "text": text,
                        "citations": citations,
                        "run_references": references,
                        "evidence_gap": chosen.omit_references,
                    }
                )
                private_mapping.append(
                    {"excerpt": excerpt_id, "turn": chosen.turn, "part": chosen.part}
                )
            context = {
                "schema": 1,
                "summary": selection.summary,
                "open_questions": selection.open_questions,
                "excerpts": excerpts,
                "runs": run_records,
            }
            if len(packed(context).encode()) > CONTEXT_LIMIT:
                raise Denied(
                    "Selected context exceeds 16 KiB. Shorten or omit content explicitly; nothing was silently truncated.",
                    400,
                )
            # Structured references are rewritten above. Do not carry private source
            # locators in free text: the owner must review and edit those explicitly.
            private_ids = {session, state["version"], *turns, *all_runs, *files}
            text_content = packed(
                [
                    selection.purpose,
                    selection.summary,
                    selection.open_questions,
                    *[e["text"] for e in excerpts],
                ]
            )
            if any(value in text_content for value in private_ids):
                raise Denied(
                    "Remove private source identifiers from the selected text. Attached references use share-local identifiers.",
                    400,
                )
            frozen_action = None
            if selection.mode == "verify":
                frozen_action = (
                    json_check_action(
                        selected_files, profile=source_action.get("profile")
                    )
                    if source_action["id"] == "json-check"
                    else copy.deepcopy(source_action)
                )
            manifest = {
                "schema": 2,
                "project": state["manifest"]["project"],
                "purpose": selection.purpose,
                "mode": selection.mode,
                "files": selected_files,
                "action": frozen_action,
                "source_kind": state["manifest"].get("source_kind", "synthetic"),
                "context": context,
            }
            version = self.store.insert_candidate(db, manifest)
            db.execute(
                "INSERT INTO owner_handoff_sources VALUES(?,?,?,?,?,?)",
                (
                    version,
                    actor.owner,
                    actor.project,
                    session,
                    selection.checkpoint,
                    packed(
                        {
                            "excerpts": private_mapping,
                            "files": file_map,
                            "runs": run_map,
                            "selection": selection.model_dump(),
                        }
                    ),
                ),
            )
        self.store.measure("handoff_selected_messages", len(excerpts))
        return self.store.owner_version(version)
