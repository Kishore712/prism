"""Build a reviewed, synthetic-only GCP startup script for an installed M0 host."""

import argparse
import base64
import gzip
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
parser.add_argument("--recover-probe", help="Exact owned 32-hex probe token to clean up after a failed run")
args = parser.parse_args()
if args.recover_probe and not re.fullmatch(r'[0-9a-f]{32}', args.recover_probe):
    parser.error("Recovery requires the exact 32-hex probe token")
root = Path(__file__).resolve().parents[2]
bundle = {
    "m0-kata-selftest.py": (root / "scripts/gcp/m0-kata-selftest.py").read_text(),
    "probe.py": (root / "src/prism/fixtures/probe.py").read_text(),
}
payload = base64.b64encode(gzip.compress(json.dumps(bundle).encode(), mtime=0)).decode()
script = '''#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
prism_ready=false
for attempt in {1..30}; do
  if timeout 2 nerdctl --namespace prism-m0 info >/dev/null 2>&1; then
    prism_ready=true
    break
  fi
  sleep 1
done
test "$prism_ready" = true
timeout 180 nerdctl --namespace prism-m0 pull docker.io/library/python@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a
python3 - <<'PRISM_PY'
import base64, gzip, json
from pathlib import Path
bundle = json.loads(gzip.decompress(base64.b64decode("PAYLOAD")))
assert set(bundle) == {'m0-kata-selftest.py', 'probe.py'}
root = Path('/var/lib/prism/kata-spike')
root.mkdir(mode=0o700, exist_ok=True)
for name, content in bundle.items():
    (root / name).write_text(content)
PRISM_PY
RECOVERY
timeout 900 python3 /var/lib/prism/kata-spike/m0-kata-selftest.py
'''.replace("PAYLOAD", payload)
recovery = ""
if args.recover_probe:
    recovery = '''python3 - <<'RECOVER_PY'
import runpy
scope = runpy.run_path('/var/lib/prism/kata-spike/m0-kata-selftest.py', run_name='owned_cleanup')
scope['cleanup']('prism-m0-probe-TOKEN', 'TOKEN')
RECOVER_PY'''.replace('TOKEN', args.recover_probe)
script = script.replace('RECOVERY', recovery)
with args.output.open("x") as destination:
    destination.write(script)
args.output.chmod(0o600)
print(args.output)
