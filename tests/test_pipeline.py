"""Consumer tests through Glass's product entry and live timer adapter."""

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from glass.pipeline import DUE_NS, LEAK_ADDR, RETRY_NS, build
from seam import PortError, tape_from_artifact
from tests.support import CASES, RELEASE, RELEASE_DIGEST, child_env, run_glass, run_proc, write_json


def release_case():
    return json.loads(Path(RELEASE).read_text())


def simulate(case):
    with tempfile.TemporaryDirectory() as directory:
        path, output = Path(directory) / "case.json", Path(directory) / "out.json"
        write_json(path, case)
        proc = run_glass(str(path), case["namespace"], str(output))
        return proc, json.loads(output.read_text())


def calls(art, port):
    return [row for row in art["port_calls"] if row["port"] == port]


class ScriptTest(unittest.TestCase):
    def test_cases_pass_and_are_byte_identical_in_separate_processes(self):
        expected = {
            "release": "released",
            "busy-then-release": "released",
            "fail-then-release": "released",
            "overdue": "overdue",
            "dropped": "dropped",
            "qc-fail-scrapped": "scrapped",
            "qc-fail-retest-busy": "dropped",
            "lost-report-ack": "released",
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, (namespace, path) in CASES.items():
                with self.subTest(case=name):
                    artifacts = []
                    log = Path(directory) / "factories.log"
                    for index in range(2):
                        output = Path(directory) / f"{name}-{index}.json"
                        proc = run_glass(
                            path, namespace, str(output), extra={"GLASS_FACTORY_LOG": str(log)}
                        )
                        self.assertEqual(proc.returncode, 0, proc.stderr + output.read_text())
                        self.assertEqual(proc.stderr, "")
                        self.assertEqual(proc.stdout.strip(), str(output))
                        self.assertFalse(log.exists())
                        data = output.read_bytes()
                        self.assertEqual(data.count(b"\n"), 1)
                        self.assertTrue(data.endswith(b"\n"))
                        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
                        self.assertFalse(Path(str(output) + ".tmp").exists())
                        artifacts.append(data)
                    self.assertEqual(*artifacts)
                    art = json.loads(artifacts[0])
                    self.assertEqual(art["final_state"]["samples"]["s1"]["status"], expected[name])
                    self.assertEqual(
                        len(calls(art, "report")), 2 if name == "lost-report-ack" else 1
                    )
                    if name == "lost-report-ack":
                        reports = calls(art, "report")
                        self.assertEqual(reports[0]["error"], "timeout")
                        self.assertEqual(reports[0]["request"], reports[1]["request"])
                        self.assertEqual(len(art["backend_states"]["report"]["filed"]), 1)
                    if name == "release":
                        self.assertEqual(art["digest"], RELEASE_DIGEST)
                        self.assertEqual(art["clock"]["end_ns"], 1_000_000_000)
                    if name == "overdue":
                        self.assertEqual(art["clock"]["end_ns"], DUE_NS)
                    if name == "busy-then-release":
                        self.assertEqual(art["clock"]["end_ns"], RETRY_NS + 1_000_000_000)

    def test_arrivals_exercise_the_retry_and_retest_delays(self):
        for name, (_, path) in CASES.items():
            case = json.loads(Path(path).read_text())
            readings = [row["at_ns"] for row in case["arrivals"] if row["handler"] == "reading"]
            with self.subTest(case=name):
                self.assertTrue(all(0 < at < DUE_NS for at in readings))
                if name == "busy-then-release":
                    self.assertGreater(readings[0], RETRY_NS)
                if name in ("fail-then-release", "qc-fail-scrapped"):
                    self.assertGreater(readings[1], readings[0] + RETRY_NS)

    def test_multiple_samples_and_duplicate_arrivals_remain_independent(self):
        case = release_case()
        case["assertions"] = []
        case["arrivals"] = [
            {"at_ns": 0, "handler": "receive", "body": {"sample": "s1", "kind": "blood"}},
            {"at_ns": 1, "handler": "receive", "body": {"sample": "s2", "kind": "urine"}},
            {"at_ns": 2, "handler": "receive", "body": {"sample": "s1", "kind": "blood"}},
            {"at_ns": 3, "handler": "reading", "body": {"sample": "s1", "value": 42}},
            {"at_ns": 4, "handler": "reading", "body": {"sample": "s1", "value": "stale"}},
        ]
        for port, reply in (
            ("bench", {"status": "ready", "machine": "m1"}),
            ("qc", {"status": "pass"}),
            ("report", {"status": "filed"}),
        ):
            case["ports"][port]["replies"] = [
                {"match": {"$any": True}, "response": reply, "repeat": "forever"}
            ]
        proc, art = simulate(case)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        samples = art["final_state"]["samples"]
        self.assertEqual(samples["s1"]["status"], "released")
        self.assertEqual(samples["s2"]["status"], "overdue")
        self.assertNotEqual(samples["s1"]["id"], samples["s2"]["id"])
        self.assertEqual(len(calls(art, "bench")), 2)
        self.assertEqual(len(calls(art, "qc")), 1)
        self.assertEqual([row["request"]["sample"] for row in calls(art, "report")], ["s1", "s2"])
        self.assertEqual([row["outcome"] for row in art["timers"]], ["cancelled", "fired"])
        self.assertEqual(len({row["name"] for row in art["timers"]}), 2)

    def test_duplicate_receipt_cannot_change_a_samples_kind(self):
        case = release_case()
        case["assertions"] = []
        case["arrivals"][1] = {
            "at_ns": 1,
            "handler": "receive",
            "body": {"sample": "s1", "kind": "urine"},
        }
        proc, art = simulate(case)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(art["fault"]["code"], "bad_value")
        self.assertEqual(len(calls(art, "bench")), 1)

    def test_retry_takes_the_kind_from_state(self):
        case = release_case()
        case["assertions"] = []
        case["arrivals"] = [
            case["arrivals"][0],
            {"at_ns": 1, "handler": "retry", "body": {"sample": "s1", "kind": "urine"}},
        ]
        case["ports"]["bench"]["replies"] = [
            {"match": {"kind": "blood"}, "response": {"status": "busy"}},
            {"match": {"kind": "blood"}, "response": {"status": "ready", "machine": "m2"}},
        ]
        case["ports"]["report"]["replies"][0]["match"] = {"disposition": "overdue"}
        proc, art = simulate(case)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            [row["request"] for row in calls(art, "bench")], [{"kind": "blood"}, {"kind": "blood"}]
        )

    def test_failed_and_timed_out_reports_retry_the_same_intent(self):
        for first in ({"response": {"status": "failed"}}, {"error": "timeout"}):
            with self.subTest(first=first):
                case = release_case()
                case["version"] = 2
                case["assertions"] = []
                case["ports"]["report"]["replies"] = [
                    {"match": {"disposition": "released"}, **first},
                    {"match": {"disposition": "released"}, "response": {"status": "filed"}},
                ]
                proc, art = simulate(case)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                sample = art["final_state"]["samples"]["s1"]
                self.assertEqual(sample["status"], "released")
                self.assertEqual(sample["report_attempts"], 2)
                reports = calls(art, "report")
                self.assertEqual(reports[0]["request"], reports[1]["request"])
                pending = [
                    row["state"]["samples"]["s1"]
                    for row in art["state_snapshots"]
                    if "s1" in row["state"]["samples"]
                ]
                self.assertTrue(any(row["status"] == "report_pending" for row in pending))
                self.assertEqual(art["clock"]["end_ns"], RETRY_NS + 1_000_000_000)

    def test_report_exhaustion_does_not_claim_release(self):
        case = release_case()
        case["assertions"] = []
        case["ports"]["report"]["replies"] = [
            {"match": {"$any": True}, "response": {"status": "failed"}, "repeat": 2}
        ]
        proc, art = simulate(case)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(art["final_state"]["samples"]["s1"]["status"], "report_failed")
        self.assertEqual(len(calls(art, "report")), 2)

    def test_malformed_receipts_and_active_readings_fault_cleanly(self):
        for body in ({"kind": "blood"}, {"sample": "s1"}, {"sample": 7, "kind": "blood"}, []):
            with self.subTest(body=body):
                case = release_case()
                case["arrivals"] = [{"at_ns": 0, "handler": "receive", "body": body}]
                case["assertions"] = []
                proc, art = simulate(case)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(art["fault"]["code"], "bad_value")
                self.assertEqual(art["port_calls"], [])
        for value in (True, "bad", None, {}, []):
            with self.subTest(value=value):
                case = release_case()
                case["arrivals"][1]["body"]["value"] = value
                case["assertions"] = []
                proc, art = simulate(case)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(art["fault"]["code"], "bad_value")

    def test_missing_readings_are_ignored_and_bad_initial_rows_fault(self):
        for body in ({"sample": "s1"}, {}, []):
            with self.subTest(body=body):
                case = release_case()
                case["arrivals"][1]["body"] = body
                case["assertions"] = []
                case["ports"]["report"]["replies"][0]["match"] = {"disposition": "overdue"}
                proc, art = simulate(case)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(calls(art, "qc"), [])
                self.assertEqual(art["final_state"]["samples"]["s1"]["status"], "overdue")
        case = release_case()
        case["initial_state"] = {"samples": {"s1": {"public": "s1", "status": "queued"}}}
        case["arrivals"] = [
            {"at_ns": 0, "handler": "retry", "body": {"sample": "s1", "kind": "blood"}}
        ]
        case["assertions"] = []
        proc, art = simulate(case)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(art["fault"]["code"], "bad_value")
        self.assertEqual(art["port_calls"], [])

    def test_bad_port_replies_fault_instead_of_advancing_the_sample(self):
        for port, reply in (
            ("bench", {"status": "ready"}),
            ("bench", {"status": "ready", "machine": ""}),
            ("bench", []),
            ("qc", {"status": "maybe"}),
            ("report", {"status": "unknown"}),
        ):
            with self.subTest(port=port, reply=reply):
                case = release_case()
                case["assertions"] = []
                case["ports"][port]["replies"][0]["response"] = reply
                proc, art = simulate(case)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(art["fault"]["code"], "bad_value")

    def test_invalid_snapshot_status_counts_and_dispositions_fault_before_outbound_calls(self):
        base = {
            "id": "smp_test",
            "public": "s1",
            "kind": "blood",
            "status": "report_pending",
            "retested": False,
            "report_attempts": 1,
            "disposition": "released",
        }
        for change in (
            {"status": "unknown"},
            {"report_attempts": -1},
            {"report_attempts": 99},
            {"disposition": "unknown"},
            {"disposition": []},
            {"status": "running", "due": "t1", "machine": ""},
        ):
            with self.subTest(change=change):
                case = release_case()
                case["initial_state"] = {"samples": {"s1": {**base, **change}}}
                case["arrivals"] = [
                    {"at_ns": 0, "handler": "report_retry", "body": {"sample": "s1"}}
                ]
                case["assertions"] = []
                proc, art = simulate(case)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(art["fault"]["code"], "bad_value")
                self.assertEqual(art["port_calls"], [])

    def test_restored_pending_report_cannot_exceed_its_attempt_budget(self):
        case = release_case()
        case["initial_state"] = {
            "samples": {
                "s1": {
                    "id": "smp_test",
                    "public": "s1",
                    "kind": "blood",
                    "status": "report_pending",
                    "retested": False,
                    "report_attempts": 2,
                    "disposition": "released",
                }
            }
        }
        case["arrivals"] = [{"at_ns": 0, "handler": "report_retry", "body": {"sample": "s1"}}]
        case["assertions"] = [
            {"op": "port_not_called", "port": "report"},
            {"op": "state_is", "path": "samples.s1.status", "value": "report_failed"},
        ]
        proc, art = simulate(case)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(art["port_calls"], [])
        self.assertEqual(art["final_state"]["samples"]["s1"]["report_attempts"], 2)

    def test_replay_keeps_future_bodies_out_of_artifacts_and_mismatch_errors(self):
        case = release_case()
        proc, original = simulate(case)
        self.assertEqual(proc.returncode, 0)
        with tempfile.TemporaryDirectory() as directory:
            tape = Path(directory) / "ports.jsonl"
            tape.write_bytes(tape_from_artifact(original))
            hidden = {
                "format": "seam-tape",
                "version": 1,
                "at_ns": 1_000_000_001,
                "port": "bench",
                "request": {},
                "response": {"marker": "HIDDEN_SENTINEL", "future_float": 1.5},
            }
            with tape.open("a") as handle:
                handle.write(json.dumps(hidden) + "\n")
            case["ports"] = {
                port: {
                    "mode": "recording",
                    "tape": tape.name,
                    "cutoff_ns": 1_000_000_000,
                    "policy": "ordered",
                }
                for port in ("bench", "qc", "report")
            }
            path, output = Path(directory) / "case.json", Path(directory) / "out.json"
            write_json(path, case)
            proc = run_glass(str(path), case["namespace"], str(output))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            replay = json.loads(output.read_text())
            self.assertEqual(original["final_state"], replay["final_state"])
            self.assertNotIn("HIDDEN_SENTINEL", output.read_text() + proc.stdout + proc.stderr)
            case["arrivals"][1]["body"]["value"] = 99
            case["assertions"] = []
            write_json(path, case)
            proc = run_glass(str(path), case["namespace"], str(output))
            art = json.loads(output.read_text())
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(art["fault"]["code"], "tape_mismatch")
            self.assertIsNone(calls(art, "qc")[0]["response"])
            self.assertNotIn("HIDDEN_SENTINEL", output.read_text() + proc.stderr)

    def test_socket_leak_faults_before_any_outbound_call(self):
        case = release_case()
        case["assertions"] = []
        case["arrivals"][0]["body"]["leak"] = "socket"
        proc, art = simulate(case)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(art["fault"]["code"], "real_io")
        self.assertEqual(art["port_calls"], [])
        self.assertNotIn(LEAK_ADDR, json.dumps(art) + proc.stderr)


