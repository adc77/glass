"""Adoption tests. Glass is the product. Seam is the library under it."""

import json
import os
import stat
import sys
import tempfile
import time
import unittest

from glass.pipeline import LEAK_ADDR
from seam import tape_from_artifact
from seam.canon import dumps, loads

from tests.support import (
    CASES,
    RELEASE,
    RELEASE_DIGEST,
    child_env,
    run_glass,
    run_proc,
    write_json,
)

#: The busy case, which leaves the sample `queued` -- the only state in which a
#: retry delivery reaches `_kind` at all.
BUSY = CASES["busy-then-release"][1]


def _read(path):
    with open(path, encoding="ascii") as handle:
        return handle.read()


def _artifact(path):
    return loads(_read(path))


class ScriptTest(unittest.TestCase):
    def test_case_arrivals_are_in_step_with_the_pipeline_delays(self):
        """Every arrival has to be placed relative to `RETRY_NS` and `DUE_NS`.

        The case files carry literal nanosecond offsets. Those literals only mean
        anything in combination with the two constants: an arrival at 6e9 is
        "just after the retry timer" only because `RETRY_NS` is 5e9. Changing a
        constant without moving the arrivals would quietly stop testing the
        branch it was written for, so the relationship is asserted here rather
        than left implicit.

        What is checked here is only what the constants themselves imply: nothing
        arrives at or after the due deadline, since the run would stop there, and
        every arrival is distinct, since two at the same nanosecond would be
        ordered by insertion rather than by time.
        """
        from glass.pipeline import DUE_NS, RETRY_NS

        self.assertGreater(DUE_NS, RETRY_NS)
        for name, (_namespace, path) in CASES.items():
            with self.subTest(case=name):
                case = json.loads(_read(path))
                ats = [item["at_ns"] for item in case["arrivals"]]
                self.assertEqual(len(set(ats)), len(ats), f"{name}: duplicate arrival time")
                for at_ns in ats:
                    # The run stops at the deadline, so an arrival at or after it
                    # would never be delivered and the case would quietly stop
                    # testing whatever that arrival was for.
                    self.assertLess(
                        at_ns, DUE_NS, f"{name}: arrival {at_ns} is at or past the due deadline"
                    )
                    self.assertGreaterEqual(at_ns, 0, f"{name}: negative arrival {at_ns}")

    def test_cases_pass_and_release_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "factories.log")
            for name, (namespace, case) in CASES.items():
                out = os.path.join(directory, name + ".json")
                proc = run_glass(case, namespace, out, extra={"GLASS_FACTORY_LOG": log})
                self.assertEqual(proc.returncode, 0, proc.stderr + _read(out))
                self.assertFalse(os.path.exists(log))
                self.assertEqual(proc.stderr, "")
                self.assertEqual(proc.stdout.strip(), os.path.abspath(out))

            # Every case is checked for cross-process byte identity, not just
            # the release. Checking one case left the other six free to become
            # non-deterministic without anything noticing, and they are the ones
            # whose arrival times encode the pipeline's own delays.
            for name, (namespace, case) in CASES.items():
                with self.subTest(case=name):
                    first = os.path.join(directory, name + "-1.json")
                    second = os.path.join(directory, name + "-2.json")
                    one = run_glass(case, namespace, first)
                    two = run_glass(case, namespace, second)
                    self.assertEqual(one.returncode, 0, one.stderr)
                    self.assertEqual(two.returncode, 0, two.stderr)
                    self.assertEqual(
                        _read(first).encode("ascii"),
                        _read(second).encode("ascii"),
                        f"{name}: two runs in separate processes differ",
                    )
                    # Byte hygiene on the artifact itself: exactly one trailing
                    # newline, no leftover temp file, and the documented mode.
                    data = _read(first).encode("ascii")
                    self.assertTrue(data.endswith(b"\n"), name)
                    self.assertEqual(data.count(b"\n"), 1, name)
                    self.assertFalse(os.path.exists(first + ".tmp"), name)
                    # Stated as a literal rather than imported from
                    # seam.artifact: this is an assertion about the guarantee
                    # glass is relying on, so reading the constant out of the
                    # library would make it true by construction. It also keeps
                    # glass off seam's internal symbols, which are free to move
                    # between releases and would otherwise couple the two
                    # repositories' CI to each other's release timing.
                    self.assertEqual(stat.S_IMODE(os.stat(first).st_mode), 0o644, name)
            # The release case specifically, by name. Reading `first` here would
            # silently pick up whichever case the loop happened to end on.
            art = _artifact(os.path.join(directory, "release-1.json"))
            self.assertEqual(art["digest"], RELEASE_DIGEST)
            self.assertEqual(art["final_state"]["sample"]["status"], "released")
            self.assertEqual(art["clock"]["end_ns"], 1_000_000_000)
            self.assertEqual(art["port_calls"][0]["source"], "script")

            overdue = _artifact(os.path.join(directory, "overdue.json"))
            self.assertEqual(overdue["clock"]["end_ns"], 3_600_000_000_000)
            busy = _artifact(os.path.join(directory, "busy-then-release.json"))
            self.assertEqual(busy["clock"]["end_ns"], 6_000_000_000)
            failed = _artifact(os.path.join(directory, "fail-then-release.json"))
            self.assertEqual(failed["clock"]["end_ns"], 7_000_000_000)
            dropped = _artifact(os.path.join(directory, "dropped.json"))
            self.assertEqual(dropped["clock"]["end_ns"], 5_000_000_000)

            # The qc-fail branches carry the logic that used to be untested.
            scrapped = _artifact(os.path.join(directory, "qc-fail-scrapped.json"))
            self.assertEqual(scrapped["final_state"]["sample"]["status"], "scrapped")
            self.assertIs(scrapped["final_state"]["sample"]["retested"], True)
            self.assertEqual(
                [call["port"] for call in scrapped["port_calls"]],
                ["bench", "qc", "bench", "qc", "report"],
            )
            self.assertEqual(
                scrapped["timers"],
                [
                    {"token": "t1", "handler": "due", "fire_at_ns": 3_600_000_000_000,
                     "outcome": "cancelled", "name": "due-once"},
                    {"token": "t2", "handler": "retest", "fire_at_ns": 6_000_000_000,
                     "outcome": "fired", "name": "retest-once"},
                    {"token": "t3", "handler": "due", "fire_at_ns": 3_606_000_000_000,
                     "outcome": "cancelled", "name": "due-retest"},
                ],
            )
            retest_busy = _artifact(os.path.join(directory, "qc-fail-retest-busy.json"))
            self.assertEqual(retest_busy["final_state"]["sample"]["status"], "dropped")
            # A busy bench on the retest scraps it; no second qc is attempted.
            self.assertEqual(
                [call["port"] for call in retest_busy["port_calls"]],
                ["bench", "qc", "bench", "report"],
            )

    def test_malformed_bodies_are_clean_faults(self):
        """A bad arrival is `bad_value`, not a KeyError surfacing as
        `handler_error`. Both are exit 2, but only one tells you what is wrong."""
        cases = {
            "missing_sample": {"kind": "blood"},
            "missing_kind": {"sample": "s1"},
            "sample_not_a_string": {"sample": 7, "kind": "blood"},
            "empty_sample": {"sample": "", "kind": "blood"},
            "body_is_a_list": ["s1", "blood"],
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, body in cases.items():
                with self.subTest(body=name):
                    case = json.loads(_read(RELEASE))
                    case["name"] = "glass-bad"
                    case["namespace"] = "sim-glass-bad"
                    case["arrivals"] = [{"at_ns": 0, "handler": "receive", "body": body}]
                    case["assertions"] = [{"op": "fault_is", "code": "bad_value"}]
                    case_path = os.path.join(directory, name + ".json")
                    write_json(case_path, case)
                    out = os.path.join(directory, name + "-out.json")
                    proc = run_glass(case_path, "sim-glass-bad", out)
                    text = proc.stdout + proc.stderr + _read(out)
                    self.assertEqual(proc.returncode, 2, text)
                    art = _artifact(out)
                    self.assertEqual(art["fault"]["code"], "bad_value", name)
                    # Nothing reached the bench.
                    self.assertEqual(art["port_calls"], [])

    def test_reading_without_a_usable_value_is_ignored(self):
        """A reading with no value is not a reading. The sample stays running and
        the due timer still files it overdue rather than raising."""
        with tempfile.TemporaryDirectory() as directory:
            case = json.loads(_read(RELEASE))
            case["name"] = "glass-novalue"
            case["namespace"] = "sim-glass-novalue"
            case["arrivals"] = [
                {"at_ns": 0, "handler": "receive", "body": {"sample": "s1", "kind": "blood"}},
                {"at_ns": 1_000_000_000, "handler": "reading", "body": {"sample": "s1"}},
            ]
            case["ports"]["report"]["replies"] = [
                {"match": {"disposition": "overdue"}, "response": {"status": "filed"}, "repeat": 1}
            ]
            case["assertions"] = [
                {"op": "port_not_called", "port": "qc"},
                {"op": "port_called", "port": "report", "times": 1, "match": {"disposition": "overdue"}},
                {"op": "state_is", "path": "sample.status", "value": "overdue"},
            ]
            case_path = os.path.join(directory, "novalue.json")
            write_json(case_path, case)
            out = os.path.join(directory, "novalue-out.json")
            proc = run_glass(case_path, "sim-glass-novalue", out)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr + _read(out))
            art = _artifact(out)
            self.assertEqual(art["status"], "passed")
            self.assertEqual(art["final_state"]["sample"]["status"], "overdue")

    def test_stale_reading_with_a_bad_value_is_still_ignored(self):
        """A delivery for a sample that is no longer running is dropped before
        its value is inspected. Validating first would fault the run over a
        field nobody was going to read."""
        with tempfile.TemporaryDirectory() as directory:
            case = json.loads(_read(RELEASE))
            case["name"] = "glass-stale"
            case["namespace"] = "sim-glass-stale"
            case["arrivals"] = [
                {"at_ns": 0, "handler": "receive", "body": {"sample": "s1", "kind": "blood"}},
                # Releases the sample, so the next reading is stale.
                {"at_ns": 1_000_000_000, "handler": "reading", "body": {"sample": "s1", "value": 42}},
                # Same sample, now released, with a value that is not an integer.
                {"at_ns": 2_000_000_000, "handler": "reading", "body": {"sample": "s1", "value": "bad"}},
            ]
            case["assertions"] = [
                {"op": "port_called", "port": "qc", "times": 1},
                {"op": "port_called", "port": "report", "times": 1, "match": {"disposition": "released"}},
                {"op": "state_is", "path": "sample.status", "value": "released"},
                {"op": "stopped", "reason": "quiescence"},
            ]
            case_path = os.path.join(directory, "stale.json")
            write_json(case_path, case)
            out = os.path.join(directory, "stale-out.json")
            proc = run_glass(case_path, "sim-glass-stale", out)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr + _read(out))
            art = _artifact(out)
            self.assertEqual(art["status"], "passed")
            self.assertNotIn("fault", art)

    def test_retry_without_a_kind_uses_the_state_kind(self):
        """`on_retry` and `on_retest` used to index `body["kind"]` directly.

        A delivery that names the sample but omits the kind then raised a
        `KeyError`, which the runner reports as `handler_error` and hides the
        real cause. State is the authority on a sample's kind anyway, since it
        was fixed when the sample was received. The unit test below drives
        `_kind` directly; this one covers the path through a real run.
        """
        with tempfile.TemporaryDirectory() as directory:
            case = json.loads(_read(RELEASE))
            case["name"] = "glass-kindless"
            case["namespace"] = "sim-glass-kindless"
            # Only the receive. The bench is busy, so the pipeline schedules its
            # own retry, and that delivery carries no `kind`.
            case["arrivals"] = [
                {"at_ns": 0, "handler": "receive", "body": {"sample": "s1", "kind": "blood"}}
            ]
            case["ports"]["bench"]["replies"] = [
                {"match": {"kind": "blood"}, "response": {"status": "busy"}, "repeat": 1},
                {"match": {"kind": "blood"}, "response": {"status": "ready", "machine": "m1"}, "repeat": 1},
            ]
            case["ports"]["report"]["replies"] = [
                {"match": {"disposition": "overdue"}, "response": {"status": "filed"}, "repeat": 1}
            ]
            case["assertions"] = [
                # The retry must ask the bench for the sample's real kind.
                {"op": "port_called", "port": "bench", "times": 2, "match": {"kind": "blood"}},
                {"op": "timer_outcome", "name": "retry-once", "outcome": "fired"},
                # No reading ever arrives, so the due timer files it overdue.
                {"op": "port_called", "port": "report", "times": 1, "match": {"disposition": "overdue"}},
                {"op": "state_is", "path": "sample.status", "value": "overdue"},
                {"op": "stopped", "reason": "quiescence"},
            ]
            case_path = os.path.join(directory, "kindless.json")
            write_json(case_path, case)
            out = os.path.join(directory, "kindless-out.json")
            proc = run_glass(case_path, "sim-glass-kindless", out)
            text = proc.stdout + proc.stderr + _read(out)
            self.assertEqual(proc.returncode, 0, text)
            art = _artifact(out)
            self.assertEqual(art["status"], "passed")
            self.assertEqual(
                [call["request"] for call in art["port_calls"] if call["port"] == "bench"],
                [{"kind": "blood"}, {"kind": "blood"}],
            )
            # The retry that `receive` scheduled carries the kind, so this is not
            # the kindless case. What is pinned here is that the retry asks the
            # bench for the sample's real kind and does not fault.
            self.assertNotIn("handler_error", text)

    def test_kindless_retry_with_no_state_kind_faults_cleanly(self):
        """If neither the body nor the state carries a kind, that is `bad_value`
        rather than a `KeyError`."""
        with tempfile.TemporaryDirectory() as directory:
            from glass.pipeline import _kind
            from seam.errors import Fault

            self.assertEqual(_kind({"kind": "blood"}, {}), "blood")
            self.assertEqual(_kind({}, {"kind": "blood"}), "blood")
            with self.assertRaises(Fault) as raised:
                _kind({}, {})
            self.assertEqual(raised.exception.code, "bad_value")

    def test_state_outranks_a_retry_body_that_lies_about_the_kind(self):
        """When state and body disagree, the state's kind wins.

        A sample's kind is fixed when it is received, so a retry or retest body
        naming a different kind is claiming something that cannot have changed.
        Preferring the body meant a bench was asked for `urine`, replied with a
        machine for `urine`, and `on_reading` then sent the state's `blood` to
        `qc` -- a urine bench result judged against a blood reference, with the
        request and the state disagreeing in the artifact.

        The existing test only covers the two agreeing cases, which return the
        same value under either preference, so it could not tell which one was
        implemented.
        """
        from glass.pipeline import _kind

        self.assertEqual(_kind({"kind": "blood"}, {"kind": "urine"}), "blood")
        self.assertEqual(_kind({"kind": "urine"}, {"kind": "blood"}), "urine")
        # The body is still a fallback for a row with no kind of its own.
        self.assertEqual(_kind({}, {"kind": "blood"}), "blood")
        # A non-string state kind does not shadow a usable body.
        self.assertEqual(_kind({"kind": None}, {"kind": "blood"}), "blood")

    def test_a_lying_retry_body_cannot_send_a_result_to_the_wrong_qc(self):
        """End to end: the kind sent to the bench must be the sample's kind.

        A retry *arrival* that names the sample but lies about its kind, arriving
        while the sample is still `queued`. This is the only shape that reaches
        `_kind` with the two disagreeing: `receive` builds the retry timer's own
        body from the true kind, and a later retry against a sample `receive`
        already moved to `running` returns before `_kind`. An earlier version of
        this test used each of those and so passed under either implementation --
        it could not fail, which is worth stating because it looked convincing.

        With the body preferred, the bench is asked for `urine` and answers with
        a urine machine while `state["kind"]` stays `blood`; `on_reading` then
        sends `blood` to `qc`, judging a urine result against a blood reference.
        """
        with tempfile.TemporaryDirectory() as directory:
            case = json.loads(_read(BUSY))
            case["name"] = "glass-kind-lie"
            case["namespace"] = "sim-glass-kind-lie"
            # The sample's real kind is `blood`; the retry body claims `urine`.
            case["arrivals"] = [
                {"at_ns": 0, "handler": "receive", "body": {"sample": "s1", "kind": "blood"}},
                {
                    "at_ns": 1_000_000,
                    "handler": "retry",
                    "body": {"sample": "s1", "kind": "urine"},
                },
            ]
            # The first bench call is busy, so the sample stays `queued` and the
            # retry above is the delivery that reaches `_kind`.
            case["ports"]["bench"]["replies"] = [
                {"match": {"$any": True}, "response": {"status": "busy"}, "repeat": 1},
                {"match": {"$any": True}, "response": {"status": "ready", "machine": "m9"}},
            ]
            case["assertions"] = [
                {"op": "port_called", "port": "bench", "times": 2},
            ]
            # No reading is ever sent, so the sample ends up overdue and calls
            # `report`; the busy case's script only matches `released`, so it is
            # replaced to keep the run from faulting on an unmatched call.
            case["ports"]["report"] = {
                "mode": "script",
                "replies": [{"match": {"$any": True}, "response": {"status": "filed"}}],
            }
            path = os.path.join(directory, "lie.json")
            write_json(path, case)
            out = os.path.join(directory, "out.json")
            proc = run_glass(path, "sim-glass-kind-lie", out)
            self.assertEqual(proc.returncode, 0, proc.stderr + _read(out))
            art = _artifact(out)

            bench_calls = [c for c in art["port_calls"] if c["port"] == "bench"]
            self.assertEqual(len(bench_calls), 2, "the retry never reached the bench")
            asked = [c["request"].get("kind") for c in bench_calls]
            self.assertEqual(
                asked,
                ["blood", "blood"],
                f"the bench was asked for {asked}, but the sample's kind is blood",
            )
            # And the state agrees with what was asked for, so the QC reference
            # and the machine that produced the result match.
            self.assertEqual(art["final_state"]["sample"]["kind"], "blood")

    def test_reading_with_a_non_integer_value_faults(self):
        with tempfile.TemporaryDirectory() as directory:
            case = json.loads(_read(RELEASE))
            case["name"] = "glass-badvalue"
            case["namespace"] = "sim-glass-badvalue"
            case["arrivals"] = [
                {"at_ns": 0, "handler": "receive", "body": {"sample": "s1", "kind": "blood"}},
                {"at_ns": 1_000_000_000, "handler": "reading", "body": {"sample": "s1", "value": "high"}},
            ]
            case["assertions"] = [{"op": "fault_is", "code": "bad_value"}]
            case_path = os.path.join(directory, "badvalue.json")
            write_json(case_path, case)
            out = os.path.join(directory, "badvalue-out.json")
            proc = run_glass(case_path, "sim-glass-badvalue", out)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr + _read(out))
            art = _artifact(out)
            self.assertEqual(art["fault"]["code"], "bad_value")
            self.assertEqual([call["port"] for call in art["port_calls"]], ["bench"])

    def test_replay_hides_the_sentinel_and_mismatch_does_too(self):
        with tempfile.TemporaryDirectory() as directory:
            scripted = os.path.join(directory, "scripted.json")
            proc = run_glass(RELEASE, "sim-glass-release", scripted)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            art = _artifact(scripted)
            buckets = {"bench": [], "qc": [], "report": []}
            for line in tape_from_artifact(art).decode("ascii").splitlines():
                buckets[loads(line)["port"]].append(line)
            cutoff = 1_000_000_000
            for port, lines in buckets.items():
                with open(os.path.join(directory, port + ".jsonl"), "w", encoding="ascii") as handle:
                    handle.write("\n".join(lines) + "\n")
            hidden = dumps(
                {
                    "format": "seam-tape",
                    "version": 1,
                    "at_ns": cutoff + 1,
                    "port": "bench",
                    "request": {"kind": "blood"},
                    "response": {"status": "SEAM_HIDDEN_SENTINEL"},
                }
            )
            with open(os.path.join(directory, "bench.jsonl"), "a", encoding="ascii") as handle:
                handle.write(hidden + "\n")

            case = json.loads(_read(RELEASE))
            case["name"] = "glass-replay"
            case["namespace"] = "sim-glass-replay"
            case["ports"] = {
                port: {
                    "mode": "recording",
                    "tape": port + ".jsonl",
                    "cutoff_ns": cutoff,
                    "policy": "ordered",
                }
                for port in ("bench", "qc", "report")
            }
            case_path = os.path.join(directory, "replay.json")
            write_json(case_path, case)
            replay_path = os.path.join(directory, "replay-out.json")
            proc = run_glass(case_path, "sim-glass-replay", replay_path)
            blob = proc.stdout + proc.stderr + _read(replay_path)
            self.assertNotIn("SEAM_HIDDEN_SENTINEL", blob)
            self.assertEqual(proc.returncode, 0, proc.stderr + blob)
            replay = _artifact(replay_path)
            self.assertNotEqual(replay["digest"], art["digest"])
            self.assertEqual(replay["final_state"], art["final_state"])
            self.assertEqual(
                [event["body"] for event in replay["events"] if event["kind"] == "deliver"],
                [event["body"] for event in art["events"] if event["kind"] == "deliver"],
            )
            self.assertEqual(
                [(call["request"], call["response"]) for call in replay["port_calls"]],
                [(call["request"], call["response"]) for call in art["port_calls"]],
            )
            self.assertTrue(all(call["source"] == "recording" for call in replay["port_calls"]))

            marked = dumps(
                {
                    "format": "seam-tape",
                    "version": 1,
                    "at_ns": cutoff,
                    "port": "qc",
                    "request": {"kind": "blood", "marker": "SEAM_HIDDEN_SENTINEL", "value": 42},
                    "response": {"status": "pass"},
                }
            )
            with open(os.path.join(directory, "qc.jsonl"), "w", encoding="ascii") as handle:
                handle.write(marked + "\n")
            case["name"] = "glass-mismatch"
            case["namespace"] = "sim-glass-mismatch"
            case["arrivals"][1]["body"] = {"sample": "s1", "value": 99}
            case["assertions"] = [{"op": "fault_is", "code": "tape_mismatch"}]
            bad_case = os.path.join(directory, "mismatch.json")
            write_json(bad_case, case)
            bad_out = os.path.join(directory, "mismatch-out.json")
            proc = run_glass(bad_case, "sim-glass-mismatch", bad_out)
            text = proc.stdout + proc.stderr + _read(bad_out)
            self.assertNotIn("SEAM_HIDDEN_SENTINEL", text)
            self.assertEqual(proc.returncode, 2, text)
            got = _artifact(bad_out)
            self.assertEqual(got["fault"]["code"], "tape_mismatch")
            qc = [call for call in got["port_calls"] if call["port"] == "qc"]
            self.assertEqual(qc[0]["request"], {"kind": "blood", "value": 99})
            self.assertIsNone(qc[0]["response"])

    def test_socket_leak_fails_before_a_port_call(self):
        with tempfile.TemporaryDirectory() as directory:
            case = json.loads(_read(RELEASE))
            case["name"] = "glass-leak"
            case["namespace"] = "sim-glass-leak"
            case["arrivals"] = [
                {
                    "at_ns": 0,
                    "handler": "receive",
                    "body": {"sample": "s1", "kind": "blood", "leak": "socket"},
                }
            ]
            case["assertions"] = [{"op": "fault_is", "code": "real_io"}]
            case_path = os.path.join(directory, "leak.json")
            write_json(case_path, case)
            out = os.path.join(directory, "leak-out.json")
            proc = run_glass(case_path, "sim-glass-leak", out, timeout=5)
            text = proc.stdout + proc.stderr + _read(out)
            self.assertNotIn(LEAK_ADDR, text)
            self.assertEqual(proc.returncode, 2, text)
            art = _artifact(out)
            self.assertEqual(art["fault"]["code"], "real_io")
            self.assertEqual(art["fault"]["op"], "socket.getaddrinfo")
            self.assertEqual(art["port_calls"], [])


