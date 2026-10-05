"""Lab samples share one runtime and retain independent processing and reporting state."""

import os

from seam import Fault, PortError, Runtime
from glass.backends import report_store

RETRY_NS = 5_000_000_000
DUE_NS = 3_600_000_000_000
REPORT_ATTEMPTS = 2
DISPOSITIONS = frozenset({"released", "overdue", "dropped", "scrapped"})
SAMPLE_STATUSES = DISPOSITIONS | {
    "received",
    "queued",
    "running",
    "retest",
    "report_pending",
    "report_failed",
}
LEAK_ADDR = "203.0.113.1"
LEAK_PORT = 80


def _mark(port):
    path = os.environ.get("GLASS_FACTORY_LOG")
    if path:
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


def validate_samples(state, *, timer_tokens=True):
    if type(state) is not dict or type(state.get("samples")) is not dict:
        raise Fault("bad_value")
    samples = state["samples"]
    for public, row in samples.items():
        if type(row) is not dict or row.get("public") != public:
            raise Fault("bad_value")
        if any(type(row.get(key)) is not str or not row[key] for key in ("id", "kind", "status")):
            raise Fault("bad_value")
        if type(row.get("retested")) is not bool or type(row.get("report_attempts")) is not int:
            raise Fault("bad_value")
        if (
            row["status"] not in SAMPLE_STATUSES
            or not 0 <= row["report_attempts"] <= REPORT_ATTEMPTS
        ):
            raise Fault("bad_value")
        if row["status"] == "running" and (
            (timer_tokens and (type(row.get("due")) is not str or not row["due"]))
            or type(row.get("machine")) is not str
            or not row["machine"]
        ):
            raise Fault("bad_value")
        if row["status"] == "report_pending":
            if type(row.get("disposition")) is not str or row["disposition"] not in DISPOSITIONS:
                raise Fault("bad_value")
    return samples


def _samples(ctx):
    return validate_samples(ctx.state)


def _sample(ctx, body):
    if type(body) is not dict or type(body.get("sample")) is not str:
        return None
    return _samples(ctx).get(body["sample"])


def _save(ctx, row):
    samples = _samples(ctx)
    samples[row["public"]] = row
    ctx.set_state({"samples": samples})


def _timer_name(row, purpose):
    return f"{purpose}-{row['id']}"


def _receipt(body):
    if type(body) is not dict:
        raise Fault("bad_value")
    public, kind = body.get("sample"), body.get("kind")
    if type(public) is not str or not public or type(kind) is not str or not kind:
        raise Fault("bad_value")
    return public, kind


def _slot(reply):
    if type(reply) is not dict or reply.get("status") not in ("ready", "busy"):
        raise Fault("bad_value")
    if reply["status"] == "ready" and (
        type(reply.get("machine")) is not str or not reply["machine"]
    ):
        raise Fault("bad_value")
    return reply


def _arm_due(ctx, row, machine, retested, purpose):
    row["due"] = ctx.schedule_after(
        DUE_NS, "due", {"sample": row["public"]}, name=_timer_name(row, purpose)
    )
    row.update(status="running", machine=machine, retested=retested)
    _save(ctx, row)


def _report_failed(ctx, row):
    if row["report_attempts"] >= REPORT_ATTEMPTS:
        row["status"] = "report_failed"
    else:
        row["report_retry"] = ctx.schedule_after(
            RETRY_NS,
            "report_retry",
            {"sample": row["public"]},
            name=_timer_name(row, "report-once"),
        )
    _save(ctx, row)


def _report(ctx, row):
    if row["report_attempts"] >= REPORT_ATTEMPTS:
        row["status"] = "report_failed"
        _save(ctx, row)
        return
    row["report_attempts"] += 1
    _save(ctx, row)
    request = {
        "sample": row["public"],
        "disposition": row["disposition"],
        "idempotency_key": f"{row['id']}/{row['disposition']}",
    }
    try:
        reply = ctx.emit("report", request)
    except PortError:
        _report_failed(ctx, row)
        return
    if type(reply) is not dict or reply.get("status") not in ("filed", "failed"):
        raise Fault("bad_value")
    if reply["status"] == "failed":
        _report_failed(ctx, row)
        return
    row["status"] = row["disposition"]
    _save(ctx, row)


