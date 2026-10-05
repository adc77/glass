# glass

A lab-sample test product for [seam](https://github.com/adc77/seam), the generalized simulation SDK. Glass is one consumer; other products should exercise different SDK boundaries.

Each sample is offered to an instrument, retried once if the bench is busy, judged when a reading arrives, and reported. A due timer marks a missing reading overdue. One runtime handles multiple samples independently, with sample-specific timer names and state under `samples.<public-id>`.

Handlers are synchronous. They take time and ids from the seam context, and the only way out is `emit` on three ports: `bench`, `qc`, and `report`.

## Setup

Seam is not vendored here. Clone it as a sibling directory, which is where the test suite looks first:

```shell
git clone https://github.com/adc77/seam.git
git clone https://github.com/adc77/glass.git
git -C seam checkout f20a79959432e88dd1ef880a328796e0cabdfc78
cd glass   # with ../seam present
```

This branch requires Seam 0.2.0. Its package dependency and CI pin a specific SDK commit; an older main checkout cannot run the new backend cases. If you keep compatible checkouts somewhere else, point the suite at it:

```shell
SEAM_SDK_PATH=/path/to/seam python3 -m unittest discover -s tests -t .
```

## Test

```shell
python3 -m unittest discover -s tests -t .
```

Nothing here needs a network, a GPU, or another machine.

## Simulate one case

```shell
SEAM_SIM=1 \
SEAM_NAMESPACE=sim-glass-release \
SEAM_CASE=glass/cases/release.json \
PYTHONPATH="../seam:." \
python3 -m glass
```

Stdout is the artifact path.

## Export-backed simulation

`glass/cases/lost_report_ack.json` uses a pinned, synthetic report-store export and a stateful simulation backend. The first report writes successfully but its acknowledgment is lost. Glass retries using the same idempotency key; the grader verifies one stored report, not merely one attempted call.

```python
from seam import run_product

result = run_product(
    "glass", "glass/cases/lost_report_ack.json", "sim-glass-lost-ack", "out.json"
)
assert result.returncode == 0
print(result.artifact["backend_states"]["report"])
```

`report_pending` is not `released`. Only a `filed` acknowledgment completes the intended disposition. A typed `PortError` or `failed` acknowledgment triggers one delayed retry; exhaustion leaves `report_failed`. The retry reuses the request and key. Exactly-once storage requires an external adapter that honors that key: Glass alone cannot guarantee it. Invalid acknowledgments fault instead of being treated as success.

Duplicate receipts with the same sample and kind do not re-offer the sample; conflicting kinds fault. Retry handlers use stored kind, not a supplied override. Readings for inactive samples are ignored. Cases now start with `{"samples": {}}`; the old single-sample state shape is intentionally unsupported.

Restored state must have recognized statuses, dispositions, and bounded attempt counts. A pending report that has already exhausted its budget cannot issue a third attempt.

## Live scaffold and limits

The CLI's live mode is a test driver, not a production service: it can deliver one `GLASS_BODY` receipt, records requested timers without running them, and closes its recording on exit. Real embedding code must provide clients and a scheduler that calls `rt.fire_timer(token)`; the tests exercise cancellation, duplicate callbacks, and report retry this way.

The bundled clients are stubs. The export is synthetic, not captured production data. A real deployment still needs durable state and timers, adapter conformance tests, report-key persistence, and sanitized point-in-time exports. See Seam's `SIMULATION.md` for SDK guarantees and limits.

Apache-2.0.
