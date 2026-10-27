import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import type { PublicExample } from "../lib/public-example";

const preparedExample = JSON.parse(readFileSync(new URL("../public/examples/pressure-v1.json", import.meta.url), "utf8")) as PublicExample;

for (const language of ["sw-KE", "ar-SA"] as const) {
  test(`recap heading and chapter navigation use ${language}`, async ({ page }) => {
    await page.goto("/example");
    const recap = page.getByRole("region", { name: "Prepared recap" });
    await expect(recap).toBeVisible();
    await page.getByLabel("Example language").selectOption(language);
    await expect(recap.getByRole("heading")).toHaveText(preparedExample.recaps[language].fan.title);
    await expect(recap.getByRole("heading")).toHaveAttribute("lang", language);
    await expect(recap.getByRole("heading")).toHaveAttribute("dir", "auto");
    const chapters = recap.locator(".example-chapters");
    await expect(chapters).toHaveAttribute("lang", language);
    await expect(chapters).toHaveAttribute("dir", "auto");
    for (const [index, moment] of preparedExample.insights.entries()) {
      const chapter = chapters.getByRole("button").nth(index);
      await expect(chapter).toHaveText(moment.narratives[language].fan.title);
      await expect(chapter).toHaveAttribute("dir", "auto");
      expect(await chapter.evaluate((element) => element.closest("[lang]")?.getAttribute("lang"))).toBe(language);
    }
    if (language === "ar-SA") {
      expect(await recap.getByRole("heading").evaluate((element) => getComputedStyle(element).direction)).toBe("rtl");
      expect(await chapters.getByRole("button").first().evaluate((element) => getComputedStyle(element).direction)).toBe("rtl");
    }
    // The surrounding instructions are still English.
    await expect(recap.locator(".status-line")).not.toHaveAttribute("lang", language);
  });
}

const malformedReplays: Array<[string, (value: Record<string, unknown>) => void]> = [
  ["missing metadata", (value) => { delete value.metadata; }],
  ["null metadata", (value) => { value.metadata = null; }],
  ["array metadata", (value) => { value.metadata = []; }],
  ["invalid warning collection", (value) => { value.metadata = { warnings: "warning" }; }],
  ["invalid warning entry", (value) => { value.metadata = { warnings: [null] }; }],
  ["null insight", (value) => { value.insights = [null]; }],
  ["missing insight title", (value) => { value.insights = [{ insight_id: "moment-4", window_start_ms: 0, window_end_ms: 1 }]; }],
  ["invalid insight window", (value) => { value.insights = [{ insight_id: "moment-4", title: "Pressure", window_start_ms: "0", window_end_ms: 1 }]; }],
];

for (const [name, mutate] of malformedReplays) {
  test(`malformed replay: ${name} shows recovery instead of crashing`, async ({ page }) => {
    const errors: string[] = [];
    const apiRequests: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => { if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url()); });
    const payload = structuredClone(preparedExample);
    const replay: Record<string, unknown> = { ...payload.replay };
    mutate(replay);
    await page.route("**/examples/pressure-v1.json", (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify({ ...payload, replay }),
    }));
    await page.goto("/example");
    await expect(page.locator(".error-notice[role=alert]")).toContainText("incomplete");
    await expect(page.getByRole("button", { name: "Retry example" })).toBeVisible();
    await page.route("**/examples/pressure-v1.json", (route) => route.fulfill({ json: preparedExample }));
    await page.getByRole("button", { name: "Retry example" }).click();
    await expect(page.getByRole("button", { name: "Play replay", exact: true })).toBeEnabled();
    expect(errors).toEqual([]);
    expect(apiRequests).toEqual([]);
  });
}

