"""Build a bounded M2.3 startup script for the installed reference Linux host."""

import argparse
import base64
import gzip
import hashlib
import json
import secrets
from pathlib import Path

UV_VERSION = "0.11.29"
UV_WHEEL = (
    "https://files.pythonhosted.org/packages/0d/28/"
    "3fa1c2061d588184840e3e4ab17e6d318c744bb2fcb15ddb0c29b5bc0bb3/"
    "uv-0.11.29-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
)
UV_SHA256 = "eec03a8b63d55915694db3af4e91324b39ced49e2aeac7af37851c7eb3f470ea"
UV_BINARY_MEMBER = "uv-0.11.29.data/scripts/uv"
PYTHON_VERSION = "3.13.5"
MAX_STARTUP_BYTES = 256 * 1024

FILES = (
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "scripts/m2-linux-runtime-check.py",
    "src/prism/__init__.py",
    "src/prism/__main__.py",
    "src/prism/cli.py",
    "src/prism/conversation.py",
    "src/prism/demo.py",
    "src/prism/doctor.py",
    "src/prism/engine.py",
    "src/prism/handoff.py",
    "src/prism/jobs.py",
    "src/prism/owner.py",
    "src/prism/projects.py",
    "src/prism/reference_runtime.py",
    "src/prism/runtime.py",
    "src/prism/selftest.py",
    "src/prism/sharing.py",
    "src/prism/webapp.py",
    "src/prism/worker.py",
    "src/prism/fixtures/__init__.py",
    "src/prism/fixtures/json_check.py",
    "src/prism/fixtures/probe.py",
)