class LiveTest(unittest.TestCase):
    def test_live_report_client_initialization_timeout_is_recoverable_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            tape = Path(directory) / "live.jsonl"
            previous = {key: os.environ.get(key) for key in ("SEAM_RECORD", "SEAM_ARTIFACT")}
            os.environ.update(SEAM_RECORD="1", SEAM_ARTIFACT=str(tape))
            try:
                attempts, timers = [], []

                def factory():
                    attempts.append(1)
                    if len(attempts) == 1:
                        raise PortError("timeout")
                    return lambda request: {"status": "filed"}

                rt = build(report_factory=factory)
                rt.set_timer_backend(lambda *args: timers.append(args))
                rt.start_live()
                rt.deliver("receive", {"sample": "s1", "kind": "blood"})
                rt.deliver("reading", {"sample": "s1", "value": 42})
                self.assertEqual(rt.state_copy()["samples"]["s1"]["status"], "report_pending")
                self.assertTrue(rt.fire_timer(timers[-1][0]))
                self.assertEqual(rt.state_copy()["samples"]["s1"]["status"], "released")
                rt.close()
                reports = [
                    json.loads(line)
                    for line in tape.read_text().splitlines()
                    if json.loads(line)["port"] == "report"
                ]
                self.assertEqual(len(reports), 2)
                self.assertEqual(reports[0]["error"], "timeout")
                self.assertEqual(reports[0]["request"], reports[1]["request"])
                self.assertEqual(len(attempts), 2)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_module_without_sim_exits_cleanly_and_live_receipt_is_recorded(self):
        import sys

        proc = run_proc([sys.executable, "-m", "glass"], child_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        with tempfile.TemporaryDirectory() as directory:
            tape = Path(directory) / "ports.jsonl"
            env = child_env(
                SEAM_RECORD="1",
                SEAM_ARTIFACT=str(tape),
                GLASS_BODY=json.dumps({"sample": "s1", "kind": "blood"}),
            )
            proc = run_proc([sys.executable, "-m", "glass"], env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rows = [json.loads(line) for line in tape.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["port"], "bench")
            self.assertEqual(rows[0]["request"], {"kind": "blood"})
            self.assertGreater(rows[0]["at_ns"], 0)

    def test_live_timer_adapter_observes_cancellation_and_fires_overdue_once(self):
        rt = build()
        timers = []
        rt.set_timer_backend(lambda *args: timers.append(args))
        rt.start_live()
        rt.deliver("receive", {"sample": "s1", "kind": "blood"})
        rt.deliver("receive", {"sample": "s2", "kind": "urine"})
        rt.deliver("reading", {"sample": "s1", "value": 42})
        self.assertFalse(rt.fire_timer(timers[0][0]))
        self.assertTrue(rt.fire_timer(timers[1][0]))
        self.assertFalse(rt.fire_timer(timers[1][0]))
        self.assertEqual(
            {key: row["status"] for key, row in rt.state_copy()["samples"].items()},
            {"s1": "released", "s2": "overdue"},
        )

    def test_live_report_timeout_is_recorded_and_retries_the_same_key(self):
        with tempfile.TemporaryDirectory() as directory:
            tape = Path(directory) / "live.jsonl"
            saved = {key: os.environ.get(key) for key in ("SEAM_RECORD", "SEAM_ARTIFACT")}
            os.environ.update(SEAM_RECORD="1", SEAM_ARTIFACT=str(tape))
            try:
                timers, requests = [], []

                def report(request):
                    requests.append(request)
                    if len(requests) == 1:
                        raise PortError("timeout")
                    return {"status": "filed"}

                rt = build(report_factory=lambda: report)
                rt.set_timer_backend(lambda *args: timers.append(args))
                rt.start_live()
                rt.deliver("receive", {"sample": "s1", "kind": "blood"})
                rt.deliver("reading", {"sample": "s1", "value": 42})
                self.assertEqual(rt.state_copy()["samples"]["s1"]["status"], "report_pending")
                rt.fire_timer(timers[-1][0])
                self.assertEqual(rt.state_copy()["samples"]["s1"]["status"], "released")
                self.assertEqual(*requests)
                rt.close()
                lines = [json.loads(line) for line in tape.read_text().splitlines()]
                self.assertEqual(
                    [row.get("error") for row in lines if row["port"] == "report"],
                    ["timeout", None],
                )
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
