"use client";

import { useEffect, useState } from "react";
import { apiFetch, responseError } from "../lib/api";

type Metrics = { requests_total?: number; errors_total?: number; duration_seconds_p95?: number };
type Status = {
  status: string; http: Metrics; pipeline: Metrics; jobs: Metrics;
  alerts: Array<{ name: string; severity: string; measured: number; threshold: number }>;
};

export default function OperationsPanel({ revision }: { revision: number }) {
  const [admin, setAdmin] = useState(false);
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void apiFetch("/api/v1/session", { signal: controller.signal }).then(async (response) => {
      if (response.ok) {
        const principal = await response.json() as { role: string };
        if (!controller.signal.aborted) setAdmin(principal.role === "admin");
      } else if (!controller.signal.aborted) setAdmin(false);
    }).catch(() => {
      if (!controller.signal.aborted) setError("Unable to check operational access.");
    });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    if (!admin || !open) return;
    const controller = new AbortController();
    async function load() {
      try {
        const response = await apiFetch("/api/v1/operations/status", { signal: controller.signal });
        if (!response.ok) throw new Error(await responseError(response));
        const next = await response.json() as Status;
        if (!controller.signal.aborted) { setStatus(next); setError(""); }
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load operational status.");
      }
    }
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [admin, open]);

  if (!admin) return null;
  return (
    <details className="advanced-panel" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Operations · administrator</summary>
      <div className="operations-panel">
        {error ? <p role="alert" className="error-notice">{error}</p> : null}
        {status ? (
          <>
            <p><strong>SLO state:</strong> {status.status.replace(/_/g, " ")}</p>
            <div className="overview-grid">
              <div className="metric-card"><span>HTTP requests</span><strong>{status.http.requests_total ?? 0}</strong></div>
              <div className="metric-card"><span>HTTP p95</span><strong>{((status.http.duration_seconds_p95 ?? 0) * 1000).toFixed(0)} ms</strong></div>
              <div className="metric-card"><span>Event processing p95</span><strong>{((status.pipeline.duration_seconds_p95 ?? 0) * 1000).toFixed(0)} ms</strong></div>
              <div className="metric-card"><span>Job failures</span><strong>{status.jobs.errors_total ?? 0}</strong></div>
            </div>
            {status.alerts.length ? <ul className="analysis-notices">
              {status.alerts.map((alert) => <li key={alert.name}>{alert.severity}: {alert.name.replace(/_/g, " ")}
                {" · "}{alert.measured.toFixed(3)} / {alert.threshold}</li>)}
            </ul> : null}
            <p className="status-line">Bounded measurements from this process. These counters do not certify deployment readiness.</p>
          </>
        ) : <p role="status">Loading operational measurements...</p>}
      </div>
    </details>
  );
}
