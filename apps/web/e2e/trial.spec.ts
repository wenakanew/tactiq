import { expect, test, type Page } from "@playwright/test";

// These are browser contract fixtures, not a live Microsoft identity or cloud-runtime test.
async function trialFixture(page: Page, initialRemaining = 3, admissionEnabled = true) {
  let established = false, member = false, remaining = initialRemaining, creates = 0, guestInitializations = 0;
  let sessionReads = 0;
  let workspaceOrigin = "";
  let failNextCreate = false;
  let failNextCapacity = false, sessionUnavailable = false, unavailableSessions = 0;
  const ids: string[] = [];
  let memberIds: string[] = [];
  const operationIds: string[] = [];
  const protectedReads: string[] = [];
  const state = (id: string) => ({ match_id: id, scenario_id: "sustained_pressure", status: "completed",
    match_clock_ms: 1000, score: { team_a: 0, team_b: 0 }, tracking_sample_count: 2, last_sequence: 2 });
  const session = () => ({ kind: member ? "member" : "guest",
    principal: { tenant_id: member ? "member-workspace" : "guest-browser", subject: member ? "Microsoft fixture" : "guest", role: "analyst" },
    trial: { remaining: member ? null : remaining, limit: 3, reset_at: remaining < 3 ? Date.now() / 1000 + 3600 : null },
    expires_at: Date.now() / 1000 + 86400 });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (path.includes("/debug") || path.includes("/operations")) protectedReads.push(path);
    if (path === "/api/v1/capabilities") return route.fulfill({ json: {
      auth_enabled: true, public_trial: { enabled: true, microsoft_login_enabled: true, owned_speech_required: true },
      paginated_streams: true, paid_generation_enabled: false, speech_configured: false, youtube_import_configured: true,
      analysis_admission_enabled: admissionEnabled,
    } });
    if (path === "/api/v1/session") {
      sessionReads += 1;
      return route.fulfill(established ? { json: session() } : { status: 401, json: { detail: "No session" } });
    }
    if (path === "/api/v1/session/guest") {
      if (sessionUnavailable) {
        unavailableSessions += 1;
        return route.fulfill({ status: 503, json: { detail: {
          code: "database_capacity", message: "Database request capacity is full; retry the same operation",
        } }, headers: { "Retry-After": "1", "Access-Control-Expose-Headers": "Retry-After" } });
      }
      established = true; guestInitializations += 1;
      return route.fulfill({ json: session() });
    }
    if (path === "/api/v1/session/logout") {
      member = false; established = false;
      ids.length = 0;
      return route.fulfill({ status: 204 });
    }
    if (path === "/api/v1/auth/microsoft/login") {
      expect(url.searchParams.get("return_to")).toBe("/workspace");
      expect([...url.searchParams.keys()]).toEqual(["return_to"]);
      member = true; established = true;
      memberIds = [...new Set([...memberIds, ...ids])];
      ids.length = 0;
      return route.fulfill({ status: 302, headers: { location: new URL("/workspace", workspaceOrigin).href } });
    }
    if (path === "/api/v1/scenarios") return route.fulfill({ json: [{ scenario_id: "sustained_pressure", total_ms: 1000 }] });
    if (path === "/api/v1/matches" && request.method() === "GET") return route.fulfill({ json: {
      matches: (member ? memberIds : ids).map((id) => ({ match_id: id, revision: 1, state: state(id) })), next_after: null,
    } });
    if (path === "/api/v1/matches" && request.method() === "POST") {
      operationIds.push(request.headers()["idempotency-key"]);
      if (failNextCreate) { failNextCreate = false; return route.abort(); }
      if (failNextCapacity) {
        failNextCapacity = false; sessionUnavailable = true;
        return route.fulfill({ status: 503, json: { detail: {
          code: "database_capacity", message: "Database request capacity is full; retry the same operation",
        } }, headers: { "Retry-After": "1", "Access-Control-Expose-Headers": "Retry-After" } });
      }
      if (!member && remaining === 0) return route.fulfill({ status: 403, json: {
        detail: { code: "sign_in_required", message: "Microsoft sign-in is required for another new analysis", reset_at: Date.now() / 1000 + 3600 },
      } });
      creates += 1;
      if (!member) remaining -= 1;
      const id = `match_trial_${creates}`; (member ? memberIds : ids).push(id);
      return route.fulfill({ status: 202, json: { match_id: id, job_id: `job_trial_${creates}`, status: "queued" } });
    }
    const job = path.match(/\/jobs\/job_trial_(\d+)$/);
    if (job) return route.fulfill({ json: { job_id: `job_trial_${job[1]}`, match_id: `match_trial_${job[1]}`,
      status: "succeeded", result: { match_id: `match_trial_${job[1]}`, event_count: 2, insight_count: 0 } } });
    const match = path.match(/\/matches\/(match_trial_\d+)\/(.+)$/);
    if (match) {
      if (match[2] === "state") return route.fulfill({ json: state(match[1]) });
      if (match[2] === "insights") return route.fulfill({ json: { insights: [] } });
      if (match[2] === "narratives") return route.fulfill({ json: { title: "Fixture", body: "A simulated match.",
        audience_mode: "fan", language: "en-GB", provider: "template", fallback_used: true } });
      if (match[2] === "replay") return route.fulfill({ json: {
        match_id: match[1], duration_ms: 1000, processed_until_ms: 1000, source_url: null, team_display_names: {},
        metadata: {}, insights: [], cursor: { revision: 1, after: "frame2" }, has_more: false,
        records: [0, 1000].map((time, index) => ({ record_id: `frame${index + 1}`, payload: {
          timestamp_ms: time, sequence: index + 1, score: { team_a: 0, team_b: 0 }, possession_team_id: null,
          tracking: { players: [], ball: null, coordinate_space: "pitch", geometry_source: "simulation" }, tactics: {},
        } })),
      } });
    }
    return route.fulfill({ status: 404, json: { detail: "Fixture endpoint unavailable" } });
  });
  return {
    async signIn() {
      const initialized = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/session/guest"
        && response.request().method() === "POST");
      await page.getByRole("link", { name: "Sign in with Microsoft", exact: true }).click();
      expect((await initialized).status()).toBe(200);
    },
    get creates() { return creates; }, get guestInitializations() { return guestInitializations; },
    get sessionReads() { return sessionReads; }, get unavailableSessions() { return unavailableSessions; },
    operationIds, protectedReads, failNext() { failNextCreate = true; },
    failCapacity() { failNextCapacity = true; },
    recoverSession() { sessionUnavailable = false; },
    addOwnedMatch(id: string) { ids.push(id); },
    async open(path = "/workspace") {
      const initialized = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/session/guest"
        && response.request().method() === "POST");
      await page.goto(path, { waitUntil: "domcontentloaded" });
      workspaceOrigin = new URL(page.url()).origin;
      const response = await initialized;
      expect(response.status()).toBe(200);
      await response.finished();
    },
  };
}

