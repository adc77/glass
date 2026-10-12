"""An independent sample domain exercises the same actor interface and exact recovery."""

from pathlib import Path
import tempfile
import unittest

from seam import Fault, checkpoint_ref, run_product, write_checkpoint
from seam.canon import dumps, loads

from glass.actors import (
    SECOND,
    metrics,
    observe,
    person,
    run_scenario,
    translate,
    write_scenario,
)
from glass.pipeline import DUE_NS


class GlassActorTests(unittest.TestCase):
    def test_arrivals_before_at_and_after_horizon_are_counted_inclusively(self):
        with tempfile.TemporaryDirectory() as directory:
            people = [
                person("before", start_ns=0),
                person("at", start_ns=SECOND),
                person("after", start_ns=SECOND + 1),
            ]
            result = run_scenario(
                Path(directory) / "run", actors=people, until_ns=SECOND
            )
            self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
            self.assertEqual(result.artifact["assertions"][0]["times"], 2)
            self.assertTrue(result.artifact["assertions"][0]["ok"])
            self.assertEqual(
                set(result.artifact["final_state"]["samples"]),
                {"before_sample", "at_sample"},
            )
            world = result.artifact["world_states"]["actors"]
            self.assertTrue(world["sealed"])
            self.assertEqual(world["actors"]["after"]["step"], 0)
            self.assertEqual(world["actors"]["after"]["next_ns"], SECOND + 1)
            tape = world["decisions"]
            replay = run_scenario(
                Path(directory) / "replay",
                actors=people,
                until_ns=SECOND,
                tape=tape,
            )
            self.assertEqual(replay.returncode, 0, (replay.stderr, replay.artifact))
            self.assertEqual(
                replay.artifact["final_state"], result.artifact["final_state"]
            )
            self.assertEqual(
                replay.artifact["world_states"]["lab"],
                result.artifact["world_states"]["lab"],
            )
            for field in ("actors", "decisions", "sealed", "started"):
                self.assertEqual(
                    replay.artifact["world_states"]["actors"][field], world[field]
                )

    def test_horizon_before_all_arrivals_seals_without_fabricating_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            people = [person(start_ns=2 * SECOND)]
            for label, tape in (("rules", None), ("replay", [])):
                with self.subTest(mode=label):
                    result = run_scenario(
                        Path(directory) / label,
                        actors=people,
                        until_ns=SECOND,
                        tape=tape,
                    )
                    self.assertEqual(
                        result.returncode, 0, (result.stderr, result.artifact)
                    )
                    self.assertEqual(result.artifact["assertions"][0]["times"], 0)
                    self.assertEqual(result.artifact["final_state"], {"samples": {}})
                    self.assertEqual(metrics(result.artifact)["unfinished_actors"], 1)
                    self.assertEqual(
                        result.artifact["world_states"]["actors"]["decisions"], []
                    )

    def test_zero_horizon_still_receives_actors_starting_at_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_scenario(
                Path(directory) / "run",
                actors=[person("at"), person("after", start_ns=1)],
                until_ns=0,
            )
            self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
            self.assertEqual(result.artifact["assertions"][0]["times"], 1)
            self.assertEqual(
                set(result.artifact["final_state"]["samples"]), {"at_sample"}
            )

    def test_missing_in_window_receipt_still_fails_the_assertion(self):
        with tempfile.TemporaryDirectory() as directory:
            actor = person()
            actor["memory"]["phase"] = "wait"
            result = run_scenario(Path(directory) / "run", actors=[actor], until_ns=0)
            self.assertEqual(result.returncode, 1, result.artifact)
            self.assertEqual(result.artifact["assertions"][0]["times"], 1)
            self.assertFalse(result.artifact["assertions"][0]["ok"])

    def test_invalid_horizons_are_refused_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, horizon in enumerate((True, None, "1", 1.0, -1, 2**63)):
                with self.subTest(horizon=horizon):
                    target = Path(directory) / f"run-{index}"
                    with self.assertRaises(ValueError):
                        write_scenario(target, until_ns=horizon)
                    self.assertFalse(target.exists())

    def test_actors_wait_for_the_busy_bench_and_report_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_scenario(Path(directory) / "run")
            self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
            outcome = metrics(result.artifact)
            self.assertEqual(outcome["released"], 2)
            self.assertEqual(outcome["filed_reports"], 2)
            self.assertEqual(outcome["unfinished_actors"], 0)
            actions = result.artifact["world_states"]["actors"]["decisions"]
            first_reading = next(
                row
                for row in actions
                if row["delivery"] is not None
                and row["delivery"]["handler"] == "reading"
                and row["request"]["actor_id"] == "alice"
            )
            self.assertEqual(first_reading["request"]["at_ns"], 6 * SECOND)
            self.assertNotIn("actors", result.artifact["final_state"])

    def test_recorded_replay_twice_and_resume_match_actual_product_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = run_scenario(root / "baseline")
            self.assertEqual(baseline.returncode, 0, baseline.artifact)
            tape = baseline.artifact["world_states"]["actors"]["decisions"]
            one, two = (
                run_scenario(root / "one", tape=tape),
                run_scenario(root / "two", tape=tape),
            )
            self.assertEqual(one.returncode, 0, (one.stderr, one.artifact))
            self.assertEqual(one.artifact, two.artifact)
            self.assertEqual(
                one.artifact["final_state"], baseline.artifact["final_state"]
            )
            path = write_scenario(root / "resumable")
            base = loads(Path(path).read_bytes())

            def execute(value, label):
                target = Path(path).parent / f"{label}.json"
                target.write_text(dumps(value))
                return run_product(
                    "glass.actor_runtime",
                    str(target),
                    base["namespace"],
                    str(target.parent / f"{label}-out.json"),
                )

            complete = execute(base, "complete")
            paused = execute(
                {**base, "checkpoint_after": 10, "assertions": []}, "paused"
            )
            self.assertEqual(paused.returncode, 4, paused.artifact)
            checkpoint = Path(path).parent / "checkpoint.json"
            write_checkpoint(checkpoint, paused.artifact["checkpoint"])
            resumed = execute({**base, "resume": checkpoint_ref(checkpoint)}, "resumed")
            self.assertEqual(resumed.returncode, 0, (resumed.stderr, resumed.artifact))
            for field in (
                "digest",
                "events",
                "timers",
                "port_calls",
                "world_states",
                "final_state",
            ):
                self.assertEqual(
                    resumed.artifact[field], complete.artifact[field], field
                )

    def test_ownership_and_observations_exclude_other_samples_and_private_ids(self):
        actor = {"id": "alice", "profile": {"kind": "blood"}, "memory": {}}
        state = {
            "samples": {
                "bob_sample": {"status": "running", "retested": False, "id": "private"}
            }
        }
        self.assertEqual(observe(actor, state, 0), {"status": None, "retested": False})
        with self.assertRaises(Fault):
            translate(
                actor,
                {"type": "reading", "body": {"sample": "bob_sample", "value": 42}},
                "act1",
            )

    def test_retest_and_overdue_are_real_pipeline_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scrapped = run_scenario(
                root / "scrapped",
                actors=[person(value=150)],
                busy_remaining=0,
                lose_ack=False,
            )
            self.assertEqual(
                scrapped.returncode, 0, (scrapped.stderr, scrapped.artifact)
            )
            self.assertEqual(metrics(scrapped.artifact)["scrapped"], 1)
            overdue = run_scenario(
                root / "overdue",
                actors=[person(reading_delay_ns=DUE_NS + SECOND)],
                busy_remaining=0,
                lose_ack=False,
                until_ns=DUE_NS + 2 * SECOND,
            )
            self.assertEqual(overdue.returncode, 0, (overdue.stderr, overdue.artifact))
            self.assertEqual(metrics(overdue.artifact)["overdue"], 1)
            readings = [
                row
                for row in overdue.artifact["world_states"]["actors"]["decisions"]
                if row["delivery"] is not None
                and row["delivery"]["handler"] == "reading"
            ]
            self.assertEqual(readings, [])
