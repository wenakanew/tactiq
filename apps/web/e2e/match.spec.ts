import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

// Real scenario reduction shares CPU with Chromium; this is not a latency benchmark.
test.describe.configure({ timeout: 150_000 });

test("live scenario, personalization, localization, and recap remain connected", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/workspace");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 90_000 });
  await page.getByRole("group", { name: "Seek to an insight", exact: true }).getByRole("button", { name: /Pressure Building/ }).first().click();
  await expect(page.locator(".insight-title")).toContainText("Pressure", { timeout: 15_000 });
  await page.getByRole("button", { name: "Analyst", exact: true }).click();
  await expect(page.locator(".full-explanation")).toContainText("progressive_actions=", { timeout: 10_000 });
  await page.locator(".full-explanation > summary").click();
  await expect(page.locator(".full-explanation p").last()).toBeVisible();
  await page.getByLabel("Language", { exact: true }).selectOption("sw-KE");
  await expect(page.locator(".insight-summary")).toContainText("Muktadha wa mechi:", { timeout: 10_000 });
  await page.getByRole("tab", { name: "The story", exact: true }).click();
  await page.getByRole("button", { name: "Generate recap", exact: true }).click();
  await expect(page.locator(".story-chapters")).toBeVisible();
  await expect(page.getByLabel("Replay position")).toBeVisible();
  expect(errors).toEqual([]);
});

test("mobile layout does not overflow and keeps primary controls reachable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workspace");
  await expect(page.getByRole("button", { name: "Start live scenario", exact: true })).toBeInViewport();
  const size = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(size.content).toBeLessThanOrEqual(size.viewport);
});

test("calibrated scene and video scrubber use the same playback clock", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const source = testInfo.outputPath("generated-replay.mp4");
  execFileSync("ffmpeg", [
    "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=320x200:r=25:d=2",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
  ]);
  await page.route("**/api/v1/matches/*/media/source", (route) => {
    const content = readFileSync(source);
    const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/);
    if (range) {
      const start = Number(range[1]);
      const end = range[2] ? Math.min(content.length - 1, Number(range[2])) : content.length - 1;
      const body = content.subarray(start, end + 1);
      return route.fulfill({ body, contentType: "video/mp4", status: 206,
        headers: { "Accept-Ranges": "bytes", "Content-Range": `bytes ${start}-${end}/${content.length}`,
          "Content-Length": String(body.length) } });
    }
    return route.fulfill({ body: content, contentType: "video/mp4", status: 200,
      headers: { "Accept-Ranges": "bytes", "Content-Length": String(content.length) } });
  });
  await page.route("**/api/v1/matches/*/replay*", (route) => {
    const matchId = new URL(route.request().url()).pathname.split("/")[4];
    const frames = Array.from({ length: 17 }, (_, index) => {
      const time = index * 125;
      const ball = {
        track_id: "ball_1", team_id: null, confidence: 1, inferred: false,
        position: { x: 20 + time / 100, y: 50 }, image_position: { x: 20 + time / 100, y: 50 },
      };
      return {
        timestamp_ms: time, sequence: index + 1, score: {}, possession_team_id: "team_a", tactics: {},
        tracking: {
          calibrated: true, detection_source: "synthetic-render-fixture", ball, ball_position: ball.position,
          players: [{ track_id: "player_1", team_id: "team_a", confidence: 1, inferred: false,
            position: { x: 45, y: 30 }, image_position: { x: 45, y: 30 } }],
        },
      };
    });
    return route.fulfill({ json: {
      match_id: matchId, duration_ms: 2000, processed_until_ms: 2000, frames, insights: [],
      source_url: `/api/v1/matches/${matchId}/media/source`, team_display_names: { team_a: "Home", team_b: "Away" },
      metadata: { width: 320, height: 200, detection_source: "synthetic-render-fixture" },
    } });
  });
  await page.goto("/workspace");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 90_000 });
  await page.getByRole("button", { name: "2.5D scene", exact: true }).click();
  await expect(page.locator(".world-pitch canvas")).toBeVisible({ timeout: 30_000 });
  const video = page.locator(".replay-video-stage video");
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(1);
  const slider = page.getByLabel("Replay position");
  await slider.focus();
  await slider.press("End");
  await expect(slider).toHaveValue("2000");
  await expect.poll(async () => {
    const seconds = await video.evaluate((element) => (element as HTMLVideoElement).currentTime);
    const requested = Number(await slider.inputValue()) / 1000;
    return Math.abs(seconds - requested);
  }).toBeLessThan(0.15);
  await expect(page.locator(".image-tracking-layer circle").last()).toHaveAttribute("cx", "40");
  await page.screenshot({ path: testInfo.outputPath("calibrated-replay.png"), fullPage: false });
  expect(errors).toEqual([]);
});

