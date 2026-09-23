"""End-to-end journey across two machines (separate processes and state).

Owner: capture a Claude Code thread -> drop a turn -> attach a resource ->
publish an immutable snapshot -> share -> invite -> serve.
Recipient (a different state dir, as if on another machine): connect through
the OAuth flow, wait for owner approval, then read the share.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = "EXCLUDED_TURN_SECRET_7f3a"
PRIVATE_FILE = "PRIVATE_FILE_SECRET_91bc"
DEMO_KEY = "AKIAJVQVGF6NXQZXKMPS"  # format-valid, not a vendor-documented placeholder


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _session_jsonl(path: Path) -> None:
    turns = [
        (f"What retrieval method should the prototype use? Demo key {DEMO_KEY}",
         "Use deterministic lexical retrieval first."),
        (f"Here is a private detail: {EXCLUDED}", "Noted, I will not repeat it."),
        ("How are recipients authorized?", "Through OAuth with owner approval of each grant."),
    ]
    records = [{"type": "custom-title", "customTitle": "Prism design thread"}]
    for index, (question, answer) in enumerate(turns, start=1):
        records.append(
            {"type": "user", "uuid": f"u{index}", "message": {"role": "user", "content": question}}
        )
        records.append(
            {
                "type": "assistant",
                "uuid": f"a{index}",
                "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]},
            }
        )
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")


class FullJourneyTest(unittest.TestCase):
    def _run(self, *arguments: str, env_extra: dict[str, str] | None = None, check: bool = True):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        environment.update(env_extra or {})
        completed = subprocess.run(
            [sys.executable, "-m", "prism", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if check and completed.returncode != 0:
            raise AssertionError(
                f"prism {' '.join(arguments)} failed ({completed.returncode})\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return completed

    def _json(self, *arguments: str, **kwargs):
        return json.loads(self._run(*arguments, "--json", **kwargs).stdout)

    def test_owner_shares_a_thread_and_a_recipient_on_another_machine_reads_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner, recipient = root / "owner", root / "recipient-machine"
            session = root / "session.jsonl"
            notes = root / "notes.md"
            secret_file = root / "private.txt"
            _session_jsonl(session)
            notes.write_text("# Notes\nRecipients authenticate with OAuth and owner approval.\n")
            secret_file.write_text(PRIVATE_FILE)
            data = ["--data-dir", str(owner)]

            # ---- owner: capture, curate, publish ---------------------------------
            inventory = self._json("capture", "list", str(session), "--adapter", "claude-code-session", *data)
            ref = inventory["conversations"][0]["conversation_ref"]
            captured = self._json(
                "capture", "import", str(session), "--adapter", "claude-code-session",
                "--conversation-ref", ref, *data,
            )
            draft_id = captured["draft_id"]
            self._run("draft", "select", draft_id, "--turns", "1,3", *data)  # drop turn 2
            self._run("draft", "resource", "add", draft_id, "--file", str(notes), *data)
            added = self._json("draft", "resource", "add", draft_id, "--file", str(secret_file), *data)
            leaked = next(a for a in added["attachments"] if a["display_name"] == "private.txt")
            self._run("draft", "resource", "remove", draft_id, leaked["attachment_id"], *data)

            # Projection layer: the demo AWS key in turn 1 blocks publication
            # until the owner explicitly reviews and allows it.
            blocked = self._run("draft", "publish", draft_id, "--yes", *data, check=False)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("must be resolved first", blocked.stderr)
            findings = self._json("draft", "findings", draft_id, *data)["findings"]
            secret = next(f for f in findings if f["finding_class"] == "secret")
            self.assertFalse(secret["decided"])
            self._run(
                "draft", "resolve", draft_id, secret["finding_id"],
                "--reason", "demo key for this test fixture, not a real credential", *data,
            )

            published = self._json("draft", "publish", draft_id, "--yes", *data)
            receipt = self._json("snapshot", "receipt", published["snapshot_id"], *data)
            self.assertTrue(receipt["signature_valid"])
            self.assertEqual(receipt["override_count"], 1)
            share = self._json("share", "create", published["snapshot_id"], "--name", "Design", *data)
            share_id = share["share"]["share_id"]
            invitation = self._json(
                "invitation", "create", share_id, "--recipient-hint", "Alice", *data
            )
            code = invitation["invitation_token"]

            # ---- owner: serve ----------------------------------------------------
            port = _free_port()
            base = f"http://127.0.0.1:{port}"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            server = subprocess.Popen(
                [sys.executable, "-m", "prism", "mcp", "serve", *data, "--host", "127.0.0.1",
                 "--port", str(port), "--owner-label", "Kishore"],
                cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            connect = None
            try:
                self._wait_ready(f"{base}/healthz", server)

                # the owner's self-check passes against the live server, and fails for a wrong URL
                checked = self._run("mcp", "check", f"{base}/mcp")
                self.assertIn("All checks passed", checked.stdout)
                wrong = self._run("mcp", "check", f"http://localhost:{port}/mcp", check=False)
                self.assertNotEqual(wrong.returncode, 0)  # resource URL != what the server advertises
                self.assertIn("--public-url", wrong.stdout)

                # unauthenticated access is refused with a discoverable challenge
                request = urllib.request.Request(
                    f"{base}/mcp", data=b"{}", headers={"Content-Type": "application/json"}
                )
                with self.assertRaises(urllib.error.HTTPError) as unauthenticated:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(unauthenticated.exception.code, 401)
                unauthenticated.exception.close()

                # ---- recipient machine: connect (blocks until the owner approves) ----
                connect = subprocess.Popen(
                    [sys.executable, "-m", "prism", "recipient", "connect", f"{base}/mcp",
                     "--headless", "--name", "Alice", "--state-dir", str(recipient), "--json"],
                    cwd=ROOT,
                    env={**environment, "PRISM_INVITATION": code},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                grant = self._wait_for_pending_grant(data)
                self.assertEqual(grant["recipient_label"], "Alice")
                still_running = connect.poll() is None
                self.assertTrue(still_running, "recipient must wait for owner approval")
                self._run("grant", "approve", grant["grant_id"], *data)
                stdout, stderr = connect.communicate(timeout=60)
                self.assertEqual(connect.returncode, 0, stderr)
                manifest = json.loads(stdout)
                self.assertEqual(manifest["provenance"]["shared_by"], "Kishore")
                self.assertEqual(manifest["provenance"]["content_kind"], "untrusted_shared_transcript")
                self.assertEqual(manifest["message_count"], 4)  # turns 1 and 3 only
                self.assertEqual(manifest["resource_count"], 1)
                self.assertEqual(manifest["resources"][0]["display_name"], "notes.md")

                # ---- recipient: read the share (stored credentials, no browser) ------
                shared = ["--state-dir", str(recipient)]
                url = f"{base}/mcp"
                query = self._json("recipient", "query", url, "How are recipients authorized?", *shared)
                self.assertTrue(any("OAuth" in block["content"] for block in query["context_blocks"]))
                first = manifest["messages"][0]["message_id"]
                message = self._json("recipient", "message", url, first, *shared)
                self.assertIn("retrieval", message["content"])
                resource = self._json("recipient", "resource", url, manifest["resources"][0]["resource_id"], *shared)
                self.assertIn("owner approval", resource["content"])

                # nothing the owner excluded ever reaches the recipient
                everything = json.dumps([manifest, query, message, resource])
                self.assertNotIn(EXCLUDED, everything)
                self.assertNotIn(PRIVATE_FILE, everything)
                self.assertNotIn("Noted, I will not repeat it", everything)
                leak_probe = self._json("recipient", "query", url, EXCLUDED, *shared)
                self.assertEqual(leak_probe["context_blocks"], [])
                # the demo key WAS explicitly reviewed and allowed, so it correctly
                # does reach the recipient as part of the turn the owner selected
                self.assertIn(DEMO_KEY, message["content"])

                # another machine cannot reuse the one-time invitation or the tokens
                other = self._run(
                    "recipient", "connect", url, "--headless", "--name", "Mallory",
                    "--state-dir", str(root / "mallory"),
                    env_extra={"PRISM_INVITATION": code}, check=False,
                )
                self.assertNotEqual(other.returncode, 0)
                self.assertIn("invalid, expired, or already used", other.stderr)
                not_connected = self._run(
                    "recipient", "manifest", url, "--state-dir", str(root / "mallory"), check=False
                )
                self.assertNotEqual(not_connected.returncode, 0)

                # owner sees who read what (metadata only) and can cut access instantly
                audit = self._json("audit", "list", *data)
                kinds = {(e["event_type"], e["detail"]) for e in audit}
                self.assertIn(("recipient_access", "query"), kinds)
                self.assertNotIn(EXCLUDED, json.dumps(audit))
                self._run("grant", "revoke", grant["grant_id"], *data)
                denied = self._run("recipient", "manifest", url, *shared, check=False)
                self.assertNotEqual(denied.returncode, 0)
                self.assertIn("ACCESS_DENIED", denied.stderr)
            finally:
                for process in (connect, server):
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate(timeout=5)

    def _wait_for_pending_grant(self, data: list[str]) -> dict:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for grant in self._json("grants", "list", *data):
                if grant["approval"] == "pending":
                    return grant
            time.sleep(0.3)
        raise AssertionError("the recipient never redeemed the invitation")

    @staticmethod
    def _wait_ready(url: str, process: subprocess.Popen) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                out, err = process.communicate()
                raise AssertionError(f"server exited early\n{out}\n{err}")
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        raise AssertionError("server did not become ready")


if __name__ == "__main__":
    unittest.main()
