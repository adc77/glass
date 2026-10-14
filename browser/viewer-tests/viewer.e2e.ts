import { test } from "@e2e-dev/web";
import { expect } from "e2e";
import { inspectDarkContrast } from "../contrast.ts";

test("evidence console inspects real paired runs without executing or mutating", async ({
  app,
  screen,
  browser,
}) => {
  await app.open();
  await expect(screen.getByRole("heading", "Simulation results")).toBeVisible();
  await expect(browser.locator("#evidence-health")).toHaveText("RUN VERIFIED");
  await expect(browser.locator("#overview")).toBeVisible();
  expect(
    await browser.evaluate(
      () => getComputedStyle(document.documentElement).colorScheme,
    ),
  ).toBe("dark");
  await expect(browser.locator("#scenario-results")).toContainText("Goals met");
  await expect(browser.locator("#scenario-results")).toContainText(
    "does not mean the product achieved its goal",
  );
  await expect(browser.locator("#run-section")).toBeHidden();
  await expect(browser.locator("#overview")).toContainText(
    "Scripted controller — not an LLM",
  );
  await app.screenshot("overview-1440px");
  const contrast = await browser.evaluate(inspectDarkContrast);
  expect(contrast.dark).toBe(true);
  expect(contrast.background_luminance < 0.02).toBe(true);
  expect(
    contrast.results.length > 5 &&
      contrast.results.every((row) => row.ratio >= 4.5),
  ).toBe(true);
  await browser.locator("#run-search").fill("no-such-run");
  await expect(browser.locator("#overview")).toContainText(
    "0 of 4 recorded runs",
  );
  await browser.locator("#run-search").fill("faults");
  await expect(browser.locator("#overview")).toContainText(
    "2 of 4 recorded runs",
  );
  await browser.locator("#run-filter").selectOption({ value: "failed" });
  await expect(browser.locator("#overview")).toContainText(
    "0 of 4 recorded runs",
  );
  await browser.locator("#run-filter").selectOption({ value: "all" });
  await screen.getByRole("button", "Explore faults repeat 7").click();
  await expect(browser.locator("#run-title")).toHaveText("faults · repeat 7");
  await expect(browser.locator("#run-section")).toBeVisible();
  await expect(browser.locator("#personas")).toContainText(
    "Accepted selection",
  );
  await expect(browser.locator("#personas")).toContainText(
    "What this persona observed",
  );
  await expect(browser.locator(".recorded-choice")).toContainText(
    "Submit my sample for processing.",
  );
  await expect(browser.locator(".persona-goals")).toContainText(
    "prefer release",
  );
  expect(
    await browser.evaluate(
      () =>
        document.querySelector<HTMLDetailsElement>(".decision-technical")
          ?.open ?? null,
    ),
  ).toBe(false);
  await app.screenshot("persona-inspector-1440px");
  await browser.locator("#tab-personas").focus();
  await browser.keyboard.press("ArrowRight");
  await expect(browser.locator("#timeline")).toBeVisible();
  await browser.locator("#event-search").fill("no-such-handler");
  await expect(browser.locator("#timeline")).toContainText("0 events");
  await browser.locator("#event-search").fill("actors_step");
  await expect(browser.locator("#timeline")).toContainText("actors_step");
  await app.screenshot("timeline-1440px");
  await browser.locator("#tab-state").click();
  await browser
    .locator("#state details summary")
    .filter({ hasText: "Final product state" })
    .click();
  await expect(browser.locator("#state")).toContainText("scrapped");
  await browser.setViewport({ width: 390, height: 844 });
  expect(
    await browser.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await app.screenshot("state-390px");
  await browser.locator("#view-inspector").focus();
  await browser.keyboard.press("ArrowLeft");
  await expect(browser.locator("#overview")).toBeVisible();
  await app.screenshot("overview-390px");
  const responsePending = browser.waitForResponse("**/api/experiments", {
    timeout: 10000,
  });
  await screen.getByRole("button", "Recheck evidence").click();
  const response = await responsePending;
  expect(response.status).toBe(200);
  await browser.locator("#experiments button").nth(1).click();
  await expect(browser.locator("#evidence-health")).toHaveText(
    "EVIDENCE UNAVAILABLE",
  );
  await expect(browser.locator("#notice")).toContainText(
    "Cannot inspect Incomplete",
  );
  await expect(browser.locator("#run-section")).toBeHidden();
  await browser.locator("#experiments button").nth(0).click();
  await expect(browser.locator("#evidence-health")).toHaveText("RUN VERIFIED");
  await expect(browser.locator("#overview")).toContainText(
    "4 of 4 recorded runs",
  );
});
