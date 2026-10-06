"""Glass's opt-in capture profile preserves IDs and reconstructs domain-owned pending timers."""

import sys

from seam import Fault, main
from seam.canon import deep_copy

from glass.capture import TIMER_FIELDS, validate_metadata, validate_state, view
from glass.lab import lab_world
from glass.pipeline import build


class ReceiptContext:
    def __init__(self, ctx, body):
        self.ctx = ctx
        self.body = body

    def __getattr__(self, name):
        return getattr(self.ctx, name)

    def id(self, prefix):
        ids = self.ctx.config["capture_ids"]
        if prefix != "smp" or self.body["sample"] not in ids:
            raise Fault("bad_value")
        return ids[self.body["sample"]]


def on_bootstrap(ctx, body):
    state = ctx.state
    validate_state(state)
    validate_metadata(state, body["metadata"], ctx.now())
    mapping = {}
    for row in body["metadata"]["timers"]:
        mapping[row["token"]] = ctx.schedule_at(
            row["at_ns"], row["handler"], row["body"], name=row["name"]
        )
    for row in state["samples"].values():
        for field in TIMER_FIELDS & set(row):
            if row[field] in mapping:
                row[field] = mapping[row[field]]
    ctx.set_state(state)


def on_observe(ctx, body):
    state = ctx.state
    ctx.set_state(
        {
            "samples": deep_copy(state["samples"]),
            "view": view(state),
            "lab": ctx.emit("inspect", {}),
        }
    )


def build_replay():
    rt = build(report_backend=None)
    rt.port("inspect", lambda: None)
    rt.sim_world("lab", lab_world, ports=("bench", "qc", "report", "inspect"))
    receive = rt.handlers["receive"]
    rt.handlers["receive"] = lambda ctx, body: receive(ReceiptContext(ctx, body), body)
    rt.on("bootstrap", on_bootstrap)
    rt.on("observe", on_observe)
    return rt


if __name__ == "__main__":
    sys.exit(main(build_replay()))
