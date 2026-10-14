import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const project = path.dirname(fileURLToPath(import.meta.url));
const arguments_ = process.argv.slice(2);
assert(
  arguments_.length === 4 &&
    arguments_[0] === "--python" &&
    arguments_[2] === "--out",
  "Usage: node proof.mjs --python /absolute/venv/bin/python --out /new/output",
);
const python = path.resolve(arguments_[1]);
const root = path.resolve(arguments_[3]);
fs.mkdirSync(root, { mode: 0o700 });
const runId = "proof-" + crypto.randomUUID();

async function command(executable, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      cwd: project,
      detached: true,
      ...options,
    });
    let timedOut = false;
    let escalation;
    const timer = setTimeout(() => {
      timedOut = true;
      process.kill(-child.pid, "SIGINT");
      escalation = setTimeout(() => process.kill(-child.pid, "SIGKILL"), 5000);
    }, 180000);
    let stdout = "",
      stderr = "";
    child.stdout.on("data", (data) => {
      stdout += data;
    });
    child.stderr.on("data", (data) => {
      stderr += data;
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      clearTimeout(escalation);
      return code === 0 && !timedOut
        ? resolve({ stdout, stderr })
        : reject(
            Object.assign(
              new Error(
                `${executable} exited ${code}; timed out: ${timedOut}\n${stdout}\n${stderr}`,
              ),
              { stdout, stderr },
            ),
          );
    });
  });
}

async function launch(args, prefix, cwd) {
  const child = spawn(python, ["-I", ...args], { cwd });
  const lines = createInterface({ input: child.stdout });
  let errors = "";
  child.stderr.on("data", (data) => {
    errors += data;
  });
  async function stop() {
    if (child.exitCode === null && child.signalCode === null) {
      const closed = new Promise((resolve) => child.once("close", resolve));
      child.kill("SIGINT");
      const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
      await closed;
      clearTimeout(timer);
    }
    lines.close();
    assert(!errors, errors);
  }
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("Workbench startup timeout")),
        10000,
      );
      lines.once("line", (line) => {
        clearTimeout(timer);
        if (!line.startsWith(prefix))
          reject(new Error("Unexpected startup output: " + line));
        else resolve(line.slice(prefix.length));
      });
      child.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`Workbench exited before startup: ${code} ${errors}`));
      });
    });
    return { url, stop };
  } catch (error) {
    await stop();
    throw error;
  }
}

