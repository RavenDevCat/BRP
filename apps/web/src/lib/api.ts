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
  ai_audit_error?: string | null;
};

export type AiAuditResponse = {
  job_id: string;
  ai_audit_status: string;
  ai_audit_report?: Record<string, unknown> | null;
  cached?: boolean;
  message?: string;
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

export type DemoWorkbook = {
  name: string;
  size_bytes: number;
  modified_at?: string;
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

export type DistanceWorkbookPreview = {
  source_label: string;
  sheet_names: string[];
  selected_sheet: string;
  columns: string[];
  row_count: number;
  sample_rows: Array<Record<string, unknown>>;
  suggested_columns: {
    address?: string;
    city?: string;
    country?: string;
    route?: string;
    sequence?: string;
    bus_type?: string;
  };
};

export type ReferenceDistanceResponse = {
  job: {
    job_id: string;
    type: string;
    created_at: string;
    label: string;
    metadata: Record<string, unknown>;
  };
  summary: {
    row_count: number;
    resolved_count: number;
    failed_count: number;
    blank_count: number;
    distance_mode: string;
  };
  results: Array<Record<string, unknown>>;
};

export type RouteCostResponse = {
  job: {
    job_id: string;
    type: string;
    created_at: string;
    label: string;
    metadata: Record<string, unknown>;
  };
  summary: {
    route_count: number;
    leg_count: number;
    total_one_way_distance_km: number;
    estimated_one_way_fuel_cost: number;
    routes_with_unresolved_stops: number;
    electric_routes_skipped: number;
    currency_code: string;
    currency_label: string;
  };
  route_results: Array<Record<string, unknown>>;
  leg_results: Array<Record<string, unknown>>;
};

export type FleetPlannerPreviewResponse = {
  summary: {
    market: string;
    mode: string;
    monitor_seats: number;
    group_count: number;
    total_riders: number;
    source: string;
  };
  assumptions: Record<string, unknown>;
  demand_workbook?: {
    source_label: string;
    school: Record<string, unknown>;
    summary: Record<string, unknown>;
    warnings: string[];
    riders: Array<Record<string, unknown>>;
  } | null;
  recommendations: Array<Record<string, unknown>>;
  mix_summary: {
    market: string;
    mode: string;
    monitor_seats: number;
    group_count: number;
    vehicle_mix: Record<string, number>;
    selections: Array<Record<string, unknown>>;
  };
  decision_details: Array<Record<string, unknown>>;
  catalog: Array<Record<string, unknown>>;
};

export type FleetPlannerGeocodeResponse = {
  source_label: string;
  summary: {
    school_status?: string;
    student_rows?: number;
    resolved_student_rows?: number;
    failed_student_rows?: number;
    resolved_students?: number;
    failed_students?: number;
    cache_hits?: number;
    cache_changed?: boolean;
  };
  school: Record<string, unknown>;
  demand_points: Array<Record<string, unknown>>;
  rows: Array<Record<string, unknown>>;
  map_html: string;
};

export type FleetPlannerClusterResponse = {
  summary: {
    cluster_count?: number;
    resolved_points?: number;
    failed_points?: number;
    resolved_students?: number;
    failed_students?: number;
    max_vehicle_student_capacity?: number;
    market?: string;
    mode?: string;
    monitor_seats?: number;
  };
  school: Record<string, unknown>;
  clusters: Array<Record<string, unknown>>;
  failed_points: Array<Record<string, unknown>>;
  rows: Array<Record<string, unknown>>;
  stop_rows: Array<Record<string, unknown>>;
  map_html: string;
};

export type FleetPlannerRoutePreviewResponse = {
  summary: {
    route_count?: number;
    total_distance_km?: number;
    total_duration_min?: number;
    service_direction?: string;
    max_route_duration_minutes?: number | null;
    candidate_vehicle_count?: number;
    solver?: string;
  };
  school: Record<string, unknown>;
  routes: Array<Record<string, unknown>>;
  rows: Array<Record<string, unknown>>;
  stop_rows: Array<Record<string, unknown>>;
  map_html: string;
  refinement_note: string;
  workbook_file_name?: string;
  workbook_base64?: string;
};

export type FleetPlannerSubmitGeneratedPlanResponse = {
  job: JobSummary & { worker_pid?: number };
  client_prep: {
    geocode_warnings: Array<Record<string, unknown>>;
    excluded_stops: Array<Record<string, unknown>>;
    elapsed_seconds: number;
    logs: string;
  };
};

type JobsResponse = {
  jobs: JobSummary[];
};

type DemoWorkbooksResponse = {
  demos: DemoWorkbook[];
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

export async function listDemoWorkbooks() {
  const payload = await apiFetch<DemoWorkbooksResponse>("/workbooks/demos");
  return payload.demos;
}

export function getWorkbookTemplateUrl() {
  return `${API_BASE_URL}/workbooks/template`;
}

export function getDemandTemplateUrl() {
  return `${API_BASE_URL}/fleet-planner/demand-template`;
}

export function getDemoWorkbookUrl(name: string) {
  return `${API_BASE_URL}/workbooks/demos/${encodeURIComponent(name)}`;
}

export async function listJobs() {
  const payload = await apiFetch<JobsResponse>("/jobs");
  return payload.jobs;
}

export function getJob(jobId: string) {
  return apiFetch<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`);
}

export function getJobArtifactUrl(jobId: string, artifactKey: string, options?: { download?: boolean; refresh?: boolean }) {
  const url = `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactKey)}`;
  const params = new URLSearchParams();
  if (options?.download) {
    params.set("download", "1");
  }
  if (options?.refresh) {
    params.set("refresh", "1");
  }
  const query = params.toString();
  return query ? `${url}?${query}` : url;
}

export function getJobExportUrl(jobId: string, exportKey: string) {
  return `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/exports/${encodeURIComponent(exportKey)}`;
}

export function generateAiAudit(jobId: string, payload: { force?: boolean; language?: string } = {}) {
  return apiFetch<AiAuditResponse>(`/jobs/${encodeURIComponent(jobId)}/ai-audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function cancelJob(jobId: string) {
  return apiFetch<JobRecord>(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export function deleteJob(jobId: string) {
  return apiFetch<{ deleted: boolean; job_id: string }>(`/jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });
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

export function previewDistanceWorkbook(payload: {
  file_name: string;
  file_base64: string;
  selected_sheet?: string;
}) {
  return apiFetch<DistanceWorkbookPreview>("/distance-checker/workbook-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function runReferenceDistanceCheck(payload: {
  file_name: string;
  file_base64: string;
  selected_sheet: string;
  address_column: string;
  city_column?: string;
  country_column?: string;
  distance_mode: "road" | "straight_line";
  origin: {
    country: string;
    city: string;
    address: string;
  };
}) {
  return apiFetch<ReferenceDistanceResponse>("/distance-checker/reference", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function runCurrentPlanRouteCost(payload: {
  file_name: string;
  file_base64: string;
  selected_sheet: string;
  route_column: string;
  address_column: string;
  sequence_column?: string;
  bus_type_column?: string;
  city_column?: string;
  country_column?: string;
  default_city: string;
  default_country: string;
  currency_code: string;
  currency_label: string;
  diesel_price_per_liter: number;
  fuel_efficiency_km_per_liter: number;
}) {
  return apiFetch<RouteCostResponse>("/distance-checker/route-cost", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function previewFleetPlanner(payload: {
  market: "KR" | "CN";
  mode: "balanced" | "cost_saver" | "comfort_saver";
  monitor_seats: number;
  rider_counts?: string;
  file_name?: string;
  file_base64?: string;
}) {
  return apiFetch<FleetPlannerPreviewResponse>("/fleet-planner/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function geocodeFleetPlannerDemand(payload: {
  file_name: string;
  file_base64: string;
}) {
  return apiFetch<FleetPlannerGeocodeResponse>("/fleet-planner/geocode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function buildFleetPlannerClusters(payload: {
  market: "KR" | "CN";
  mode: "balanced" | "cost_saver" | "comfort_saver";
  monitor_seats: number;
  sector_count: 4 | 8 | 12;
  geocode_result: {
    school: Record<string, unknown>;
    demand_points: Array<Record<string, unknown>>;
    summary: Record<string, unknown>;
  };
}) {
  return apiFetch<FleetPlannerClusterResponse>("/fleet-planner/clusters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function buildFleetPlannerRoutePreview(payload: {
  market: "KR" | "CN";
  mode: "balanced" | "cost_saver" | "comfort_saver";
  monitor_seats: number;
  service_direction: "to_school" | "from_school";
  max_route_duration_minutes?: number;
  cluster_result: {
    school: Record<string, unknown>;
    clusters: Array<Record<string, unknown>>;
    failed_points: Array<Record<string, unknown>>;
    summary: Record<string, unknown>;
  };
}) {
  return apiFetch<FleetPlannerRoutePreviewResponse>("/fleet-planner/route-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function buildFleetPlannerGlobalPlan(payload: {
  market: "KR" | "CN";
  mode: "balanced" | "cost_saver" | "comfort_saver";
  monitor_seats: number;
  service_direction: "to_school" | "from_school";
  geocode_result: {
    school: Record<string, unknown>;
    demand_points: Array<Record<string, unknown>>;
    summary: Record<string, unknown>;
  };
}) {
  return apiFetch<FleetPlannerRoutePreviewResponse>("/fleet-planner/global-plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function submitFleetPlannerGeneratedPlan(payload: {
  job_name: string;
  max_route_duration_minutes?: number;
  route_preview: {
    summary: Record<string, unknown>;
    school: Record<string, unknown>;
    routes: Array<Record<string, unknown>>;
  };
}) {
  return apiFetch<FleetPlannerSubmitGeneratedPlanResponse>("/fleet-planner/submit-generated-plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
