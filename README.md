# glass

A lab-sample test product for [seam](https://github.com/adc77/seam), the generalized simulation SDK. Glass is one consumer; other products should exercise different SDK boundaries.

Each sample is offered to an instrument, retried once if the bench is busy, judged when a reading arrives, and reported. A due timer marks a missing reading overdue. One runtime handles multiple samples independently, with sample-specific timer names and state under `samples.<public-id>`.

Handlers are synchronous. They take time and ids from the seam context, and the only way out is `emit` on three ports: `bench`, `qc`, and `report`.

## Setup

Seam is not vendored here. The package dependency and CI both pin the compatible Seam 0.4.0 commit `ec454cbc1040ada7fed57a78ae94148c30406857`. Keep matching checkouts as siblings:

```shell
git -C seam checkout ec454cbc1040ada7fed57a78ae94148c30406857
python -m pip install ./glass
cd glass
```

Installation resolves that exact Git revision rather than assuming Seam 0.4.0 is on a package index. Older SDK checkouts cannot run the capture profile. If compatible checkouts live elsewhere:

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

## Live capture and replay

`glass-capture` is a separate loopback-only development service; `python -m glass` and the legacy cases remain unchanged. The new service injects a persistent SQLite lab adapter into the existing sample handlers and actually drives their timers. Product state, pending deadlines, lab effects, and captures share one SQLite transaction per operation. The same lab adapter executes inside a fresh in-memory SQLite world during replay.

```shell
glass-capture init --db /path/to/lab.sqlite
glass-capture serve --db /path/to/lab.sqlite --port 8766
```

The default lab returns one busy bench response, passes readings up to 100, and commits the first report while losing its acknowledgment. `--busy-count`, `--qc-max`, and `--keep-report-ack` configure initialization. Initialization refuses to overwrite an existing database. One process owns the database, enforced by a file lease. Do not expose this unauthenticated demo through a public proxy.

Send `POST /events` with a closed-schema object:

```json
{"handler":"receive","body":{"sample":"s1","kind":"blood"}}
{"handler":"reading","body":{"sample":"s1","value":42}}
```

`GET /state` returns the product outcome view and lab state. Call `POST /capture/start` with `{}` at a consistent current boundary, continue submitting events, then save the response from `POST /capture/finish` with `{}`. Capture includes raw sample state, dependency data, active retry/due/retest/report timers, existing and newly generated sample IDs, arrivals, and observed outcome assertions. Failed deliveries roll back their product and lab changes and invalidate an active capture; they are never silently omitted from a successful export. `POST /capture/abort` with `{}` explicitly discards an active or failed session without discarding product state or the last completed capture.

Restart `serve` with the same database to recover samples, private IDs, deadlines, and unfinished captures. Missed timers run at their original logical deadlines, in insertion order at equal timestamps, before new arrivals. Lab writes and report retries commit with the product frame, preserving report idempotency after a lost acknowledgment. Operations return only after commit: process death before commit rolls back both sides; death after commit preserves both. Retrying an identical receipt or reading uses the existing domain handlers; successful retries remain recorded as arrivals.

Completed captures are saved before their HTTP response is sent. If that response is lost, `GET /capture/latest` retrieves the exact frozen bundle without driving timers. Repeating `POST /capture/finish` with no active session returns the same bundle and cutoff. Only the last completed capture is retained; export it before completing another session. The database is created with mode 0600, and a product-owned file lease permits only one service writer.

Recovery requires the same Python-source identity for Glass and Seam. Changed sources, invalid frames, backwards clocks, and pre-durability databases with only a lab table are refused; there is no silent reset or automatic migration. Previously volatile sample state cannot be reconstructed from lab records. Initialize a new database explicitly when beginning a new run. SDK `SEAM_RECORD=1` is refused by this service because that external recording file is outside the SQLite transaction.

The envelope, checksum, Python-source identity, bounded import, protected case packaging, and supervised replay come from Seam's capture API (`../seam/CAPTURE.md` in the matching sibling checkout). Glass owns schema validation, ID preservation, timer reconstruction, and the observable outcome projection. Timer tokens are restored into new SDK bindings rather than compared as business state; sample IDs and report idempotency keys are preserved exactly.

```shell
glass-capture replay --bundle /path/to/capture.json --out /path/to/new-replay
glass-capture import --bundle /path/to/capture.json --out /path/to/new-case
glass-capture replay --bundle /path/to/capture.json --out /path/to/late --reading-delay-ns 3605000000000
```

Every output directory must be new. Captured inputs and metadata are mode 0600 inside a mode 0700 directory; artifacts use the SDK's existing file mode within that private directory. Generated cases explicitly process timers before arrivals at equal timestamps, matching the live service. Their assertions compare captured product and lab outcomes, not just return codes. The delayed-reading run keeps the original assertions: an altered outcome exits 1, not a false pass.

Run the installed-service proof from outside the checkout, without `PYTHONPATH`:

```shell
python /path/to/glass/scripts/capture_proof.py --out /path/to/new-proof --installed
```

The proof launches the actual HTTP process, captures a queued sample with a pending retry, receives another sample after the snapshot, and forcibly kills/restarts the process three times: during an active capture, while a report acknowledgment is pending, and after capture completion. It waits for real retries and replays twice in fresh processes. It requires recovered captures, byte-identical artifacts, preserved IDs, one stored report per sample, a delayed-reading `overdue` counterfactual, and an unchanged live database hash. Expect roughly ten seconds of real timer waiting. Tests additionally force process exit before/after SQLite commits, cover all four pending timer types, rollback after lab writes, exact-deadline arrivals, and checkpoint/resume at an equal-time boundary.

## Limits

The original `python -m glass` live mode remains a one-receipt scaffold with recorded, non-driving timers. Use the new capture CLI for the executable service proof.

All inputs and dependencies are owned, synthetic examples, not production instrumentation. Recovery covers process crashes with intact SQLite storage, not arbitrary storage loss. Its atomicity applies only to the product-owned SQLite lab and frame, not external instruments, HTTP services, or multiple databases. Capture begins now, not at an arbitrary historical date. The serialized development service does not prove distributed concurrency, historical capture, or real instrument behavior. Captures allow at most 1,000 external deliveries and 8 MiB; the complete live frame is bounded to 16 MiB, and the SDK's case, state, and artifact budgets can be tighter. Checksums are not signatures; exporters remain responsible for sanitization. Source identity hashes Python files, not native dependencies, runtime configuration, or hardware.

Apache-2.0.