async function start(page: Page, number: number) {
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.locator("#match-stage")).toHaveAttribute("data-match-id", `match_trial_${number}`);
}

test("guest trial initializes explicitly, admits three analyses and offers fixed Microsoft redirect on the fourth", async ({ page }) => {
  const fixture = await trialFixture(page);
  await fixture.open("/workspace?start=demo");
  await expect(page.getByText("3 of 3 free analyses remaining", { exact: true })).toBeVisible();
  expect(fixture.sessionReads).toBe(0);
  await expect(page.getByText(/Guest analyses expire after 24 hours/)).toBeVisible();
  await expect(page.getByLabel("Workspace access token", { exact: true })).toHaveCount(0);
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  for (let number = 1; number <= 3; number += 1) {
    if (number > 1) await page.getByRole("button", { name: "New match", exact: true }).click();
    await start(page, number);
    await expect(page.getByText(`${3 - number} of 3 free analyses remaining`, { exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: "New match", exact: true }).click();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.getByText(/Microsoft sign-in is required for another new analysis/, { exact: true })).toBeVisible();
  expect(fixture.creates).toBe(3);
  expect(new Set(fixture.operationIds).size).toBe(4);
  expect(fixture.guestInitializations).toBeGreaterThan(0);
  expect(fixture.protectedReads).toEqual([]);
  await fixture.signIn();
  await expect(page.getByText("Microsoft fixture", { exact: true })).toBeVisible();
  await expect(page.getByText(/free analyses remaining/)).toHaveCount(0);
  await page.getByRole("button", { name: "Saved matches", exact: true }).click();
  await expect(page.locator(".saved-match")).toHaveCount(3);
  expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }))).not.toMatch(/token|credential|authorization/i);
});

