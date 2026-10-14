import { test } from "@e2e-dev/web";
import { expect } from "e2e";
import { inspectDarkContrast } from "../contrast.ts";

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
  await expect(screen.getByRole("heading", "Live lab workspace")).toBeVisible();
  await expect(browser.locator("#guide-title")).toHaveText(
    "Add a sample to see the pipeline work",
  );
  expect(
    await browser.evaluate(
      () => getComputedStyle(document.documentElement).colorScheme,
    ),
  ).toBe("dark");
  await expect(screen.getByRole("alert")).toBeHidden();
  await agent.act("Start capture");
  await expect(browser.locator("#capture-status")).toHaveText(
    "Capture: active",
  );
  const sample = scenario + "_sample";
  await agent.act("Receive sample", { params: { sample } });
  const status = browser.locator(`tr[data-sample="${sample}"] .sample-status`);
  if (scenario === "adverse")
    await expect(status).toHaveText("Waiting for bench");
  await expect(status).toHaveText("Ready for reading");
  await expect(browser.locator("#guide-title")).toHaveText(
    `${sample} is ready for a reading`,
  );
  await browser.locator("#guide-action").click();
  expect(await browser.evaluate(() => document.activeElement?.id ?? null)).toBe(
    "reading",
  );
  const value = scenario === "healthy" ? 10 : 150;
  await agent.act("Record reading", { params: { value } });
  if (scenario === "adverse") {
    await expect(status).toHaveText("Retest scheduled");
    await expect(status).toHaveText("Ready for reading");
    await agent.act("Record reading", { params: { value } });
    await expect(status).toHaveText("Report retry");
  }
  await expect(status).toHaveText(
    scenario === "healthy" ? "Released" : "Rejected",
  );
  await expect(browser.locator("#report-count")).toHaveText(
    "Stored reports: 1",
  );
  await expect(screen.getByRole("alert")).toBeHidden();
  await expect(browser.locator("#guide-title")).toHaveText(
    "The workflow has reached its final results",
  );
  await browser.locator("#guide-action").click();
  await expect(browser.locator("#guide-title")).toHaveText(
    "Your workflow recording is saved",
  );
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
  const contrast = await browser.evaluate(inspectDarkContrast);
  expect(contrast.dark).toBe(true);
  expect(contrast.background_luminance < 0.02).toBe(true);
  expect(
    contrast.results.length > 5 &&
      contrast.results.every((row) => row.ratio >= 4.5),
  ).toBe(true);
  await screen.getByRole("button", sample).click();
  await expect(browser.locator("#selected-name")).toHaveText(sample);
  await expect(browser.locator("#selected-explanation")).toContainText(
    scenario === "healthy"
      ? "preferred release outcome is achieved"
      : "not the preferred release outcome",
  );
  await screen
    .getByRole("searchbox", "Search samples")
    .fill("no-matching-sample");
  await expect(browser.locator("#empty-title")).toHaveText(
    "No matching samples",
  );
  await expect(browser.locator("#visible-count")).toHaveText("0 of 1 samples");
  await screen.getByRole("searchbox", "Search samples").clear();
  await screen
    .getByRole("combobox", "Status filter")
    .selectOption({ value: "running" });
  await expect(browser.locator("#visible-count")).toHaveText("0 of 1 samples");
  await screen
    .getByRole("combobox", "Status filter")
    .selectOption({ value: "all" });
  await expect(status).toHaveText(
    scenario === "healthy" ? "Released" : "Rejected",
  );
  await screen.getByRole("button", "New sample").click();
  await expect(screen.getByRole("dialog")).toBeVisible();
  await browser.keyboard.press("Escape");
  await expect(screen.getByRole("dialog")).toBeHidden();
  expect(await browser.evaluate(() => document.activeElement?.id ?? null)).toBe(
    "new-sample",
  );
  await browser.locator("#guide-action").click();
  await expect(browser.locator("#capture-metadata")).toBeVisible();
  await expect(browser.locator("#recorded-inputs")).toHaveText(
    scenario === "healthy" ? "2" : "3",
  );
  await expect(browser.locator("#recorded-reports")).toHaveText("1");
  expect(
    await browser.evaluate(
      () =>
        document.querySelector<HTMLDetailsElement>(".technical-details")
          ?.open ?? null,
    ),
  ).toBe(false);
  await browser.locator(".technical-details summary").click();
  await expect(browser.locator("#capture-facts")).toContainText(
    "Not performed by this screen",
  );
  await expect(browser.locator("#captured-outcomes")).toContainText(
    scenario === "healthy" ? "Released: 1" : "Rejected: 1",
  );
  await app.screenshot("capture-evidence-1280px");
  await browser.setViewport({ width: 390, height: 844 });
  expect(
    await browser.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await app.screenshot("capture-evidence-390px");
  await browser.locator("#nav-queue").click();
  await browser.route("**/api/state", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "deliberately-invalid-json",
    }),
  );
  await expect(browser.locator("#connection-label")).toHaveText(
    "Disconnected · stale state",
  );
  await expect(screen.getByRole("button", "New sample")).toBeDisabled();
  await expect(screen.getByRole("button", "Start capture")).toBeDisabled();
  await expect(screen.getByRole("alert")).toContainText(
    "Displayed values are stale",
  );
  await expect(status).toHaveText(
    scenario === "healthy" ? "Released" : "Rejected",
  );
  await browser.unroute("**/api/state");
  await expect(browser.locator("#connection-label")).toHaveText("Connected");
  await expect(screen.getByRole("button", "New sample")).toBeEnabled();
  await expect(screen.getByRole("alert")).toBeHidden();
  let attemptedWrites = 0;
  await browser.route("**/api/events", (route) => {
    attemptedWrites += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      json: { unexpected: "state schema" },
    });
  });
  await screen.getByRole("button", "New sample").click();
  await screen.getByRole("textbox", "Sample name").fill("uncertain_write");
  await screen.getByRole("button", "Receive sample").click();
  await expect(browser.locator("#intake-error")).toContainText(
    "Unexpected product state schema",
  );
  await expect(browser.locator("#connection-label")).toHaveText("Connected");
  await screen.getByRole("button", "Cancel").click();
  await expect(screen.getByRole("alert")).toContainText(
    "The outcome may be unknown",
  );
  await expect(browser.locator("#guide-title")).toHaveText(
    "Review the state before trying again",
  );
  await browser.waitForResponse("**/api/state", { timeout: 10000 });
  expect(attemptedWrites).toBe(1);
  await browser.unroute("**/api/events");
});
