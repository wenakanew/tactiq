import type { ReplayFrame, ReplayPayload } from "./match-types";

type Cursor = { revision: string | number; after: string | number | null };
type ReplayPage = Omit<ReplayPayload, "frames"> & {
  records: Array<{ record_id: string | number; payload: ReplayFrame }>;
  cursor: Cursor;
  has_more: boolean;
};

export async function loadCloudReplay(
  matchId: string, after: string | number | null,
  request: (path: string) => Promise<Response>,
): Promise<{ payload: ReplayPayload; after: string | number | null }> {
  let revision: string | number | undefined;
  const frames: ReplayFrame[] = [];
  const seen = new Set<string>();
  for (let page = 0; page < 1000; page += 1) {
    const query = new URLSearchParams({ limit: "100" });
    if (after !== null) query.set("after", String(after));
    if (revision !== undefined) query.set("revision", String(revision));
    const response = await request(`/api/v1/matches/${encodeURIComponent(matchId)}/replay?${query}`);
    if (!response.ok) throw new Error(`Replay unavailable (${response.status}).`);
    const next = await response.json() as ReplayPage;
    if (!next || next.match_id !== matchId || !Array.isArray(next.records) || !next.cursor
      || !["string", "number"].includes(typeof next.cursor.revision)
      || (next.cursor.after !== null && !["string", "number"].includes(typeof next.cursor.after))
      || typeof next.has_more !== "boolean" || !Number.isFinite(next.processed_until_ms)
      || (revision !== undefined && next.cursor.revision !== revision)
      || next.records.some((record) => !record.payload || !Number.isFinite(record.payload.timestamp_ms)
        || !Number.isFinite(record.payload.sequence))) {
      throw new Error("The replay response is incomplete or changed its snapshot.");
    }
    frames.push(...next.records.map((record) => record.payload));
    after = next.cursor.after;
    revision = next.cursor.revision;
    if (!next.has_more) {
      const { records: _records, cursor: _cursor, has_more: _hasMore, ...metadata } = next;
      return { payload: { ...metadata, frames }, after };
    }
    const cursor = JSON.stringify(next.cursor);
    if (!next.records.length || seen.has(cursor)) throw new Error("Replay pagination did not advance.");
    seen.add(cursor);
  }
  throw new Error("This replay exceeds the browser's safe loading limit.");
}
