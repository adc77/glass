"""The packaged free demo runs real handlers and refuses ambiguous or destructive inputs."""

import json
import io
import shlex
from contextlib import redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from glass.demo import main, require, scripted_provider, verify, viewer_command, seconds
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
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(["--out", str(root), "--scripted"]), 2)
            self.assertIn("Choose a new --out", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
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
            "metrics": {"disposition_completed": 2, "preferred_outcome_achieved": 2},
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
            with self.assertRaisesRegex(RuntimeError, "unexpected preferred outcome"):
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
                [
                    row["metrics"]["preferred_outcome_achieved"]
                    for row in result["runs"]
                ],
                [2, 2, 0, 0],
            )
            self.assertEqual(
                [row["metrics"]["disposition_completed"] for row in result["runs"]],
                [2, 2, 2, 2],
            )
            self.assertIn("Starting four paired runs", proc.stderr)
            self.assertIn(
                "2 dispositions completed; 0 preferred releases; 2 scrapped",
                proc.stderr,
            )
            self.assertIn(viewer_command(root.resolve()), proc.stderr)
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
            command = shlex.split(viewer_command(root.resolve()))
            command = command[:-2] + ["--check"]
            elsewhere = subprocess.run(
                command,
                cwd=directory,
                env=child_env(),
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(elsewhere.returncode, 0, elsewhere.stderr)

    def test_missing_parent_error_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "missing parent" / "demo"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(["--out", str(root), "--scripted"]), 2)
            self.assertIn("Output parent does not exist", stderr.getvalue())
            self.assertIn("mkdir -p", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertFalse(root.parent.exists())

    def test_viewer_command_quotes_spaces_and_is_independent_of_working_directory(self):
        root = Path("/owned demo")
        command = shlex.split(viewer_command(root))
        self.assertEqual(command[0], sys.executable)
        self.assertIn("Glass=/owned demo/experiment", command)
        self.assertEqual(seconds(4_000_000_000), "4")
        self.assertEqual(seconds(1_000_000_001), "1.000000001")

    def test_permission_error_is_actionable_without_starting_experiment(self):
        stderr = io.StringIO()
        with (
            patch("glass.demo.Path.mkdir", side_effect=PermissionError),
            redirect_stderr(stderr),
        ):
            self.assertEqual(main(["--out", "/owned-demo", "--scripted"]), 2)
        self.assertIn("writable parent", stderr.getvalue())

    def test_file_as_output_parent_is_preserved_and_explained(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "a-file"
            parent.write_text("keep me")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(["--out", str(parent / "demo"), "--scripted"]), 2)
            self.assertIn("not a directory", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertEqual(parent.read_text(), "keep me")

    def test_verification_failure_never_prints_success(self):
        stderr = io.StringIO()
        with (
            patch("glass.demo.execute", side_effect=RuntimeError("replay mismatch")),
            redirect_stderr(stderr),
        ):
            self.assertEqual(main(["--out", "/owned-demo", "--scripted"]), 1)
        self.assertIn("Demo failed: replay mismatch", stderr.getvalue())
        self.assertNotIn("Engine checks passed", stderr.getvalue())
