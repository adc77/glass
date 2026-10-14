"use strict";
const byId = (name) => document.getElementById(name);
let busy = false;
let current = null;
let refreshing = false;
let generation = 0;

async function request(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? { cache: "no-store", credentials: "omit" }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          credentials: "omit",
        },
  );
  const value = await response.json();
  if (!response.ok) throw new Error(value.error);
  return value;
}

function error(message) {
  byId("error").textContent = message;
  byId("error").hidden = false;
}

function render(state) {
  current = state;
  byId("samples").replaceChildren();
  for (const sample of state.samples) {
    const row = document.createElement("tr");
    row.dataset.sample = sample.public;
    for (const [index, value] of [
      sample.public,
      sample.kind,
      sample.status,
      sample.retested ? "Yes" : "No",
      sample.report_attempts,
    ].entries()) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      if (index === 2) cell.className = "sample-status";
      row.append(cell);
    }
    byId("samples").append(row);
  }
  byId("empty").hidden = state.samples.length !== 0;
  const selected = byId("reading-sample").value;
  const running = state.samples.filter((sample) => sample.status === "running");
  const expected = running.length
    ? running.map((sample) => sample.public)
    : [""];
  const existing = Array.from(
    byId("reading-sample").options,
    (option) => option.value,
  );
  if (JSON.stringify(existing) !== JSON.stringify(expected)) {
    byId("reading-sample").replaceChildren();
    for (const name of expected) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name === "" ? "No running samples" : name;
      byId("reading-sample").append(option);
    }
    if (running.some((sample) => sample.public === selected))
      byId("reading-sample").value = selected;
  }
  byId("report-count").textContent = "Stored reports: " + state.stored_reports;
  byId("capture-status").textContent = "Capture: " + state.capture;
  byId("reading-button").disabled = busy || running.length === 0;
  byId("capture-start").disabled = busy || state.capture !== "idle";
  byId("capture-finish").disabled = busy || state.capture !== "active";
  byId("capture-abort").disabled = busy || state.capture === "idle";
  byId("download").hidden =
    !state.capture_available || state.capture !== "idle";
}

async function refresh() {
  if (busy || refreshing) return false;
  refreshing = true;
  const started = generation;
  try {
    const state = await request("api/state");
    if (started !== generation) return false;
    render(state);
    return true;
  } catch (failure) {
    error("Could not refresh state: " + failure.message);
  } finally {
    refreshing = false;
  }
}

async function mutate(path, body, message) {
  if (busy) return;
  busy = true;
  generation += 1;
  byId("error").hidden = true;
  for (const button of document.querySelectorAll("button"))
    button.disabled = true;
  try {
    const state = await request(path, body);
    byId("message").textContent = message;
    render(state);
  } catch (failure) {
    error(
      "Request failed: " +
        failure.message +
        ". Its outcome may be unknown. Refresh state before trying again; this page does not retry writes.",
    );
  } finally {
    busy = false;
    for (const button of document.querySelectorAll("button"))
      button.disabled = false;
    if (current) render(current);
    await refresh();
  }
}

byId("receive-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(
    "api/events",
    {
      handler: "receive",
      body: { sample: byId("sample").value, kind: byId("kind").value },
    },
    "Receipt accepted. Inspect the sample's actual status.",
  );
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
    "Reading accepted. Inspect quality and report outcomes.",
  );
});
for (const name of ["start", "finish", "abort"]) {
  byId("capture-" + name).addEventListener("click", () =>
    mutate(
      "api/capture/" + name,
      {},
      "Capture command accepted; inspect its recorded state.",
    ),
  );
}
byId("refresh").addEventListener("click", refresh);
const initialGeneration = generation;
refresh().then((connected) => {
  if (generation !== initialGeneration) return;
  byId("message").textContent = connected
    ? "Connected. All actions use the real local product handlers."
    : "Connection unavailable. Refresh state to reconnect.";
});
setInterval(refresh, 500);
