# Glass workbench and browser-to-capture proof

Glass 0.8.0 provides a local operations console over the existing durable service. Receiving samples,
recording readings, quality checks, retries, and report writes use the ordinary product
handlers and SQLite lab adapter. The workbench is not Seam's read-only evidence viewer,
and the browser does not execute inside Seam's guarded simulation process.

## Open the local product

Use Python 3.11+, Git, and a fresh working folder/environment outside system temporary
storage. Installation needs internet. Use new database/output names; preserve old
environments and databases for old captures. Source identities are enforced, not migrated.

```sh
git clone https://github.com/adc77/glass.git glass-source
python3 -m venv .venv
. .venv/bin/activate
python -m pip install ./glass-source
mkdir runs
glass-workbench init --db runs/healthy.sqlite --scenario healthy
glass-workbench serve --db runs/healthy.sqlite --port 0
```

Open the exact private localhost URL printed by the server, including its random path.
Stop with Ctrl-C. A restart prints a new URL; use that URL, not the old one.
`init` never replaces an existing database. Only the local operator is modeled; there
are no production accounts, customer authorization, or calibrated user personas here.

Start a capture, use **New sample** to receive a named synthetic sample of kind `blood`, record an integer
reading of `10` while it is running, then finish the capture and download it. The healthy
sample is released with one report. The operator screen omits private IDs and timer tokens;
the downloaded capture includes them for reproducibility and must be kept private.

For adverse behavior, initialize a different database with `--scenario adverse`. Its
first bench request is busy, quality readings over `100` fail, and the first report
acknowledgement is lost after the write commits. Use `150` for both readings: wait for
the sample to become running before each reading, including after the first failed check.
The sample is scrapped after the retest, with one stored report and two report attempts.
Each product retry takes five real seconds. The UI does not fast-forward time or expose
internal retry/timer handlers. Scrapping completes a disposition, not the preferred release.

The screen refreshes state without retrying writes. A failed request may have committed;
inspect current state before issuing another action. Failed domain delivery invalidates
an active capture; explicitly abort it before starting another. Old completed captures
are not shown as the new active capture's download. A capture can end with pending work;
successful replay then proves that pending outcome, not a completed goal.

The sample queue supports name/kind search and status filters. Counts come from the
observed product state, not fabricated activity. Select a sample for its status,
retest/report attempts, and preferred-outcome verdict. The measurement selector retains
your choice across observations. Intake uses a keyboard-accessible dialog with explicit
cancel and focus restoration. Network/schema failures label state stale and disable
writes until a successful observation; recovering the connection does not clear an
uncertain-write warning. Requests are bounded to ten seconds without automatic retries.

**Capture evidence** displays the retained payload/source hashes, input count, exact
capture-window nanoseconds, and actual outcomes/reports. Its metadata projection omits
private sample IDs and does not claim that a replay has run. Active/invalidated captures
do not appear as the latest completed capture. Use the verifier below for that verdict.

## Replay the downloaded product capture

From the same working folder/environment, place the downloaded file at `runs/capture.json`:

```sh
glass-workbench verify --bundle runs/capture.json --out runs/verified
```

This validates the capture, reconstructs captured state/dependency data/IDs/timers, and
runs the actual handlers twice in fresh supervised simulations with isolated lab worlds.
Both results must pass the capture's product/lab assertions and be byte-identical. The
verifier reads no live database and makes no provider calls. Its status is an engine/replay
verdict, not a claim about the sample's preferred outcome. Evidence stays in the new output
directory; existing output is never overwritten. Interrupted/failed runs keep their files.

Browser execution is real-time and nondeterministic. Product replay is virtual-time and
strict within its pinned source/environment. Browser action recordings and product capture
replays are different evidence types; this proof does not claim to replay browser pixels.

## Run the installed browser bridge proof

The optional `browser/` project uses published, locked `e2e` and `@e2e-dev/web` packages.
Python installation needs no Node dependency; only this proof needs Node 24.8+ (or Node
22.22.3+) and npm. Chromium is installed with the version the web engine pins.
On Linux, install it with `--with-deps`; macOS does not need that flag.

With `.venv` still active, from your working folder:

```sh
cd glass-source/browser
npm ci
npx e2e-web install chromium
npx tsc --noEmit
node proof.mjs --python "$(python -c 'import sys; print(sys.executable)')" --out ../../runs/browser-proof
```

The proof initializes separate fresh healthy/adverse databases, starts actual private
workbench processes, and drives forms through e2e's public observed-node action interface.
Its custom controller is explicitly **scripted, not an LLM**. It has no model, no adaptive
cache, no retries, and no provider fallback. Independent locator checks verify outcomes.
Unknown scripted operations block; production handlers are not invoked directly by tests.

For each scenario, it checks the 390px layout, HTML validation patterns, hidden internal
IDs, absence of console/network errors, and real capture download. It retains screenshots,
video, per-step traces, and the e2e report. The downloaded artifact's hash is verified against
that report before importing it. Two product replays must match the captured actual state,
and the stopped live database's bytes must remain unchanged during replay.

The proof also creates a fresh four-run scripted experiment and drives the installed
Seam evidence console: run search/filtering, persona decisions, timeline search, final
state, keyboard tabs, and desktop/narrow-screen layouts. It requires all experiment file
hashes to remain unchanged. Browser evidence is retained separately from the workbench
captures. The browser tests additionally cover queue filters, intake cancellation/focus,
capture metadata, stale-state write blocking, and successful reconnection.

The output has `healthy/`, `adverse/`, and `verification.json`; each scenario retains its
live database, downloaded capture, browser evidence, and two replay artifacts. Unique
original browser records also remain under `browser/.e2e/`. Keep these private; downloads
contain internal IDs and video is not a generic secret-redaction guarantee. No raw evidence
is committed. Reruns require new outputs and never clear old browser evidence.

The compact console is an interface-quality upgrade, not public-production certification.
The backend remains a serialized, local synthetic example. Multi-user authentication,
authorization, TLS, operational monitoring, deployment controls, and an explicit data
retention policy must be designed before public exposure. No hosting is enabled here.

The proof refuses missing dependencies/settings rather than downloading a model or
substituting another controller. Browser reports show zero model calls. This proves
the UI-to-handler-to-capture bridge, not goal reasoning, autonomous exploration, arbitrary
product compatibility, or realistic humans. Real-LLM integration is a later explicit,
budgeted validation step; no API keys, GPU, or paid inference are required here.

## Safety boundary

The server binds only loopback, uses an unpredictable path, and checks Host, mutation
Origin, and cross-site fetch metadata. Routes and JSON bodies are closed, request sizes
bounded, internal handlers inaccessible, and observations inserted as text rather than
HTML. Pages set no-store, restrictive CSP, and no-referrer headers. These prevent common
browser-origin attacks; they are not account authentication, an OS sandbox, or a production
deployment guarantee. A local process with your OS authority can still access the service.

Use synthetic owned data only. Do not put organization code, credentials, production
exports, or real lab/customer data in this example. Seam's runtime, original Glass
handlers, capture semantics, and durable service are unchanged by this milestone.
