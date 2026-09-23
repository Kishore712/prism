"""Management-boundary regressions; real guest behavior is tested on Linux."""

import importlib.util
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "kata_spike", Path(__file__).resolve().parents[1] / "scripts/gcp/m0-kata-selftest.py"
)
spike = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spike)


def response(value=b"", code=0):
    return SimpleNamespace(returncode=code, stdout=value, stderr=b"")


class KataOwnershipTests(unittest.TestCase):
    def test_foreign_label_cannot_authorize_removal(self):
        info = [{"Config": {"Labels": {spike.LABEL: "someone-else"}}}]
        with patch.object(spike, "command", return_value=response(json.dumps(info).encode())) as command:
            with self.assertRaisesRegex(RuntimeError, "ownership mismatch"):
                spike.cleanup("prism-m0-probe-test", "expected")
        self.assertEqual(command.call_count, 1)

    def test_inspection_failure_does_not_mean_absent(self):
        with patch.object(spike, "command", side_effect=[response(code=1), response(b"still-present")]) as command:
            with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                spike.cleanup("prism-m0-probe-test", "expected")
        self.assertFalse(any(call.args[0][0] == "rm" for call in command.call_args_list))

    def test_owned_removal_requires_absence_confirmation(self):
        info = [{"Config": {"Labels": {spike.LABEL: "expected"}}}]
        with patch.object(spike, "command", side_effect=[
            response(json.dumps(info).encode()), response(), response(b"still-present"),
        ]):
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                spike.cleanup("prism-m0-probe-test", "expected")

    def test_invalid_probe_parameters_never_start_a_process(self):
        with patch.object(spike.subprocess, "Popen") as start:
            for action, value in [("shell", None), ("evaluate", True), ("evaluate", "7; id"), ("evaluate", -1)]:
                with self.assertRaises(ValueError):
                    spike.run_probe("", action, value)
        start.assert_not_called()


class OutputDrainTests(unittest.TestCase):
    def test_backpressured_writer_can_exit_during_discard(self):
        process = subprocess.Popen(
            [sys.executable, '-I', '-c', 'import os\nfor _ in range(256): os.write(1,b"x"*4096)'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stop = threading.Event()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            drain = threading.Thread(target=spike.discard_until_stopped, args=(selector, stop))
            drain.start()
            try:
                self.assertEqual(process.wait(timeout=5), 0)
                drain.join(timeout=1)
                self.assertFalse(drain.is_alive())
            finally:
                stop.set()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                drain.join(timeout=1)
                process.stdout.close()
                process.stderr.close()

    def test_silent_stream_does_not_block_stopping_drain(self):
        reader, writer = os.pipe()
        stop = threading.Event()
        with os.fdopen(reader, 'rb') as stream, selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_READ)
            drain = threading.Thread(target=spike.discard_until_stopped, args=(selector, stop))
            drain.start()
            try:
                stop.set()
                drain.join(timeout=1)
                self.assertFalse(drain.is_alive())
            finally:
                os.close(writer)


if __name__ == "__main__":
    unittest.main()
