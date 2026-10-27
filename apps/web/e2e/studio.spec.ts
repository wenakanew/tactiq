import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { clockLabel } from "../lib/workspace-types";

test.describe.configure({ timeout: 150_000 });

async function scenario(page: Page) {
  await page.goto("/workspace?start=demo");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  const created = page.waitForResponse((response) => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v1/matches");
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  const id = (await (await created).json() as { match_id: string }).match_id;
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 90_000 });
  await expect(page.locator(".insight-title")).toBeVisible();
  return id;
}

test("public landing is independent of the analysis service and never auto-starts a demo", async ({ page }, info) => {
  const requests: Array<{ method: string; path: string }> = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) requests.push({ method: request.method(), path });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Understand the");
  await expect(page.getByRole("link", { name: "Explore an example match", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("landing-desktop.png"), fullPage: true });
  expect(requests).toEqual([]);
  await page.getByRole("link", { name: "Start a new analysis", exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\?start=demo$/);
  await expect(page.getByRole("button", { name: "Start live scenario", exact: true })).toBeVisible();
  expect(requests.filter((request) => request.method !== "GET")).toEqual([]);
  await page.screenshot({ path: info.outputPath("workspace-entry-desktop.png"), fullPage: true });
});

test("mobile landing keeps its primary actions visible without horizontal overflow", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const action = page.getByRole("link", { name: "Explore an example match", exact: true });
  await expect(action).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("landing-mobile.png"), fullPage: true });
  await page.getByRole("link", { name: "Analyze your video", exact: true }).click();
  await expect(page.getByRole("button", { name: "Your video", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Upload video file", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload and analyze", exact: true })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("workspace-entry-mobile.png"), fullPage: true });
});

test("video intent survives protected sign-in without storing the access token", async ({ page }) => {
  let signedIn = false;
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: { auth_enabled: true } }));
  await page.route("**/api/v1/session", (route) => {
    if (route.request().method() === "POST") signedIn = true;
    return route.fulfill(signedIn ? { json: { tenant_id: "fixture", subject: "analyst", role: "analyst" } }
      : { status: 401, json: { detail: "Sign in" } });
  });
  await page.goto("/workspace?start=video");
  await expect(page.getByLabel("Workspace access token", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Upload video file", { exact: true })).toHaveCount(0);
  await page.getByLabel("Workspace access token", { exact: true }).fill("offline-ui-credential");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Your video", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Upload video file", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }).includes("offline-ui-credential"))).toBe(false);
});

test("viewer access exposes saved matches, not unauthorized analysis actions", async ({ page }) => {
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: { auth_enabled: true } }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ json: { tenant_id: "fixture", subject: "reader", role: "viewer" } }));
  await page.route("**/api/v1/matches", (route) => route.fulfill({ json: [] }));
  await page.goto("/workspace?start=video");
  await expect(page.getByRole("button", { name: "Saved matches", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "New match", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start live scenario", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Upload video file", { exact: true })).toHaveCount(0);
});

test("an unavailable backend is a recoverable access state, not an empty analysis", async ({ page }) => {
  await page.route("**/api/v1/capabilities", (route) => route.abort());
  await page.goto("/workspace");
  await expect(page.getByRole("heading", { name: "Let's reconnect.", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry connection", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start live scenario", exact: true })).toHaveCount(0);
});