const results = [];
for (const scenario of ["healthy", "adverse"]) {
  const directory = path.join(root, scenario);
  fs.mkdirSync(directory, { mode: 0o700 });
  const db = path.join(directory, "live.sqlite");
  await command(
    python,
    ["-I", "-m", "glass.workbench", "init", "--db", db, "--scenario", scenario],
    { cwd: root },
  );
  const server = await launch(
    ["-m", "glass.workbench", "serve", "--db", db],
    "Glass workbench: ",
    root,
  );
  const output = `.e2e/${runId}-${scenario}`;
  fs.mkdirSync(path.join(project, output), { recursive: true, mode: 0o700 });
  const capture = path.join(directory, "capture.json");
  try {
    const browser = await command(
      process.execPath,
      [
        path.join(project, "node_modules/e2e/dist/cli/bin.js"),
        "run",
        "--config",
        "e2e.config.ts",
      ],
      {
        env: {
          ...process.env,
          E2E_TELEMETRY_DISABLED: "1",
          GLASS_WORKBENCH_URL: server.url,
          GLASS_BROWSER_OUTPUT: output,
          GLASS_SCENARIO: scenario,
        },
      },
    );
    fs.writeFileSync(
      path.join(directory, "browser.stdout.log"),
      browser.stdout,
      { flag: "wx" },
    );
    fs.writeFileSync(
      path.join(directory, "browser.stderr.log"),
      browser.stderr,
      { flag: "wx" },
    );
  } finally {
    await server.stop();
  }
  const report = JSON.parse(
    fs.readFileSync(path.join(project, output, "report.json"), "utf8"),
  );
  assert.equal(report.schemaVersion, "report-1");
  assert.equal(report.run.status, "passed");
  assert.equal(report.run.usage.maxModelCallsInStep, 0);
  const attempts = report.run.results.flatMap((result) => result.attempts);
  assert.equal(attempts.length, 1);
  if (attempts[0].appLog !== undefined)
    assert(
      !attempts[0].appLog.some((entry) => entry.level === "error"),
      "browser console/network errors",
    );
  const downloads = attempts[0].artifacts.filter(
    (artifact) => artifact.kind === "download",
  );
  assert.equal(downloads.length, 1);
  const resultRoot = path.join(project, output, "results");
  const download = path.resolve(resultRoot, downloads[0].path);
  assert(
    download.startsWith(resultRoot + path.sep),
    "download must stay inside retained browser evidence",
  );
  assert.equal(
    crypto.createHash("sha256").update(fs.readFileSync(download)).digest("hex"),
    downloads[0].sha256,
  );
  fs.copyFileSync(download, capture, fs.constants.COPYFILE_EXCL);
  const before = crypto
    .createHash("sha256")
    .update(fs.readFileSync(db))
    .digest("hex");
  const replay = await command(
    python,
    [
      "-I",
      "-m",
      "glass.workbench",
      "verify",
      "--bundle",
      capture,
      "--out",
      path.join(directory, "replay"),
    ],
    { cwd: root },
  );
  const result = JSON.parse(replay.stdout);
  assert.equal(result.status, "passed");
  assert.equal(result.byte_identical_replays, 2);
  assert.equal(
    result.view.samples[scenario + "_sample"].status,
    scenario === "healthy" ? "released" : "scrapped",
  );
  assert.equal(Object.keys(result.lab.filed).length, 1);
  assert.equal(result.arrivals.length, scenario === "healthy" ? 2 : 3);
  assert.equal(
    crypto.createHash("sha256").update(fs.readFileSync(db)).digest("hex"),
    before,
    "replay modified the live database",
  );
  fs.cpSync(
    path.join(project, output),
    path.join(directory, "browser-evidence"),
    { recursive: true, errorOnExist: true, force: false },
  );
  results.push({
    scenario,
    status: "passed",
    outcome: result.view.samples[scenario + "_sample"].status,
    preferred_release_achieved: scenario === "healthy",
    stored_reports: 1,
    product_replays: 2,
    live_db_unchanged: true,
  });
}
function hashTree(directory) {
  const entries = [];
  function visit(folder) {
    for (const name of fs.readdirSync(folder).sort()) {
      const file = path.join(folder, name);
      if (fs.statSync(file).isDirectory()) visit(file);
      else
        entries.push([
          path.relative(directory, file),
          crypto
            .createHash("sha256")
            .update(fs.readFileSync(file))
            .digest("hex"),
        ]);
    }
  }
  visit(directory);
  return JSON.stringify(entries);
}
const demoRoot = path.join(root, "viewer-demo");
const demo = await command(
  python,
  ["-I", "-m", "glass.demo", "--scripted", "--out", demoRoot],
  { cwd: root },
);
assert.equal(JSON.parse(demo.stdout).status, "passed");
fs.writeFileSync(path.join(root, "viewer-demo.stdout.log"), demo.stdout, {
  flag: "wx",
});
fs.writeFileSync(path.join(root, "viewer-demo.stderr.log"), demo.stderr, {
  flag: "wx",
});
const experiment = path.join(demoRoot, "experiment");
const originalEvidence = hashTree(experiment);
const incomplete = path.join(root, "viewer-incomplete");
fs.mkdirSync(incomplete, { mode: 0o700 });
const viewer = await launch(
  [
    "-m",
    "seam_viewer",
    "--experiment",
    "Glass=" + experiment,
    "--experiment",
    "Incomplete=" + incomplete,
  ],
  "Seam viewer: ",
  root,
);
const viewerOutput = `.e2e/${runId}-viewer`;
fs.mkdirSync(path.join(project, viewerOutput), {
  recursive: true,
  mode: 0o700,
});
try {
  const run = await command(
    process.execPath,
    [
      path.join(project, "node_modules/e2e/dist/cli/bin.js"),
      "run",
      "--config",
      "viewer.config.ts",
    ],
    {
      env: {
        ...process.env,
        E2E_TELEMETRY_DISABLED: "1",
        GLASS_VIEWER_URL: viewer.url,
        GLASS_BROWSER_OUTPUT: viewerOutput,
      },
    },
  );
  fs.writeFileSync(path.join(root, "viewer-browser.stdout.log"), run.stdout, {
    flag: "wx",
  });
  fs.writeFileSync(path.join(root, "viewer-browser.stderr.log"), run.stderr, {
    flag: "wx",
  });
} finally {
  await viewer.stop();
}
const viewerReport = JSON.parse(
  fs.readFileSync(path.join(project, viewerOutput, "report.json"), "utf8"),
);
assert.equal(viewerReport.schemaVersion, "report-1");
assert.equal(viewerReport.run.status, "passed");
assert.equal(viewerReport.run.usage.maxModelCallsInStep, 0);
const viewerAttempts = viewerReport.run.results.flatMap((row) => row.attempts);
assert.equal(viewerAttempts.length, 1);
assert(
  !viewerAttempts[0].appLog?.some((entry) => entry.level === "error"),
  "viewer console/network errors",
);
assert.equal(
  hashTree(experiment),
  originalEvidence,
  "viewer modified experiment evidence",
);
fs.cpSync(
  path.join(project, viewerOutput),
  path.join(root, "viewer-browser-evidence"),
  { recursive: true, errorOnExist: true, force: false },
);
const summary = {
  status: "passed",
  controller: "scripted-browser-not-an-LLM",
  model_calls: 0,
  scope:
    "real-time browser workflows plus deterministic product capture replay; not deterministic browser execution or human behavior",
  results,
  viewer: {
    status: "passed",
    recorded_runs: 4,
    evidence_unchanged: true,
    model_calls: 0,
  },
};
fs.writeFileSync(
  path.join(root, "verification.json"),
  JSON.stringify(summary, null, 2) + "\n",
  { flag: "wx" },
);
console.log(JSON.stringify(summary));
