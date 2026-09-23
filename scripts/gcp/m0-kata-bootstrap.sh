#!/bin/bash
# Trusted, dedicated-host setup. Never run on a personal/shared Linux workspace.
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
python3 - <<'PY'
import json
from pathlib import Path
record = json.loads(Path('/var/lib/prism/m0-host-preflight.json').read_text())
assert record['prerequisites_ready'] and record['empty_kvm_vm_created']
PY

export DEBIAN_FRONTEND=noninteractive
timeout 180 apt-get update -qq
timeout 180 apt-get install -y -qq ca-certificates curl zstd
install -d -m 0700 /var/lib/prism/downloads
cd /var/lib/prism/downloads
curl --fail --location --retry 2 --max-time 600 \
  https://github.com/kata-containers/kata-containers/releases/download/4.2.0/kata-go-static-4.2.0-amd64.tar.zst \
  -o kata-go-static-4.2.0-amd64.tar.zst
printf '%s\n' '7dda31ca54b397cbf8165f620d6041872d3c45b7a77383ce0e86f76a06e103d0  kata-go-static-4.2.0-amd64.tar.zst' | sha256sum -c -
curl --fail --location --retry 2 --max-time 300 \
  https://github.com/containerd/nerdctl/releases/download/v2.3.5/nerdctl-full-2.3.5-linux-amd64.tar.gz \
  -o nerdctl-full-2.3.5-linux-amd64.tar.gz
printf '%s\n' 'b697295c623639734aaab737523c808fd3cc8d3046039fd94fff1744e4c317aa  nerdctl-full-2.3.5-linux-amd64.tar.gz' | sha256sum -c -
test ! -e /opt/kata
test ! -e /usr/local/bin/containerd
tar --zstd -xf kata-go-static-4.2.0-amd64.tar.zst -C /
tar -xzf nerdctl-full-2.3.5-linux-amd64.tar.gz -C /usr/local
ln -s /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
install -d -m 0755 /etc/kata-containers /etc/containerd
python3 - <<'PY'
import re
import tomllib
from pathlib import Path
source = Path('/opt/kata/share/defaults/kata-containers/configuration-qemu.toml')
content = source.read_text()
for key, value in {
    'disable_guest_seccomp': 'false',
    'enable_annotations': '[]',
    'default_memory': '512',
    'default_maxmemory': '1024',
    'default_vcpus': '1',
    'default_maxvcpus': '2',
    'seccompsandbox': '"on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"',
}.items():
    content, count = re.subn(r'^' + key + r'\s*=.*$', key + ' = ' + value, content, flags=re.M)
    assert count == 1, key
tomllib.loads(content)
Path('/etc/kata-containers/configuration.toml').write_text(content)
PY
/usr/local/bin/containerd config default > /etc/containerd/config.toml
cat > /etc/systemd/system/containerd.service <<'UNIT'
[Unit]
Description=Prism M0 dedicated containerd
After=network.target
[Service]
ExecStart=/usr/local/bin/containerd --config /etc/containerd/config.toml
Delegate=yes
KillMode=process
Restart=on-failure
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now containerd
# Type=simple can report started before containerd creates its API socket.
prism_ready=false
for attempt in {1..30}; do
  if timeout 2 nerdctl --namespace prism-m0 info >/dev/null 2>&1; then
    prism_ready=true
    break
  fi
  sleep 1
done
test "$prism_ready" = true
/opt/kata/bin/kata-runtime --version
/opt/kata/bin/kata-runtime check
nerdctl --version
containerd --version
timeout 180 nerdctl --namespace prism-m0 pull docker.io/library/python@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a
prism_probe_name=prism-m0-smoke-$(cat /proc/sys/kernel/random/uuid)
trap 'nerdctl --namespace prism-m0 rm --force "$prism_probe_name" >/dev/null 2>&1 || true' EXIT
timeout 90 nerdctl --namespace prism-m0 run --rm --name "$prism_probe_name" \
  --runtime io.containerd.kata.v2 --network none --read-only \
  --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges \
  --memory 128m --memory-swap 128m --cpus 0.5 --pids-limit 32 --ulimit nproc=32:32 \
  --tmpfs /scratch:rw,noexec,nosuid,nodev,size=8m,mode=1777 --workdir /scratch \
  --log-driver none --env HOME=/nonexistent \
  docker.io/library/python@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a \
  python3 -I -B -c 'import json,os,platform; print("PRISM_M0_KATA_BOOT "+json.dumps({"guest_kernel":platform.release(),"uid":os.getuid(),"pilot_ready":False}))'
printf '%s\n' PRISM_M0_KATA_BOOTSTRAP_COMPLETE
