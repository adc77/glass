"""Glass populations use the generic runner without changing sample handler or durability semantics."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from seam.canon import loads
from seam.experiments import read_experiment, run_experiment

from glass.experiments import demo_plan, prepare
from tests.support import REPO, SEAM


class GlassExperimentTests(unittest.TestCase):
    def test_module_cli_uses_the_importable_adapter_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"PYTHONPATH": os.pathsep.join(path for path in (SEAM, REPO) if path)}
            process = subprocess.run(
                [sys.executable, "-m", "glass.experiments", "--out", str(root / "cli")],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(read_experiment(root / "cli")["status"], "passed")

    def test_seeded_personas_are_paired_and_changed_reading_delay_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = demo_plan()
            left = prepare(root / "left", spec["variants"][0]["parameters"], 7)
            right = prepare(root / "right", spec["variants"][1]["parameters"], 7)
            cases = [loads(path.read_bytes()) for path in (left, right)]
            self.assertEqual([case["seed"] for case in cases], [7, 7])
            populations = [
                loads((path.parent / case["datasets"]["actors"]["path"]).read_bytes())
                for path, case in zip((left, right), cases)
            ]
            for baseline, variant in zip(
                populations[0]["actors"], populations[1]["actors"]
            ):
                self.assertEqual(baseline["start_ns"], variant["start_ns"])
                self.assertEqual(
                    baseline["profile"]["value"], variant["profile"]["value"]
                )
                self.assertEqual(
                    variant["profile"]["reading_delay_ns"]
                    - baseline["profile"]["reading_delay_ns"],
                    14_000_000_000,
                )
            repeat = prepare(root / "repeat", spec["variants"][0]["parameters"], 7)
            self.assertEqual(
                (left.parent / "data-actors.json").read_bytes(),
                (repeat.parent / "data-actors.json").read_bytes(),
            )

    def test_actual_sample_handler_experiments_repeat_and_verify_from_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"PYTHONPATH": os.pathsep.join(path for path in (SEAM, REPO) if path)}
            left = run_experiment(root / "left", demo_plan(), env=env)
            right = run_experiment(root / "right", demo_plan(), env=env)
            self.assertEqual(left, right)
            self.assertEqual(left["status"], "passed")
            self.assertEqual(read_experiment(root / "left"), left)
            self.assertEqual(
                left["comparisons"]["slow_readings"]["paired_seeds"], [7, 19, 31]
            )
            self.assertLessEqual(
                left["comparisons"]["slow_readings"]["delta"]["filed_reports"]["max"], 0
            )
            self.assertGreater(
                left["comparisons"]["slow_readings"]["delta"]["unfinished_actors"][
                    "sum"
                ],
                0,
            )

    def test_invalid_population_and_parameters_do_not_become_defaults(self):
        for changes in (
            {"population_size": True},
            {"population_size": 9},
            {"reading_delay_ns": -1},
            {"until_ns": 0},
            {"extra": 1},
        ):
            with (
                self.subTest(changes=changes),
                tempfile.TemporaryDirectory() as directory,
                self.assertRaises(ValueError),
            ):
                parameters = copy.deepcopy(demo_plan()["variants"][0]["parameters"])
                prepare(Path(directory) / "run", {**parameters, **changes}, 7)
