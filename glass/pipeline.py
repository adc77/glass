"""Lab sample pipeline. A seam product, not the checkout proof.

A sample arrives, an instrument takes it or the bench retries once, a reading
is judged, and a report is filed once. A due timer scraps a sample that never
comes back. Time, ids, and outbound calls go through the seam context.
"""

import os

from seam import Runtime
from seam.errors import Fault

# Retry and due delays. The case files in `glass/cases` place their arrivals in
# terms of these two numbers, so changing either one means the arrivals have to
# move with it: a reading that arrives before the retest it is testing would
# silently stop testing the retest. The suite fails loudly if they drift apart,
# which is what keeps the two in step without either being derived.
RETRY_NS = 5_000_000_000
DUE_NS = 3_600_000_000_000


def _mark(port):
    path = os.environ.get("GLASS_FACTORY_LOG")
    if not path:
        return
    with open(path, "a", encoding="ascii") as handle:
        handle.write(port + "\n")


def make_bench():
    _mark("bench")
    return lambda request: {"status": "ready", "machine": "m1"}


def make_qc():
    _mark("qc")
    return lambda request: {"status": "pass"}


def make_report():
    _mark("report")
    return lambda request: {"status": "filed"}


def _sample(ctx):
    current = ctx.state
    if type(current) is not dict:
        return None
    row = current.get("sample")
    if type(row) is not dict:
        return None
    return row


def _same(row, body):
    return type(body) is dict and row.get("public") == body.get("sample")


def _observed(body):
    """The integer `value` on a reading body, or None if this is not a reading.

    Called only after the sample and status checks have already passed, so a
    stale or duplicate delivery is still ignored rather than faulting a run over
    a field nobody was going to read. A body that claims to be a reading but
    carries a non-integer value is a fault: the bench was waiting for a number.
    """
    if type(body) is not dict or "value" not in body:
        return None
    value = body["value"]
    if type(value) is not int:
        raise Fault("bad_value")
    return value


def _slot(reply):
    if type(reply) is not dict or reply.get("status") not in ("ready", "busy"):
        raise Fault("bad_value")
    if reply["status"] == "ready" and type(reply.get("machine")) is not str:
        raise Fault("bad_value")
    return reply


def _arm_due(ctx, public, kind, sid, machine, retested, due_name):
    token = ctx.schedule_after(DUE_NS, "due", {"sample": public}, name=due_name)
    ctx.set_state(
        {
            "sample": {
                "id": sid,
                "public": public,
                "kind": kind,
                "status": "running",
                "machine": machine,
                "retested": retested,
                "due": token,
            }
        }
    )


def _file(ctx, public, disposition):
    ctx.patch("sample.status", disposition)
    ctx.emit("report", {"sample": public, "disposition": disposition})


def _receipt(body):
    """Validate an inbound arrival body.

    Every other handler in this pipeline treats a body it does not recognise
    as a no-op. `receive` used to index the body directly, so a malformed
    arrival surfaced as a KeyError and the run ended as `handler_error` rather
    than a clean `bad_value`. Validating here keeps the failure legible.
    """
    if type(body) is not dict:
        raise Fault("bad_value")
    sample = body.get("sample")
    kind = body.get("kind")
    if type(sample) is not str or sample == "" or type(kind) is not str or kind == "":
        raise Fault("bad_value")
    return sample, kind


#: Address the deliberate leak in `on_receive` aims at. 203.0.113.0/24 is
#: TEST-NET-3 (RFC 5737), reserved and unroutable, so the connection attempt
#: always fails at `getaddrinfo` rather than depending on the network. The test
#: suite and the CI leak check both assert on this same value, so it is named
#: here rather than spelled out in three places.
LEAK_ADDR = "203.0.113.1"
LEAK_PORT = 80


