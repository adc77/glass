"use strict";
const byId = (id) => document.getElementById(id);
const activeStatuses = new Set([
  "received",
  "queued",
  "running",
  "retest",
  "report_pending",
]);
const exceptionStatuses = new Set([
  "scrapped",
  "overdue",
  "dropped",
  "report_failed",
]);
const statusLabels = {
  received: "Received",
  queued: "Waiting for bench",
  running: "Ready for reading",
  retest: "Retest scheduled",
  report_pending: "Report retry",
  released: "Released",
  scrapped: "Rejected",
  overdue: "Reading overdue",
  dropped: "Not accepted",
  report_failed: "Report failed",
};
const explanations = {
  received:
    "Receipt recorded. Inspect subsequent product state before submitting a reading.",
  queued:
    "Bench is busy. The product has scheduled its retry; a reading is not yet permitted.",
  running:
    "Ready for a measurement. The product enforces the sample's reading deadline.",
  retest:
    "Quality check failed. One product-owned retest is scheduled; wait until running.",
  report_pending:
    "Report acknowledgement is pending. A write may already exist; retries reuse the same key.",
  released:
    "The report is acknowledged and the preferred release outcome is achieved.",
  scrapped:
    "Final disposition after failed quality checks. Completed, but not the preferred release outcome.",
  overdue:
    "The reading deadline expired. This is an exception, not a successful release.",
  dropped:
    "The bench could not accept the sample after its retry. No release was achieved.",
  report_failed:
    "Report attempts are exhausted. Do not treat the intended disposition as acknowledged.",
};
let current = null,
  busy = false,
  connected = false,
  refreshing = false;
let generation = 0,
  captureGeneration = 0,
  selected = null,
  queueSignature = "";
let guideIntent = "none";
let view = "queue",
  operationError = null,
  connectionError = null;

