"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { mediaUrl } from "../../lib/api";

export default function EvidenceClip({ path, label, active = true }: { path: string; label: string; active?: boolean }) {
  const [failed, setFailed] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const source = useMemo(() => {
    try {
      const resolved = mediaUrl(path);
      const protocol = new URL(resolved).protocol;
      return protocol === "http:" || protocol === "https:" ? resolved : null;
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
      return null;
    }
  }, [path]);
  useEffect(() => {
    setFailed(false);
    const element = video.current;
    return () => {
      element?.pause();
      if (element && !element.isConnected) {
        element.removeAttribute("src");
        element.load();
      }
    };
  }, [source]);
  useEffect(() => { if (!active) video.current?.pause(); }, [active, source]);
  if (!source) return <p className="notice" role="alert">The evidence clip has an invalid media reference.</p>;
  return (
    <div className="hero-clip">
      <video ref={video} className="clip-player" controls preload="none" crossOrigin="use-credentials" aria-label={label}
        src={source} onError={() => setFailed(true)} />
      {failed ? <p className="notice" role="alert">This evidence clip could not be played. Check source availability and your workspace access.</p> : null}
    </div>
  );
}
