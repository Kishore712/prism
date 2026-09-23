"""Optional public-HTTPS tunnel for the recipient listener.

Prism itself never opens a public socket: the recipient listener binds to
loopback. A tunnel client (ngrok or cloudflared) makes an outbound connection
to its provider, which publishes an HTTPS URL that forwards to that loopback
port. This wrapper only starts such a client, reads the URL it announces, and
stops it when Prism exits.

Trust note: the tunnel provider terminates TLS and can observe recipient
traffic (including shared content). Use one you trust.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

from .exceptions import PrismError


class TunnelError(PrismError):
    code = "TUNNEL_FAILED"
    exit_code = 21


_URL = re.compile(r"https://[A-Za-z0-9.-]+\.(?:ngrok[a-z.-]*|trycloudflare)\.(?:app|dev|io|com)")


@dataclass(frozen=True)
class _Provider:
    name: str
    binary_env: str
    default_binary: str

    def command(self, binary: str, port: int, domain: str | None) -> list[str]:
        if self.name == "ngrok":
            command = [binary, "http", str(port), "--log=stdout", "--log-format=json"]
            if domain:
                command += ["--url", domain]
            return command
        return [binary, "tunnel", "--url", f"http://127.0.0.1:{port}"]


PROVIDERS = {
    "ngrok": _Provider("ngrok", "PRISM_NGROK_BIN", "ngrok"),
    "cloudflared": _Provider("cloudflared", "PRISM_CLOUDFLARED_BIN", "cloudflared"),
}


def _announced_url(line: str) -> str | None:
    line = line.strip()
    if line.startswith("{"):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {}
        url = record.get("url")
        if isinstance(url, str) and url.startswith("https://"):
            return url.rstrip("/")
    match = _URL.search(line)
    return match.group(0).rstrip("/") if match else None


class Tunnel:
    def __init__(self, provider: str, port: int, *, domain: str | None = None) -> None:
        if provider not in PROVIDERS:
            raise TunnelError(f"Unknown tunnel provider '{provider}'. Use ngrok or cloudflared.")
        self._provider = PROVIDERS[provider]
        self._port = port
        self._domain = domain
        self._process: subprocess.Popen[str] | None = None
        self._tail: list[str] = []

    def start(self, *, timeout: float = 25.0) -> str:
        binary = os.environ.get(self._provider.binary_env) or shutil.which(
            self._provider.default_binary
        )
        if not binary:
            raise TunnelError(
                f"{self._provider.name} is not installed (or set {self._provider.binary_env})."
            )
        self._process = subprocess.Popen(
            self._provider.command(binary, self._port, self._domain),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        found: list[str] = []
        ready = threading.Event()

        def pump() -> None:
            assert self._process is not None and self._process.stdout is not None
            for line in self._process.stdout:
                self._tail = (self._tail + [line.rstrip()])[-15:]
                if not found:
                    url = _announced_url(line)
                    if url:
                        found.append(url)
                        ready.set()

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready.wait(0.1):
                return found[0]
            if self._process.poll() is not None:
                break
        detail = " | ".join(self._tail[-4:])
        self.stop()
        raise TunnelError(
            f"{self._provider.name} did not publish a URL"
            + (f": {detail}" if detail else ".")
        )

    def stop(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