function element(tag, text, className) {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = String(text);
  if (className) value.className = className;
  return value;
}
function tone(status) {
  if (status === "released") return "success";
  if (exceptionStatuses.has(status)) return "failure";
  if (["queued", "retest", "report_pending"].includes(status)) return "warning";
  return "active";
}
function facts(id, rows) {
  byId(id).replaceChildren(
    ...rows.flatMap(([key, value]) => [
      element("dt", key),
      element("dd", value),
    ]),
  );
}
function error(message, kind = "operation") {
  if (kind === "connection") connectionError = message;
  else operationError = message;
  showErrors();
  if (byId("receive-dialog").open) {
    byId("intake-error").textContent = message;
    byId("intake-error").hidden = false;
  }
}
function showErrors() {
  const messages = [operationError, connectionError].filter(
    (value) => value !== null,
  );
  byId("error").textContent = messages.join(" ");
  byId("error").hidden = messages.length === 0;
}
function connection(value) {
  connected = value;
  byId("connection").dataset.state = value ? "connected" : "disconnected";
  byId("connection-label").textContent = value
    ? "Connected"
    : "Disconnected · stale state";
  syncControls();
}
async function request(path, body) {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
      ...(body === undefined
        ? {}
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error);
    return value;
  } finally {
    clearTimeout(deadline);
  }
}
function validateState(state) {
  if (
    !state ||
    !Array.isArray(state.samples) ||
    !Number.isSafeInteger(state.stored_reports) ||
    state.stored_reports < 0 ||
    !["idle", "active", "invalidated"].includes(state.capture) ||
    typeof state.capture_available !== "boolean" ||
    state.mode !== "real-time-local-synthetic-product"
  )
    throw new Error("Unexpected product state schema");
  const names = new Set();
  for (const row of state.samples) {
    if (
      typeof row.public !== "string" ||
      typeof row.kind !== "string" ||
      !Object.hasOwn(explanations, row.status) ||
      typeof row.retested !== "boolean" ||
      !Number.isSafeInteger(row.report_attempts) ||
      row.report_attempts < 0 ||
      row.report_attempts > 2 ||
      names.has(row.public)
    )
      throw new Error("Unexpected sample state schema");
    names.add(row.public);
  }
}
function syncControls() {
  const blocked = busy || !connected || current === null;
  byId("new-sample").disabled = blocked;
  byId("receive-button").disabled = blocked;
  byId("close-receive").disabled = busy;
  byId("cancel-receive").disabled = busy;
  byId("reading-button").disabled =
    blocked || !current.samples.some((row) => row.status === "running");
  byId("capture-start").disabled = blocked || current.capture !== "idle";
  byId("capture-finish").disabled = blocked || current.capture !== "active";
  byId("capture-abort").disabled = blocked || current.capture === "idle";
  const available =
    !blocked && current.capture_available && current.capture === "idle";
  byId("download").hidden = !available;
  byId("inspect-capture").disabled = !available;
  byId("refresh-capture").disabled = !available;
  byId("guide-action").disabled = blocked;
  renderGuide();
}
function renderGuide() {
  const ready = current?.samples.find((row) => row.status === "running");
  const waiting = current?.samples.find((row) =>
    activeStatuses.has(row.status),
  );
  let title, description, action;
  guideIntent = "none";
  if (!connected || !current) {
    title = "The lab is disconnected";
    description =
      "The displayed results may be old. Actions remain disabled until a fresh observation succeeds.";
    action = "Waiting for connection";
  } else if (operationError !== null) {
    title = "Review the state before trying again";
    description =
      "A request returned an uncertain result. Check the sample queue before repeating an action; the page will not retry writes for you.";
    action = "Review samples";
    guideIntent = "review";
  } else if (current.capture === "invalidated") {
    title = "This recording cannot be saved";
    description =
      "An input failed. Cancel the recording explicitly before starting another; product state and the previous saved recording are preserved.";
    action = "Cancel failed recording";
    guideIntent = "abort";
  } else if (ready) {
    title = `${ready.public} is ready for a reading`;
    description =
      "In the standard demo, 10 passes and 150 exercises quality failure and retesting. The configured lab—not this screen—decides the outcome.";
    action = "Enter a reading";
    guideIntent = "reading";
  } else if (waiting) {
    title = "Glass is waiting or retrying";
    description = explanations[waiting.status];
    action = "Inspect waiting sample";
    guideIntent = "inspect";
  } else if (current.capture === "active" && current.samples.length) {
    title = "The workflow has reached its final results";
    description =
      "Check which samples released and which failed, then stop recording to save these inputs and results.";
    action = "Save recording";
    guideIntent = "finish";
  } else if (current.capture_available && current.capture === "idle") {
    title = "Your workflow recording is saved";
    description =
      "Inspect its actual results or start a new sample. A saved recording is not the same as a checked replay.";
    action = "View recording";
    guideIntent = "evidence";
  } else {
    title =
      current.capture === "active"
        ? "Recording is on. Now add a sample"
        : "Add a sample to see the pipeline work";
    description =
      current.capture === "active"
        ? "Use New sample, keep kind blood, and submit a test receipt. Enter a reading when Glass says it is ready."
        : "Start a recording first if you want replay evidence. Then use New sample, keep kind blood, and enter a reading when it is ready.";
    action = "Add a sample";
    guideIntent = "new";
  }
  byId("guide-title").textContent = title;
  byId("guide-description").textContent = description;
  byId("guide-action").textContent = action;
}
function renderQueue() {
  if (!current) return;
  const query = byId("sample-search").value.trim().toLowerCase();
  const filter = byId("status-filter").value;
  const signature = JSON.stringify([current.samples, query, filter, selected]);
  if (signature === queueSignature) return;
  queueSignature = signature;
  const filtered = current.samples.filter(
    (row) =>
      `${row.public} ${row.kind}`.toLowerCase().includes(query) &&
      (filter === "all" ||
        (filter === "active"
          ? activeStatuses.has(row.status)
          : filter === "exceptions"
            ? exceptionStatuses.has(row.status)
            : row.status === filter)),
  );
  const focusedSample = document.activeElement?.dataset.sampleLink;
  const rows = filtered.map((sample) => {
    const row = element("tr");
    row.dataset.sample = sample.public;
    row.dataset.status = sample.status;
    row.dataset.selected = String(selected === sample.public);
    const nameCell = element("td");
    const control = element("button", sample.public, "sample-link");
    control.type = "button";
    control.dataset.sampleLink = sample.public;
    control.setAttribute("aria-pressed", String(selected === sample.public));
    control.addEventListener("click", () => selectSample(sample.public));
    nameCell.append(control);
    const statusCell = element("td");
    statusCell.append(
      element(
        "span",
        statusLabels[sample.status],
        `badge sample-status ${tone(sample.status)}`,
      ),
    );
    row.append(
      nameCell,
      element("td", sample.kind),
      statusCell,
      element("td", sample.retested ? "Yes" : "No"),
      element("td", sample.report_attempts),
    );
    return row;
  });
  byId("samples").replaceChildren(...rows);
  if (focusedSample)
    Array.from(byId("samples").querySelectorAll("button"))
      .find((control) => control.dataset.sampleLink === focusedSample)
      ?.focus({ preventScroll: true });
  byId("empty").hidden = filtered.length !== 0;
  byId("empty-title").textContent = current.samples.length
    ? "No matching samples"
    : "No samples received";
  byId("empty-description").textContent = current.samples.length
    ? "Change the search or status filter to see the queue."
    : "Use New sample to submit a synthetic receipt to the product.";
  byId("visible-count").textContent =
    `${filtered.length} of ${current.samples.length} samples`;
}
function selectSample(name) {
  selected = name;
  const row = current.samples.find((item) => item.public === name);
  if (row?.status === "running") byId("reading-sample").value = name;
  renderInspector();
  renderQueue();
}
function renderInspector() {
  const row = current.samples.find((item) => item.public === selected);
  byId("selected-name").textContent = row ? row.public : "No sample selected";
  byId("selected-explanation").textContent = row
    ? explanations[row.status]
    : "Select a sample from the queue to inspect its actual state.";
  facts(
    "selected-facts",
    row
      ? [
          ["Status", statusLabels[row.status]],
          ["Kind", row.kind],
          ["Retested", row.retested ? "Yes" : "No"],
          ["Report attempts", `${row.report_attempts} / 2`],
          [
            "Preferred release",
            row.status === "released" ? "Achieved" : "Not achieved",
          ],
        ]
      : [],
  );
}
function render(state) {
  current = state;
  byId("metric-total").textContent = state.samples.length;
  byId("metric-active").textContent = state.samples.filter((row) =>
    activeStatuses.has(row.status),
  ).length;
  byId("metric-released").textContent = state.samples.filter(
    (row) => row.status === "released",
  ).length;
  byId("metric-exceptions").textContent = state.samples.filter((row) =>
    exceptionStatuses.has(row.status),
  ).length;
  byId("queue-badge").textContent = state.samples.length;
  byId("evidence-badge").textContent = state.capture_available ? "1" : "0";
  byId("queue-summary").textContent =
    `${state.samples.length} samples · actual product results, not mock data`;
  byId("report-count").textContent = "Stored reports: " + state.stored_reports;
  const previous = byId("reading-sample").value;
  const running = state.samples
    .filter((row) => row.status === "running")
    .map((row) => row.public);
  const expected = running.length ? running : [""];
  if (
    JSON.stringify(
      Array.from(byId("reading-sample").options, (option) => option.value),
    ) !== JSON.stringify(expected)
  ) {
    byId("reading-sample").replaceChildren(
      ...expected.map((name) => {
        const option = element("option", name || "No running samples");
        option.value = name;
        return option;
      }),
    );
    if (running.includes(previous)) byId("reading-sample").value = previous;
  }
  byId("capture-status").textContent = "Capture: " + state.capture;
  byId("capture-indicator").textContent = state.capture.toUpperCase();
  byId("capture-indicator").className =
    `badge ${state.capture === "invalidated" ? "failure" : state.capture === "active" ? "active" : "neutral"}`;
  byId("capture-description").textContent =
    state.capture === "active"
      ? "Inputs are being recorded. Finish to freeze an export."
      : state.capture === "invalidated"
        ? "A delivery failed. Explicitly abort before starting a new capture."
        : state.capture_available
          ? "A completed capture is retained. Replay must be verified separately."
          : "Start before the workflow to record its inputs and outcomes.";
  byId("observed-at").textContent =
    "Last observed " + new Date().toLocaleTimeString();
  renderQueue();
  renderInspector();
  syncControls();
  if (state.capture !== "idle" || !state.capture_available) clearCapture();
}
async function refresh() {
  if (busy || refreshing) return false;
  refreshing = true;
  const started = generation;
  try {
    const state = await request("api/state");
    if (started !== generation) return false;
    validateState(state);
    connection(true);
    render(state);
    connectionError = null;
    showErrors();
    return true;
  } catch (failure) {
    if (started !== generation) return false;
    connection(false);
    error(
      "Could not observe current state: " +
        failure.message +
        ". Displayed values are stale; writes are disabled.",
      "connection",
    );
  } finally {
    refreshing = false;
  }
}
async function mutate(path, body, message) {
  if (busy || !connected) return false;
  busy = true;
  generation += 1;
  captureGeneration += 1;
  byId("error").hidden = true;
  byId("intake-error").hidden = true;
  operationError = null;
  showErrors();
  syncControls();
  let accepted = false;
  try {
    const state = await request(path, body);
    validateState(state);
    byId("message").textContent = message;
    clearCapture();
    render(state);
    accepted = true;
  } catch (failure) {
    connection(false);
    error(
      "Request failed: " +
        failure.message +
        ". The outcome may be unknown. Inspect refreshed state before trying again; writes are never automatically retried.",
    );
  } finally {
    busy = false;
    syncControls();
    await refresh();
  }
  return accepted;
}
function clearCapture() {
  byId("capture-metadata").hidden = true;
  byId("capture-empty").hidden = false;
  byId("capture-empty").querySelector("h3").textContent =
    current?.capture === "active"
      ? "Capture in progress"
      : current?.capture === "invalidated"
        ? "Capture invalidated"
        : "No completed capture selected";
}
async function inspectCapture() {
  const started = ++captureGeneration;
  clearCapture();
  if (!connected || !current?.capture_available || current.capture !== "idle")
    return;
  byId("capture-empty").querySelector("h3").textContent =
    "Loading capture metadata";
  try {
    const value = await request("api/capture/summary");
    if (started !== captureGeneration || current.capture !== "idle") return;
    facts("capture-facts", [
      ["Payload SHA-256", value.sha256],
      ["Product module", value.source.module],
      ["Product SHA-256", value.source.product_sha256],
      ["SDK version", value.source.sdk_version],
      ["SDK SHA-256", value.source.sdk_sha256],
      ["Recorded inputs", value.arrival_count],
      ["Window (nanoseconds)", value.duration_ns],
      ["Stored reports at cutoff", value.stored_reports],
      ["Replay verification", "Not performed by this screen"],
    ]);
    byId("recorded-inputs").textContent = value.arrival_count;
    byId("recorded-reports").textContent = value.stored_reports;
    byId("captured-outcomes").replaceChildren(
      ...Object.entries(value.outcomes).map(([status, count]) =>
        element(
          "span",
          `${statusLabels[status]}: ${count}`,
          `badge ${tone(status)}`,
        ),
      ),
    );
    byId("capture-empty").hidden = true;
    byId("capture-metadata").hidden = false;
  } catch (failure) {
    if (started !== captureGeneration) return;
    byId("capture-empty").querySelector("h3").textContent =
      "Capture metadata unavailable";
    error("Could not inspect the completed capture: " + failure.message);
  }
}
function showView(name) {
  const changed = view !== name;
  view = name;
  for (const item of ["queue", "evidence"]) {
    byId(item + "-view").hidden = item !== name;
    if (item === name) byId("nav-" + item).setAttribute("aria-current", "page");
    else byId("nav-" + item).removeAttribute("aria-current");
  }
  byId("breadcrumb-view").textContent =
    name === "queue" ? "Sample queue" : "Capture evidence";
  byId("page-title").textContent =
    name === "queue" ? "Live lab workspace" : "Your workflow recording";
  byId("page-description").textContent =
    name === "queue"
      ? "Glass is the example product. Add a sample, enter a reading, and see what the real pipeline does."
      : "The inputs and results saved from the product, ready for isolated replay.";
  if (name === "evidence") inspectCapture();
  if (changed) window.scrollTo(0, 0);
}
byId("sample-search").addEventListener("input", renderQueue);
byId("status-filter").addEventListener("change", renderQueue);
byId("reading-sample").addEventListener("change", (event) =>
  selectSample(event.target.value),
);
byId("new-sample").addEventListener("click", () => {
  showView("queue");
  byId("intake-error").hidden = true;
  byId("receive-dialog").showModal();
  byId("sample").focus();
});
for (const id of ["close-receive", "cancel-receive"])
  byId(id).addEventListener("click", () => byId("receive-dialog").close());
