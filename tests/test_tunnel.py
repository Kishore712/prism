from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prism.tunnel import Tunnel, TunnelError, _announced_url


def _fake(directory: str, body: str) -> str:
    path = Path(directory) / "fake-tunnel"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class TunnelTests(unittest.TestCase):
    def test_url_extraction_from_ngrok_json_and_cloudflared_text(self) -> None:
        self.assertEqual(
            _announced_url('{"msg":"started tunnel","url":"https://abc.ngrok-free.app"}'),
            "https://abc.ngrok-free.app",
        )
        self.assertEqual(
            _announced_url("|  https://quiet-river-1234.trycloudflare.com  |"),
            "https://quiet-river-1234.trycloudflare.com",
        )
        self.assertIsNone(_announced_url('{"msg":"no url here"}'))
        self.assertIsNone(_announced_url('{"url":"http://insecure.example"}'))

    def test_start_returns_the_announced_url_and_stop_terminates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = _fake(
                directory,
                'echo \'{"msg":"started tunnel","url":"https://demo.ngrok-free.app"}\'\nsleep 30\n',
            )
            with mock.patch.dict(os.environ, {"PRISM_NGROK_BIN": binary}):
                tunnel = Tunnel("ngrok", 8766)
                self.assertEqual(tunnel.start(timeout=5), "https://demo.ngrok-free.app")
                tunnel.stop()
                self.assertIsNotNone(tunnel._process.poll())

    def test_failure_surfaces_the_provider_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = _fake(directory, "echo 'authentication failed: no authtoken'\nexit 1\n")
            with mock.patch.dict(os.environ, {"PRISM_NGROK_BIN": binary}):
                with self.assertRaises(TunnelError) as caught:
                    Tunnel("ngrok", 8766).start(timeout=5)
                self.assertIn("authentication failed", str(caught.exception))

    def test_missing_binary_and_unknown_provider(self) -> None:
        with mock.patch.dict(os.environ, {"PRISM_CLOUDFLARED_BIN": "", "PATH": "/nonexistent"}):
            with self.assertRaises(TunnelError):
                Tunnel("cloudflared", 1).start(timeout=1)
        with self.assertRaises(TunnelError):
            Tunnel("nope", 1)


if __name__ == "__main__":
    unittest.main()


class ApproveHereFailsFastTests(unittest.TestCase):
    """A `--approve-here` refusal must not leave a tunnel process running."""

    def test_approve_here_without_a_tty_never_starts_the_tunnel(self) -> None:
        import tempfile
        from unittest import mock

        from prism import cli
        from prism.exceptions import ValidationError

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("sys.stdin") as stdin, mock.patch(
                "prism.cli.Tunnel"
            ) as tunnel_cls:
                stdin.isatty.return_value = False
                with self.assertRaises(ValidationError):
                    cli.mcp_serve(
                        Path(directory), "127.0.0.1", 0, None, None, None, None,
                        "ngrok", None, True,
                    )
                tunnel_cls.assert_not_called()
