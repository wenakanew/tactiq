export type Point = { x: number; y: number };

export type InsightWindowMetadata = {
  total_insight_count?: number;
  stored_insight_limit?: number;
  insights_truncated?: boolean;
};

export type TrackedEntity = {
  track_id: string;
  team_id: string | null;
  position: Point | null;
  image_position?: Point | null;
  confidence: number;
  inferred: boolean;
  speed_kmh?: number | null;
};

export type TrackingSnapshot = {
  ball_position?: Point | null;
  raw_ball_position?: Point | null;
  smoothed_ball_position?: Point | null;
  ball?: TrackedEntity | null;
  players?: TrackedEntity[];
  ball_speed_kmh?: number | null;
  ball_travel_direction?: string;
  top_player_speed_kmh?: number | null;
  tracked_players?: number;
  team_identification_confidence?: number | null;
  possession_team_id?: string | null;
  possession_player_id?: string | null;
  possession_transition_confidence?: number;
  control_index_by_team?: Record<string, number>;
  control_delta?: number;
  controlling_team_id?: string;
  pitch_zone?: string;
  tracking_source?: string;
  detection_source?: string;
  coordinate_space?: string;
  calibrated?: boolean;
  geometry_source?: "camera" | "simulation" | "image";
  frame_width?: number;
  frame_height?: number;
  scene_id?: number;
  updated_at_ms?: number;
  calibration?: { revision?: number; status?: string };
};

export type PossessionChain = {
  chain_id: string;
  team_id: string;
  start_ms: number;
  end_ms: number;
  actions: number;
  completed_passes: number;
  progression_m: number | null;
  xt_gain_estimate: number | null;
  xg_estimate: number | null;
  phase?: string;
  terminal_event: string | null;
};

export type TacticalState = {
  phase?: string;
  chain_count?: number;
  recent_chains?: PossessionChain[];
  lanes?: Record<string, Array<{ lane: string; attackers: number; defenders: number }>>;
  team_shapes?: Record<string, {
    width_m: number;
    depth_m: number;
    observed_players: number;
    formation_estimate: string | null;
    formation_status: string;
    possession_phase: string;
  }>;
  value_model?: { version: string | null; status: string; training_source?: string | null };
};

export type MatchStatePayload = {
  match_id: string;
  scenario_id?: string;
  status?: string;
  match_clock_ms?: number;
  score?: Record<string, number>;
  possession_team_id?: string | null;
  last_sequence?: number;
  action_count?: number;
  tracking_sample_count?: number;
  scenario_recipe_version?: number;
  team_display_names?: Record<string, string>;
  player_roster?: Record<string, { player_id: string; team_id: string | null; display_name?: string | null }>;
  cv_live?: TrackingSnapshot;
  tactical_state?: TacticalState;
  analysis_metadata?: InsightWindowMetadata & { notes?: string; warnings?: string[]; detection_source?: string;
    tracking_quality?: { sampled_frames?: number; ball_observed_frames?: number; ball_predicted_frames?: number;
      scene_changes?: number; player_observed_samples?: number } };
};

export type ReplayFrame = {
  timestamp_ms: number;
  sequence: number;
  score: Record<string, number>;
  possession_team_id: string | null;
  tracking: TrackingSnapshot;
  tactics: TacticalState;
};

export type ReplayPayload = {
  match_id: string;
  duration_ms: number;
  processed_until_ms: number;
  after_sequence?: number;
  source_url: string | null;
  team_display_names: Record<string, string>;
  frames: ReplayFrame[];
  insights: Array<{ insight_id: string; title: string; window_end_ms: number; window_start_ms: number;
    provenance?: { quality?: { eligible: boolean; reason: string } } }>;
  metadata: InsightWindowMetadata & { notes?: string; warnings?: string[]; detection_source?: string; width?: number; height?: number };
};
