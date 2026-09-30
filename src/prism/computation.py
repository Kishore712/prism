"""Owner-approved Python copies: bounded protocol, no host execution of project code."""

import base64
import hashlib
import json
import math
import re
from importlib.resources import files

from prism.engine import IMAGE
from prism.sharing import FILE_LIMIT, TOTAL_LIMIT, Denied, Source, digest, packed

PAYLOAD_LIMIT = 160 * 1024
RESULT_LIMIT = 96 * 1024
TRANSPORT_LIMIT = 192 * 1024
ID = re.compile(r"^[0-9a-f]{24}$")


def program():
    return files("prism.fixtures").joinpath("python_workspace.py").read_text()


def python_action(shared, entrypoint, outputs, inputs=None):
    catalog = {f["name"]: f for f in shared}
    if (
        not isinstance(entrypoint, str)
        or entrypoint not in catalog
        or not entrypoint.endswith(".py")
        or not isinstance(outputs, list)
        or not 1 <= len(outputs) <= 2
        or any(not isinstance(n, str) or n not in catalog for n in outputs)
        or len(set(outputs)) != len(outputs)
        or any(not n.startswith("results/") or not n.endswith(".json") for n in outputs)
    ):
        raise Denied(
            "Choose one selected Python script and one or two selected results/*.json outputs.",
            400,
        )
    names = list(catalog)
    if inputs is not None and (
        not isinstance(inputs, list)
        or not inputs
        or any(not isinstance(n, str) or n not in catalog for n in inputs)
        or len(set(inputs)) != len(inputs)
        or entrypoint not in inputs
        or set(inputs) & set(outputs)
    ):
        raise Denied(
            "Execution inputs must be selected files, include the script and exclude result paths.",
            400,
        )
    if any(a != b and b.startswith(a + "/") for a in names for b in names):
        raise Denied("Selected file paths overlap.", 400)
    return {
        "id": "python-workspace",
        "label": "Run the approved Python working copy",
        "program": program(),
        "program_sha256": digest(program()),
        "image": IMAGE,
        "entrypoint": catalog[entrypoint]["id"],
        "entrypoint_name": entrypoint,
        "outputs": [{"id": catalog[n]["id"], "name": n} for n in sorted(outputs)],
        "required_inputs": sorted(
            inputs if inputs is not None else (n for n in catalog if n not in outputs)
        ),
        "profile": "development",
        "timeout_seconds": 30,
        "network": "none",
        "memory_mib": 256,
        "cpu": 1,
        "processes": 32,
        "scratch_mib": 32,
        "output_kib": 96,
        "runs_per_session": 6,
    }


def payload(action, current):
    selected = [
        {
            "id": f["id"],
            "name": f["name"],
            "text": f["text"],
            "sha256": digest(f["text"]),
        }
        for name in action["required_inputs"]
        for f in current
        if f["name"] == name
    ]
    value = {
        "entrypoint": action["entrypoint_name"],
        "inputs": selected,
        "outputs": action["outputs"],
    }
    encoded = base64.b64encode(packed(value).encode()).decode("ascii")
    if len(encoded) > PAYLOAD_LIMIT:
        raise Denied("The computation inputs exceed their transport limit.", 400)
    return {
        "policy_sha256": digest(packed(action)),
        "files": [{k: f[k] for k in ("id", "name", "sha256")} for f in selected],
        "outputs": action["outputs"],
    }, encoded


def decode_payload(encoded):
    """Recheck all staging names/bytes in the key-free worker before any filesystem write."""
    if not isinstance(encoded, str) or len(encoded) > PAYLOAD_LIMIT:
        raise ValueError("Invalid computation payload")
    try:
        value = json.loads(base64.b64decode(encoded, validate=True))
        if not isinstance(value, dict) or set(value) != {
            "entrypoint",
            "inputs",
            "outputs",
        }:
            raise ValueError()
        inputs, outputs = value["inputs"], value["outputs"]
        if (
            not isinstance(inputs, list)
            or not 1 <= len(inputs) <= 7
            or not isinstance(outputs, list)
            or not 1 <= len(outputs) <= 2
        ):
            raise ValueError()
        names, ids, size = set(), set(), 0
        for item in inputs + outputs:
            expected = (
                {"id", "name", "text", "sha256"} if item in inputs else {"id", "name"}
            )
            if (
                not isinstance(item, dict)
                or set(item) != expected
                or not isinstance(item["id"], str)
                or not ID.fullmatch(item["id"])
            ):
                raise ValueError()
            Source.checked_name(item["name"])
            if item["name"] in names or item["id"] in ids:
                raise ValueError()
            names.add(item["name"])
            ids.add(item["id"])
        for item in inputs:
            text = item["text"]
            if (
                not isinstance(text, str)
                or digest(text) != item["sha256"]
                or len(text.encode()) > FILE_LIMIT
            ):
                raise ValueError()
            size += len(text.encode())
        if size > TOTAL_LIMIT or any(
            a != b and b.startswith(a + "/") for a in names for b in names
        ):
            raise ValueError()
        if value["entrypoint"] not in {f["name"] for f in inputs} or not value[
            "entrypoint"
        ].endswith(".py"):
            raise ValueError()
        if any(
            not f["name"].startswith("results/") or not f["name"].endswith(".json")
            for f in outputs
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError, Denied):
        raise ValueError("Invalid computation payload") from None
    return value


def accepted_output(actual, parameters):
    """Untrusted guest data only; never extract an archive or execute results on the host."""
    if (
        not isinstance(actual, dict)
        or set(actual) != {"action", "files", "stdout", "stderr"}
        or actual["action"] != "python-workspace"
    ):
        raise ValueError("Invalid computation output")
    if any(
        not isinstance(actual[k], str) or len(actual[k].encode()) > 4096
        for k in ("stdout", "stderr")
    ):
        raise ValueError("Invalid computation logs")
    returned = actual["files"]
    if not isinstance(returned, list) or len(returned) != len(parameters["outputs"]):
        raise ValueError("Invalid result set")
    total = 0
    for item, expected in zip(returned, parameters["outputs"]):
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "name", "text", "sha256"}
            or {k: item[k] for k in ("id", "name")} != expected
        ):
            raise ValueError("Undeclared result")
        Source.checked_name(item["name"])
        if not isinstance(item["text"], str):
            raise TypeError("Invalid result bytes")
        raw = item["text"].encode()
        total += len(raw)
        if (
            len(raw) > FILE_LIMIT
            or total > RESULT_LIMIT
            or hashlib.sha256(raw).hexdigest() != item["sha256"]
        ):
            raise ValueError("Result limit or digest mismatch")

        def finite_number(value):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("Nonfinite JSON")
            return number

        parsed = json.loads(
            item["text"],
            parse_float=finite_number,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("Nonfinite JSON")
            ),
        )
        if not isinstance(parsed, (dict, list)) or Source.has_control(item["text"]):
            raise ValueError("Results must contain bounded JSON objects or arrays")
    return actual
