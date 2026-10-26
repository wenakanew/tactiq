import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

type RequestBody = { depth?: string; overview?: boolean; at_ms?: number; turns?: Array<{ role: string; text: string }>;
  match_id?: string; script_id?: string };

async function videoWorkspace(page: Page, info: TestInfo, size = "216x384", deferred = false, slowSpeech = false, owned = false) {
  const video = info.outputPath("clock-fixture.mp4"), audio = info.outputPath("speech-fixture.mp3");
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", `color=c=green:s=${size}:r=10:d=65`,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", video], { timeout: 20000 });
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
    "-c:a", "libmp3lame", audio], { timeout: 20000 });
  await page.addInitScript(() => {
    const Native = window.Audio, records: HTMLAudioElement[] = [];
    Object.defineProperty(window, "__commentaryAudio", { value: records });
    Object.defineProperty(window, "Audio", { value: function observedAudio(src?: string) {
      const element = new Native(src); records.push(element); return element;
    } });
  });
  const match = "match_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
  const scripts: RequestBody[] = [], speeches: RequestBody[] = [];
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const ball = { track_id: "ball_1", confidence: .8, inferred: false, team_id: null,
    position: null, image_position: { x: 25, y: 75 } };
  const tracking = { calibrated: false, geometry_source: "image", tracking_source: "offline-browser-fixture",
    detection_source: "model", players: [], ball };
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: {
    auth_enabled: owned, cv_model_configured: true, cv_detector: "onnx", youtube_import_configured: true,
    ...(owned ? { public_trial: { enabled: true, microsoft_login_enabled: false, owned_speech_required: true },
      analysis_admission_enabled: true } : {}),
    speech_configured: true, speech: { configured: true, languages: { "en-GB": { single: true, dual: true } } },
    narrative: { status: "disabled" },
  } }));
  if (owned) await page.route("**/api/v1/session/guest", (route) => route.fulfill({ json: {
    kind: "guest", principal: { tenant_id: "fixture", subject: "guest", role: "analyst" },
    trial: { limit: 3, remaining: 3, reset_at: null }, expires_at: Date.now() / 1000 + 86400,
  } }));
  await page.route("**/api/v1/matches", (route) => route.fulfill({ json: [{ match_id: match, updated_at: "2026-10-01T00:00:00Z" }] }));
  await page.route(`**/api/v1/matches/${match}/state`, (route) => route.fulfill({ json: {
    match_id: match, scenario_id: "cv_upload", status: "completed", match_clock_ms: 65000,
    score: {}, action_count: 0, tracking_sample_count: 261, last_sequence: 261, cv_live: tracking,
    analysis_metadata: { detection_source: "model", tracking_quality: { sampled_frames: 261, ball_observed_frames: 261 } },
  } }));
  await page.route(`**/api/v1/matches/${match}/insights`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v1/matches/${match}/commentary/debug*`, (route) => route.fulfill({ json: { entries: [] } }));
  const frames = Array.from({ length: 261 }, (_, index) => ({ timestamp_ms: index * 250, sequence: index + 1,
    tracking, tactics: {}, score: {}, possession_team_id: null }));
  await page.route(`**/api/v1/matches/${match}/replay*`, (route) => {
    const after = Number(new URL(route.request().url()).searchParams.get("after") ?? 0);
    const page = frames.slice(after, after + 100);
    return route.fulfill({ json: {
    match_id: match, duration_ms: 65000, processed_until_ms: 65000,
    source_url: `/api/v1/matches/${match}/media/source`, insights: [], team_display_names: {},
    metadata: { width: 1920, height: 1080, detection_source: "model" },
    ...(owned ? { records: page.map((payload) => ({ record_id: payload.sequence, payload })),
      cursor: { revision: 1, after: after + page.length }, has_more: after + page.length < frames.length } : { frames }),
  } });
  });
  await page.route(`**/api/v1/matches/${match}/media/source`, (route) => {
    const bytes = readFileSync(video);
    const range = route.request().headers().range?.match(/^bytes=(\d+)-(\d*)$/);
    const start = range ? Number(range[1]) : 0;
    const end = range?.[2] ? Math.min(bytes.length - 1, Number(range[2])) : bytes.length - 1;
    return route.fulfill({ status: range ? 206 : 200, contentType: "video/mp4", body: bytes.subarray(start, end + 1),
      headers: { "Accept-Ranges": "bytes", "Content-Length": String(end - start + 1),
        ...(range ? { "Content-Range": `bytes ${start}-${end}/${bytes.length}` } : {}) } });
  });
  await page.route(`**/api/v1/matches/${match}/commentary/dual`, async (route) => {
    const body = route.request().postDataJSON() as RequestBody;
    scripts.push(body);
    const stale = deferred && scripts.length === 1;
    if (stale) await gate;
    try {
      await route.fulfill({ json: { provider: "offline-browser-script", fallback_used: true,
        script_id: "owned_fixture_script", turns: [
        { role: "lead", text: stale ? "Obsolete fixture commentary." : "An observation overview, not a tactical conclusion." },
        { role: "analyst", text: "The recorded samples keep observations separate from predictions." },
        ...(body.depth === "deep" ? [
          { role: "lead", text: "The selected window has its own explicit supporting samples." },
          { role: "analyst", text: "Image coordinates do not establish surveyed pitch distances." },
        ] : []),
      ] } });
    } catch (error) {
      if (!stale) throw error;
    }
  });
  await page.route("**/api/v1/speech/synthesize-dual", async (route) => {
    speeches.push(route.request().postDataJSON() as RequestBody);
    if (slowSpeech) await gate;
    return route.fulfill({ json: { provider: "offline-playback-fixture", content_type: "audio/mpeg",
      audio_base64: readFileSync(audio).toString("base64") } });
  });
  await page.goto("/workspace");
  await page.getByRole("button", { name: "Saved matches", exact: true }).click();
  await page.locator(".saved-match").first().click();
  await expect(page.getByRole("button", { name: "Listen to analysis overview", exact: true })).toBeVisible();
  const source = page.locator(".replay-video-stage video");
  await expect.poll(() => source.evaluate((element) => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(1);
  return { scripts, speeches, release, source };
}

async function playingAudio(page: Page) {
  return page.evaluate(() => {
    const records: unknown = Object.getOwnPropertyDescriptor(window, "__commentaryAudio")?.value;
    return Array.isArray(records) && records.some((item: unknown) => item instanceof HTMLAudioElement && !item.paused);
  });
}

test("manual uploaded-video dual commentary works with automatic speech off and pauses only replay", async ({ page }, info) => {
  const fixture = await videoWorkspace(page, info);
  expect(fixture.scripts).toEqual([]);
  expect(fixture.speeches).toEqual([]);
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Play replay", exact: true }).click();
  await expect.poll(() => fixture.source.evaluate((element) => (element as HTMLVideoElement).paused)).toBe(false);
  await page.getByRole("button", { name: "Listen to analysis overview", exact: true }).click();
  await expect.poll(() => playingAudio(page)).toBe(true);
  expect(fixture.scripts).toHaveLength(1);
  expect(fixture.scripts[0]).toMatchObject({ depth: "deep", overview: true });
  expect(fixture.speeches[0].turns?.map((turn) => turn.role)).toEqual(["lead", "analyst", "lead", "analyst"]);
  await expect.poll(() => fixture.source.evaluate((element) => (element as HTMLVideoElement).paused)).toBe(true);
  await expect.poll(() => playingAudio(page), { timeout: 8000 }).toBe(false);
  expect(await fixture.source.evaluate((element) => (element as HTMLVideoElement).paused)).toBe(true);
  await page.getByLabel("Commentary transcript").locator("summary").click();
  await expect(page.getByLabel("Commentary transcript")).toContainText("Image coordinates");
});

test("cloud guest speech sends only the owned match and script references, never arbitrary text", async ({ page }, info) => {
  const fixture = await videoWorkspace(page, info, "216x384", false, false, true);
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Listen to analysis overview", exact: true }).click();
  await expect.poll(() => playingAudio(page)).toBe(true);
  expect(fixture.speeches).toEqual([{
    match_id: "match_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", script_id: "owned_fixture_script",
  }]);
  await page.getByRole("button", { name: "Stop audio", exact: true }).click();
  await expect.poll(() => playingAudio(page)).toBe(false);
});

test("automatic upload commentary starts with playback and discards an obsolete pending script after seek", async ({ page }, info) => {
  const fixture = await videoWorkspace(page, info, "216x384", true);
  try {
  expect(fixture.scripts).toEqual([]);
  await page.getByRole("button", { name: "Play replay", exact: true }).click();
  await expect.poll(() => fixture.scripts.length).toBe(1);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await fixture.source.evaluate((element) => { (element as HTMLVideoElement).currentTime = 35; });
  fixture.release();
  await page.waitForTimeout(300);
  expect(fixture.speeches).toEqual([]);
  await page.getByRole("button", { name: "Play replay", exact: true }).click();
  await expect.poll(() => playingAudio(page)).toBe(true);
  expect(fixture.scripts[fixture.scripts.length - 1]).toMatchObject({ depth: "broadcast", overview: true });
  expect(fixture.scripts[fixture.scripts.length - 1].at_ms).toBeGreaterThanOrEqual(35000);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect.poll(() => playingAudio(page)).toBe(false);
  await expect(page.getByLabel("Commentary transcript")).not.toContainText("Obsolete fixture");
  } finally { fixture.release(); }
});

test("late synthesized audio is skipped rather than starting out of sync", async ({ page }, info) => {
  const fixture = await videoWorkspace(page, info, "216x384", false, true);
  try {
    await page.getByRole("button", { name: "Play replay", exact: true }).click();
    await expect.poll(() => fixture.speeches.length).toBe(1);
    await expect.poll(() => fixture.source.evaluate((element) => (element as HTMLVideoElement).currentTime),
      { timeout: 12000 }).toBeGreaterThan(7);
    fixture.release();
    await expect(page.getByText("Skipping late commentary to stay aligned. Listen remains available.", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => {
      const records: unknown = Object.getOwnPropertyDescriptor(window, "__commentaryAudio")?.value;
      return Array.isArray(records) ? records.length : -1;
    })).toBe(0);
    expect(await fixture.source.evaluate((element) => (element as HTMLVideoElement).paused)).toBe(false);
  } finally { fixture.release(); }
});

for (const size of ["216x384", "384x216"]) {
  test(`tracking overlays align with ${size} native video through resizing and wrong legacy metadata`, async ({ page }, info) => {
    const fixture = await videoWorkspace(page, info, size);
    await page.getByLabel("Live commentary", { exact: true }).uncheck();
    for (const width of [1440, 390, 1000]) {
      await page.setViewportSize({ width, height: 900 });
      await expect.poll(async () => page.evaluate(() => {
        const video = document.querySelector<HTMLVideoElement>(".replay-video-stage video");
        const overlay = document.querySelector<SVGSVGElement>(".image-tracking-layer");
        if (!video || !overlay) return Infinity;
        const frame = video.getBoundingClientRect(), rect = overlay.getBoundingClientRect();
        const scale = Math.min(frame.width / video.videoWidth, frame.height / video.videoHeight);
        const expectedWidth = video.videoWidth * scale, expectedHeight = video.videoHeight * scale;
        return Math.max(Math.abs(rect.width - expectedWidth), Math.abs(rect.height - expectedHeight),
          Math.abs(rect.left - frame.left - (frame.width - expectedWidth) / 2),
          Math.abs(rect.top - frame.top - (frame.height - expectedHeight) / 2));
      })).toBeLessThan(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    expect(await fixture.source.evaluate((element) => (element as HTMLVideoElement).videoWidth)).toBe(Number(size.split("x")[0]));
    await expect(page.getByRole("button", { name: "2.5D scene", exact: true })).toBeDisabled();
    await expect(page.getByRole("img", { name: "Image-space player and ball candidates", exact: true })).toBeVisible();
  });
}
