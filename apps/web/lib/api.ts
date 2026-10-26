const configuredBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").trim().replace(/\/+$/, "");
export const API_BASE = configuredBase || (typeof window !== "undefined" ? window.location.origin : "");

export function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const url = path.startsWith("http://") || path.startsWith("https://") ? path : `${API_BASE}${path}`;
  return fetch(url, { ...options, credentials: "include", cache: "no-store" });
}

export async function responseError(response: Response): Promise<string> {
  const payload: unknown = await response.json().catch(() => null);
  if (payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string") {
    return payload.detail;
  }
  if (payload && typeof payload === "object" && "detail" in payload && payload.detail
    && typeof payload.detail === "object" && "message" in payload.detail && typeof payload.detail.message === "string") {
    return payload.detail.message;
  }
  return `Request failed (${response.status})`;
}

export function mediaUrl(path: string): string {
  return new URL(path, API_BASE || window.location.origin).toString();
}
