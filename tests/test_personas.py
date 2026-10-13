"""Mixed goal fixtures retain ownership, paired inputs, and adverse product outcomes."""

import tempfile
import unittest
from pathlib import Path

from seam import run_product
from seam.canon import dumps, loads
from seam.dataset import dataset_ref
from seam.inference import InferenceRun, settings

from glass.actors import sample_policy, validate_profile
from glass.goal_experiments import measure, prepare, variants
from glass.personas import PROMPT, mixed_population
from tests.support import REPO, SEAM


def fake_provider(envelope):
    return {
        "request_id": envelope["request_id"],
        "model": envelope["model"],
        "returned_model": envelope["model"],
        "decision": sample_policy(envelope["request"], None),
        "usage": {"input_tokens": 1, "output_tokens": 1, "cost_microusd": 0},
    }


class PersonaTests(unittest.TestCase):
    def test_mixed_population_and_validation(self):
        population = mixed_population(191)
        self.assertEqual(population, mixed_population(191))
        self.assertNotEqual(
            population[0]["profile"]["reading_delay_ns"],
            population[1]["profile"]["reading_delay_ns"],
        )
        for actor in population:
            validate_profile(actor["profile"])
            self.assertEqual(actor["profile"]["persona"]["provenance"], "synthetic")
        invalid = {**population[0]["profile"], "persona": {}}
        with self.assertRaises(ValueError):
            validate_profile(invalid)
        with self.assertRaises(ValueError):
            validate_profile({**population[0]["profile"], "unknown": True})

    def test_adverse_outcomes_are_not_mislabeled_as_goals(self):
        for variant in variants():
            with (
                self.subTest(variant=variant["name"]),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = prepare(Path(directory) / "case", variant["parameters"], 191)
                result = run_product(
                    "glass.actor_runtime",
                    str(path),
                    "sim-sample-actors",
                    str(Path(directory) / "artifact.json"),
                    env={"PYTHONPATH": ":".join(p for p in (SEAM, REPO) if p)},
                )
                self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
                metrics = measure(result.artifact)
                self.assertEqual(metrics["filed_reports"], 2)
                self.assertEqual(metrics["unfinished_actors"], 0)
                self.assertEqual(
                    metrics["goal_achieved"], 2 if variant["name"] == "healthy" else 0
                )
                if variant["name"] == "faults":
                    self.assertEqual(metrics["scrapped"], 2)

    def test_goal_requests_use_real_handler_and_exact_offline_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = prepare(root / "case", variants()[0]["parameters"], 191)
            case = loads(Path(path).read_bytes())
            actors_path = Path(path).parent / case["datasets"]["actors"]["path"]
            population = loads(actors_path.read_bytes())
            population["mode"] = "inference"
            actors_path.write_text(dumps(population))
            case["datasets"]["actors"] = dataset_ref(actors_path, 0)
            case["datasets"]["actors"]["path"] = actors_path.name
            Path(path).write_text(dumps(case))
            with InferenceRun.create(
                root / "run",
                "glass.actor_runtime",
                path,
                config=settings("fake", "fake", PROMPT, max_calls=80),
                env={"PYTHONPATH": ":".join(p for p in (SEAM, REPO) if p)},
            ) as session:
                session.invoke(fake_provider)
            with InferenceRun(root / "run") as session:
                result = session.run(fake_provider)
                self.assertEqual(measure(result.artifact)["goal_achieved"], 2)
                one, two = session.replay(), session.replay()
                self.assertEqual(one.artifact, two.artifact)

    def test_invalid_parameters_refused_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, value in (
                ("qc_max", True),
                ("busy_remaining", -1),
                ("lose_ack", 1),
                ("until_ns", 0),
            ):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    prepare(
                        Path(directory) / key,
                        {**variants()[0]["parameters"], key: value},
                        191,
                    )
                self.assertFalse((Path(directory) / key).exists())
