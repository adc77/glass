# One end-to-end demo

Python 3.11 or newer and Git are required. Installation needs internet; the demo does not.
Use a fresh virtual environment, not an environment holding old simulation journals.

```sh
git clone https://github.com/adc77/glass.git
python3 -m venv glass-demo-env
. glass-demo-env/bin/activate
python -m pip install ./glass
cd glass-demo-env
glass-demo --scripted --out demo
```

No API key, GPU, model download, Modal account, or sibling Seam checkout is needed.
Glass installs its exact pinned Seam dependency. The output directory must not exist;
for another run, choose `--out demo-2`. Failed runs retain their evidence, not a clean reset.

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
without calling the controller. Each replay pair must be byte-identical. Failure exits
nonzero; successful execution prints JSON with `status: passed`. The faulted scenario
passes the software checks while **zero personas achieve their release goal**.
Fresh-process continuation is not a forced-crash proof; the durable-service proof below is.

## Inspect it

```sh
python -m seam_viewer --experiment "Glass=demo/experiment" --check
python -m seam_viewer --experiment "Glass=demo/experiment" --port 0
```

Open the printed loopback URL. Compare scenarios and inspect observations, authorized
choices, selected decisions, resolved actions, timing, and outcomes. The viewer is read-only
and makes no model calls. Stop it with Ctrl-C. Keep the private `demo` directory intact:
it contains the verified summary, inputs, journals, artifacts, and replay evidence.

## Real models and the v1 boundary

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
