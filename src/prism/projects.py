"""Conservative import of one explicit local project manifest format."""

import base64
import json
import re
import unicodedata
from pathlib import Path

from prism.engine import IMAGE
from prism.runtime import json_check_program
from prism.sharing import CONFIG_LIMIT, Denied, Source, digest

SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
JSON_PAYLOAD_LIMIT = 100 * 1024


def _visible(value, *, maximum):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value.strip() == value
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def json_check_payload(files):
    payload = json.dumps(
        [{k: file[k] for k in ("id", "sha256", "text")} for file in files],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    encoded = base64.b64encode(payload)
    if len(encoded) > JSON_PAYLOAD_LIMIT:
        raise Denied(
            "The selected JSON input is too large for this bounded action. Choose fewer or smaller JSON files.",
            400,
        )
    return encoded.decode("ascii")


def json_check_action(files, *, profile="development"):
    if profile not in ("development", "reference-linux"):
        raise Denied("The configured runtime profile is unavailable.", 400)
    names = sorted(f["name"] for f in files if f["name"].lower().endswith(".json"))
    if not names:
        raise Denied(
            "JSON verification requires at least one selected .json file.", 400
        )
    json_check_payload([file for file in files if file["name"] in names])
    program = json_check_program()
    action = {
        "id": "json-check",
        "label": "Check selected JSON files",
        "program": program,
        "program_sha256": digest(program),
        "image": IMAGE,
        "required_inputs": names,
        "profile": profile,
        "timeout_seconds": 10,
        "network": "none",
        "memory_mib": 128,
        "cpu": 0.5,
        "processes": 32,
        "scratch_mib": 8,
        "output_kib": 64,
        "runs_per_session": 6,
    }
    if profile == "reference-linux":
        action["runtime_handler"] = "io.containerd.kata.v2"
        action["profile_revision"] = 1
    return action


class ProjectSource(Source):
    source_kind = "local-project"

    def __init__(
        self, root, *, project_id, title, names, action, action_profile="development"
    ):
        super().__init__(root, names=names, strict_ancestors=True)
        self.project_id = project_id
        self.title = title
        self.action_id = action
        self.action_profile = action_profile

    @classmethod
    def from_manifest(cls, path: Path, *, action_profile="development"):
        if action_profile not in ("development", "reference-linux"):
            raise Denied("The configured runtime profile is unavailable.", 400)
        path = Path(path)
        if (
            not path.is_absolute()
            or path.name != ".prism-project.json"
            or any(part in (".", "..") for part in path.parts)
        ):
            raise Denied("Use an absolute .prism-project.json path.", 400)
        reader = Source(path.parent, names=(path.name,), strict_ancestors=True)
        raw = reader.read_bytes(path.name, CONFIG_LIMIT)
        try:
            config = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=lambda pairs: _unique_object(pairs),
            )
        except (UnicodeError, ValueError) as exc:
            raise Denied("The project manifest must be valid UTF-8 JSON.", 400) from exc
        if not isinstance(config, dict) or set(config) != {
            "schema",
            "id",
            "title",
            "files",
            "action",
        }:
            raise Denied("The project manifest has unsupported or missing fields.", 400)
        names = config["files"]
        if (
            type(config["schema"]) is not int
            or config["schema"] != 1
            or not isinstance(config["id"], str)
            or not SLUG.fullmatch(config["id"])
            or not _visible(config["title"], maximum=120)
            or not isinstance(names, list)
            or not 1 <= len(names) <= 8
            or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names)
            or config["action"] not in (None, "json-check")
        ):
            raise Denied("The project manifest is outside the supported format.", 400)
        for name in names:
            Source.checked_name(name)
        source = cls(
            path.parent,
            project_id=config["id"],
            title=config["title"],
            names=tuple(names),
            action=config["action"],
            action_profile=action_profile,
        )
        source.catalog()
        return source

    def freeze(self, names, purpose, mode):
        if (
            not isinstance(purpose, str)
            or not 5 <= len(purpose) <= 1000
            or not purpose.strip()
            or Source.has_control(purpose)
        ):
            raise Denied("Enter a bounded visible handoff purpose.", 400)
        evidence = self.freeze_files(names)
        if mode not in ("inspect", "verify"):
            raise Denied("Choose inspect or verify mode.", 400)
        if mode == "verify" and self.action_id != "json-check":
            raise Denied("This project has no approved verification action.", 400)
        action = (
            json_check_action(evidence, profile=self.action_profile)
            if mode == "verify"
            else None
        )
        return {
            "schema": 1,
            "project": self.title,
            "purpose": purpose,
            "mode": mode,
            "files": evidence,
            "action": action,
            "source_kind": self.source_kind,
        }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object member")
        result[key] = value
    return result
