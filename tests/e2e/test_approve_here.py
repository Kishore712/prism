"""`prism mcp serve --approve-here` on a real pseudo-terminal.

The owner answers the approval prompt in the server's own terminal while a
recipient on separate state connects and waits. Also proves that excluded
content is unreachable through the real recipient path.
"""

from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    import pty
except ImportError:  # pragma: no cover - non-POSIX
    pty = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(pty is None, "requires a POSIX pseudo-terminal")
class ApproveHereTest(unittest.TestCase):
    def _run(self, *arguments, env_extra=None):
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env_extra or {})}
        completed = subprocess.run(
            [sys.executable, "-m", "prism", *arguments],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def test_owner_approves_from_the_server_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = ["--data-dir", str(root / "owner")]
            session = root / "s.jsonl"
            rows = []
            for index, (question, answer) in enumerate(
                [("Question one?", "Answer about OAuth."), ("Secret BLUEHERON-4471?", "noted")], 1
            ):
                rows.append({"type": "user", "uuid": f"u{index}", "message": {"role": "user", "content": question}})
                rows.append({"type": "assistant", "uuid": f"a{index}",
                             "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]}})
            session.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            ref = json.loads(self._run("capture", "list", str(session), "--adapter", "claude-code-session", "--json", *data).stdout)["conversations"][0]["conversation_ref"]
            draft = json.loads(self._run("capture", "import", str(session), "--adapter", "claude-code-session", "--conversation-ref", ref, "--json", *data).stdout)["draft_id"]
            self._run("draft", "select", draft, "--turns", "1", *data)
            snapshot = json.loads(self._run("draft", "publish", draft, "--yes", "--json", *data).stdout)["snapshot_id"]
            share = json.loads(self._run("share", "create", snapshot, "--json", *data).stdout)["share"]["share_id"]
            code = json.loads(self._run("invitation", "create", share, "--recipient-hint", "Alice", "--json", *data).stdout)["invitation_token"]
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            url = f"http://127.0.0.1:{port}/mcp"

            master, slave = pty.openpty()
            server = subprocess.Popen(
                [sys.executable, "-m", "prism", "mcp", "serve", *data, "--port", str(port),
                 "--approve-here", "--owner-label", "Kishore"],
                cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                stdin=slave, stdout=slave, stderr=slave, close_fds=True,
            )
            terminal = bytearray()

            def drain() -> None:
                while True:
                    try:
                        ready, _, _ = select.select([master], [], [], 0.2)
                        if ready:
                            terminal.extend(os.read(master, 65536))
                    except OSError:
                        return

            threading.Thread(target=drain, daemon=True).start()
            connect = None
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if subprocess.run(
                        [sys.executable, "-m", "prism", "mcp", "check", url],
                        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                        capture_output=True,
                    ).returncode == 0:
                        break
                    time.sleep(0.2)
                else:
                    self.fail("server never became ready:\n" + terminal.decode(errors="replace"))
                state = str(root / "recipient")
                connect = subprocess.Popen(
                    [sys.executable, "-m", "prism", "recipient", "connect", url, "--headless",
                     "--name", "Alice", "--state-dir", state, "--json"],
                    cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PRISM_INVITATION": code},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                deadline = time.monotonic() + 30
                while b"Approve?" not in terminal and time.monotonic() < deadline:
                    time.sleep(0.1)
                text = terminal.decode(errors="replace")
                self.assertIn("'Alice' redeemed an invitation you made for 'Alice'", text)
                self.assertIsNone(connect.poll(), "recipient must wait for approval")
                os.write(master, b"y\n")
                out, err = connect.communicate(timeout=60)
                self.assertEqual(connect.returncode, 0, err)
                manifest = json.loads(out)
                self.assertEqual(manifest["message_count"], 2)
                canary = json.loads(self._run("recipient", "query", url, "BLUEHERON-4471", "--state-dir", state, "--json").stdout)
                self.assertEqual(canary["context_blocks"], [])
                found = json.loads(self._run("recipient", "query", url, "OAuth", "--state-dir", state, "--json").stdout)
                self.assertEqual(len(found["context_blocks"]), 1)
            finally:
                for process in (connect, server):
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                os.close(slave)


if __name__ == "__main__":
    unittest.main()
