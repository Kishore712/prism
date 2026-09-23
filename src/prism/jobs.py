"""Durable, bounded synthetic jobs. Only the trusted worker controls the engine."""

import json
import signal
import subprocess
import sys
import threading
import time

from prism.engine import IMAGE, EngineError, socket_path
from prism.projects import json_check_action, json_check_payload
from prism.reference_runtime import HANDLER, RuntimeRegistry
from prism.sharing import Denied, bootstrap_action, digest, ident, packed


class Jobs:
    def __init__(self, store, socket=None, *, recover=True, registry=None):
        self.store = store
        self.socket = str(socket_path(socket))
        self.registry = registry or RuntimeRegistry(
            profile="development", socket=self.socket
        )
        self.threads = set()
        self.stop = threading.Event()
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, request_key TEXT NOT NULL,
                    seed INTEGER NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                    finished REAL, result TEXT, error TEXT,
                    UNIQUE(session, request_key));
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
            if "action" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN action TEXT")
            if "parameters" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN parameters TEXT")
            for name in ("runtime_profile", "runtime_resource", "runtime_token"):
                if name not in columns:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {name} TEXT")
        if recover:
            self._recover()
            if self.registry.profile == "reference-linux" and not self.registry.blocked:
                self.registry.activate()

    def _recover(self):
        with self.store.connect() as db:
            interrupted = list(
                db.execute("SELECT * FROM runs WHERE status IN ('queued','running')")
            )
        for row in interrupted:
            status = "uncertain"
            error = "Service restarted before runtime cleanup was confirmed. Owner inspection is required."
            if (
                row["runtime_profile"] == "reference-linux"
                and row["runtime_resource"]
                and row["runtime_token"]
            ):
                try:
                    self.registry.reconcile(
                        row["runtime_profile"], row["runtime_resource"], row["runtime_token"]
                    )
                    error = "Service restarted; the observed owned Kata resource was removed, but controller termination is unconfirmed. No work was retried."
                except (EngineError, ValueError):
                    error = "Service restarted but exact Kata cleanup was not confirmed. Owner inspection is required."
            with self.store.connect() as db:
                db.execute(
                    "UPDATE runs SET status=?,finished=?,error=? WHERE id=?",
                    (status, time.time(), error, row["id"]),
                )
        if self.registry.profile == "reference-linux" and self.registry.reference:
            try:
                self.registry.blocked = bool(self.registry.reference.all_resources())
            except (EngineError, ValueError):
                self.registry.blocked = True
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM runs WHERE status='uncertain'").fetchone():
                    self.registry.blocked = True

    def submit(self, session, actor, seed, request_key):
        if (
            type(seed) is not int
            or not 0 <= seed <= 1000
            or not isinstance(request_key, str)
            or not 8 <= len(request_key) <= 80
        ):
            raise Denied(
                "Use an integer seed from 0 to 1000 and a bounded request identifier.",
                400,
            )
        return self._submit(
            session,
            actor,
            "bootstrap",
            {"seed": seed},
            request_key,
            str(seed),
        )

    def submit_json_check(self, session, actor, request_key):
        if not isinstance(request_key, str) or not 8 <= len(request_key) <= 80:
            raise Denied("Use a bounded request identifier.", 400)
        with self.store.connect() as db:
            state = self.store.authorized(db, session, actor)
            action = state["manifest"].get("action")
            if (
                state["mode"] != "verify"
                or not action
                or action.get("id") != "json-check"
            ):
                raise Denied("This session may not run the JSON check.")
            files = {file["name"]: file for file in state["manifest"].get("files", [])}
            required = action.get("required_inputs")
            if (
                not isinstance(required, list)
                or not required
                or len(set(required)) != len(required)
                or any(name not in files for name in required)
            ):
                raise Denied("The approved JSON inputs are unavailable.", 409)
            selected = [files[name] for name in required]
            profile = action.get("profile")
            expected = json_check_action(selected, profile=profile)
            if packed(action) != packed(expected):
                raise Denied(
                    "The installed JSON checker policy changed. Prepare and approve a new version.",
                    409,
                )
            argument = json_check_payload(selected)
            parameters = {
                "files": [
                    {"id": file["id"], "sha256": file["sha256"]} for file in selected
                ]
            }
        return self._submit(
            session,
            actor,
            "json-check",
            parameters,
            request_key,
            argument,
        )

    def _submit(self, session, actor, action_id, parameters, request_key, argument):
        with self.store.connect() as db:
            state = self.store.authorized(db, session, actor)
            action = state["manifest"].get("action")
            if (
                state["mode"] != "verify"
                or action is None
                or action.get("id") != action_id
            ):
                raise Denied(
                    "This session may inspect evidence but may not run verification."
                )
            if action_id == "bootstrap":
                canonical = bootstrap_action()
                historical = {
                    k: v for k, v in canonical.items() if k != "required_inputs"
                }
                if packed(action) not in (packed(canonical), packed(historical)):
                    raise Denied(
                        "The installed evaluator changed. Prepare and approve a new version.",
                        409,
                    )
            profile = action.get("profile")
            previous = db.execute(
                "SELECT * FROM runs WHERE session=? AND request_key=?",
                (session, request_key),
            ).fetchone()
            if previous:
                previous_action = previous["action"] or "bootstrap"
                previous_parameters = (
                    json.loads(previous["parameters"])
                    if previous["parameters"]
                    else {"seed": previous["seed"]}
                )
                if previous_action != action_id or previous_parameters != parameters:
                    raise Denied(
                        "This request identifier already belongs to different parameters.",
                        409,
                )
                return self.public(previous, state)
            try:
                self.registry.assert_ready(profile)
            except (EngineError, ValueError):
                raise Denied(
                    "The approved runtime profile is not ready on this host.", 503
                ) from None
            if db.execute("SELECT 1 FROM runs WHERE status='uncertain'").fetchone():
                raise Denied(
                    "Uncertain earlier work requires owner runtime inspection before any new execution.",
                    503,
                )
            if db.execute(
                "SELECT 1 FROM runs WHERE status IN ('queued','running')"
            ).fetchone():
                raise Denied(
                    "Another verification is running. Wait for its confirmed result.",
                    409,
                )
            if (
                db.execute(
                    "SELECT count(*) FROM runs WHERE session=?", (session,)
                ).fetchone()[0]
                >= 6
                or db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 24
            ):
                raise Denied("The local verification budget is exhausted.", 429)
            if self.stop.is_set():
                raise Denied("The execution service is stopping.", 503)
            run = ident()
            runtime_resource = runtime_token = None
            if profile == "reference-linux":
                runtime_resource = self.registry.reference.resource_name(run)
                runtime_token = ident()
            db.execute(
                "INSERT INTO runs(id,session,request_key,seed,status,created,action,parameters,runtime_profile,runtime_resource,runtime_token) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    session,
                    request_key,
                    parameters.get("seed", 0),
                    "queued",
                    time.time(),
                    action_id,
                    packed(parameters),
                    profile,
                    runtime_resource,
                    runtime_token,
                ),
            )
            self.store.event(db, "run_reserved", actor, run)
        self._launch(
            run,
            session,
            actor,
            action_id,
            argument,
            action["program_sha256"],
            parameters,
            profile,
            runtime_resource,
            runtime_token,
        )
        return self.get(session, actor, run)

    def _launch(
        self, run, session, actor, action, argument, program_hash, parameters,
        profile="development", resource=None, token=None,
    ):
        thread = threading.Thread(
            target=self._execute,
            args=(run, session, actor, action, argument, program_hash, parameters, profile, resource, token),
            daemon=True,
        )
        self.threads.add(thread)
        thread.start()

    @staticmethod
    def public(row, state):
        action = row["action"] or "bootstrap"
        parameters = (
            json.loads(row["parameters"])
            if row["parameters"]
            else {"seed": row["seed"]}
        )
        record = {
            "id": row["id"],
            "session": row["session"],
            "version": state["version"],
            "action": action,
            "parameters": parameters,
            "status": row["status"],
            "created": row["created"],
            "finished": row["finished"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
            "runtime_profile": row["runtime_profile"] or "development",
        }
        if action == "bootstrap":
            record["seed"] = parameters["seed"]
        return record

    def get(self, session, actor, run):
        with self.store.connect() as db:
            state = self.store.authorized(db, session, actor)
            row = db.execute(
                "SELECT * FROM runs WHERE id=? AND session=?", (run, session)
            ).fetchone()
            if row is None:
                raise Denied()
            return self.public(row, state)

    def list(self, session, actor):
        with self.store.connect() as db:
            state = self.store.authorized(db, session, actor)
            return [
                self.public(row, state)
                for row in db.execute(
                    "SELECT * FROM runs WHERE session=? ORDER BY created", (session,)
                )
            ]

    def _execute(
        self, run, session, actor, action, argument, program_hash, parameters,
        profile, resource, token,
    ):
        process = None
        status, error, result = (
            "failed",
            "Runtime unavailable. Confirm that the fixed image and local engine are ready.",
            None,
        )
        try:
            self.store.session(session, actor)
            if self.stop.is_set():
                raise Denied()
            with self.store.connect() as db:
                db.execute("UPDATE runs SET status='running' WHERE id=?", (run,))
            # Fixed argv, no shell, no inherited model credentials or source path.
            runtime_locator = (
                self.socket if profile == "development" else "reference-linux-local"
            )
            worker_argv = [
                    sys.executable,
                    "-I",
                    "-m",
                    "prism.worker",
                    runtime_locator,
                    action,
                    program_hash,
                ]
            if profile == "reference-linux":
                worker_argv.extend([profile, resource, token])
            process = subprocess.Popen(
                worker_argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            encoded_argument = argument.encode("ascii")
            if process.stdin is None or len(encoded_argument) > 100 * 1024:
                raise ValueError("Invalid bounded action payload")
            process.stdin.write(encoded_argument)
            process.stdin.close()
            process.stdin = None
            worker_output = bytearray()
            output_overflow = threading.Event()

            def drain_worker():
                while True:
                    chunk = process.stdout.read(4096)
                    if not chunk:
                        return
                    remaining = 80 * 1024 - len(worker_output)
                    worker_output.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        output_overflow.set()

            drain = threading.Thread(target=drain_worker, daemon=True)
            drain.start()
            deadline, cancellation = time.monotonic() + 30, False
            while process.poll() is None:
                try:
                    self.store.session(session, actor)
                    allowed = not self.stop.is_set()
                except Denied:
                    allowed = False
                if not allowed or time.monotonic() > deadline:
                    process.send_signal(signal.SIGTERM)
                    cancellation = True
                    break
                time.sleep(0.05)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                if profile == "reference-linux":
                    try:
                        self.registry.reconcile(profile, resource, token)
                        error = "Worker termination required force. The observed Kata resource was removed, but late creation or controller termination is unconfirmed."
                    except (EngineError, ValueError):
                        error = "Worker termination required force. Kata cleanup is unconfirmed; owner inspection is required."
                    status = "uncertain"
                else:
                    status, error = (
                        "uncertain",
                        "Worker termination required force. Container cleanup is unconfirmed; owner inspection is required.",
                    )
                return
            drain.join(timeout=2)
            if drain.is_alive():
                status, error = "uncertain", "Worker output termination was not confirmed."
                return
            output = bytes(worker_output)
            if process.returncode == 0 and not output_overflow.is_set():
                record = json.loads(output)
                if not isinstance(record, dict):
                    raise ValueError("Invalid worker result")
                if record.get("cleaned_up") is not True:
                    status, error = (
                        "uncertain",
                        "Container cleanup was not confirmed. Owner inspection is required.",
                    )
                    return
                if record.get("program_sha256") != program_hash:
                    raise ValueError("Invalid result provenance")
                if profile == "reference-linux" and (
                    record.get("runtime_handler") != HANDLER
                    or record.get("image_id") != self.registry.reference.image_id
                ):
                    raise ValueError("Invalid reference runtime provenance")
                if cancellation or record.get("stop_reason") == "cancelled":
                    status, error = (
                        "cancelled",
                        "Execution was cancelled and its container was removed.",
                    )
                elif (
                    record.get("exit_code") == 0
                    and record.get("stop_reason") == "exited"
                    and not record.get("output_limited")
                ):
                    actual = json.loads(record["stdout"])
                    if profile == "reference-linux" and (
                        not record.get("guest_kernel")
                        or not record.get("guest_boot_id")
                    ):
                        raise ValueError("Invalid reference runtime provenance")
                    if action == "bootstrap" and (
                        actual.get("seed") != parameters["seed"]
                        or actual.get("synthetic") is not True
                    ):
                        raise ValueError("Invalid result provenance")
                    if action == "json-check":
                        expected = parameters["files"]
                        returned = actual.get("files")
                        if (
                            actual.get("action") != "json-check"
                            or not isinstance(returned, list)
                            or [
                                {"id": item.get("id"), "sha256": item.get("sha256")}
                                for item in returned
                            ]
                            != expected
                        ):
                            raise ValueError("Invalid result provenance")
                    result = {
                        "action": action,
                        "inputs": parameters,
                        "output": actual,
                        "output_sha256": digest(record["stdout"]),
                        "image_id": record["image_id"],
                        "image": IMAGE,
                        "program_sha256": program_hash,
                        "exit_code": record["exit_code"],
                        "elapsed_seconds": record["elapsed_seconds"],
                        "cleaned_up": True,
                        "profile": profile,
                    }
                    if profile == "reference-linux":
                        result.update(
                            runtime_handler=record["runtime_handler"],
                            guest_kernel=record["guest_kernel"],
                            guest_boot_id=record["guest_boot_id"],
                        )
                    status, error = "completed", None
                else:
                    error = (
                        "Verification did not complete successfully within its limits."
                    )
            elif process.returncode == 3:
                status = "uncertain"
                error = "Worker could not confirm cleanup. Owner runtime inspection is required."
                if profile == "reference-linux":
                    try:
                        self.registry.reconcile(profile, resource, token)
                        error = "Worker could not confirm cleanup. The observed Kata resource was removed, but execution remains uncertain."
                    except (EngineError, ValueError):
                        error = "Worker could not confirm cleanup, and exact Kata cleanup is unconfirmed."
            elif profile == "reference-linux":
                try:
                    self.registry.reconcile(profile, resource, token)
                    error = "The reference worker returned no trusted cleanup record. The observed resource was removed, but execution remains uncertain."
                except (EngineError, ValueError):
                    error = "The reference worker returned no trusted cleanup record and cleanup is unconfirmed."
                status = "uncertain"
        except (Denied, OSError, ValueError, KeyError, TypeError, AttributeError):
            if process is None:
                status, error = (
                    "cancelled",
                    "Execution did not start. Access ended or the local worker was unavailable.",
                )
            else:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                status, error = (
                    "uncertain",
                    "The run could not be reconciled safely. Owner runtime inspection is required.",
                )
            if profile == "reference-linux" and resource and token:
                try:
                    self.registry.reconcile(profile, resource, token)
                    error = "The result was rejected and the observed Kata resource was removed, but the run remains uncertain."
                except (EngineError, ValueError):
                    error = "The run could not be reconciled safely. Owner runtime inspection is required."
                status = "uncertain"
        finally:
            with self.store.connect() as db:
                db.execute(
                    "UPDATE runs SET status=?,finished=?,result=?,error=? WHERE id=?",
                    (
                        status,
                        time.time(),
                        packed(result) if result else None,
                        error,
                        run,
                    ),
                )
                self.store.event(db, "run_finished", actor, run, status)
            if result:
                self.store.measure("runtime_seconds", result["elapsed_seconds"])
            self.threads.discard(threading.current_thread())

    def shutdown(self):
        self.stop.set()
        for thread in list(self.threads):
            thread.join(timeout=50)
        if any(thread.is_alive() for thread in list(self.threads)):
            self.registry.blocked = True
            with self.store.connect() as db:
                db.execute(
                    "UPDATE runs SET status='uncertain',finished=?,error=? WHERE status IN ('queued','running')",
                    (
                        time.time(),
                        "Service shutdown ended before worker and runtime termination were confirmed.",
                    ),
                )