byId("receive-dialog").addEventListener("cancel", (event) => {
  if (busy) event.preventDefault();
});
byId("receive-dialog").addEventListener("close", () =>
  byId("new-sample").focus(),
);
byId("receive-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = byId("sample").value;
  if (
    await mutate(
      "api/events",
      { handler: "receive", body: { sample: name, kind: byId("kind").value } },
      "Receipt accepted. Actual product state is shown below.",
    )
  ) {
    selectSample(name);
    byId("receive-dialog").close();
    byId("sample").value = "";
  }
});
byId("reading-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = Number(byId("reading").value);
  if (!Number.isSafeInteger(value) || value < -1000000 || value > 1000000) {
    error("Enter an integer reading between -1000000 and 1000000.");
    return;
  }
  mutate(
    "api/events",
    {
      handler: "reading",
      body: { sample: byId("reading-sample").value, value },
    },
    "Reading accepted. Inspect the quality and report outcome.",
  );
});
for (const name of ["start", "finish", "abort"])
  byId("capture-" + name).addEventListener("click", async () => {
    if (
      name === "abort" &&
      !window.confirm(
        "Abort this capture? Its recorded inputs will be discarded. Product state and the last completed capture are preserved.",
      )
    )
      return;
    await mutate(
      "api/capture/" + name,
      {},
      "Capture command accepted. Inspect its recorded state.",
    );
    if (view === "evidence") inspectCapture();
  });
