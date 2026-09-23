"""Fixed JSON validation program executed inside the bounded container."""

import base64
import json
import math
import sys


class UnsupportedDepth(ValueError):
    pass


class UnsupportedNumber(ValueError):
    pass


def reject_constant(value):
    raise UnsupportedNumber("non-standard numeric constant")


def parse_integer(raw):
    if len(raw.lstrip("-")) > 256:
        raise UnsupportedNumber("integer too large")
    return int(raw)


def parse_number(raw):
    if len(raw.lstrip("-")) > 256:
        raise UnsupportedNumber("number too large")
    value = float(raw)
    if not math.isfinite(value):
        raise UnsupportedNumber("number outside the supported finite range")
    return value


def count_types(value, counts, depth=0):
    if depth > 80:
        raise UnsupportedDepth("JSON nesting is too deep")
    if isinstance(value, dict):
        counts["objects"] += 1
        counts["keys"] += len(value)
        for item in value.values():
            count_types(item, counts, depth + 1)
    elif isinstance(value, list):
        counts["arrays"] += 1
        counts["items"] += len(value)
        for item in value:
            count_types(item, counts, depth + 1)
    else:
        counts["scalars"] += 1


def kind(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    return "object"


def main():
    if len(sys.argv) != 2 or len(sys.argv[1]) > 100 * 1024:
        return 2
    try:
        payload = json.loads(base64.b64decode(sys.argv[1], validate=True))
        if not isinstance(payload, list) or not 1 <= len(payload) <= 8:
            return 2
        output = []
        for file in payload:
            if not isinstance(file, dict) or set(file) != {"id", "sha256", "text"}:
                return 2
            record = {"id": file["id"], "sha256": file["sha256"]}
            try:
                value = json.loads(
                    file["text"],
                    parse_constant=reject_constant,
                    parse_float=parse_number,
                    parse_int=parse_integer,
                )
                counts = {
                    "objects": 0,
                    "arrays": 0,
                    "keys": 0,
                    "items": 0,
                    "scalars": 0,
                }
                count_types(value, counts)
                record.update(valid=True, type=kind(value), counts=counts, error=None)
            except json.JSONDecodeError as exc:
                record.update(
                    valid=False,
                    type=None,
                    counts=None,
                    error={"reason": "syntax", "line": exc.lineno, "column": exc.colno},
                )
            except (RecursionError, UnsupportedDepth):
                record.update(
                    valid=False,
                    type=None,
                    counts=None,
                    error={"reason": "unsupported_depth", "line": None, "column": None},
                )
            except UnsupportedNumber:
                record.update(
                    valid=False,
                    type=None,
                    counts=None,
                    error={
                        "reason": "unsupported_number",
                        "line": None,
                        "column": None,
                    },
                )
            output.append(record)
        print(
            json.dumps({"action": "json-check", "files": output}, separators=(",", ":"))
        )
        return 0
    except (ValueError, UnicodeError, TypeError, KeyError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
