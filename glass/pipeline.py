"""Lab sample pipeline. A seam product, not the checkout proof.

A sample arrives, an instrument takes it or the bench retries once, a reading
is judged, and a report is filed once. A due timer scraps a sample that never
comes back. Time, ids, and outbound calls go through the seam context.
"""

import os

from seam import Runtime
from seam.errors import Fault

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


def on_receive(ctx, body):
    # One guarded leak, so this product can prove the sim process fails closed.
    if type(body) is dict and body.get("leak") == "socket":
        import socket

        socket.create_connection(("203.0.113.1", 80), timeout=1)
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


def on_retry(ctx, body):
    row = _sample(ctx)
    if row is None or row.get("status") != "queued" or not _same(row, body):
        return
    slot = _slot(ctx.emit("bench", {"kind": body["kind"]}))
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
    slot = _slot(ctx.emit("bench", {"kind": body["kind"]}))
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
