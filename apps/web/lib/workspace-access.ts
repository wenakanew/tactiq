export type Principal = { tenant_id: string; subject: string; role: "viewer" | "analyst" | "admin" };
export type Trial = { remaining: number | null; limit: number; reset_at: number | null };
export type PublicTrialCapability = {
  enabled: true; microsoft_login_enabled: boolean; owned_speech_required: boolean;
};
export type WorkspaceAccess = {
  status: "checking" | "local" | "anonymous" | "guest" | "authenticated" | "error";
  principal: Principal | null;
  cloud?: PublicTrialCapability;
  trial?: Trial;
  expiresAt?: number;
};

export function principalFrom(value: unknown): Principal {
  if (!value || typeof value !== "object") throw new Error("Workspace access returned an incomplete response.");
  const fields = value as Record<string, unknown>;
  const role = fields.role;
  if (typeof fields.tenant_id !== "string" || !fields.tenant_id || typeof fields.subject !== "string" || !fields.subject
    || (role !== "viewer" && role !== "analyst" && role !== "admin")) {
    throw new Error("Workspace access returned an incomplete response.");
  }
  return { tenant_id: fields.tenant_id, subject: fields.subject, role };
}

export function cloudSessionFrom(value: unknown, cloud: PublicTrialCapability): WorkspaceAccess {
  if (!value || typeof value !== "object" || cloud.enabled !== true || typeof cloud.microsoft_login_enabled !== "boolean"
    || cloud.owned_speech_required !== true) throw new Error("Your trial session could not be verified.");
  const fields = value as Record<string, unknown>;
  const trial = fields.trial as Trial | undefined;
  const guest = fields.kind === "guest";
  if ((!guest && fields.kind !== "member") || !trial || trial.limit !== 3
    || typeof fields.expires_at !== "number" || !Number.isFinite(fields.expires_at) || fields.expires_at <= 0
    || (guest ? !Number.isInteger(trial.remaining) || trial.remaining! < 0 || trial.remaining! > trial.limit
      : trial.remaining !== null)
    || (trial.reset_at !== null && (typeof trial.reset_at !== "number" || !Number.isFinite(trial.reset_at) || trial.reset_at <= 0))) {
    throw new Error("Your trial session could not be verified.");
  }
  return { status: guest ? "guest" : "authenticated", principal: principalFrom(fields.principal),
    cloud, trial, expiresAt: fields.expires_at };
}

export function mayRead(access: WorkspaceAccess): boolean {
  return access.status === "local" || access.status === "authenticated" || access.status === "guest";
}

export function mayAnalyze(access: WorkspaceAccess): boolean {
  return mayRead(access) && (access.principal?.role === "analyst" || access.principal?.role === "admin");
}

export function mayStartAnalysis(access: WorkspaceAccess, admissionEnabled: boolean | undefined,
  pausedUntil = 0, now = Date.now()): boolean {
  return mayAnalyze(access) && (!access.cloud || admissionEnabled === true) && pausedUntil <= now;
}

export function retryAfterDeadline(value: string | null, now = Date.now()): number {
  const header = value?.trim() ?? "";
  const maximum = now + 86_400_000;
  if (/^\d+$/.test(header)) {
    const seconds = Number(header);
    if (Number.isFinite(seconds)) return Math.min(maximum, now + seconds * 1000);
  }
  const date = /^[A-Za-z]{3},/.test(header) ? Date.parse(header) : Number.NaN;
  return Number.isFinite(date) ? Math.min(maximum, Math.max(now, date)) : now + 60_000;
}

export function mayInspect(access: WorkspaceAccess): boolean {
  return access.status !== "guest" && mayAnalyze(access);
}

export function sessionTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
