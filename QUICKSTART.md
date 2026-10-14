# One end-to-end demo

Python 3.11 or newer and Git are required on macOS or Linux. Installation needs internet; the demo does not.
Use a fresh virtual environment, not an environment holding old simulation journals.

```sh
mkdir glass-work
cd glass-work
git clone https://github.com/adc77/glass.git
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./glass
mkdir runs
glass-demo --scripted --out runs/demo
```

No API key, GPU, model download, Modal account, or sibling Seam checkout is needed.
Glass installs its exact pinned Seam dependency. The output directory must not exist;
for another run, choose `--out runs/demo-2`. Keep running these commands from `glass-work`,
not from inside `.venv` or the cloned `glass` folder. Results live separately in `runs`.
The parent directory must exist; path errors explain what to fix without overwriting evidence.
Failed runs retain their evidence, not a clean reset.

The controller deliberately picks the first product-authorized action. **It is not an LLM.**
It uses the same durable external-decision handoff as model runs, with zero-cost synthetic
usage counters. It does not measure tokens, model reasoning, or real-user behavior.

## What happens

Two synthetic submitters with different reading delays run against the real Glass sample
handlers, twice each with paired seeds 7 and 19:

| Scenario | Expected samples | Expected stored reports |
| --- | --- | --- |
| Healthy lab | 2 released | 2 |
| Busy bench, failed quality, lost report acknowledgement | 2 scrapped after retest | 2 |

The command submits one decision, closes the journal, and continues in a fresh Python
process. It checks outcomes, verifies viewer evidence, and replays all four runs twice
without calling the controller. Each replay pair must be byte-identical. Progress and a
readable outcome summary appear on stderr; stdout remains JSON with `status: passed`.
Failure exits nonzero. Both scenarios complete two final dispositions per run
(`disposition_completed`); only the healthy scenario achieves two preferred releases
(`preferred_outcome_achieved`). Scrapping completes a disposition, not the preferred release.
Engine checks passing and actor decisions finishing are separate from these business outcomes.
Fresh-process continuation is not a forced-crash proof; the durable-service proof below is.

## Inspect it

```sh
python -m seam_viewer --experiment "Glass=runs/demo/experiment" --check
python -m seam_viewer --experiment "Glass=runs/demo/experiment" --port 0
```

These commands assume the same `glass-work` working directory and activated environment.
The demo also prints a fully quoted viewer command with the absolute interpreter and
experiment paths: copy that command to run the viewer from any folder, without activation.
Open the printed loopback URL. Compare scenarios and inspect observations, authorized
choices, selected decisions, resolved actions, timing, and outcomes. The viewer is read-only
and makes no model calls. Seconds and averages are readable; exact original values remain
available for inspection. Stop it with Ctrl-C. Keep the private `runs/demo` directory intact:
it contains the verified summary, inputs, journals, artifacts, and replay evidence.

## Real models and the v1 boundary

For an actual operator UI over the durable product, use the separate
[workbench/browser bridge](WORKBENCH.md). It runs real-time browser workflows and
imports their captures for strict product replay; it is not the evidence viewer or
an autonomous real-model persona certificate.

The free demo does not silently upgrade to a paid endpoint. For explicit model runs,
use `glass.goal_experiments`, `glass.personas.PROMPT`, and Seam's
[provider-neutral experiment controller](https://github.com/adc77/seam/blob/main/INFERENCE_EXPERIMENTS.md).
Supply your provider callable, model, limits, and credentials explicitly. Product code
owns legal action choices and timing; the model only selects an offered choice.

[Existing real-model results](PERSONA_VALIDATION.md) include a genuine persona goal failure.
Passing replay and runtime checks does not certify realistic or successful agents.
Seam 0.10.0 is the frozen developer-v1 core, not a promise of calibrated human forecasts
or source-compatible replay across upgrades. Preserve old environments and journals.

For the separate actual HTTP/SQLite forced-crash capture proof, see
[live capture and replay](README.md#live-capture-and-replay). The packaged persona demo
uses isolated simulation worlds; it does not contact instruments or production services.

## Usability-pass verification

Seam's 261 tests and Glass's 94 tests pass on Python 3.11, 3.13, and 3.14. Normal
installed packages also pass the four-run demo, eight offline replays, viewer asset/HTTP
proof, and actual Glass HTTP/SQLite proof with three forced restarts on each interpreter.
Chrome checks cover scenario selection, actor/decision inspection, tabs, timeline search,
390-pixel layout, another product's metrics, missing paths, and failed cells without
invented zero outcomes. Exact-value formatting has separate BigInt regression tests.
Seam's SDK core and Glass's normal handlers/durable service are unchanged.
