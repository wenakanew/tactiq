type Owner = { tenant_id: string; subject: string };
export type WorkspaceReference = { owner: Owner; matchId: string | null; jobId: string | null };
export const WORKSPACE_REFERENCE_KEY = "tactiq.workspace.reference.v1";

function validId(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && /^[A-Za-z0-9_-]{1,256}$/.test(value));
}

export function readWorkspaceReference(storage: Pick<Storage, "getItem" | "removeItem">, owner: Owner): WorkspaceReference | null {
  const raw = storage.getItem(WORKSPACE_REFERENCE_KEY);
  if (raw === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    storage.removeItem(WORKSPACE_REFERENCE_KEY);
    throw new Error("Your previous workspace shortcut was invalid and has been cleared.");
  }
  if (!parsed || typeof parsed !== "object") {
    storage.removeItem(WORKSPACE_REFERENCE_KEY);
    throw new Error("Your previous workspace shortcut was invalid and has been cleared.");
  }
  const value = parsed as Record<string, unknown>;
  const savedOwner = value.owner;
  if (!savedOwner || typeof savedOwner !== "object" || !("tenant_id" in savedOwner) || !("subject" in savedOwner)) {
    storage.removeItem(WORKSPACE_REFERENCE_KEY);
    throw new Error("Your previous workspace shortcut was invalid and has been cleared.");
  }
  if (savedOwner.tenant_id !== owner.tenant_id || savedOwner.subject !== owner.subject) {
    storage.removeItem(WORKSPACE_REFERENCE_KEY);
    return null;
  }
  if (!validId(value.matchId) || !validId(value.jobId)) {
    storage.removeItem(WORKSPACE_REFERENCE_KEY);
    throw new Error("Your previous workspace shortcut was invalid and has been cleared.");
  }
  return { owner, matchId: value.matchId, jobId: value.jobId };
}

export function saveWorkspaceReference(storage: Pick<Storage, "setItem" | "removeItem">, reference: WorkspaceReference): void {
  if (!validId(reference.matchId) || !validId(reference.jobId)) throw new Error("Workspace identifiers are invalid.");
  if (reference.matchId === null && reference.jobId === null) storage.removeItem(WORKSPACE_REFERENCE_KEY);
  else storage.setItem(WORKSPACE_REFERENCE_KEY, JSON.stringify({
    owner: { tenant_id: reference.owner.tenant_id, subject: reference.owner.subject },
    matchId: reference.matchId, jobId: reference.jobId,
  }));
}
