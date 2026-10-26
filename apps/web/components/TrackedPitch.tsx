"use client";

import type { ReplayFrame, TacticalState, TrackingSnapshot } from "../lib/match-types";
import { hasPitchGeometry, trajectoryPoints } from "../lib/replay";

type Props = {
  tracking?: TrackingSnapshot;
  frames?: ReplayFrame[];
  timeMs?: number;
  selectedPlayer?: string;
  onSelectPlayer?: (trackId: string) => void;
  lanes?: TacticalState["lanes"];
};

export default function TrackedPitch({ tracking, frames = [], timeMs = 0, selectedPlayer, onSelectPlayer, lanes }: Props) {
  const ball = tracking?.ball;
  const physical = tracking ? hasPitchGeometry(tracking) : false;
  const eventPitch = tracking?.tracking_source === "event_stream_v1" && tracking?.detection_source === "synthetic";
  const pitch = physical || eventPitch;
  const width = pitch ? 105 : tracking?.frame_width ?? 100;
  const height = pitch ? 68 : tracking?.frame_height ?? 100;
  const unit = Math.min(width, height) / 68;
  const ballPosition = pitch ? ball?.position ?? (eventPitch ? tracking?.ball_position : null) : ball?.image_position;
  const players = (tracking?.players ?? []).filter((player) => pitch ? player.position : player.image_position);
  const trail = ball ? trajectoryPoints(frames, timeMs, ball.track_id, pitch ? "pitch" : "image") : [];
  const px = (value: number) => value / 100 * width;
  const py = (value: number) => value / 100 * height;
  return (
    <svg className="tracked-pitch" viewBox={`${-3 * unit} ${-3 * unit} ${width + 6 * unit} ${height + 6 * unit}`} role="img"
      aria-label={physical ? "Player and ball pitch coordinates" : eventPitch ? "Simulated event-based pitch projection" : "Image-space player and ball candidates"}>
      <title>{pitch ? "Pitch positions" : "Image coordinates, not physical pitch positions"}. Faded markers indicate inferred positions.</title>
      <rect x={-3 * unit} y={-3 * unit} width={width + 6 * unit} height={height + 6 * unit} rx={3 * unit} fill={pitch ? "#0b3022" : "#111e1a"} />
      {physical ? lanes?.team_a?.map((lane, index) => (
        <rect key={lane.lane} x="0" y={index * 68 / 3} width="105" height={68 / 3}
          fill={lane.attackers >= lane.defenders ? "#5f9dff" : "#ff649c"}
          opacity={Math.min(0.2, Math.abs(lane.attackers - lane.defenders) * 0.025)}>
          <title>{lane.lane} channel: {lane.attackers} home and {lane.defenders} away players observed</title>
        </rect>
      )) : null}
      {pitch ? <g fill="none" stroke="#88b9a0" strokeWidth="0.25">
        <rect width="105" height="68" />
        <path d="M52.5 0V68M0 13.84H16.5V54.16H0M105 13.84H88.5V54.16H105" />
        <circle cx="52.5" cy="34" r="9.15" />
      </g> : <rect width={width} height={height} fill="none" stroke="#78988c" strokeWidth={unit * .25} />}
      <g stroke="#88b9a0" strokeWidth="0.2" strokeDasharray="1 2" opacity="0.35">
        {pitch ? <path d="M0 22.67H105M0 45.33H105" /> : null}
      </g>
      {trail.length > 1 ? (
        <polyline fill="none" stroke="#ffde77" strokeWidth={unit * .5}
          points={trail.map((point) => `${px(point.x)},${py(point.y)}`).join(" ")} />
      ) : null}
      {players.map((player) => {
        const point = (pitch ? player.position : player.image_position)!;
        return (
          <g key={player.track_id}
            role={onSelectPlayer ? "button" : undefined}
            tabIndex={onSelectPlayer ? 0 : undefined}
            aria-label={`${player.track_id}, ${player.team_id ?? "unassigned team"}${player.inferred ? ", inferred" : ""}`}
            onClick={() => onSelectPlayer?.(player.track_id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelectPlayer?.(player.track_id);
              }
            }}>
            <circle cx={px(point.x)} cy={py(point.y)} r={unit * 1.2}
              fill={player.team_id === "team_a" ? "#5f9dff" : player.team_id === "team_b" ? "#ff649c" : "#b7bccc"}
              opacity={player.inferred ? 0.4 : 1}
              stroke={selectedPlayer === player.track_id ? "#ffe173" : "#fff"}
              strokeWidth={unit * (selectedPlayer === player.track_id ? .6 : .15)} />
          </g>
        );
      })}
      {ballPosition ? (
        <circle cx={px(ballPosition.x)} cy={py(ballPosition.y)} r={unit * .75}
          fill="#fff5c4" stroke="#172519" strokeWidth={unit * .15} opacity={ball?.inferred ? .35 : 1} />
      ) : null}
      {!ballPosition && players.length === 0 ? (
        <text x={width / 2} y={height / 2} textAnchor="middle" fill="#c1d9cd" fontSize={unit * 2.4}>
          {pitch ? "No positions at this timestamp" : "No object observations at this timestamp"}
        </text>
      ) : null}
    </svg>
  );
}
