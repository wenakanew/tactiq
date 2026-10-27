"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Loader2, LockKeyhole, LogOut } from "lucide-react";
import { API_BASE, apiFetch, responseError } from "../lib/api";
import { cloudSessionFrom, principalFrom, sessionTime, type WorkspaceAccess } from "../lib/workspace-access";
import type { ServiceCapabilities } from "../lib/service-capabilities";
export type { Principal, WorkspaceAccess } from "../lib/workspace-access";

export default function SessionAccess({ onSessionChanged, onAccessChanged, onCapabilitiesChanged, refreshRevision = 0, signInRequired = false }: {
  onSessionChanged: (clearPendingOperations?: boolean) => void;
  onAccessChanged?: (access: WorkspaceAccess) => void;
  onCapabilitiesChanged?: (capabilities: ServiceCapabilities) => void;
  refreshRevision?: number;
  signInRequired?: boolean;
}) {
  const [access, setAccess] = useState<WorkspaceAccess>({ status: "checking", principal: null });
  const [credential, setCredential] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const current = useRef(access);
  current.current = access;
  const initialized = useRef(false);
  const sessionChanged = useRef(onSessionChanged);
  sessionChanged.current = onSessionChanged;

  useEffect(() => {
    const controller = new AbortController();
    function update(next: WorkspaceAccess) {
      const previous = current.current;
      if (previous.principal && (previous.principal.tenant_id !== next.principal?.tenant_id
        || previous.principal.subject !== next.principal?.subject)) onSessionChanged(next.status !== "error");
      current.current = next;
      setAccess(next);
      onAccessChanged?.(next);
    }
    if (!initialized.current) update({ status: "checking", principal: null });
    setMessage("");
    void (async () => {
      try {
        const capabilities = await apiFetch("/api/v1/capabilities", { signal: controller.signal });
        if (!capabilities.ok) throw new Error(await responseError(capabilities));
        const config = await capabilities.json() as ServiceCapabilities;
        if (controller.signal.aborted) return;
        onCapabilitiesChanged?.(config);
        if (config.public_trial?.enabled === true) {
          // This endpoint reuses guest/member sessions and only issues a guest when needed.
          const session = await apiFetch("/api/v1/session/guest", { method: "POST", signal: controller.signal });
          if (!session.ok) throw new Error(await responseError(session));
          const next = cloudSessionFrom(await session.json(), config.public_trial);
          if (!controller.signal.aborted) update(next);
          return;
        }
        if (config.auth_enabled === false) {
          update({ status: "local", principal: { tenant_id: "local", subject: "Local workspace", role: "admin" } });
          return;
        }
        if (config.auth_enabled !== true) throw new Error("Workspace availability could not be verified.");
        const session = await apiFetch("/api/v1/session", { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (session.ok) {
          const principal = principalFrom(await session.json());
          if (!controller.signal.aborted) update({ status: "authenticated", principal });
        } else if (session.status === 401) update({ status: "anonymous", principal: null });
        else throw new Error(await responseError(session));
      } catch (error) {
        if (!controller.signal.aborted) {
          update({ status: "error", principal: null });
          setMessage(error instanceof Error ? error.message : "Unable to reach the workspace.");
        }
      } finally {
        if (!controller.signal.aborted) initialized.current = true;
      }
    })();
    return () => controller.abort();
  }, [retry, refreshRevision, onAccessChanged, onCapabilitiesChanged]);

  useEffect(() => {
    if (!access.cloud || !access.expiresAt) return;
    const check = () => {
      if (Date.now() >= access.expiresAt! * 1000) {
        sessionChanged.current();
        setRetry((value) => value + 1);
      }
    };
    const timer = setInterval(check, 30_000);
    const onFocus = () => setRetry((value) => value + 1);
    window.addEventListener("focus", onFocus);
    return () => { clearInterval(timer); window.removeEventListener("focus", onFocus); };
  }, [access.cloud, access.expiresAt]);

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const response = await apiFetch("/api/v1/session", {
        method: "POST", headers: { Authorization: `Bearer ${credential}` },
      });
      if (!response.ok) throw new Error(await responseError(response));
      const principal = principalFrom(await response.json());
      onSessionChanged();
      const next: WorkspaceAccess = { status: "authenticated", principal };
      setAccess(next);
      onAccessChanged?.(next);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Sign-in failed.");
    } finally {
      setCredential("");
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    try {
      const response = await apiFetch("/api/v1/session/logout", { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response));
      onSessionChanged();
      const next: WorkspaceAccess = { status: access.cloud ? "checking" : "anonymous", principal: null };
      setAccess(next);
      onAccessChanged?.(next);
      if (access.cloud) setRetry((value) => value + 1);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Sign-out failed.");
    } finally {
      setBusy(false);
    }
  }

  if (access.status === "checking") return (
    <section className="access-card" aria-label="Workspace access" role="status">
      <Loader2 size={22} className="icon-spin" aria-hidden="true" />
      <h1>Opening your workspace.</h1><p>Checking your connection and workspace access.</p>
    </section>
  );

  return (
    <section className={access.status === "anonymous" || access.status === "error" ? "access-card" : "session-access"} aria-label="Workspace access">
      {access.status === "local" ? <span className="local-label"><i aria-hidden="true" />Local mode · access control is disabled</span> : null}
      {access.status === "guest" && access.trial ? (
        <div className="trial-access">
          <div>
            <p className="eyebrow">Your private guest workspace</p>
            <strong>{access.trial.remaining} of {access.trial.limit} free analyses remaining</strong>
            <p>Three new analyses per IP address per 24 hours. Replay, Listen and reopening a match do not use another analysis.</p>
            <p>{access.trial.reset_at ? <>Allowance resets {sessionTime(access.trial.reset_at)}.</> : "Your 24-hour allowance begins with your first analysis."}
              {" "}Guest analyses expire after 24 hours{access.expiresAt ? ` (this session: ${sessionTime(access.expiresAt)})` : ""}. Sign in to keep them.</p>
            {signInRequired || access.trial.remaining === 0 ? <p className="trial-signin-notice" role="status">Your free trial allowance has been used. Sign in with Microsoft to start another analysis, or return after the allowance resets. Your existing matches remain available until guest expiry.</p> : null}
          </div>
          <div className="trial-actions">
            {access.cloud?.microsoft_login_enabled ? <a className="btn btn--primary btn--sm"
              href={`${API_BASE}/api/v1/auth/microsoft/login?return_to=%2Fworkspace`}>Sign in with Microsoft<ArrowRight size={14} aria-hidden="true" /></a>
              : <p>Microsoft sign-in is not configured on this server. Return after the allowance resets to start another analysis.</p>}
            <span>Free · personal, work or school account</span>
          </div>
        </div>
      ) : null}
      {access.status === "authenticated" && access.principal ? (
        <div className="session-controls">
          <div className="session-identity"><strong>{access.principal.subject}</strong><span>{access.principal.role} workspace</span></div>
          <button className="btn btn--quiet btn--sm" type="button" disabled={busy} onClick={() => void signOut()}><LogOut size={14} aria-hidden="true" />{busy ? "Signing out..." : "Sign out"}</button>
        </div>
      ) : access.status === "anonymous" ? (
        <>
          <p className="eyebrow"><LockKeyhole size={14} aria-hidden="true" />Your private match space</p>
          <h1>Welcome to the workspace.</h1>
          <p>Use the access token provided by your workspace administrator. Your selected demo or video flow will be waiting for you.</p>
          <form onSubmit={(event) => void signIn(event)}>
            <label htmlFor="session-credential">Workspace access token</label>
            <input id="session-credential" type="password" autoComplete="off" value={credential}
              onChange={(event) => setCredential(event.target.value)} required />
            <button className="btn btn--primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}<ArrowRight size={15} aria-hidden="true" /></button>
          </form>
          <p className="status-line">Secure session access. Your token is never saved in browser storage.</p>
        </>
      ) : access.status === "error" ? (
        <><h1>Let&apos;s reconnect.</h1><p>The landing page is available, but the analysis service could not be reached.</p>
          <button type="button" className="btn btn--primary" onClick={() => setRetry((value) => value + 1)}>Retry connection</button></>
      ) : null}
      {message ? <p role="alert" className="error-notice">{message}</p> : null}
    </section>
  );
}
