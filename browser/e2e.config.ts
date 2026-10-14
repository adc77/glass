import { web } from "@e2e-dev/web";
import type { E2EConfig } from "e2e";
import { scripted } from "./scripted-executor.ts";

const url = process.env.GLASS_WORKBENCH_URL;
const output = process.env.GLASS_BROWSER_OUTPUT;
if (!url || !output)
  throw new Error(
    "Use node proof.mjs with explicit Python and output settings.",
  );

export default {
  projectId: "glass-local-workbench",
  agents: { default: { executor: scripted, maxSteps: 8 } },
  targets: [
    {
      name: "local-lab",
      engine: web({
        browser: "chromium",
        viewport: { width: 390, height: 844 },
      }),
      app: { url },
    },
  ],
  output,
  cache: "off",
  retries: 0,
  workers: 1,
  timeout: 60000,
  assertionTimeout: 20000,
  trace: "on",
  video: "on",
} satisfies E2EConfig;