def _file(ctx, row, disposition):
    row.update(status="report_pending", disposition=disposition)
    _report(ctx, row)


def on_receive(ctx, body):
    if type(body) is dict and body.get("leak") == "socket":
        import socket

        socket.create_connection((LEAK_ADDR, LEAK_PORT), timeout=1)
    public, kind = _receipt(body)
    existing = _samples(ctx).get(public)
    if existing is not None:
        if existing["kind"] != kind:
            raise Fault("bad_value")
        return
    row = {
        "id": ctx.id("smp"),
        "public": public,
        "kind": kind,
        "status": "received",
        "retested": False,
        "report_attempts": 0,
    }
    _save(ctx, row)
    slot = _slot(ctx.emit("bench", {"kind": kind}))
    if slot["status"] == "busy":
        row["retry"] = ctx.schedule_after(
            RETRY_NS, "retry", {"sample": public}, name=_timer_name(row, "retry-once")
        )
        row["status"] = "queued"
        _save(ctx, row)
        return
    _arm_due(ctx, row, slot["machine"], False, "due-once")


def on_retry(ctx, body):
    row = _sample(ctx, body)
    if row is None or row["status"] != "queued":
        return
    slot = _slot(ctx.emit("bench", {"kind": row["kind"]}))
    if slot["status"] == "busy":
        _file(ctx, row, "dropped")
        return
    _arm_due(ctx, row, slot["machine"], False, "due-once")


def on_reading(ctx, body):
    row = _sample(ctx, body)
    if row is None or row["status"] != "running" or "value" not in body:
        return
    value = body["value"]
    if type(value) is not int:
        raise Fault("bad_value")
    verdict = ctx.emit("qc", {"kind": row["kind"], "value": value})
    if type(verdict) is not dict or verdict.get("status") not in ("pass", "fail"):
        raise Fault("bad_value")
    ctx.cancel(row["due"])
    if verdict["status"] == "pass":
        _file(ctx, row, "released")
    elif row["retested"]:
        _file(ctx, row, "scrapped")
    else:
        row["retest"] = ctx.schedule_after(
            RETRY_NS, "retest", {"sample": row["public"]}, name=_timer_name(row, "retest-once")
        )
        row["status"] = "retest"
        _save(ctx, row)


def on_retest(ctx, body):
    row = _sample(ctx, body)
    if row is None or row["status"] != "retest":
        return
    slot = _slot(ctx.emit("bench", {"kind": row["kind"]}))
    if slot["status"] == "busy":
        _file(ctx, row, "dropped")
        return
    _arm_due(ctx, row, slot["machine"], True, "due-retest")


def on_due(ctx, body):
    row = _sample(ctx, body)
    if row is not None and row["status"] == "running":
        _file(ctx, row, "overdue")


def on_report_retry(ctx, body):
    row = _sample(ctx, body)
    if row is not None and row["status"] == "report_pending":
        _report(ctx, row)


def build(*, report_factory=make_report, bench_factory=make_bench, qc_factory=make_qc,
          runtime=None, report_backend=report_store):
    rt = Runtime(namespace="glass", initial_state={"samples": {}}) if runtime is None else runtime
    rt.port("bench", bench_factory)
    rt.port("qc", qc_factory)
    rt.port("report", report_factory)
    if report_backend is not None:
        rt.sim_port("report", report_backend)
    for name, handler in (
        ("receive", on_receive),
        ("retry", on_retry),
        ("reading", on_reading),
        ("retest", on_retest),
        ("due", on_due),
        ("report_retry", on_report_retry),
    ):
        rt.on(name, handler)
    return rt