test("stop audio pauses the active media element rather than only clearing its queue", async ({ page }, testInfo) => {
  const source = testInfo.outputPath("speech-fixture.mp3");
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i",
    "sine=frequency=440:duration=5", "-c:a", "libmp3lame", source]);
  await page.addInitScript(() => {
    const NativeAudio = window.Audio;
    const instances: HTMLAudioElement[] = [];
    Object.defineProperty(window, "__testAudioInstances", { value: instances });
    Object.defineProperty(window, "Audio", { value: function trackedAudio(source?: string) {
      const audio = new NativeAudio(source);
      instances.push(audio);
      return audio;
    } });
  });
  await page.route("**/api/v1/speech/synthesize", (route) => route.fulfill({ json: {
    audio_base64: readFileSync(source).toString("base64"), content_type: "audio/mpeg", provider: "test-audio-fixture",
  } }));
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: { auth_enabled: false, speech_configured: true } }));
  await page.goto("/workspace");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByLabel("Dual commentary", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 90_000 });
  await expect(page.locator(".insight-summary")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Listen", exact: true }).click();
  await expect.poll(() => page.evaluate(() => {
    const records: unknown = Object.getOwnPropertyDescriptor(window, "__testAudioInstances")?.value;
    return Array.isArray(records) && records.some((audio: unknown) => audio instanceof HTMLAudioElement && !audio.paused);
  })).toBeTruthy();
  await page.getByRole("button", { name: "Stop audio", exact: true }).click();
  await expect.poll(() => page.evaluate(() => {
    const records: unknown = Object.getOwnPropertyDescriptor(window, "__testAudioInstances")?.value;
    return Array.isArray(records) && records.length > 0
      && records.every((audio: unknown) => audio instanceof HTMLAudioElement && audio.paused);
  })).toBeTruthy();
});

test("signing out clears match evidence and saved identifiers from the interface", async ({ page }) => {
  let signedOut = false;
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: { auth_enabled: true } }));
  await page.route("**/api/v1/session", (route) => route.fulfill(signedOut
    ? { status: 401, json: { detail: "Sign in" } }
    : { json: { tenant_id: "fixture", subject: "operator", role: "admin" } }));
  await page.route("**/api/v1/session/logout", (route) => {
    signedOut = true;
    return route.fulfill({ json: { status: "signed_out" } });
  });
  await page.route("**/api/v1/matches", (route) => signedOut && route.request().method() === "GET"
    ? route.fulfill({ status: 401, json: { detail: "Sign in" } }) : route.continue());
  await page.goto("/workspace");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 90_000 });
  await expect(page.locator(".insight-title")).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.locator(".insight-title")).toHaveCount(0);
  await expect(page.locator(".timeline-item")).toHaveCount(0);
  await expect(page.getByLabel("Replay position")).toHaveCount(0);
  await expect(page.getByLabel("Restore a saved match")).toHaveCount(0);
});

test("scrubbing an event replay during playback keeps the requested time", async ({ page }) => {
  await page.route("**/api/v1/matches/*/replay*", (route) => route.fulfill({ json: {
    match_id: "playback-fixture", duration_ms: 10_000, processed_until_ms: 10_000, source_url: null,
    team_display_names: {}, metadata: { detection_source: "synthetic-render-fixture" }, insights: [],
    frames: [0, 5000, 10_000].map((timestamp) => ({
      timestamp_ms: timestamp, sequence: timestamp + 1, score: {}, possession_team_id: null, tactics: {},
      tracking: { players: [], ball: null, ball_position: { x: timestamp / 100, y: 50 } },
    })),
  } }));
  await page.goto("/workspace");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 90_000 });
  const replay = page.getByLabel("Synchronized tactical replay");
  await replay.getByRole("button", { name: "Play replay", exact: true }).click();
  const slider = page.getByLabel("Replay position");
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(0);
  await expect.poll(async () => Number(await slider.inputValue())).toBeLessThan(10_000);
  await slider.focus();
  await slider.press("End");
  await expect(replay.getByRole("button", { name: "Play replay", exact: true })).toBeVisible();
  await expect(slider).toHaveValue("10000");
});

test("restoring a match that completed during connection uses the authoritative snapshot status", async ({ page }) => {
  await page.goto("/workspace");
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  const created = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/matches");
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  const matchId = (await (await created).json() as { match_id: string }).match_id;
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 90_000 });
  let intercepted = false;
  const completedSnapshot = new Promise<void>((resolve) => {
    page.on("websocket", (socket) => {
      socket.on("framereceived", (frame) => {
        const message = JSON.parse(String(frame.payload)) as { type: string; payload?: { status?: string } };
        if (message.type === "match.snapshot" && message.payload?.status === "completed") resolve();
      });
    });
  });
  await page.route(`**/api/v1/matches/${matchId}/state`, async (route) => {
    if (intercepted) return route.continue();
    intercepted = true;
    const actual = await route.fetch();
    const state = await actual.json() as Record<string, unknown>;
    return route.fulfill({ json: { ...state, status: "running" } });
  });
  await page.getByRole("button", { name: "New match", exact: true }).click();
  await page.getByRole("button", { name: "Saved matches", exact: true }).click();
  await page.getByLabel("Restore a saved match").getByRole("button").filter({ hasText: matchId.slice(-8) }).click();
  await expect.poll(() => intercepted).toBeTruthy();
  await completedSnapshot;
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.locator(".live-pill")).toContainText("Full time", { timeout: 10_000 });
});
