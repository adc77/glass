import { test } from "@e2e-dev/web";
import { expect } from "e2e";

const scenario = process.env.GLASS_SCENARIO;
if (!["healthy", "adverse"].includes(scenario ?? ""))
  throw new Error("Explicit proof settings required.");

test("scripted browser workflow produces real captured product outcomes", async ({
  app,
  agent,
  screen,
  browser,
}) => {
  await app.open();
  await expect(
    screen.getByRole("heading", "From sample to outcome."),
  ).toBeVisible();
  await expect(screen.getByRole("alert")).toBeHidden();
  await agent.act("Start capture");
  await expect(browser.locator("#capture-status")).toHaveText(
    "Capture: active",
  );
  const sample = scenario + "_sample";
  await agent.act("Receive sample", { params: { sample } });
  const status = browser.locator(`tr[data-sample="${sample}"] .sample-status`);
  if (scenario === "adverse") await expect(status).toHaveText("queued");
  await expect(status).toHaveText("running");
  const value = scenario === "healthy" ? 10 : 150;
  await agent.act("Record reading", { params: { value } });
  if (scenario === "adverse") {
    await expect(status).toHaveText("retest");
    await expect(status).toHaveText("running");
    await agent.act("Record reading", { params: { value } });
    await expect(status).toHaveText("report_pending");
  }
  await expect(status).toHaveText(
    scenario === "healthy" ? "released" : "scrapped",
  );
  await expect(browser.locator("#report-count")).toHaveText(
    "Stored reports: 1",
  );
  await expect(screen.getByRole("alert")).toBeHidden();
  await agent.act("Finish capture");
  await expect(browser.locator("#capture-status")).toHaveText("Capture: idle");
  await expect(screen.getByRole("link", "Download capture")).toBeVisible();
  const download = await browser.waitForDownload(() =>
    screen.getByRole("link", "Download capture").click(),
  );
  expect(download.suggestedFilename).toBe("glass-capture.json");
  expect(
    await browser.evaluate(() =>
      Array.from(
        document.querySelectorAll<HTMLInputElement>("input[pattern]"),
      ).every((input) => new RegExp(input.pattern, "v").test("valid-sample_1")),
    ),
  ).toBe(true);
  expect(
    await browser.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(
    await browser.evaluate(() => document.body.innerText.includes("smp_")),
  ).toBe(false);
  await browser.evaluate(() => {
    document.querySelector(".outcomes")?.scrollIntoView();
    return null;
  });
  await app.screenshot("final-outcome-390px");
  await browser.setViewport({ width: 1280, height: 1000 });
  await browser.evaluate(() => {
    window.scrollTo(0, 0);
    return null;
  });
  await app.screenshot("workbench-1280px");
});
