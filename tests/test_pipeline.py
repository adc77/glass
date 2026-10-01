"""Adoption tests. Glass is the product. Seam is the library under it."""

import json
import os
import stat
import sys
import tempfile
import time
import unittest

from seam import tape_from_artifact
from seam.canon import dumps, loads

from tests.support import CASES, RELEASE, RELEASE_DIGEST, child_env, run_glass, run_proc, write_json


def _read(path):
    with open(path, encoding="ascii") as handle:
        return handle.read()


def _artifact(path):
    return loads(_read(path))


class ScriptTest(unittest.TestCase):
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

            first = os.path.join(directory, "release.json")
            second = os.path.join(directory, "release-again.json")
            again = run_glass(RELEASE, "sim-glass-release", second)
            self.assertEqual(again.returncode, 0, again.stderr)
            data = _read(first).encode("ascii")
            self.assertEqual(data, _read(second).encode("ascii"))
            self.assertTrue(data.endswith(b"\n"))
            self.assertEqual(data.count(b"\n"), 1)
            self.assertFalse(os.path.exists(first + ".tmp"))
            mode = stat.S_IMODE(os.stat(first).st_mode)
            self.assertEqual(mode, 0o644)
            art = _artifact(first)
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
            self.assertNotIn("203.0.113.1", text)
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
