"""Exercise a real HTTP service, in-flight capture, installed replay, and a counterfactual."""

import argparse
import hashlib
import http.client
from pathlib import Path
import select
import subprocess
import sys
import time

import glass
from glass.capture import run_bundle, write_new
from glass.capture_http import LOOPBACK
from glass.pipeline import DUE_NS, RETRY_NS
from seam.canon import dumps, loads


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def request(port, path, body=None):
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=5)
    try:
        connection.request(
            "GET" if body is None else "POST",
            path,
            body=None if body is None else dumps(body),
        )
        response = connection.getresponse()
        value = loads(response.read())
        check(response.status == 200, f"HTTP {response.status}: {value}")
        return value
    finally:
        connection.close()


def launch(db):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "glass.capture_cli",
            "serve",
            "--db",
            str(db),
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([process.stdout], [], [], 10)
        check(bool(ready), "HTTP service did not start")
        line = process.stdout.readline().strip()
        check(line.startswith("glass listening on localhost:"), f"bad startup: {line}")
        return process, int(line.rsplit(":", 1)[1])
    except BaseException:
        stop(process)
        raise


def stop(process):
    process.kill()
    _, stderr = process.communicate(timeout=5)
    check(not stderr, f"HTTP service stderr: {stderr}")


def proof(root, installed):
    if installed:
        check(
            "site-packages" in Path(glass.__file__).parts,
            "proof imported source checkout",
        )
    root.mkdir(parents=True, mode=0o700)
    db = root / "live.sqlite"
    subprocess.run(
        [sys.executable, "-m", "glass.capture_cli", "init", "--db", str(db)],
        check=True,
        timeout=10,
    )
    process, port = launch(db)
    try:
        state = request(
            port,
            "/events",
            {
                "handler": "receive",
                "body": {"sample": "s1", "kind": "blood"},
            },
        )
        check(state["samples"]["s1"]["status"] == "queued", "sample was not queued")
        ident = state["samples"]["s1"]["id"]
        request(port, "/capture/start", {})
        state = request(
            port,
            "/events",
            {
                "handler": "receive",
                "body": {"sample": "s2", "kind": "blood"},
            },
        )
        future_ident = state["samples"]["s2"]["id"]
        stop(process)
        process, port = launch(db)
        recovered = request(port, "/state")["product"]["samples"]
        check(
            recovered["s1"]["id"] == ident and recovered["s2"]["id"] == future_ident,
            "restart changed IDs",
        )
        timeout = time.monotonic() + 10
        while (
            request(port, "/state")["product"]["samples"]["s1"]["status"] != "running"
        ):
            check(time.monotonic() < timeout, "bench retry timer did not fire")
            time.sleep(0.05)
        for sample in ("s1", "s2", "s1"):
            state = request(
                port,
                "/events",
                {
                    "handler": "reading",
                    "body": {"sample": sample, "value": 42},
                },
            )
            expected = "report_pending" if sample == "s1" else "released"
            check(
                state["samples"][sample]["status"] == expected,
                "unexpected report outcome",
            )
        stop(process)
        process, port = launch(db)
        timeout = time.monotonic() + 10
        while True:
            state = request(port, "/state")
            if all(
                row["status"] == "released"
                for row in state["product"]["samples"].values()
            ):
                break
            check(time.monotonic() < timeout, "report retry timer did not fire")
            time.sleep(0.05)
        document = request(port, "/capture/finish", {})
        write_new(root / "capture.json", document)
        check(
            document["payload"]["initial_state"]["samples"]["s1"]["status"] == "queued",
            "empty snapshot",
        )
        check(
            len(document["payload"]["product_data"]["timers"]) == 1,
            "pending retry was not exported",
        )
        stop(process)
        process, port = launch(db)
        check(
            request(port, "/capture/latest") == document,
            "finished capture did not survive restart",
        )
        check(
            request(port, "/capture/finish", {}) == document,
            "finish retry changed the capture cutoff",
        )
    finally:
        stop(process)
    original_db = hashlib.sha256(db.read_bytes()).hexdigest()
    results = []
    for name, delay in (
        ("replay1", 0),
        ("replay2", 0),
        ("late-reading", DUE_NS + RETRY_NS),
    ):
        result = run_bundle(document, root / name, reading_delay_ns=delay)
        check(
            result.returncode == (1 if delay else 0),
            f"{name}: {result.artifact}, {result.stderr}",
        )
        results.append(result)
    check(
        (root / "replay1/artifact.json").read_bytes()
        == (root / "replay2/artifact.json").read_bytes(),
        "replays differ",
    )
    samples = results[0].artifact["final_state"]["view"]["samples"]
    check(
        samples["s1"]["id"] == ident and samples["s2"]["id"] == future_ident,
        "private IDs changed",
    )
    lab = results[0].artifact["world_states"]["lab"]
    check(
        len(lab["filed"]) == 2 and sorted(lab["attempts"].values()) == [1, 2],
        "reports were duplicated",
    )
    check(
        all(
            row["status"] == "overdue"
            for row in results[2].artifact["final_state"]["view"]["samples"].values()
        ),
        "late readings did not miss the deadline",
    )
    check(
        hashlib.sha256(db.read_bytes()).hexdigest() == original_db,
        "replay modified live database",
    )
    summary = {
        "proof": "passed",
        "installed": installed,
        "capture": str(root / "capture.json"),
        "replay_digest": results[0].artifact["digest"],
        "byte_identical": True,
        "private_ids_preserved": True,
        "live_database_unchanged": True,
        "counterfactual": "overdue",
        "forced_restarts": 3,
        "completed_capture_recovered": True,
    }
    write_new(root / "summary.json", summary)
    print(dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    proof(args.out.resolve(), args.installed)
