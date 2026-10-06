"""Glass captures preserve live IDs, active retries, dependency state, and deadline ordering."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from seam import checkpoint_ref, run_product, write_checkpoint
from seam.canon import digest

from glass.capture import MODULE, read_bundle, validate_bundle, write_case, write_new
from glass.lab import Lab, initial_lab
from glass.live_capture import Service
from glass.pipeline import DUE_NS, RETRY_NS


class Clock:
    def __init__(self, at=100):
        self.at = at

    def __call__(self):
        return self.at


def execute(document, root, label, *, delay=0):
    case = write_case(document, root / label, reading_delay_ns=delay)
    return run_product(
        MODULE, str(case), "sim-glass-capture", str(case.parent / "artifact.json")
    )


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()

    def service(self, *, busy=1, lose_ack=True):
        service = Service(
            self.root / "lab.sqlite",
            initial_lab=initial_lab(busy_remaining=busy, lose_ack=lose_ack),
            clock=self.clock,
        )
        self.addCleanup(service.close)
        return service

    def receive(self, service, sample="s1"):
        service.submit("receive", {"sample": sample, "kind": "blood"})

    def reading(self, service, sample="s1", value=5):
        service.submit("reading", {"sample": sample, "value": value})

    def capture_queued(self):
        service = self.service()
        self.receive(service)
        self.clock.at += 1
        service.begin_capture()
        self.clock.at = 100 + RETRY_NS + 1
        self.reading(service)
        self.clock.at += RETRY_NS + 1
        service.pump()
        return service, service.finish_capture()

    def test_queued_sample_and_lost_ack_retry_replay_twice(self):
        service, document = self.capture_queued()
        private = document["payload"]["initial_state"]["samples"]["s1"]["id"]
        before = service.lab.snapshot()
        first, second = (
            execute(document, self.root, "one"),
            execute(document, self.root, "two"),
        )
        for result in (first, second):
            self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
            row = result.artifact["final_state"]["samples"]["s1"]
            self.assertEqual(row["id"], private)
            self.assertEqual(row["status"], "released")
            reports = [
                row for row in result.artifact["port_calls"] if row["port"] == "report"
            ]
            self.assertEqual(len(reports), 2)
            self.assertEqual(reports[0]["request"], reports[1]["request"])
            self.assertEqual(reports[0]["error"], "timeout")
        self.assertEqual(first.artifact, second.artifact)
        self.assertEqual(
            Path(first.artifact_path).read_bytes(),
            Path(second.artifact_path).read_bytes(),
        )
        self.assertEqual(service.lab.snapshot(), before)

    def test_receive_after_snapshot_preserves_new_private_id(self):
        service = self.service(busy=0, lose_ack=False)
        service.begin_capture()
        self.receive(service)
        self.receive(service)
        self.clock.at += 1
        self.reading(service)
        self.reading(service)
        document = service.finish_capture()
        result = execute(document, self.root, "new-id")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertEqual(
            result.artifact["final_state"]["samples"]["s1"]["id"],
            document["payload"]["product_data"]["ids"]["s1"],
        )
        self.assertEqual(len(result.artifact["world_states"]["lab"]["filed"]), 1)

    def test_capture_pending_report_after_write_before_ack(self):
        service = self.service(busy=0)
        self.receive(service)
        self.reading(service)
        service.begin_capture()
        self.clock.at += RETRY_NS
        service.pump()
        document = service.finish_capture()
        result = execute(document, self.root, "report-pending")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertEqual(len(result.artifact["world_states"]["lab"]["filed"]), 1)

    def test_capture_pending_retest(self):
        service = self.service(busy=0, lose_ack=False)
        self.receive(service)
        self.reading(service, value=101)
        service.begin_capture()
        self.clock.at += RETRY_NS
        self.reading(service, value=5)
        result = execute(service.finish_capture(), self.root, "retest")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertTrue(result.artifact["final_state"]["samples"]["s1"]["retested"])

    def test_reading_at_retry_deadline_runs_after_retry(self):
        service = self.service(lose_ack=False)
        self.receive(service)
        service.begin_capture()
        self.clock.at += RETRY_NS
        self.reading(service)
        result = execute(service.finish_capture(), self.root, "retry-boundary")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertEqual(
            result.artifact["final_state"]["samples"]["s1"]["status"], "released"
        )

    def test_reading_at_due_deadline_is_overdue(self):
        service = self.service(busy=0, lose_ack=False)
        self.receive(service)
        service.begin_capture()
        self.clock.at += DUE_NS
        self.reading(service)
        result = execute(service.finish_capture(), self.root, "due-boundary")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertEqual(
            result.artifact["final_state"]["samples"]["s1"]["status"], "overdue"
        )

    def test_running_sample_can_remain_in_flight_at_capture_end(self):
        service = self.service(busy=0, lose_ack=False)
        self.receive(service)
        service.begin_capture()
        self.clock.at += 10
        result = execute(service.finish_capture(), self.root, "pending")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertEqual(result.artifact["timers"][0]["outcome"], "dropped")
        self.assertEqual(
            result.artifact["final_state"]["samples"]["s1"]["status"], "running"
        )

    def test_counterfactual_delayed_reading_changes_disposition(self):
        _, document = self.capture_queued()
        result = execute(document, self.root, "late-reading", delay=DUE_NS + RETRY_NS)
        self.assertEqual(result.returncode, 1, (result.stderr, result.artifact))
        self.assertEqual(
            result.artifact["final_state"]["samples"]["s1"]["status"], "overdue"
        )
        self.assertEqual(len(result.artifact["world_states"]["lab"]["filed"]), 1)

    def test_checkpoint_restores_shared_lab_and_timer_bindings(self):
        _, document = self.capture_queued()
        case = write_case(document, self.root / "paused")
        original = json.loads(case.read_text())
        paused_case = copy.deepcopy(original)
        paused_case.update(checkpoint_after=1, assertions=[])
        case.write_text(json.dumps(paused_case))
        paused = run_product(
            MODULE, str(case), "sim-glass-capture", str(case.parent / "paused.json")
        )
        self.assertEqual(paused.returncode, 4, (paused.stderr, paused.artifact))
        checkpoint = case.parent / "checkpoint.json"
        write_checkpoint(checkpoint, paused.artifact["checkpoint"])
        paused_case.pop("checkpoint_after")
        paused_case.update(
            resume=checkpoint_ref(checkpoint), assertions=original["assertions"]
        )
        case.write_text(json.dumps(paused_case))
        resumed = run_product(
            MODULE, str(case), "sim-glass-capture", str(case.parent / "resumed.json")
        )
        complete = execute(document, self.root, "uninterrupted")
        self.assertEqual(resumed.returncode, 0, (resumed.stderr, resumed.artifact))
        self.assertEqual(resumed.artifact["digest"], complete.artifact["digest"])

    def test_checkpoint_keeps_timer_precedence_at_an_exact_arrival_boundary(self):
        service = self.service(lose_ack=False)
        self.receive(service)
        service.begin_capture()
        self.clock.at += RETRY_NS
        self.reading(service)
        document = service.finish_capture()
        case = write_case(document, self.root / "equal-time-paused")
        original = json.loads(case.read_text())
        paused_case = {**original, "checkpoint_after": 1, "assertions": []}
        case.write_text(json.dumps(paused_case))
        paused = run_product(
            MODULE, str(case), "sim-glass-capture", str(case.parent / "paused.json")
        )
        self.assertEqual(paused.returncode, 4, (paused.stderr, paused.artifact))
        checkpoint = case.parent / "checkpoint.json"
        write_checkpoint(checkpoint, paused.artifact["checkpoint"])
        case.write_text(json.dumps({**original, "resume": checkpoint_ref(checkpoint)}))
        resumed = run_product(
            MODULE, str(case), "sim-glass-capture", str(case.parent / "resumed.json")
        )
        complete = execute(document, self.root, "equal-time-complete")
        self.assertEqual(resumed.returncode, 0, (resumed.stderr, resumed.artifact))
        self.assertEqual(resumed.artifact["digest"], complete.artifact["digest"])

    def test_missing_timers_and_inconsistent_ids_refuse_before_export(self):
        _, original = self.capture_queued()
        for mutate in (
            lambda p: p["product_data"]["timers"].clear(),
            lambda p: p["product_data"]["timers"][0].update(at_ns=99),
            lambda p: p["product_data"]["ids"].update(s1="smp_0000000000000000"),
            lambda p: p["product_data"]["timers"][0].update(handler="due"),
        ):
            with self.subTest(mutate=mutate):
                document = copy.deepcopy(original)
                mutate(document["payload"])
                document["sha256"] = digest(document["payload"])
                with self.assertRaises(ValueError):
                    validate_bundle(document)

    def test_roundtrip_uses_shared_sdk_capture_envelope(self):
        _, document = self.capture_queued()
        path = self.root / "capture.json"
        write_new(path, document)
        self.assertEqual(document["format"], "seam-capture")
        self.assertEqual(read_bundle(path), document)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_delivery_invalidates_active_capture(self):
        service = self.service()
        self.receive(service)
        service.begin_capture()
        with self.assertRaises(Exception):
            service.submit("receive", {"sample": "s1", "kind": "changed"})
        with self.assertRaisesRegex(ValueError, "invalidated"):
            service.finish_capture()


class LabTests(unittest.TestCase):
    def test_lost_ack_commits_and_duplicate_request_is_idempotent(self):
        from seam import PortError

        lab = Lab(":memory:", snapshot=initial_lab())
        try:
            request = {
                "sample": "s1",
                "disposition": "released",
                "idempotency_key": "key",
            }
            with self.assertRaises(PortError):
                lab.call("report", request)
            self.assertEqual(len(lab.snapshot()["filed"]), 1)
            self.assertEqual(lab.call("report", request), {"status": "filed"})
            before = lab.snapshot()
            with self.assertRaises(ValueError):
                lab.call("report", {**request, "sample": "other"})
            self.assertEqual(lab.snapshot(), before)
        finally:
            lab.close()
