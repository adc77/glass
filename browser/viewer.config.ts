import { web } from "@e2e-dev/web";
import type { E2EConfig } from "e2e";
import { scripted } from "./scripted-executor.ts";

const url = process.env.GLASS_VIEWER_URL;
const output = process.env.GLASS_BROWSER_OUTPUT;
if (!url || !output)
  throw new Error("Explicit viewer proof settings required.");

export default {
  projectId: "glass-readonly-evidence-console",
  tests: "viewer-tests/viewer.e2e.ts",
  agents: { default: { executor: scripted, maxSteps: 1 } },
  targets: [
    {
      name: "local-evidence",
      engine: web({
        browser: "chromium",
        viewport: { width: 1440, height: 1000 },
      }),
      app: { url },
    },
  ],
  output,
  cache: "off",
  retries: 0,
  workers: 1,
  timeout: 60000,
  assertionTimeout: 10000,
  trace: "on",
  video: "on",
} satisfies E2EConfig;
