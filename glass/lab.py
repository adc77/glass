"""A transactional lab adapter used unchanged by normal operation and isolated replay."""

import sqlite3

from seam import PortError, World
from seam.canon import deep_copy, dumps, loads

from glass.pipeline import DISPOSITIONS


def initial_lab(*, busy_remaining=1, lose_ack=True, qc_max=100):
    state = {
        "busy_remaining": busy_remaining,
        "lose_ack": lose_ack,
        "qc_max": qc_max,
        "filed": {},
        "attempts": {},
    }
    validate_lab(state)
    return state


def validate_lab(state):
    if type(state) is not dict or set(state) != {
        "busy_remaining",
        "lose_ack",
        "qc_max",
        "filed",
        "attempts",
    }:
        raise ValueError("invalid lab schema")
    if (
        type(state["busy_remaining"]) is not int
        or state["busy_remaining"] < 0
        or type(state["qc_max"]) is not int
        or type(state["lose_ack"]) is not bool
        or type(state["filed"]) is not dict
        or type(state["attempts"]) is not dict
        or set(state["filed"]) != set(state["attempts"])
    ):
        raise ValueError("invalid lab values")
    for key, report in state["filed"].items():
        if (
            type(key) is not str
            or not key
            or type(report) is not dict
            or set(report) != {"sample", "disposition"}
            or type(report["sample"]) is not str
            or not report["sample"]
            or type(report["disposition"]) is not str
            or report["disposition"] not in DISPOSITIONS
            or type(state["attempts"][key]) is not int
            or state["attempts"][key] < 1
        ):
            raise ValueError("invalid lab report")
    deep_copy(state)


class Lab:
    def __init__(self, path, *, snapshot=None):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS lab (id INTEGER PRIMARY KEY, body TEXT)"
            )
            row = self.db.execute("SELECT body FROM lab WHERE id=1").fetchone()
            if row is None:
                if snapshot is None:
                    raise ValueError("new lab database requires initial state")
                validate_lab(snapshot)
                self.db.execute("INSERT INTO lab VALUES (1, ?)", (dumps(snapshot),))
            elif snapshot is not None:
                raise ValueError("cannot replace an existing lab database")
            validate_lab(self.snapshot())
        except BaseException:
            self.db.close()
            raise

    def snapshot(self):
        return loads(self.db.execute("SELECT body FROM lab WHERE id=1").fetchone()[0])

    def restore(self, state):
        validate_lab(state)
        self.db.execute("UPDATE lab SET body=? WHERE id=1", (dumps(state),))

    def call(self, port, request):
        if type(request) is not dict:
            raise ValueError("invalid lab request")
        if port == "inspect":
            if request:
                raise ValueError("invalid inspection request")
            return self.snapshot()
        self.db.execute("BEGIN IMMEDIATE")
        lost = False
        try:
            state = self.snapshot()
            if port == "bench":
                if (
                    set(request) != {"kind"}
                    or type(request["kind"]) is not str
                    or not request["kind"]
                ):
                    raise ValueError("invalid bench request")
                if state["busy_remaining"]:
                    state["busy_remaining"] -= 1
                    reply = {"status": "busy"}
                else:
                    reply = {"status": "ready", "machine": "m1"}
            elif port == "qc":
                if (
                    set(request) != {"kind", "value"}
                    or type(request["kind"]) is not str
                    or not request["kind"]
                    or type(request["value"]) is not int
                ):
                    raise ValueError("invalid qc request")
                reply = {
                    "status": "pass" if request["value"] <= state["qc_max"] else "fail"
                }
            elif port == "report":
                if (
                    set(request) != {"sample", "disposition", "idempotency_key"}
                    or any(
                        type(value) is not str or not value
                        for value in request.values()
                    )
                    or request["disposition"] not in DISPOSITIONS
                ):
                    raise ValueError("invalid report request")
                key = request["idempotency_key"]
                report = {
                    "sample": request["sample"],
                    "disposition": request["disposition"],
                }
                if key in state["filed"] and state["filed"][key] != report:
                    raise ValueError("report idempotency key conflicts")
                state["filed"][key] = report
                state["attempts"][key] = state["attempts"].get(key, 0) + 1
                lost, state["lose_ack"] = state["lose_ack"], False
                reply = {"status": "filed"}
            else:
                raise ValueError("unknown lab port")
            validate_lab(state)
            self.db.execute("UPDATE lab SET body=? WHERE id=1", (dumps(state),))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        if lost:
            raise PortError("timeout")
        return reply

    def close(self):
        self.db.close()


def lab_world(ctx, data, config):
    lab = Lab(":memory:", snapshot=data)
    handlers = {
        port: (lambda request, port=port: lab.call(port, request))
        for port in ("bench", "qc", "report", "inspect")
    }
    return World(handlers, lab.snapshot, lab.restore, lab.close)