class LiveTest(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in list(os.environ)
            if key.startswith("SEAM_") or key.startswith("GLASS_")
        }
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("SEAM_") or key.startswith("GLASS_"):
                os.environ.pop(key, None)
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
        self.assertIsInstance(time.time(), float)

    def test_live_deliver_records_one_tape_line(self):
        from glass.pipeline import build

        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "factories.log")
            tape = os.path.join(directory, "live.jsonl")
            os.environ["GLASS_FACTORY_LOG"] = log
            os.environ["SEAM_RECORD"] = "1"
            os.environ["SEAM_ARTIFACT"] = tape
            seen = []
            rt = build()
            rt.set_timer_backend(
                lambda token, at_ns, handler, body, name: seen.append((token, handler, name))
            )
            rt.start_live()
            rt.deliver("receive", {"sample": "s1", "kind": "blood"})
            rt.deliver("reading", {"sample": "s1", "value": 42})
            rt._record.close()
            self.assertEqual(rt.state_copy()["sample"]["status"], "released")
            self.assertTrue(rt.state_copy()["sample"]["id"].startswith("smp_"))
            self.assertEqual(seen[0][0][:1], "t")
            self.assertEqual(seen[0][1:], ("due", "due-once"))
            with open(log, encoding="ascii") as handle:
                self.assertEqual(handle.read(), "bench\nqc\nreport\n")
            with open(tape, encoding="ascii") as handle:
                lines = handle.read().splitlines()
            self.assertEqual(len(lines), 3)
            first = loads(lines[0])
            self.assertEqual(first["format"], "seam-tape")
            self.assertEqual(first["port"], "bench")
            self.assertEqual(first["request"], {"kind": "blood"})
            self.assertEqual(first["response"]["status"], "ready")
            self.assertIsInstance(first["at_ns"], int)
            self.assertEqual(loads(lines[2])["port"], "report")

    def test_module_without_sim_exits_clean(self):
        proc = run_proc([sys.executable, "-m", "glass"], child_env(), timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")