test("public example works without identity, jobs or provider requests", async ({ page, context }, testInfo) => {
  const forbidden: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") || !["127.0.0.1", "localhost"].includes(url.hostname)) forbidden.push(request.url());
  });
  await page.goto("/");
  await page.getByRole("link", { name: "Explore an example match", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Why is the pressure building?" })).toBeVisible();
  const explanation = page.getByRole("article", { name: "Current match explanation" });
  await expect(explanation.getByRole("heading", { name: "Pressure Building", exact: true })).toBeVisible();
  const firstText = await explanation.locator(".insight-summary").textContent();
  await page.getByRole("button", { name: "Analyst", exact: true }).click();
  await expect(explanation.locator(".insight-summary")).not.toHaveText(firstText ?? "");
  await expect(explanation.locator(".insight-summary")).toContainText("measures");
  await page.getByLabel("Example language").selectOption("sw-KE");
  await expect(explanation.locator(".insight-summary")).toHaveAttribute("lang", "sw-KE");
  await expect(explanation.locator(".insight-summary")).toContainText("Vipimo");
  await page.getByLabel("Example language").selectOption("en-GB");
  await page.getByRole("button", { name: "Fan", exact: true }).click();
  await page.getByRole("button", { name: "How was this made?" }).click();
  await expect(page.getByText("prepared deterministic domain templates; not model-generated", { exact: false })).toBeVisible();
  await expect(page.locator("#example-provenance")).toContainText("seed 42001");
  await page.getByText("Resolve measurements to synthetic events", { exact: true }).click();
  await expect(page.locator("#example-provenance")).toContainText("event-");
  const audio = page.getByLabel("Prepared selected explanation", { exact: true });
  await audio.evaluate(async (element: HTMLAudioElement) => { await element.play(); });
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeGreaterThan(0);
  await audio.evaluate((element: HTMLAudioElement) => element.pause());
  const replay = page.getByLabel("Replay position", { exact: true });
  await replay.fill("1000");
  await expect(page.getByTestId("replay-clock")).toContainText("0:01");
  await page.getByRole("button", { name: "Play replay", exact: true }).click();
  await expect.poll(async () => Number(await replay.inputValue())).toBeGreaterThan(1000);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.getByRole("button", { name: "2.5D scene", exact: true }).click();
  await expect(page.locator("canvas")).toBeVisible();
  await page.getByRole("button", { name: "2D pitch", exact: true }).click();
  await page.getByRole("group", { name: "Seek to an insight" }).getByRole("button").first().click();
  await expect(explanation).not.toHaveAttribute("data-insight-id", "moment-4");
  await expect(page.getByRole("region", { name: "Selected explanation audio" })).toContainText("No prepared audio");
  await expect(page.getByRole("region", { name: "Prepared recap" })).toBeVisible();
  await page.getByRole("region", { name: "Prepared recap" }).getByRole("button", { name: "Pressure Building", exact: true }).click();
  await expect(explanation).toHaveAttribute("data-insight-id", "moment-4");
  await page.screenshot({ path: testInfo.outputPath("public-example.png"), fullPage: true });
  await page.reload();
  await expect(page.getByRole("button", { name: "Play replay", exact: true })).toBeEnabled();
  expect(forbidden).toEqual([]);
  expect(errors).toEqual([]);
  expect(await context.cookies()).toEqual([]);
});

test("mobile example remains within viewport and handles missing optional media", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/examples/pressure-en.wav", (route) => route.abort());
  await page.goto("/example");
  const audio = page.getByLabel("Prepared selected explanation", { exact: true });
  await expect(audio).toBeVisible();
  await audio.evaluate((element: HTMLAudioElement) => { element.load(); });
  await expect(page.locator(".error-notice[role=alert]")).toContainText("Prepared audio is unavailable");
  await expect(page.getByRole("article", { name: "Current match explanation" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("failed or malformed example is explicit and never falls back to analysis", async ({ page }) => {
  const calls: string[] = [];
  page.on("request", (request) => { if (new URL(request.url()).pathname.startsWith("/api/")) calls.push(request.url()); });
  await page.route("**/examples/pressure-v1.json", (route) => route.fulfill({ status: 503, body: "Unavailable" }));
  await page.goto("/example");
  await expect(page.locator(".error-notice[role=alert]")).toContainText("503");
  await expect(page.getByRole("button", { name: "Retry example" })).toBeVisible();
  await page.route("**/examples/pressure-v1.json", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ version: 1 }),
  }));
  await page.getByRole("button", { name: "Retry example" }).click();
  await expect(page.locator(".error-notice[role=alert]")).toContainText("incomplete");
  expect(calls).toEqual([]);
});

test("usable first load within five seconds on a declared connection", async ({ page, context }, testInfo) => {
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false, latency: 50, downloadThroughput: 2_500_000, uploadThroughput: 625_000, connectionType: "wifi",
  });
  const started = performance.now();
  await page.goto("/example");
  await expect(page.getByRole("article", { name: "Current match explanation" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Play replay", exact: true })).toBeEnabled();
  const milliseconds = Math.round(performance.now() - started);
  await testInfo.attach("first-load.json", { contentType: "application/json", body: JSON.stringify({
    milliseconds, target_ms: 5000, server: "local production build, warm process", browser: "Chromium, empty browser cache",
    connection: "20 Mbps down / 5 Mbps up, 50 ms added latency, no CPU throttling",
    limitation: "One controlled run; not an Azure cold-start or mobile-network guarantee",
  }) });
  expect(milliseconds).toBeLessThan(5000);
});