for (const name of ["queue", "evidence"])
  byId("nav-" + name).addEventListener("click", () => showView(name));
byId("inspect-capture").addEventListener("click", () => showView("evidence"));
byId("refresh-capture").addEventListener("click", inspectCapture);
byId("refresh").addEventListener("click", refresh);
byId("guide-action").addEventListener("click", () => {
  if (busy || !connected || !current) return;
  const ready = current.samples.find((row) => row.status === "running");
  const waiting = current.samples.find((row) => activeStatuses.has(row.status));
  if (guideIntent === "review") {
    showView("queue");
    byId("sample-search").focus();
  } else if (guideIntent === "abort") byId("capture-abort").click();
  else if (guideIntent === "reading" && ready) {
    selectSample(ready.public);
    byId("reading").scrollIntoView({ block: "center" });
    byId("reading").focus();
  } else if (guideIntent === "inspect" && waiting) {
    selectSample(waiting.public);
    byId("selected-name").scrollIntoView({ block: "center" });
  } else if (guideIntent === "finish") byId("capture-finish").click();
  else if (guideIntent === "evidence") showView("evidence");
  else if (guideIntent === "new") byId("new-sample").click();
});
const initialGeneration = generation;
refresh().then((value) => {
  if (generation === initialGeneration)
    byId("message").textContent = value
      ? "Connected to the local product. Outcomes reflect actual handlers."
      : "Service unavailable. Displayed state must not be treated as current.";
});
setInterval(refresh, 1000);
