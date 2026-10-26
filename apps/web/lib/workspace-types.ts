export type Insight = {
  insight_id?: string;
  type?: string;
  title: string;
  summary: string;
  confidence: number;
  evidence: Array<{ metric: string; value: number | string }>;
  clip?: {
    status?: "ready" | "pending" | "failed" | "unavailable";
    url?: string;
    start_ms?: number;
    end_ms?: number;
    duration_ms?: number;
    reason?: string;
    error?: string;
  };
  provenance?: {
    quality?: { eligible: boolean; reason: string };
    reason_code?: string;
    confidence?: number;
    window?: { start_ms?: number; end_ms?: number };
    metrics?: Array<{ metric: string; value: number | string; unit?: string }>;
  };
};

export function clockLabel(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const seconds = Math.floor(value / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function supportedMoments<T extends Pick<Insight, "provenance">>(items: T[]): T[] {
  return items.filter((item) => item.provenance?.quality?.eligible !== false);
}

export function readableLabel(value?: string): string {
  return (value ?? "unknown").replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function teamMonogram(name: string): string {
  const words = name.trim().split(/\s+/);
  return (words.length > 1 ? words.slice(0, 2).map((word) => Array.from(word)[0]) : Array.from(words[0]).slice(0, 2))
    .join("").toLocaleUpperCase() || "?";
}

export function evidenceMetrics(insight: Insight): Array<{ metric: string; value: number | string; unit?: string }> {
  const metrics = new Map<string, { metric: string; value: number | string; unit?: string }>();
  for (const item of insight.evidence ?? []) metrics.set(item.metric, { ...item });
  for (const item of insight.provenance?.metrics ?? []) {
    const existing = metrics.get(item.metric);
    metrics.set(item.metric, existing ? { ...existing, unit: existing.unit ?? item.unit } : { ...item });
  }
  return Array.from(metrics.values());
}

export const CONTENT_LANGUAGES = [
  ["en-GB", "English"], ["sw-KE", "Kiswahili"], ["fr-FR", "Français"],
  ["es-ES", "Español"], ["pt-BR", "Português"], ["ar-SA", "العربية"], ["hi-IN", "हिन्दी"],
] as const;
