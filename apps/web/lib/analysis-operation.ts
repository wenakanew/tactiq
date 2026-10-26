type PendingOperation = { fingerprint: string; id: string };
type OperationStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
export type AnalysisKind = "scenario" | "upload" | "link";

const PREFIX = "tactiq.analysis.operation.v1.";

export class AnalysisOperations {
  private pending = new Map<AnalysisKind, PendingOperation>();
  private storage?: OperationStorage;

  constructor(storage?: OperationStorage) { this.storage = storage; }

  async begin(kind: AnalysisKind, owner: string, input: unknown): Promise<string> {
    const bytes = new TextEncoder().encode(JSON.stringify([owner, input]));
    const fingerprint = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
      .map((byte) => byte.toString(16).padStart(2, "0")).join("");
    let previous = this.pending.get(kind);
    if (!previous) {
      try {
        const raw: unknown = JSON.parse(this.storage?.getItem(PREFIX + kind) ?? "null");
        if (raw && typeof raw === "object" && "fingerprint" in raw && "id" in raw
          && typeof raw.fingerprint === "string" && typeof raw.id === "string"
          && /^[a-f0-9]{64}$/.test(raw.fingerprint) && /^[a-f0-9-]{36}$/.test(raw.id)) {
          previous = { fingerprint: raw.fingerprint, id: raw.id };
        }
      } catch { /* Storage is optional; retries still share the in-memory operation. */ }
    }
    const operation = previous?.fingerprint === fingerprint ? previous : { fingerprint, id: crypto.randomUUID() };
    this.pending.set(kind, operation);
    try { this.storage?.setItem(PREFIX + kind, JSON.stringify(operation)); } catch { /* Keep the in-memory operation. */ }
    return operation.id;
  }

  complete(kind: AnalysisKind) {
    this.pending.delete(kind);
    try { this.storage?.removeItem(PREFIX + kind); } catch { /* Storage may be unavailable. */ }
  }

  clear() {
    for (const kind of ["scenario", "upload", "link"] as const) this.complete(kind);
  }
}