def checked_bundle(root: Path) -> tuple[bytes, str]:
    bundle = {}
    for relative in FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Required reviewed file is unavailable: {relative}")
        bundle[relative] = path.read_text(encoding="utf-8")
    encoded = json.dumps(
        bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return encoded, hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    bundle, bundle_hash = checked_bundle(root)
    payload = base64.b64encode(gzip.compress(bundle, mtime=0)).decode("ascii")
    run_nonce = secrets.token_hex(4)
    allowlist = json.dumps(list(FILES), separators=(",", ":"))

    script = r"""#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
prism_stage=preflight
trap 'prism_code=$?; if test "$prism_code" -ne 0; then printf "PRISM_M23_FAILURE %s exit=%s\n" "$prism_stage" "$prism_code"; fi' EXIT

python3 - <<'PRISM_PREFLIGHT'
import json
from pathlib import Path
record = json.loads(Path('/var/lib/prism/m0-host-preflight.json').read_text())
assert record.get('prerequisites_ready') is True
assert record.get('empty_kvm_vm_created') is True
PRISM_PREFLIGHT

prism_stage=containerd-readiness
prism_ready=false
for prism_attempt in {1..30}; do
  if timeout 2 /usr/local/bin/nerdctl --address /run/containerd/containerd.sock --namespace prism-m0 info >/dev/null 2>&1; then
    prism_ready=true
    break
  fi
  sleep 1
done
test "$prism_ready" = true

prism_stage=workspace
export PRISM_M23_RUN_ROOT=/var/lib/prism/m23-integration/BUNDLE_PREFIX-RUN_NONCE
install -d -m 0700 /var/lib/prism/m23-integration
mkdir -m 0700 "$PRISM_M23_RUN_ROOT"
export PRISM_M23_PROJECT_ROOT="$PRISM_M23_RUN_ROOT/project"
mkdir -m 0700 "$PRISM_M23_PROJECT_ROOT"

python3 - <<'PRISM_BUNDLE'
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

expected = ALLOWLIST
raw = gzip.decompress(base64.b64decode("PAYLOAD"))
assert hashlib.sha256(raw).hexdigest() == "BUNDLE_HASH"
bundle = json.loads(raw)
assert sorted(bundle) == sorted(expected)
root = Path(os.environ["PRISM_M23_PROJECT_ROOT"])
for relative, content in bundle.items():
    path = PurePosixPath(relative)
    assert not path.is_absolute() and ".." not in path.parts and "." not in path.parts
    destination = root.joinpath(*path.parts)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as output:
        output.write(content)
PRISM_BUNDLE

prism_stage=uv-install
mkdir -m 0700 "$PRISM_M23_RUN_ROOT/downloads" "$PRISM_M23_RUN_ROOT/bin"
timeout 300 curl --fail --location --retry 2 --max-time 280 'UV_WHEEL' -o "$PRISM_M23_RUN_ROOT/downloads/uv.whl"
printf '%s  %s\n' 'UV_SHA256' "$PRISM_M23_RUN_ROOT/downloads/uv.whl" | sha256sum -c -
python3 - <<'PRISM_UV'
import os
from pathlib import Path
from zipfile import ZipFile
root = Path(os.environ["PRISM_M23_RUN_ROOT"])
with ZipFile(root / "downloads/uv.whl") as wheel:
    names = wheel.namelist()
    assert names.count("UV_BINARY_MEMBER") == 1
    binary = wheel.read("UV_BINARY_MEMBER")
destination = root / "bin/uv"
with destination.open("xb") as output:
    output.write(binary)
destination.chmod(0o700)
PRISM_UV
prism_uv_version="$("$PRISM_M23_RUN_ROOT/bin/uv" --version)"
printf 'PRISM_M23_UV_VERSION %s\n' "$prism_uv_version"
case "$prism_uv_version" in
  'uv PINNED_UV'|'uv PINNED_UV '*) ;;
  *) exit 1 ;;
esac

export UV_PYTHON_INSTALL_DIR="$PRISM_M23_RUN_ROOT/python"
export UV_CACHE_DIR="$PRISM_M23_RUN_ROOT/cache"
export UV_PROJECT_ENVIRONMENT="$PRISM_M23_RUN_ROOT/venv"
export UV_NO_CONFIG=1
export UV_NO_PROGRESS=1
prism_stage=python-install
timeout 600 "$PRISM_M23_RUN_ROOT/bin/uv" python install PYTHON_VERSION
export UV_PYTHON_DOWNLOADS=never

prism_stage=locked-sync
cd "$PRISM_M23_PROJECT_ROOT"
timeout 900 "$PRISM_M23_RUN_ROOT/bin/uv" sync --frozen --no-editable --python PYTHON_VERSION

prism_stage=integration-check
export PRISM_M23_RESULT_PATH="$PRISM_M23_RUN_ROOT/m2-linux-runtime-check.json"
timeout 1200 "$PRISM_M23_RUN_ROOT/bin/uv" run --frozen --no-editable --python PYTHON_VERSION \
  python scripts/m2-linux-runtime-check.py | tee "$PRISM_M23_RUN_ROOT/validation.log"
test -s "$PRISM_M23_RESULT_PATH"
printf 'PRISM_M23_STARTUP_COMPLETE bundle=%s run=%s\n' 'BUNDLE_HASH' 'RUN_NONCE'
"""
    replacements = {
        "BUNDLE_HASH": bundle_hash,
        "BUNDLE_PREFIX": bundle_hash[:16],
        "RUN_NONCE": run_nonce,
        "ALLOWLIST": allowlist,
        "UV_WHEEL": UV_WHEEL,
        "UV_SHA256": UV_SHA256,
        "UV_BINARY_MEMBER": UV_BINARY_MEMBER,
        "PINNED_UV": UV_VERSION,
        "PYTHON_VERSION": PYTHON_VERSION,
    }
    for old, new in replacements.items():
        script = script.replace(old, new)
    script = script.replace("PAYLOAD", payload)
    encoded_script = script.encode("utf-8")
    if len(encoded_script) >= MAX_STARTUP_BYTES:
        raise SystemExit(
            f"Startup metadata is {len(encoded_script)} bytes; limit is below {MAX_STARTUP_BYTES}."
        )
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(script)
    args.output.chmod(0o600)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "bytes": len(encoded_script),
                "bundle_sha256": bundle_hash,
                "run_nonce": run_nonce,
                "uv": UV_VERSION,
                "python": PYTHON_VERSION,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
