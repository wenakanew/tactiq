"use client";

import { useEffect, useRef, useState } from "react";
import type { Group, OrthographicCamera, Scene, WebGLRenderer } from "three";
import type { ReplayFrame, TrackedEntity, TrackingSnapshot } from "../lib/match-types";
import { frameIndexAt, trajectoryPoints } from "../lib/replay";
import TrackedPitch from "./TrackedPitch";

type Props = { tracking: TrackingSnapshot; frames: ReplayFrame[]; timeMs: number };

export default function WorldPitch({ tracking, frames, timeMs }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const renderFrame = useRef<((state: TrackingSnapshot, history: ReplayFrame[], clock: number) => void) | null>(null);
  const latest = useRef({ tracking, frames, timeMs });
  const [error, setError] = useState("");
  latest.current = { tracking, frames, timeMs };

  useEffect(() => {
    let disposed = false;
    let renderer: WebGLRenderer | undefined;
    let scene: Scene | undefined;
    let camera: OrthographicCamera | undefined;
    let observer: ResizeObserver | undefined;
    const root = container.current;
    if (!root) return;
    void import("three").then((THREE) => {
      if (disposed) return;
      try {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.domElement.setAttribute("role", "img");
        renderer.domElement.setAttribute("aria-label", "Ground-plane reconstruction from calibrated player and ball coordinates");
        root.appendChild(renderer.domElement);
        scene = new THREE.Scene();
        scene.background = new THREE.Color("#0a1c16");
        camera = new THREE.OrthographicCamera(-70, 70, 48, -48, 0.1, 500);
        camera.position.set(65, 95, 88);
        camera.lookAt(0, 0, 0);
        scene.add(new THREE.AmbientLight(0xffffff, 2));
        const field = new THREE.Mesh(
          new THREE.PlaneGeometry(105, 68), new THREE.MeshBasicMaterial({ color: "#0c3d29", side: THREE.DoubleSide }),
        );
        field.rotation.x = -Math.PI / 2;
        scene.add(field);
        const lineMaterial = new THREE.LineBasicMaterial({ color: "#8ebba2" });
        const line = (points: Array<[number, number, number]>) => {
          const geometry = new THREE.BufferGeometry().setFromPoints(points.map((point) => new THREE.Vector3(...point)));
          scene!.add(new THREE.Line(geometry, lineMaterial));
        };
        line([[-52.5, 0.02, -34], [52.5, 0.02, -34], [52.5, 0.02, 34], [-52.5, 0.02, 34], [-52.5, 0.02, -34]]);
        line([[0, 0.02, -34], [0, 0.02, 34]]);
        line(Array.from({ length: 65 }, (_, i) => {
          const angle = i / 64 * Math.PI * 2;
          return [Math.cos(angle) * 9.15, 0.02, Math.sin(angle) * 9.15] as [number, number, number];
        }));
        const actors = new Map<string, Group>();
        const trailMaterial = new THREE.LineBasicMaterial({ color: "#ffe69a" });
        let trail: import("three").Line | undefined;
        let trailKey = "";
        renderFrame.current = (state, history, clock) => {
          if (!scene || !renderer || !camera) return;
          renderer.domElement.setAttribute("aria-label", state.geometry_source === "simulation"
            ? "Simulated ground-plane player and ball positions"
            : "Ground-plane reconstruction from calibrated player and ball coordinates");
          const entities: TrackedEntity[] = [
            ...(state.players ?? []),
            ...(state.ball ? [state.ball] : []),
          ].filter((entity) => entity.position);
          const present = new Set(entities.map((entity) => entity.track_id));
          for (const [id, actor] of actors) {
            if (!present.has(id)) {
              scene.remove(actor);
              actor.traverse((child) => {
                if (child instanceof THREE.Mesh) {
                  child.geometry.dispose();
                  if (child.material instanceof THREE.MeshBasicMaterial) child.material.dispose();
                }
              });
              actors.delete(id);
            }
          }
          for (const entity of entities) {
            let actor = actors.get(entity.track_id);
            if (!actor) {
              actor = new THREE.Group();
              const ball = entity.track_id === state.ball?.track_id;
              const color = ball ? "#fff3be" : entity.team_id === "team_a" ? "#5797ff"
                : entity.team_id === "team_b" ? "#ff588f" : "#b2b6c7";
              const mesh = new THREE.Mesh(
                ball ? new THREE.SphereGeometry(0.32, 12, 8) : new THREE.CylinderGeometry(0.5, 0.65, 1.8, 10),
                new THREE.MeshBasicMaterial({ color, transparent: true }),
              );
              mesh.position.y = ball ? 0.32 : 0.9;
              actor.add(mesh);
              actors.set(entity.track_id, actor);
              scene.add(actor);
            }
            actor.visible = true;
            actor.position.set(entity.position!.x * 1.05 - 52.5, 0, entity.position!.y * 0.68 - 34);
            actor.traverse((child) => {
              if (child instanceof THREE.Mesh && child.material instanceof THREE.MeshBasicMaterial) {
                child.material.opacity = entity.inferred ? 0.35 : 1;
                child.material.color.set(entity.track_id === state.ball?.track_id ? "#fff3be"
                  : entity.team_id === "team_a" ? "#5797ff" : entity.team_id === "team_b" ? "#ff588f" : "#b2b6c7");
              }
            });
          }
          const nextTrailKey = `${state.ball?.track_id ?? ""}:${frameIndexAt(history, clock)}`;
          if (nextTrailKey !== trailKey) {
            trailKey = nextTrailKey;
            if (trail) {
              scene.remove(trail);
              trail.geometry.dispose();
            }
            const points = state.ball ? trajectoryPoints(history, clock, state.ball.track_id) : [];
            if (points.length > 1) {
              const geometry = new THREE.BufferGeometry().setFromPoints(
                points.map((point) => new THREE.Vector3(point.x * 1.05 - 52.5, 0.1, point.y * 0.68 - 34)),
              );
              trail = new THREE.Line(geometry, trailMaterial);
              scene.add(trail);
            } else {
              trail = undefined;
            }
          }
          renderer.render(scene, camera);
        };
        observer = new ResizeObserver(() => {
          if (!renderer || !camera || !root) return;
          const width = root.clientWidth;
          const height = Math.max(240, Math.round(width * 0.6));
          renderer.setSize(width, height, false);
          const aspect = width / height;
          camera.left = -48 * aspect;
          camera.right = 48 * aspect;
          camera.updateProjectionMatrix();
          const current = latest.current;
          renderFrame.current?.(current.tracking, current.frames, current.timeMs);
        });
        observer.observe(root);
        const current = latest.current;
        renderFrame.current(current.tracking, current.frames, current.timeMs);
      } catch {
        setError("3D rendering unavailable on this device. Showing the same observations in 2D.");
      }
    }).catch(() => {
      if (!disposed) setError("Unable to load the 3D renderer. Showing 2D observations.");
    });
    return () => {
      disposed = true;
      observer?.disconnect();
      renderFrame.current = null;
      scene?.traverse((object) => {
        if ("geometry" in object && object.geometry instanceof Object && "dispose" in object.geometry) {
          const geometry = object.geometry as { dispose: () => void };
          geometry.dispose();
        }
        if ("material" in object) {
          const materials = Array.isArray(object.material) ? object.material : [object.material];
          materials.forEach((material) => {
            if (material && typeof material === "object" && "dispose" in material) material.dispose();
          });
        }
      });
      renderer?.dispose();
      renderer?.domElement.remove();
    };
  }, []);

  useEffect(() => {
    renderFrame.current?.(tracking, frames, timeMs);
  }, [tracking, frames, timeMs]);

  if (error) return <><p role="status" className="status-line">{error}</p><TrackedPitch tracking={tracking} frames={frames} timeMs={timeMs} /></>;
  return <div className="world-pitch" ref={container} />;
}
