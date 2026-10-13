"""The packaged free demo runs real handlers and refuses ambiguous or destructive inputs."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from glass.demo import main, require, scripted_provider, verify
from tests.support import child_env


class DemoTests(unittest.TestCase):
    def test_controller_is_explicit_and_selects_only_offered_choice(self):
        envelope = {
            "request_id": "test",
            "provider": "scripted",
            "model": "first-valid-choice-v1",
            "request": {"choices": [{"id": "wait"}]},
        }
        response = scripted_provider(envelope)
        self.assertEqual(response["decision"], {"choice": "wait"})
        self.assertEqual(response["usage"]["cost_microusd"], 0)
        with self.assertRaises(KeyError):
            scripted_provider({**envelope, "request": {}})
        with self.assertRaises(RuntimeError):
            scripted_provider({**envelope, "provider": "real-provider"})

    def test_no_implicit_controller_or_output_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SystemExit):
                main(["--out", str(root / "missing")])
            self.assertFalse((root / "missing").exists())
            marker = root / "keep.txt"
            marker.write_text("preserve")
            with self.assertRaises(FileExistsError):
                main(["--out", str(root), "--scripted"])
            self.assertEqual(marker.read_text(), "preserve")

    def test_require_is_not_an_optimizable_assertion(self):
        with self.assertRaisesRegex(RuntimeError, "failure"):
            require(False, "failure")

    def test_verification_rejects_failed_runs_and_false_goal_claims(self):
        with patch("glass.demo.read_experiment", return_value={"status": "failed"}):
            with self.assertRaisesRegex(RuntimeError, "experiment did not pass"):
                verify(Path("unused"))
        row = {
            "status": "passed",
            "variant": "faults",
            "seed": 7,
            "metrics": {"goal_achieved": 2},
        }
        summary = {
            "status": "passed",
            "runs": [row] * 4,
            "comparisons": {"faults": {"paired_seeds": [7, 19]}},
        }
        viewer = Mock()
        viewer.overview.return_value = {"available": True}
        with (
            patch("glass.demo.read_experiment", return_value=summary),
            patch("glass.demo.open_experiments", return_value=[viewer]),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected goal outcome"):
                verify(Path("unused"))

    def test_fresh_process_demo_and_viewer_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo"
            proc = subprocess.run(
                [sys.executable, "-m", "glass.demo", "--out", str(root), "--scripted"],
                env=child_env(),
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(proc.returncode, 0, (proc.stdout, proc.stderr))
            result = json.loads(proc.stdout)
            self.assertEqual(result["controller"], "scripted-not-an-LLM")
            self.assertTrue(result["fresh_process_continuation"])
            self.assertEqual(result["byte_identical_replays"], 8)
            self.assertEqual(len(result["runs"]), 4)
            self.assertEqual(
                [row["metrics"]["goal_achieved"] for row in result["runs"]],
                [2, 2, 0, 0],
            )
            check = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "seam_viewer",
                    "--experiment",
                    f"Glass={root / 'experiment'}",
                    "--check",
                ],
                env=child_env(),
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(check.returncode, 0, (check.stdout, check.stderr))