test("unapproved processing allocation disables only new analyses while owned history and replay stay open", async ({ page }) => {
  const fixture = await trialFixture(page, 3, false);
  fixture.addOwnedMatch("match_trial_9");
  await fixture.open();
  await expect(page.getByText(/New analyses are unavailable until this server enables an approved processing allocation/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Start live scenario", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Your video", exact: true }).click();
  await page.getByLabel("Upload video file", { exact: true }).setInputFiles({
    name: "fixture.mp4", mimeType: "video/mp4", buffer: Buffer.from("transport fixture only"),
  });
  await page.getByLabel("Video link · YouTube or direct file", { exact: true }).fill("https://example.com/authorized.mp4");
  await expect(page.getByRole("button", { name: "Upload and analyze", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Analyze from link", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Saved matches", exact: true }).click();
  await page.locator(".saved-match").click();
  await expect(page.locator("#match-stage")).toHaveAttribute("data-match-id", "match_trial_9");
  await expect(page.getByRole("button", { name: "Play replay", exact: true })).toBeEnabled();
  expect(fixture.creates).toBe(0);
  await expect(page.getByText("3 of 3 free analyses remaining", { exact: true })).toBeVisible();
});

test("transport retries reuse an operation; replay, reopening and browser navigation do not start another analysis", async ({ page }) => {
  const fixture = await trialFixture(page);
  // Install before the app loads so React and the workspace timers share one controllable clock.
  await page.clock.install();
  await fixture.open();
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  fixture.failNext();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  await expect(page.getByText(/Failed to fetch|NetworkError|Load failed/)).toBeVisible();
  await start(page, 1);
  expect(fixture.operationIds.length).toBe(2);
  expect(fixture.operationIds[0]).toBe(fixture.operationIds[1]);
  await page.getByRole("button", { name: "Play replay", exact: true }).click();
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/");
  await expect(page.getByRole("heading", { name: "Watch the game. Understand the why.", exact: true })).toBeVisible();
  await fixture.open();
  await expect(page.locator("#match-stage")).toHaveAttribute("data-match-id", "match_trial_1");
  expect(fixture.creates).toBe(1);
  expect(fixture.protectedReads).toEqual([]);
  await page.getByRole("button", { name: "New match", exact: true }).click();
  await page.clock.pauseAt(new Date(Date.now() + 2_000));
  fixture.failCapacity();
  await page.getByRole("button", { name: "Start live scenario", exact: true }).click();
  // Losing the guest principal resets the workspace, which makes one automatic session attempt.
  // Recover only after that attempt has also failed so the explicit retry is the one that succeeds.
  await expect.poll(() => fixture.unavailableSessions).toBeGreaterThanOrEqual(2);
  await expect(page.getByRole("heading", { name: "Let's reconnect.", exact: true })).toBeVisible();
  fixture.recoverSession();
  await page.getByRole("button", { name: "Retry connection", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Let's reconnect.", exact: true })).toHaveCount(0);
  await page.clock.runFor(1100);
  await page.clock.resume();
  await expect(page.getByRole("button", { name: "Start live scenario", exact: true })).toBeEnabled();
  await start(page, 2);
  expect(fixture.operationIds[2]).toBe(fixture.operationIds[3]);
  expect(fixture.creates).toBe(2);
});

test("Microsoft fixture sign-out clears private media and identifiers before opening a fresh guest view", async ({ page }) => {
  const fixture = await trialFixture(page);
  await fixture.open();
  await fixture.signIn();
  await page.getByLabel("Live commentary", { exact: true }).uncheck();
  await start(page, 1);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByText(/free analyses remaining/)).toBeVisible();
  await expect(page.locator("#match-stage")).toHaveCount(0);
  await page.getByRole("button", { name: "Saved matches", exact: true }).click();
  await expect(page.locator(".saved-match")).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("tactiq.workspace.reference.v1"))).toBeNull();
  expect(fixture.creates).toBe(1);
});
