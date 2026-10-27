import type { PublicTrialCapability } from "./workspace-access";

export type ServiceCapabilities = {
  auth_enabled: boolean;
  public_trial?: PublicTrialCapability;
  cv_model_configured: boolean;
  cv_detector: string;
  youtube_import_configured: boolean;
  speech_configured: boolean;
  speech?: { configured: boolean; languages: Record<string, { single: boolean; dual: boolean }> };
  narrative?: { status: string; last_failure?: { reason?: string } | null };
  paginated_streams?: boolean;
  paid_generation_enabled?: boolean;
  deterministic_recap_enabled?: boolean;
  analysis_admission_enabled?: boolean;
};