test("a selected moment seeks its actual window and keeps the stage mounted across tabs", async ({ page }, info) => {
  const id = await scenario(page);
  const response = await page.request.get(`${process.env.TACTIQ_E2E_API_URL}/api/v1/matches/${id}/insights`);
  const insights = await response.json() as Array<{ insight_id: string; title: string; provenance: { window: { end_ms: number } } }>;
  const first = insights[0];
  const stage = await page.getByLabel("Synchronized tactical replay").elementHandle();
  await page.getByRole("tab", { name: "The match", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Moments/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "The story", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(page.getByRole("tab", { name: "The match", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(await stage!.evaluate((element) => element.isConnected)).toBe(true);
  await page.getByRole("tab", { name: /^Moments/ }).click();
  await page.getByRole("button", { name: `View moment: ${first.title} at ${clockLabel(first.provenance.window.end_ms)}`, exact: true }).click();
  await expect(page.getByRole("article", { name: "Current match explanation", exact: true })).toHaveAttribute("data-insight-id", first.insight_id);
  await expect(page.getByLabel("Replay position")).toHaveValue(String(first.provenance.window.end_ms));
  const explanation = page.getByRole("article", { name: "Current match explanation", exact: true });
  await explanation.locator("summary").filter({ hasText: "Explore the supporting evidence" }).click();
  await expect(explanation.getByText("Evidence window", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("workspace-match-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("workspace-match-mobile.png"), fullPage: true });
});

test("returning to the workspace restores existing work without creating another match", async ({ page }) => {
  let creates = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/matches") creates += 1;
  });
  const id = await scenario(page);
  await page.getByRole("link", { name: "Tactiq home", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Understand the");
  await page.getByRole("link", { name: "Open workspace", exact: true }).click();
  await expect(page.locator("#match-stage")).toHaveAttribute("data-match-id", id, { timeout: 15_000 });
  await expect(page.locator(".live-pill")).toContainText("Full time");
  expect(creates).toBe(1);
  await page.getByRole("link", { name: "Tactiq home", exact: true }).click();
  await page.getByRole("link", { name: "Analyze your video", exact: true }).click();
  await expect(page.getByRole("button", { name: "Your video", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Upload video file", { exact: true })).toBeVisible();
});

test("a delayed replay response does not discard a selected moment's seek intent", async ({ page }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/matches/*/replay*", async (route) => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  try {
    const id = await scenario(page);
    const response = await page.request.get(`${process.env.TACTIQ_E2E_API_URL}/api/v1/matches/${id}/insights`);
    const moments = await response.json() as Array<{ insight_id: string; title: string; provenance: { window: { end_ms: number } } }>;
    const selected = moments[0];
    await page.getByRole("tab", { name: /^Moments/ }).click();
    await page.getByRole("button", { name: `View moment: ${selected.title} at ${clockLabel(selected.provenance.window.end_ms)}`, exact: true }).click();
    release();
    await expect(page.getByLabel("Replay position")).toHaveValue(String(selected.provenance.window.end_ms));
    await page.getByRole("button", { name: "Latest moment", exact: true }).click();
    await expect(page.getByRole("article", { name: "Current match explanation", exact: true })).toHaveAttribute("data-insight-id", moments[moments.length - 1].insight_id);
  } finally { release(); }
});

test("a generated video reaches the real job, unknown scoreline, and playable source flow", async ({ page }, info) => {
  test.setTimeout(90_000);
  const video = info.outputPath("generated-analysis.mp4");
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
    "testsrc2=size=160x90:rate=10", "-t", "1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", video]);
  await page.goto("/workspace?start=video");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByLabel("Upload video file", { exact: true }).setInputFiles(video);
  await page.getByRole("button", { name: "Upload and analyze", exact: true }).click();
  await expect(page.locator(".live-pill")).toContainText("Analysis complete", { timeout: 60_000 });
  await expect(page.locator(".score-block")).toHaveText("–:–");
  const player = page.locator(".replay-video-stage video");
  await expect.poll(() => player.evaluate((element) => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(1);
  await expect(page.getByRole("button", { name: "Split view", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Video only", exact: true }).click();
  await expect(page.locator(".replay-world")).toBeHidden();
  await page.getByRole("button", { name: "Tracking only", exact: true }).click();
  await expect(page.locator(".replay-video-stage")).toBeHidden();
  await page.getByRole("button", { name: "Split view", exact: true }).click();
  await expect(player).toBeVisible();
  await expect(page.locator(".replay-world")).toBeVisible();
});

test("Arabic and Hindi explanations retain direction, content, and responsive layout", async ({ page }) => {
  await scenario(page);
  await page.getByLabel("Language", { exact: true }).selectOption("ar-SA");
  await expect(page.locator(".insight-summary")).toHaveAttribute("lang", "ar-SA");
  await expect(page.locator(".insight-summary")).toHaveAttribute("dir", "auto");
  await expect(page.locator(".insight-summary")).toContainText(/[\u0600-\u06ff]/);
  await page.getByLabel("Language", { exact: true }).selectOption("hi-IN");
  await expect(page.locator(".insight-summary")).toHaveAttribute("lang", "hi-IN");
  await expect(page.locator(".insight-summary")).toContainText(/[\u0900-\u097f]/);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("an invalid clip reference is disclosed without breaking the match workspace", async ({ page }) => {
  const id = await scenario(page);
  await page.getByRole("link", { name: "Tactiq home", exact: true }).click();
  await page.route(`**/api/v1/matches/${id}/insights`, async (route) => {
    const response = await route.fetch();
    const moments = await response.json() as Array<Record<string, unknown>>;
    moments[moments.length - 1].clip = { status: "ready", url: "https://[" };
    await route.fulfill({ json: moments });
  });
  await page.getByRole("link", { name: "Open workspace", exact: true }).click();
  await page.getByRole("group", { name: "Seek to an insight", exact: true }).getByRole("button").last().click();
  await expect(page.getByRole("article", { name: "Current match explanation", exact: true })
    .getByText("The evidence clip has an invalid media reference.", { exact: true })).toBeVisible();
  await expect(page.locator(".insight-title")).toBeVisible();
  await expect(page.getByRole("tab", { name: "The match", exact: true })).toBeVisible();
});

test("job navigation restores pending work instead of the previously open match", async ({ page }) => {
  await scenario(page);
  let cancelled = false;
  let terminal = false;
  await page.route("**/api/v1/video/jobs", (route) => route.fulfill({ status: 202, json: { job_id: "job_ui_fixture", status: "queued" } }));
  await page.route("**/api/v1/jobs/job_ui_fixture", (route) => route.fulfill({ json: {
    job_id: "job_ui_fixture", status: terminal ? "cancelled" : "running", cancel_requested: cancelled, result: null,
  } }));
  await page.route("**/api/v1/jobs/job_ui_fixture/cancel", (route) => {
    cancelled = true;
    return route.fulfill({ json: { job_id: "job_ui_fixture", status: "running", cancel_requested: true } });
  });
  await page.getByRole("button", { name: "New match", exact: true }).click();
  await page.getByRole("button", { name: "Your video", exact: true }).click();
  await page.getByLabel("Upload video file", { exact: true }).setInputFiles({
    name: "offline-fixture.mp4", mimeType: "video/mp4", buffer: Buffer.from("fixture transport only; no real decode"),
  });
  await page.getByRole("button", { name: "Upload and analyze", exact: true }).click();
  await expect(page.getByLabel("Analysis progress")).toBeVisible();
  await page.getByRole("link", { name: "Tactiq home", exact: true }).click();
  await page.getByRole("link", { name: "Open workspace", exact: true }).click();
  await expect(page.getByLabel("Analysis progress")).toBeVisible();
  await expect(page.locator("#match-stage")).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel analysis", exact: true }).click();
  await expect.poll(() => cancelled).toBe(true);
  await expect(page.getByLabel("Analysis progress")).toContainText(/cancelling/);
  terminal = true;
  await expect(page.getByLabel("Analysis progress")).toHaveCount(0);
  await expect(page.getByText("analysis cancelled", { exact: true })).toBeVisible();
});

test("switching supporting views pauses a playing inline evidence clip", async ({ page }, info) => {
  const video = info.outputPath("evidence-fixture.mp4");
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
    "color=c=green:s=160x90:r=10:d=5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", video]);
  const id = await scenario(page);
  await page.getByRole("link", { name: "Tactiq home", exact: true }).click();
  await page.route(`**/api/v1/matches/${id}/insights`, async (route) => {
    const response = await route.fetch();
    const moments = await response.json() as Array<Record<string, unknown>>;
    moments[moments.length - 1].clip = { status: "ready", url: `/media/${id}/evidence-fixture.mp4` };
    await route.fulfill({ json: moments });
  });
  await page.route(`**/media/${id}/evidence-fixture.mp4`, (route) => route.fulfill({ contentType: "video/mp4", body: readFileSync(video) }));
  await page.getByRole("link", { name: "Open workspace", exact: true }).click();
  await expect(page.locator("#match-stage")).toHaveAttribute("data-match-id", id);
  await page.getByRole("tab", { name: /^Moments/ }).click();
  const clip = page.getByRole("tabpanel", { name: /^Moments/ }).locator("video");
  const element = await clip.elementHandle();
  expect(element).not.toBeNull();
  await element!.evaluate(async (element) => {
    const video = element as HTMLVideoElement;
    video.muted = true;
    await video.play();
  });
  await expect.poll(() => element!.evaluate((element) => (element as HTMLVideoElement).paused)).toBe(false);
  await page.getByRole("tab", { name: "The story", exact: true }).click();
  await expect.poll(() => element!.evaluate((element) => (element as HTMLVideoElement).paused)).toBe(true);
});

test("shared text and action colours satisfy AA contrast in their intended surfaces", async ({ page }) => {
  await page.goto("/");
  const ratios = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    function luminance(variable: string) {
      const value = style.getPropertyValue(variable).trim().replace("#", "");
      const linear = [0, 2, 4].map((offset) => parseInt(value.slice(offset, offset + 2), 16) / 255)
        .map((channel) => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
      return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
    }
    return [["--text-primary", "--bg"], ["--text-secondary", "--surface"], ["--muted", "--surface-elevated"]]
      .map(([foreground, background]) => {
        const a = luminance(foreground), b = luminance(background);
        return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
      });
  });
  expect(ratios.every((ratio) => ratio >= 4.5)).toBe(true);
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe("auto");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content", exact: true })).toBeFocused();
});