def on_receive(ctx, body):
    # One guarded leak, so this product can prove the sim process fails closed.
    if type(body) is dict and body.get("leak") == "socket":
        import socket

        socket.create_connection((LEAK_ADDR, LEAK_PORT), timeout=1)
    public, kind = _receipt(body)
    sid = ctx.id("smp")
    slot = _slot(ctx.emit("bench", {"kind": kind}))
    if slot["status"] == "busy":
        token = ctx.schedule_after(
            RETRY_NS,
            "retry",
            {"sample": public, "kind": kind},
            name="retry-once",
        )
        ctx.set_state(
            {
                "sample": {
                    "id": sid,
                    "public": public,
                    "kind": kind,
                    "status": "queued",
                    "retested": False,
                    "retry": token,
                }
            }
        )
        return
    _arm_due(ctx, public, kind, sid, slot["machine"], False, "due-once")


def _kind(row, body):
    """The sample kind for a retry or retest delivery.

    `receive` validates its body, but `retry` and `retest` deliveries are
    scheduled by the pipeline and only carry what the scheduler was given.
    Anything that reaches `body["kind"]` unguarded turns a missing field into a
    `KeyError`, which the runner reports as `handler_error` and hides the cause.
    Falling back to the state's own kind is also more correct: the sample's kind
    was fixed when it was received, and state is the authority on it.
    """
    if type(body) is dict:
        kind = body.get("kind")
        if type(kind) is str and kind:
            return kind
    if type(row) is dict:
        kind = row.get("kind")
        if type(kind) is str and kind:
            return kind
    raise Fault("bad_value")


def on_retry(ctx, body):
    row = _sample(ctx)
    if row is None or row.get("status") != "queued" or not _same(row, body):
        return
    slot = _slot(ctx.emit("bench", {"kind": _kind(row, body)}))
    if slot["status"] == "busy":
        _file(ctx, body["sample"], "dropped")
        return
    token = ctx.schedule_after(DUE_NS, "due", {"sample": body["sample"]}, name="due-once")
    ctx.patch("sample.status", "running")
    ctx.patch("sample.machine", slot["machine"])
    ctx.patch("sample.due", token)


def on_reading(ctx, body):
    row = _sample(ctx)
    # Stale or duplicate deliveries are ignored before the value is inspected.
    if row is None or row.get("status") != "running" or not _same(row, body):
        return
    value = _observed(body)
    if value is None:
        return
    due = row.get("due")
    if type(due) is str:
        ctx.cancel(due)
    verdict = ctx.emit("qc", {"kind": row["kind"], "value": value})
    if type(verdict) is not dict or verdict.get("status") not in ("pass", "fail"):
        raise Fault("bad_value")
    if verdict["status"] == "pass":
        _file(ctx, body["sample"], "released")
        return
    if row.get("retested") is True:
        _file(ctx, body["sample"], "scrapped")
        return
    token = ctx.schedule_after(
        RETRY_NS,
        "retest",
        {"sample": body["sample"], "kind": row["kind"]},
        name="retest-once",
    )
    ctx.patch("sample.status", "retest")
    ctx.patch("sample.retest", token)


def on_retest(ctx, body):
    row = _sample(ctx)
    if row is None or row.get("status") != "retest" or not _same(row, body):
        return
    slot = _slot(ctx.emit("bench", {"kind": _kind(row, body)}))
    if slot["status"] != "ready":
        _file(ctx, body["sample"], "dropped")
        return
    token = ctx.schedule_after(
        DUE_NS,
        "due",
        {"sample": body["sample"]},
        name="due-retest",
    )
    ctx.patch("sample.status", "running")
    ctx.patch("sample.machine", slot["machine"])
    ctx.patch("sample.retested", True)
    ctx.patch("sample.due", token)


def on_due(ctx, body):
    row = _sample(ctx)
    if row is None or row.get("status") != "running" or not _same(row, body):
        return
    _file(ctx, body["sample"], "overdue")


def build():
    rt = Runtime(namespace="glass")
    rt.port("bench", make_bench)
    rt.port("qc", make_qc)
    rt.port("report", make_report)
    rt.on("receive", on_receive)
    rt.on("retry", on_retry)
    rt.on("reading", on_reading)
    rt.on("retest", on_retest)
    rt.on("due", on_due)
    return rt
