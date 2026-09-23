"""Fixed local Kata runtime for the approved reference Linux profile."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import selectors
import stat
import subprocess
import threading
import time
import tomllib
import uuid
from pathlib import Path

from prism.engine import IMAGE, EngineError
from prism.runtime import OUTPUT_LIMIT, RunResult, action_program, probe_arguments

PROFILE = "reference-linux"
HANDLER = "io.containerd.kata.v2"
LABEL = "org.prism.reference-run"
NERDCTL = Path("/usr/local/bin/nerdctl")
CONTAINERD = Path("/usr/local/bin/containerd")
KATA_RUNTIME = Path("/opt/kata/bin/kata-runtime")
KATA_SHIM = Path("/usr/local/bin/containerd-shim-kata-v2")
KATA_CONFIG = Path("/etc/kata-containers/configuration.toml")
SOCKET = Path("/run/containerd/containerd.sock")
NAMESPACE = "prism-m0"
ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
}
KATA_CHECK_ENV = {
    **ENV,
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}
MANAGEMENT_LIMIT = 1024 * 1024
KATA_POLICY = {
    "disable_guest_seccomp": False,
    "enable_annotations": [],
    "default_memory": 512,
    "default_maxmemory": 1024,
    "default_vcpus": 1,
    "default_maxvcpus": 2,
    "seccompsandbox": "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
}
RESOURCE_NAME = re.compile(r"^prism-m23-(?:run|ready)-[0-9a-f]{32}$")
TOKEN = re.compile(r"^[0-9a-f]{32}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def _base_cli() -> list[str]:
    return [str(NERDCTL), "--address", str(SOCKET), "--namespace", NAMESPACE]


def _command(arguments, *, check=True, timeout=20):
    try:
        result = subprocess.run(
            [*_base_cli(), *arguments],
            env=ENV,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            "The local reference runtime did not respond within its bound."
        ) from exc
    if len(result.stdout) + len(result.stderr) > MANAGEMENT_LIMIT:
        raise EngineError("The local reference runtime response exceeded its bound.")
    if check and result.returncode:
        raise EngineError(
            "The local reference runtime rejected a fixed management operation "
            f"(exit={result.returncode})."
        )
    return result


def _fixed_command(path: Path, arguments, *, timeout=20, env=ENV):
    try:
        result = subprocess.run(
            [str(path), *arguments],
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            "A required reference runtime component is unavailable."
        ) from exc
    if len(result.stdout) + len(result.stderr) > MANAGEMENT_LIMIT:
        raise EngineError(
            "A required reference runtime check exceeded its bounded output limit."
        )
    if result.returncode:
        raise EngineError(
            "A required reference runtime check failed "
            f"(component={path.name}, exit={result.returncode})."
        )
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


def _discard_until_stopped(selector, stop):
    while selector.get_map() and not stop.is_set():
        for key, _ in selector.select(timeout=0.05):
            if not os.read(key.fileobj.fileno(), 4096):
                selector.unregister(key.fileobj)


def _validate_kata_config(config):
    def configured(key):
        found = []

        def visit(value):
            if not isinstance(value, dict):
                return
            if key in value:
                found.append(value[key])
            for child in value.values():
                visit(child)

        visit(config)
        if len(found) != 1:
            raise EngineError("The fixed Kata policy is ambiguous or incomplete.")
        return found[0]

    if any(configured(key) != value for key, value in KATA_POLICY.items()):
        raise EngineError(
            "The installed Kata policy does not match the approved profile."
        )


def _validated_image_metadata(image):
    if isinstance(image, list):
        if len(image) != 1:
            raise EngineError("The fixed reference image metadata is invalid.")
        image = image[0]
    if not isinstance(image, dict):
        raise EngineError("The fixed reference image metadata is invalid.")
    digests = image.get("RepoDigests")
    config = image.get("Config", {})
    if (
        not isinstance(digests, list)
        or any(not isinstance(item, str) for item in digests)
        or not isinstance(config, dict)
    ):
        raise EngineError("The fixed reference image metadata is invalid.")
    expected_digest = IMAGE.split("@", 1)[1]
    digest_matches = any(
        item.count("@") == 1
        and bool(item.split("@", 1)[0])
        and item.split("@", 1)[1] == expected_digest
        for item in digests
    )
    if not digest_matches or config.get("Volumes"):
        raise EngineError(
            "The fixed reference image is unavailable or declares volumes."
        )
    return image.get("Id") or image.get("ID")


def _listed_image_id(output):
    if not isinstance(output, bytes):
        raise EngineError("The fixed reference image listing is invalid.")
    try:
        lines = output.decode("utf-8", errors="strict").splitlines()
        rows = [json.loads(line) for line in lines]
    except (UnicodeError, ValueError, TypeError) as exc:
        raise EngineError("The fixed reference image listing is invalid.") from exc
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise EngineError("The fixed reference image listing is invalid.")
    for row in rows:
        if (
            not isinstance(row.get("Digest"), str)
            or not row["Digest"]
            or not isinstance(row.get("ID"), str)
            or not row["ID"]
        ):
            raise EngineError("The fixed reference image listing is invalid.")
    expected_digest = IMAGE.split("@", 1)[1]
    matches = [row for row in rows if row["Digest"] == expected_digest]
    if len(matches) != 1 or not IMAGE_ID.fullmatch(matches[0]["ID"]):
        raise EngineError("The fixed reference image is unavailable or ambiguous.")
    return matches[0]["ID"]


def _verified_image_id():
    listing = _command(
        ["images", "--no-trunc", "--digests", "--format", "{{json .}}"]
    ).stdout
    image_id = _listed_image_id(listing)
    try:
        image = json.loads(_command(["image", "inspect", image_id]).stdout)
    except (ValueError, TypeError) as exc:
        raise EngineError("The fixed reference image metadata is invalid.") from exc
    inspected_id = _validated_image_metadata(image)
    if not isinstance(inspected_id, str) or not IMAGE_ID.fullmatch(inspected_id):
        raise EngineError("The fixed reference image metadata is invalid.")
    return image_id


def reference_argv(program, arguments, name, token):
    _validated_identity(name, token)
    return [
        *_base_cli(),
        "run",
        "--pull=never",
        "--name",
        name,
        "--label",
        LABEL + "=" + token,
        "--runtime",
        HANDLER,
        "--network",
        "none",
        "--read-only",
        "--user",
        "65534:65534",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "32",
        "--ulimit",
        "nproc=32:32",
        "--tmpfs",
        "/scratch:rw,noexec,nosuid,nodev,size=8m,mode=1777",
        "--workdir",
        "/scratch",
        "--log-driver",
        "none",
        "--env",
        "HOME=/nonexistent",
        IMAGE,
        "python3",
        "-I",
        "-B",
        "-u",
        "-c",
        program,
        *arguments,
    ]


def _validated_identity(name, token):
    if (
        not isinstance(name, str)
        or not RESOURCE_NAME.fullmatch(name)
        or not isinstance(token, str)
        or not TOKEN.fullmatch(token)
    ):
        raise ValueError("Invalid reference runtime resource identity.")


def _live_host_checks():
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or os.geteuid() != 0
    ):
        raise EngineError(
            "The reference profile requires its dedicated x86_64 Linux host."
        )
    if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise EngineError("The reference profile requires cgroups v2.")
    for path in (NERDCTL, CONTAINERD, KATA_RUNTIME):
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or not os.access(path, os.X_OK)
        ):
            raise EngineError("A fixed reference runtime executable is unavailable.")
    if (
        not KATA_SHIM.is_symlink()
        or KATA_SHIM.resolve() != Path("/opt/kata/bin/containerd-shim-kata-v2")
        or not KATA_SHIM.resolve().is_file()
        or not os.access(KATA_SHIM.resolve(), os.X_OK)
    ):
        raise EngineError("The fixed Kata containerd handler is unavailable.")
    config_info = KATA_CONFIG.lstat()
    if KATA_CONFIG.is_symlink() or not stat.S_ISREG(config_info.st_mode):
        raise EngineError("The fixed Kata policy file is unavailable.")
    try:
        config = tomllib.loads(KATA_CONFIG.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise EngineError("The fixed Kata policy file is invalid.") from exc

    _validate_kata_config(config)
    socket_info = SOCKET.lstat()
    if SOCKET.is_symlink() or not stat.S_ISSOCK(socket_info.st_mode):
        raise EngineError("The fixed local containerd socket is unavailable.")
    descriptor = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
    try:
        if fcntl.ioctl(descriptor, 0xAE00, 0) != 12:
            raise EngineError("The host KVM API is unsupported.")
        vm_descriptor = fcntl.ioctl(descriptor, 0xAE01, 0)
        os.close(vm_descriptor)
    finally:
        os.close(descriptor)
    versions = {
        "nerdctl": _fixed_command(NERDCTL, ["--version"]),
        "containerd": _fixed_command(CONTAINERD, ["--version"]),
        "kata": _fixed_command(KATA_RUNTIME, ["--version"]),
    }
    if not all(
        expected in versions[name]
        for name, expected in (
            ("nerdctl", "2.3.5"),
            ("containerd", "2.3.3"),
            ("kata", "4.2.0"),
        )
    ):
        raise EngineError("The installed reference runtime versions are not approved.")
    _fixed_command(KATA_RUNTIME, ["check"], timeout=30, env=KATA_CHECK_ENV)
    return _verified_image_id()


class ReferenceLinuxRuntime:
    """One fixed-program controller; it never receives a source path or mount."""

    def __init__(self, *, readiness=False):
        try:
            self.image_id = _live_host_checks()
        except OSError as exc:
            raise EngineError(
                "The fixed reference runtime host is unavailable."
            ) from exc
        self.readiness = self._readiness_probe() if readiness else None

    @staticmethod
    def resource_name(run_id: str) -> str:
        if (
            not isinstance(run_id, str)
            or len(run_id) != 32
            or any(c not in "0123456789abcdef" for c in run_id)
        ):
            raise ValueError("A bounded run identifier is required.")
        return "prism-m23-run-" + run_id

    def inspect_owned(self, name: str, token: str):
        _validated_identity(name, token)
        result = _command(["inspect", name], check=False)
        if result.returncode:
            remaining = _command(
                ["ps", "-aq", "--filter", "name=" + name]
            ).stdout.strip()
            if not remaining:
                return None
            raise EngineError("The named reference resource could not be inspected.")
        try:
            info = json.loads(result.stdout)[0]
        except (ValueError, TypeError, IndexError) as exc:
            raise EngineError(
                "The named reference resource returned invalid metadata."
            ) from exc
        if info.get("Config", {}).get("Labels", {}).get(LABEL) != token:
            raise EngineError(
                "Reference resource ownership did not match; mutation was refused."
            )
        return info

    def reconcile(self, name: str, token: str, *, settle_seconds=0) -> bool:
        if self.inspect_owned(name, token) is not None:
            _command(["kill", "--signal", "KILL", name], check=False)
            _command(["rm", "--force", name])
        deadline = time.monotonic() + settle_seconds
        while True:
            if self.inspect_owned(name, token) is not None:
                raise EngineError("Reference resource removal was not confirmed.")
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        return True

    def owned_resources(self):
        output = _command(
            ["ps", "-a", "--filter", "label=" + LABEL, "--format", "{{.Names}}"]
        ).stdout.decode("utf-8", errors="strict")
        names = [line for line in output.splitlines() if line]
        if any(not name.startswith("prism-m23-") for name in names):
            raise EngineError("Unexpected resource name in the reference namespace.")
        return sorted(names)

    def all_resources(self):
        try:
            output = _command(["ps", "-a", "--format", "{{.Names}}"]).stdout.decode(
                "utf-8", errors="strict"
            )
        except UnicodeError as exc:
            raise EngineError(
                "The reference namespace returned invalid names."
            ) from exc
        return sorted(line for line in output.splitlines() if line)

    def _readiness_probe(self):
        token = uuid.uuid4().hex
        name = "prism-m23-ready-" + token
        program = (
            "import json,platform,pathlib;print(json.dumps({"
            '"ready":True,"guest_kernel":platform.release(),'
            '"guest_boot_id":pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()}))'
        )
        result = self._execute(
            program,
            [],
            timeout=10,
            cancel=None,
            name=name,
            token=token,
            result_action="readiness",
        )
        try:
            output = json.loads(result.stdout)
        except ValueError as exc:
            raise EngineError(
                "The reference readiness guest returned invalid output."
            ) from exc
        if (
            not isinstance(output, dict)
            or result.exit_code != 0
            or result.stop_reason != "exited"
            or not result.cleaned_up
            or output.get("ready") is not True
            or not output.get("guest_kernel")
            or output.get("guest_kernel") == platform.release()
            or not output.get("guest_boot_id")
        ):
            raise EngineError(
                "The reference readiness guest did not establish Kata execution."
            )
        return {
            "ready": True,
            "profile": PROFILE,
            "runtime_handler": HANDLER,
            "image": IMAGE,
            "image_id": self.image_id,
            "guest_kernel": output["guest_kernel"],
            "guest_boot_id": output["guest_boot_id"],
        }

    def run(
        self, action, argument=None, *, timeout=10, cancel=None, name=None, token=None
    ):
        if action != "json-check":
            raise ValueError("The reference profile accepts only the fixed JSON check.")
        args = probe_arguments(action, argument)
        program_hash = hashlib.sha256(action_program(action).encode()).hexdigest()
        if cancel is not None and cancel.is_set():
            return RunResult(
                action,
                -1,
                "cancelled",
                False,
                "",
                "",
                False,
                True,
                0.0,
                self.image_id,
                program_hash,
                runtime_handler=HANDLER,
            )
        prefix = (
            "import json,platform,pathlib;print(json.dumps({"
            '"guest_kernel":platform.release(),'
            '"guest_boot_id":pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()}),flush=True)\n'
        )
        result = self._execute(
            prefix + action_program(action),
            args,
            timeout=timeout,
            cancel=cancel,
            name=name or "prism-m23-run-" + uuid.uuid4().hex,
            token=token or uuid.uuid4().hex,
        )
        result.runtime_handler = HANDLER
        result.program_sha256 = program_hash
        if result.stop_reason != "exited":
            return result
        first, separator, rest = result.stdout.partition("\n")
        try:
            metadata = json.loads(first)
        except ValueError as exc:
            raise EngineError("The reference guest provenance was invalid.") from exc
        if (
            not isinstance(metadata, dict)
            or not separator
            or not metadata.get("guest_kernel")
            or not metadata.get("guest_boot_id")
        ):
            raise EngineError("The reference guest provenance was incomplete.")
        if metadata["guest_kernel"] == platform.release():
            raise EngineError(
                "The reference workload did not report a separate guest kernel."
            )
        result.stdout = rest
        result.guest_kernel = metadata["guest_kernel"]
        result.guest_boot_id = metadata["guest_boot_id"]
        return result

    def _execute(
        self,
        program,
        arguments,
        *,
        timeout,
        cancel,
        name,
        token,
        result_action="json-check",
    ):
        if not 0.1 <= timeout <= 30:
            raise ValueError("Reference runtime timeout is outside its fixed bound.")
        argv = reference_argv(program, arguments, name, token)
        started = time.monotonic()
        reason = "exited"
        stdout, stderr = bytearray(), bytearray()
        try:
            process = subprocess.Popen(
                argv,
                env=ENV,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise EngineError("The fixed reference client could not start.") from exc
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, stdout)
                selector.register(process.stderr, selectors.EVENT_READ, stderr)
                while selector.get_map():
                    for key, _ in selector.select(timeout=0.05):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        remaining = OUTPUT_LIMIT - len(stdout) - len(stderr)
                        key.data.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            reason = "output_limit"
                    if reason == "exited" and cancel is not None and cancel.is_set():
                        reason = "cancelled"
                    if reason == "exited" and time.monotonic() - started >= timeout:
                        reason = "timeout"
                    if reason != "exited":
                        stop_drain = threading.Event()
                        drain = threading.Thread(
                            target=_discard_until_stopped,
                            args=(selector, stop_drain),
                            daemon=True,
                        )
                        drain.start()
                        try:
                            self.reconcile(name, token)
                        finally:
                            stop_drain.set()
                            drain.join(timeout=1)
                            if drain.is_alive():
                                raise EngineError(
                                    "Reference output drain did not stop."
                                )
                        break
            try:
                exit_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=5)
                raise EngineError(
                    "The bounded reference client did not terminate."
                ) from exc
        finally:
            try:
                cleaned = self.reconcile(name, token)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()
        return RunResult(
            result_action,
            exit_code,
            reason,
            exit_code == 137,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            reason == "output_limit",
            cleaned,
            round(time.monotonic() - started, 3),
            self.image_id,
            hashlib.sha256(program.encode()).hexdigest(),
        )


class RuntimeRegistry:
    def __init__(self, *, profile="development", socket=None, reference=None):
        if profile not in ("development", "reference-linux"):
            raise ValueError("Unknown runtime profile.")
        self.profile = profile
        self.socket = socket
        self.reference = reference
        self.readiness = None
        self.blocked = False
        self.activated = False
        if profile == "reference-linux":
            self.reference = reference or ReferenceLinuxRuntime()

    def activate(self):
        if self.profile != "reference-linux" or self.blocked:
            return
        if not self.activated:
            self.readiness = self.reference._readiness_probe()
            self.reference.readiness = self.readiness
            self.activated = True

    def reconcile(self, profile, resource, token):
        if profile != "reference-linux" or self.reference is None:
            raise EngineError("This runtime profile has no durable reconciler.")
        return self.reference.reconcile(resource, token, settle_seconds=3)

    def assert_ready(self, profile):
        if profile != self.profile or self.blocked:
            raise EngineError("The approved runtime profile is not ready on this host.")
        if profile == "reference-linux":
            if (
                not self.activated
                or not self.readiness
                or not self.readiness.get("ready")
            ):
                raise EngineError(
                    "The reference runtime readiness gate has not passed."
                )
            if self.reference.all_resources():
                raise EngineError("The dedicated reference namespace is not empty.")
            image_id = _live_host_checks()
            if image_id != self.reference.image_id:
                raise EngineError("The fixed reference image identity changed.")
