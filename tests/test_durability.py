"""Committed work survives process death; uncommitted lab and product writes roll back together."""

import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from seam import Fault
from seam.canon import dumps, loads

from glass.capture import run_bundle
from glass.journal import Journal
from glass.lab import Lab, initial_lab
from glass.live_capture import Service
from glass.pipeline import DUE_NS, RETRY_NS
from tests.support import child_env
from tests.test_capture import Clock

CRASH = """
import os, sys
from glass.journal import Journal
from glass.live_capture import Service
db, at, operation, phase = sys.argv[1:]
def die_after_write(self, frame):
    original(self, frame)
    os._exit(77)
original = Journal.save
if operation == 'pump' and phase == 'before':
    Journal.save = die_after_write
service = Service(db, clock=lambda: int(at))
if phase == 'before':
    service.journal.save = lambda frame: die_after_write(service.journal, frame)
if operation == 'reading':
    service.submit('reading', {'sample': 's1', 'value': 5})
elif operation == 'finish':
    service.finish_capture()
elif operation != 'pump':
    raise RuntimeError('unknown crash operation')
os._exit(77)
"""


class DurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "lab.sqlite"
        self.clock = Clock()

    def open(self, *, create=False, busy=0, lose_ack=False):
        service = Service(
            self.db,
            clock=self.clock,
            initial_lab=initial_lab(busy_remaining=busy, lose_ack=lose_ack)
            if create
            else None,
        )
        self.addCleanup(service.close)
        return service

    def receive(self, service, sample="s1"):
        return service.submit("receive", {"sample": sample, "kind": "blood"})

    def reading(self, service, value=5):
        return service.submit("reading", {"sample": "s1", "value": value})

    def restart(self, service):
        service.close()
        return self.open()

    def crash(self, operation, phase):
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                CRASH,
                str(self.db),
                str(self.clock.at),
                operation,
                phase,
            ],
            env=child_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(process.returncode, 77, process.stderr)
        self.assertEqual(process.stdout, "")
        self.assertEqual(process.stderr, "")

    def frame(self):
        with closing(sqlite3.connect(self.db)) as db:
            return loads(db.execute("SELECT body FROM live WHERE id=1").fetchone()[0])

    def replace_frame(self, frame):
        with closing(sqlite3.connect(self.db)) as db:
            with db:
                db.execute("UPDATE live SET body=? WHERE id=1", (dumps(frame),))

    def test_all_pending_states_recover_the_original_deadline_and_id(self):
        for status in ("queued", "running", "retest", "report_pending"):
            with self.subTest(status=status):
                self.db = self.root / f"{status}.sqlite"
                self.clock.at = 100
                service = self.open(
                    create=True,
                    busy=int(status == "queued"),
                    lose_ack=status == "report_pending",
                )
                self.receive(service)
                if status in ("retest", "report_pending"):
                    self.reading(service, value=101 if status == "retest" else 5)
                original = service.snapshot()
                self.assertEqual(original["product"]["samples"]["s1"]["status"], status)
                timers = list(service.active.values())
                service = self.restart(service)
                self.assertEqual(service.snapshot(), original)
                restored = list(service.active.values())
                self.assertEqual(
                    [
                        {key: value for key, value in row.items() if key != "token"}
                        for row in restored
                    ],
                    [
                        {key: value for key, value in row.items() if key != "token"}
                        for row in timers
                    ],
                )
                row = service.runtime.state_copy()["samples"]["s1"]
                field = {
                    "queued": "retry",
                    "running": "due",
                    "retest": "retest",
                    "report_pending": "report_retry",
                }[status]
                self.assertEqual(row[field], restored[0]["token"])
                self.clock.at = timers[0]["at_ns"] + 10
                service = self.restart(service)
                expected = (
                    "running"
                    if status in ("queued", "retest")
                    else "overdue"
                    if status == "running"
                    else "released"
                )
                state = service.snapshot()
                self.assertEqual(state["product"]["samples"]["s1"]["status"], expected)
                self.assertEqual(
                    state["product"]["samples"]["s1"]["id"],
                    original["product"]["samples"]["s1"]["id"],
                )
                if expected == "running":
                    self.assertEqual(
                        next(iter(service.active.values()))["at_ns"],
                        timers[0]["at_ns"] + DUE_NS,
                    )
                if status == "report_pending":
                    self.assertEqual(len(state["lab"]["filed"]), 1)
                    self.assertEqual(list(state["lab"]["attempts"].values()), [2])
                service.close()

    def test_overdue_chain_catches_up_at_logical_deadlines_not_restart_time(self):
        service = self.open(create=True, busy=1, lose_ack=True)
        self.receive(service)
        self.clock.at = 100 + RETRY_NS + DUE_NS + RETRY_NS + 10
        service = self.restart(service)
        self.assertEqual(
            service.snapshot()["product"]["samples"]["s1"]["status"], "overdue"
        )
        self.assertEqual(list(service.lab.snapshot()["attempts"].values()), [2])
        self.assertEqual(service.active, {})

    def test_equal_time_timers_keep_their_insertion_order_after_restart(self):
        service = self.open(create=True, busy=2, lose_ack=True)
        self.receive(service)
        self.receive(service, "s2")
        service.begin_capture()
        names = [row["name"] for row in service.active.values()]
        service = self.restart(service)
        self.assertEqual([row["name"] for row in service.active.values()], names)
        self.clock.at += RETRY_NS + DUE_NS + RETRY_NS
        service = self.restart(service)
        captured = service.finish_capture()
        result = run_bundle(captured, self.root / "equal-time")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertEqual(result.artifact["world_states"]["lab"], service.lab.snapshot())

    def test_active_capture_spans_restarts_and_replays_identically(self):
        service = self.open(create=True, busy=1, lose_ack=True)
        self.receive(service)
        service.begin_capture()
        self.receive(service, "s2")
        service = self.restart(service)
        self.clock.at += RETRY_NS
        self.reading(service)
        service = self.restart(service)
        service.submit("reading", {"sample": "s2", "value": 5})
        self.clock.at += RETRY_NS
        service = self.restart(service)
        captured = service.finish_capture()
        self.assertEqual(len(captured["payload"]["arrivals"]), 3)
        self.assertEqual(
            captured["payload"]["initial_state"]["samples"]["s1"]["status"], "queued"
        )
        results = [run_bundle(captured, self.root / name) for name in ("one", "two")]
        for result in results:
            self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))
        self.assertEqual(
            Path(results[0].artifact_path).read_bytes(),
            Path(results[1].artifact_path).read_bytes(),
        )
        self.assertEqual(
            results[0].artifact["final_state"]["view"], service.snapshot()["product"]
        )

    def test_finished_capture_is_durable_idempotent_and_not_advanced_by_fetch(self):
        service = self.open(create=True)
        service.begin_capture()
        self.receive(service)
        captured = service.finish_capture()
        service = self.restart(service)
        self.clock.at += DUE_NS
        before = self.frame()
        self.assertEqual(service.latest_capture(), captured)
        self.assertEqual(service.finish_capture(), captured)
        self.assertEqual(self.frame(), before)
        fetched = service.latest_capture()
        fetched["payload"]["arrivals"].clear()
        self.assertEqual(service.latest_capture(), captured)
        service.begin_capture()
        self.assertEqual(service.latest_capture(), captured)
        service.abort_capture()
        self.assertEqual(service.latest_capture(), captured)

    def test_failed_capture_survives_restart_and_requires_explicit_abort(self):
        service = self.open(create=True)
        self.receive(service)
        service.begin_capture()
        before = service.snapshot()
        with self.assertRaises(Fault):
            service.submit("receive", {"sample": "s1", "kind": "changed"})
        service = self.restart(service)
        self.assertEqual(service.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "invalidated"):
            service.finish_capture()
        with self.assertRaisesRegex(ValueError, "already active"):
            service.begin_capture()
        self.assertEqual(service.abort_capture(), {"aborted": True})
        service.begin_capture()
        self.reading(service)
        self.assertEqual(
            run_bundle(service.finish_capture(), self.root / "after-abort").returncode,
            0,
        )

    def test_unknown_reading_remains_a_capturable_noop(self):
        service = self.open(create=True)
        service.begin_capture()
        service.submit("reading", {"sample": "absent", "value": 5})
        service = self.restart(service)
        self.assertEqual(
            run_bundle(service.finish_capture(), self.root / "noop").returncode, 0
        )

    def test_save_failure_rolls_back_lab_product_timer_and_capture_together(self):
        service = self.open(create=True, lose_ack=True)
        self.receive(service)
        service.begin_capture()
        before = service.snapshot()
        frame = self.frame()
        original = Journal.save

        def fail_after_write(journal, value):
            original(journal, value)
            raise OSError("injected save failure")

        with patch.object(Journal, "save", fail_after_write):
            with self.assertRaisesRegex(OSError, "injected"):
                self.reading(service)
        self.assertEqual(self.frame(), frame)
        self.assertEqual(service.snapshot(), before)
        self.assertEqual(service.capture["arrivals"], [])
        self.reading(service)
        self.assertEqual(len(service.lab.snapshot()["filed"]), 1)

    def test_delivery_failure_after_lab_write_rolls_back_and_invalidates_capture(self):
        service = self.open(create=True)
        self.receive(service)
        service.begin_capture()
        before = service.snapshot()
        original = service.runtime.handlers["reading"]

        def fail(ctx, body):
            original(ctx, body)
            raise Fault("bad_value")

        service.runtime.handlers["reading"] = fail
        with self.assertRaises(Fault):
            self.reading(service)
        self.assertEqual(service.snapshot(), before)
        self.assertTrue(self.frame()["failed_capture"])
        self.assertEqual(service.capture["arrivals"], [])

    def test_real_crash_before_commit_rolls_back_a_filed_report_and_arrival(self):
        service = self.open(create=True, lose_ack=True)
        self.receive(service)
        service.begin_capture()
        before = service.snapshot()
        service.close()
        self.clock.at += 1
        self.crash("reading", "before")
        service = self.open()
        self.assertEqual(service.snapshot(), before)
        self.assertEqual(service.capture["arrivals"], [])
        self.reading(service)
        self.assertEqual(len(service.lab.snapshot()["filed"]), 1)

    def test_real_crash_after_commit_keeps_report_pending_and_captured_arrival(self):
        service = self.open(create=True, lose_ack=True)
        self.receive(service)
        ident = service.snapshot()["product"]["samples"]["s1"]["id"]
        service.begin_capture()
        service.close()
        self.clock.at += 1
        self.crash("reading", "after")
        service = self.open()
        self.assertEqual(
            service.snapshot()["product"]["samples"]["s1"]["status"], "report_pending"
        )
        self.assertEqual(service.snapshot()["product"]["samples"]["s1"]["id"], ident)
        self.assertEqual(len(service.capture["arrivals"]), 1)
        self.clock.at += RETRY_NS
        service = self.restart(service)
        self.assertEqual(list(service.lab.snapshot()["attempts"].values()), [2])
        self.assertEqual(
            run_bundle(service.finish_capture(), self.root / "crash-replay").returncode,
            0,
        )

    def test_real_crash_before_timer_commit_can_retry_the_same_timer(self):
        service = self.open(create=True, busy=1)
        self.receive(service)
        original = self.frame()
        service.close()
        self.clock.at += RETRY_NS
        self.crash("pump", "before")
        self.assertEqual(self.frame(), original)
        service = self.open()
        self.assertEqual(
            service.snapshot()["product"]["samples"]["s1"]["status"], "running"
        )
        self.assertEqual(
            next(iter(service.active.values()))["at_ns"], 100 + RETRY_NS + DUE_NS
        )

    def test_real_crash_before_capture_commit_leaves_capture_active(self):
        service = self.open(create=True)
        service.begin_capture()
        self.receive(service)
        service.close()
        self.clock.at += 1
        self.crash("finish", "before")
        service = self.open()
        self.assertIsNotNone(service.capture)
        self.assertIsNone(service.completed)
        self.assertEqual(len(service.finish_capture()["payload"]["arrivals"]), 1)

    def test_real_crash_after_capture_commit_recovers_the_lost_response(self):
        service = self.open(create=True)
        service.begin_capture()
        self.receive(service)
        service.close()
        self.clock.at += 1
        self.crash("finish", "after")
        completed = self.frame()["completed"]
        self.clock.at += 100
        service = self.open()
        self.assertIsNone(service.capture)
        self.assertEqual(service.latest_capture(), completed)
        self.assertEqual(service.finish_capture(), completed)
        self.assertEqual(
            run_bundle(completed, self.root / "lost-response").returncode, 0
        )

    def test_corrupt_or_incompatible_frames_are_refused_without_overwrite(self):
        service = self.open(create=True)
        self.receive(service)
        service.begin_capture()
        original = self.frame()
        service.close()
        for mutate in (
            lambda frame: frame.update(version=True),
            lambda frame: frame["source"].update(product_sha256="different"),
            lambda frame: frame["timers"].clear(),
            lambda frame: frame["timers"][0].update(at_ns=99),
            lambda frame: frame["capture"].update(as_of_ns=101),
            lambda frame: frame["capture"]["product_data"]["timers"].clear(),
            lambda frame: frame["capture"]["datasets"]["lab"].update(as_of_ns=True),
            lambda frame: frame.update(extra=True),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(original)
                mutate(changed)
                self.replace_frame(changed)
                with self.assertRaises(ValueError):
                    self.open()
                self.assertEqual(self.frame(), changed)
        self.replace_frame(original)
        self.assertEqual(
            self.open().snapshot()["product"]["samples"]["s1"]["status"], "running"
        )

    def test_volatile_database_is_not_silently_treated_as_an_empty_product(self):
        Lab(self.db, snapshot=initial_lab()).close()
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, "no durable product frame"):
            self.open()
        self.assertEqual(self.db.read_bytes(), before)

    def test_clock_regression_and_concurrent_writer_are_refused(self):
        service = self.open(create=True)
        self.receive(service)
        with self.assertRaises(BlockingIOError):
            self.open()
        service.close()
        before = self.frame()
        self.clock.at -= 1
        with self.assertRaisesRegex(ValueError, "backwards"):
            self.open()
        self.assertEqual(self.frame(), before)
        self.clock.at += 1
        self.assertEqual(
            self.open().snapshot()["product"]["samples"]["s1"]["status"], "running"
        )

    def test_initialization_is_private_and_never_replaces_an_existing_database(self):
        with self.assertRaisesRegex(ValueError, "initialize it explicitly"):
            self.open()
        self.assertFalse(self.db.exists())
        service = self.open(create=True)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.receive(service)
        service.close()
        before = self.db.read_bytes()
        with self.assertRaises(FileExistsError):
            self.open(create=True)
        self.assertEqual(self.db.read_bytes(), before)

    def test_sdk_recording_is_explicitly_rejected_outside_the_transaction(self):
        with patch.dict("os.environ", {"SEAM_RECORD": "1"}):
            with self.assertRaisesRegex(
                ValueError, "not part of the durable transaction"
            ):
                self.open(create=True)
        self.assertFalse(self.db.exists())
