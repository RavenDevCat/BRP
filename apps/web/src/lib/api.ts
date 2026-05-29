const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export type ApiUser = {
  email: string;
  is_admin: boolean;
  auth_mode: string;
};

export type ApiHealth = {
  status: string;
};

export type JobSummary = {
  job_id: string;
  owner_email?: string;
  status: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  metadata?: Record<string, unknown>;
  prepared_payload_summary?: Record<string, unknown>;
  error?: string | null;
};

export type JobRecord = JobSummary & {
  result?: Record<string, unknown> | null;
  traceback?: string | null;
  ai_audit_status?: string | null;
  ai_audit_report?: Record<string, unknown> | null;
};

export type PlannerConfigPayload = {
  large_bus_name: string;
  mid_bus_name: string;
  small_bus_name: string;
  large_bus_capacity: number;
  mid_bus_capacity: number;
  small_bus_capacity: number;
  large_bus_max_count: number;
  mid_bus_max_count: number;
  small_bus_max_count: number;
  free_baseline_large_bus_ratio: number;
  free_baseline_mid_bus_ratio: number;
  free_baseline_small_bus_ratio: number;
  express_threshold_km: number;
  reserved_express_buses: number;
  express_skip_inner_km: number;
  max_route_duration_minutes: number;
  stop_service_minutes: number;
  subway_search_radius_m: number;
  max_subway_walk_distance_m: number;
  nearby_cluster_radius_m: number;
  traffic_profile_name: string;
  service_direction: string;
  include_subway_aggregation_scenario: boolean;
  include_nearby_aggregation_scenario: boolean;
  operating_cost_per_km: number;
  revenue_rules: Array<{ min_km: number; max_km: number | null; fee_per_person: number }> | null;
};

export type WorkbookPreview = {
  source_label: string;
  selected_sheet: string;
  job_default_name: string;
  summary: Record<string, unknown>;
  fleet: Array<Record<string, unknown>>;
  input_record_count: number;
  subway_aggregation_block_reason?: string | null;
  suggested_config: PlannerConfigPayload;
};

export type WorkbookSubmitResponse = {
  job: JobSummary & { worker_pid?: number };
  source_label: string;
  selected_sheet: string;
  summary: Record<string, unknown>;
  client_prep: {
    geocode_warnings: Array<Record<string, unknown>>;
    excluded_stops: Array<Record<string, unknown>>;
    elapsed_seconds: number;
    logs: string;
  };
  subway_aggregation_block_reason?: string | null;
};

type JobsResponse = {
  jobs: JobSummary[];
};

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === "object" && payload && "error" in payload
        ? String((payload as { error?: unknown }).error)
        : `Request failed with ${response.status}`;
    throw new Error(message);
  }

  return payload as T;
}

export function getHealth() {
  return apiFetch<ApiHealth>("/health");
}

export function getCurrentUser() {
  return apiFetch<ApiUser>("/me");
}

export async function listJobs() {
  const payload = await apiFetch<JobsResponse>("/jobs");
  return payload.jobs;
}

export function getJob(jobId: string) {
  return apiFetch<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`);
}

export function previewWorkbook(payload: {
  file_name: string;
  file_base64: string;
  config: PlannerConfigPayload;
}) {
  return apiFetch<WorkbookPreview>("/workbooks/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function submitWorkbookJob(payload: {
  file_name: string;
  file_base64: string;
  config: PlannerConfigPayload;
  job_custom_name?: string;
}) {
  return apiFetch<WorkbookSubmitResponse>("/workbooks/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
