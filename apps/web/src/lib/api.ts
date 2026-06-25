const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export type ApiUser = {
    email: string;
    is_admin: boolean;
    auth_mode: string;
    auth?: AuthConfig;
};

export type ApiHealth = {
    status: string;
};

export type AuthConfig = {
    provider: string;
    display_name: string;
    login_url: string;
    logout_url: string;
    sso_ready: boolean;
    admin_source: string;
};

export type GoogleGeocodeUsage = {
    enabled: boolean;
    month_key?: string;
    used?: number;
    limit?: number;
    label?: string;
};

export type TrafficRolloutStatusResponse = {
    status: string;
    next_step?: string;
    endpoint?: {
        read_only?: boolean;
        provider_api_called?: boolean;
        osrm_started?: boolean;
    };
    rollout_gate?: {
        status?: string;
        passed_requirement_count?: number;
        failed_requirement_count?: number;
        missing_profiles?: Array<Record<string, unknown>>;
        failure_reason_counts?: Record<string, number>;
    };
    timers?: {
        problem_count?: number;
        next_relevant_timer?: {
            unit?: string;
            next_elapse_local?: string;
            seconds_until_next_elapse?: number;
        } | null;
    };
    services?: {
        problem_count?: number;
        problem_services?: Array<Record<string, unknown>>;
    };
    api_budget?: {
        problem?: boolean;
        total_estimated_api_call_count?: number;
        max_estimated_api_call_count?: number;
        provider_api_called?: boolean;
        osrm_started?: boolean;
        safety_violation_reasons?: string[];
    };
    osrm_manager?: {
        available?: boolean;
        lock_count?: number;
        stale_lock_count?: number;
        running_region_count?: number;
        running_regions?: string[];
    };
    market_overview?: TrafficMarketOverview;
};

export type TrafficMarketOverview = {
    status?: string;
    sample_dir?: string;
    sample_file_count?: number;
    filtered_file_count?: number;
    unreadable_file_count?: number;
    default_traffic_coefficient_mode?: string;
    stale_after_hours?: number;
    deployment_tier?: string;
    market_scope?: string[];
    blocked_count?: number;
    warning_count?: number;
    markets?: TrafficMarketStatus[];
};

export type TrafficMarketPeriodStatus = {
    period: string;
    status?: string;
    sample_file_count?: number;
    route_sample_count?: number;
    geo_route_sample_count?: number;
    geo_route_sample_ratio?: number;
    latest_measured_at?: string;
    latest_sample?: string;
    providers?: string[];
    weekdays?: string[];
    age_hours?: number | null;
};

export type TrafficMarketStatus = {
    market: string;
    city: string;
    label: string;
    status: string;
    traffic_mode: string;
    provider: string;
    observed_providers?: string[];
    active_source?: string;
    fallback_multiplier?: number | null;
    requires_samples?: boolean;
    required_periods?: string[];
    sample_file_count?: number;
    route_sample_count?: number;
    geo_route_sample_count?: number;
    geo_route_sample_ratio?: number;
    latest_measured_at?: string;
    latest_sample?: string;
    stale_after_hours?: number;
    warnings?: string[];
    periods?: TrafficMarketPeriodStatus[];
};

export type DeploymentFeatures = {
    language_switch_enabled: boolean;
    default_traffic_coefficient_mode?: string;
};

export type JobSummary = {
    job_id: string;
    owner_email?: string;
    status: string;
    created_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    scheduled_start_at?: string | null;
    scheduled_trigger_label?: string | null;
    metadata?: Record<string, unknown>;
    prepared_payload_summary?: Record<string, unknown>;
    error?: string | null;
};

export type JobRecord = JobSummary & {
    result?: Record<string, unknown> | null;
    traceback?: string | null;
    ai_audit_status?: string | null;
    ai_audit_report?: Record<string, unknown> | null;
    ai_audit_reports?: Record<string, Record<string, unknown>> | null;
    ai_audit_error?: string | null;
};

export type AiAuditResponse = {
    job_id: string;
    ai_audit_status: string;
    ai_audit_report?: Record<string, unknown> | null;
    ai_audit_reports?: Record<string, Record<string, unknown>> | null;
    cached?: boolean;
    message?: string;
};

export type JobTrafficAttributionScenarioSummary = {
    scenario_key: string;
    scenario_label: string;
    route_estimate_count: number;
    attributed_route_count: number;
    geo_attributed_route_count: number;
    geo_attributed_route_ratio: number;
    method_counts: Record<string, number>;
    quality_reason_counts: Record<string, number>;
    non_geo_routes: Array<Record<string, unknown>>;
    route_evidence?: Array<Record<string, unknown>>;
};

export type JobTrafficAttributionResponse = {
    job_id: string;
    job_status?: string;
    traffic_profile_name?: string;
    traffic_time_multiplier?: number;
    traffic_coefficient_mode?: string;
    has_traffic_attribution: boolean;
    attribution_enabled: boolean;
    attribution_succeeded: boolean;
    attribution_mode?: string;
    attribution_method?: string;
    attribution_reason?: string;
    attribution_confidence?: string;
    route_level_applied: boolean;
    observed_route_sample_count: number;
    geo_route_sample_count: number;
    scale_only_route_sample_count: number;
    geo_route_sample_ratio: number;
    scenario_count: number;
    scenarios: JobTrafficAttributionScenarioSummary[];
};

export type JobMapBounds = {
    min_lng: number;
    min_lat: number;
    max_lng: number;
    max_lat: number;
};

export type JobMapRoute = {
    id: string;
    route_index: number;
    vehicle_id?: string | number | null;
    bus_type_name: string;
    load: number;
    bus_capacity?: number | null;
    comfort_capacity?: number | null;
    stop_count: number;
    max_stops?: number | null;
    distance_m: number;
    duration_s: number;
    raw_duration_s: number;
    traffic_time_source?: string;
    geometry: number[][];
    display_geometry?: number[][] | null;
    display_geometry_source?: string;
    display_geometry_message?: string;
    stop_ids: string[];
    time_impact?: JobMapTimeImpactSummary;
};

export type JobMapStop = {
    id: string;
    route_id: string;
    route_index: number;
    order: number;
    node_index: number;
    address: string;
    requested_address?: string;
    passenger_count: number;
    is_depot: boolean;
    lat: number;
    lng: number;
    cumulative_duration_s: number;
    cumulative_distance_m: number;
    demand_batch_index?: number | null;
    demand_batch_count?: number | null;
    schedule_anchor_label?: string;
    schedule_anchor_kind?: string;
    scheduled_offset_s?: number;
    scheduled_time_minutes?: number;
    scheduled_time_label?: string;
    time_impact?: JobMapStopTimeImpact;
};

export type JobMapStopTimeImpact = {
    comparison_available?: boolean;
    comparison_status?: "matched" | "current_stop_not_found" | "schedule_time_missing" | string;
    matched_key?: string;
    time_role?: "pickup" | "dropoff" | string;
    current_route_id?: string;
    new_route_id?: string;
    current_route_index?: number | null;
    new_route_index?: number | null;
    current_stop_order?: number | null;
    new_stop_order?: number | null;
    current_time_minutes?: number | null;
    new_time_minutes?: number | null;
    current_time_label?: string;
    new_time_label?: string;
    current_offset_s?: number | null;
    new_offset_s?: number | null;
    delta_minutes?: number;
    absolute_delta_minutes?: number;
    adverse_delta_minutes?: number;
    acceptance_threshold_minutes?: number;
    within_acceptance?: boolean;
    acceptance_status?: "within" | "over" | string;
    over_acceptance_minutes?: number;
    benefit_delta_minutes?: number;
    adverse_direction?: "earlier_pickup" | "later_dropoff" | string;
    change_direction?: "earlier" | "later" | "same" | string;
    impact_direction?: "worse" | "better" | "neutral" | string;
    affected_rider_count?: number;
    adverse_rider_minutes?: number;
    absolute_rider_minutes?: number;
    benefit_rider_minutes?: number;
    level?: "better" | "acceptable" | "notice" | "elevated" | "severe" | "critical" | string;
    route_changed?: boolean;
};

export type JobMapTimeImpactTopStop = {
    stop_id: string;
    address: string;
    route_id: string;
    current_route_id?: string;
    new_route_id?: string;
    current_time_label?: string;
    new_time_label?: string;
    delta_minutes?: number;
    adverse_delta_minutes?: number;
    absolute_delta_minutes?: number;
    acceptance_threshold_minutes?: number;
    within_acceptance?: boolean;
    acceptance_status?: string;
    over_acceptance_minutes?: number;
    affected_rider_count?: number;
    level?: string;
    impact_direction?: string;
    route_changed?: boolean;
};

export type JobMapTimeImpactSummary = {
    available?: boolean;
    service_stop_count?: number;
    compared_stop_count?: number;
    unavailable_stop_count?: number;
    compared_rider_count?: number;
    acceptance_threshold_minutes?: number;
    within_acceptance_stop_count?: number;
    within_acceptance_rider_count?: number;
    over_acceptance_stop_count?: number;
    over_acceptance_rider_count?: number;
    acceptance_stop_ratio?: number;
    acceptance_rider_ratio?: number;
    max_over_acceptance_delta_minutes?: number;
    avg_adverse_delta_minutes?: number;
    avg_absolute_delta_minutes?: number;
    avg_signed_delta_minutes?: number;
    weighted_avg_adverse_delta_minutes?: number;
    weighted_avg_absolute_delta_minutes?: number;
    p90_adverse_delta_minutes?: number;
    max_adverse_delta_minutes?: number;
    max_absolute_delta_minutes?: number;
    notice_stop_count?: number;
    elevated_stop_count?: number;
    severe_stop_count?: number;
    critical_stop_count?: number;
    high_risk_stop_count?: number;
    worse_stop_count?: number;
    better_stop_count?: number;
    neutral_stop_count?: number;
    worse_rider_count?: number;
    better_rider_count?: number;
    neutral_rider_count?: number;
    high_risk_rider_count?: number;
    total_adverse_rider_minutes?: number;
    total_absolute_rider_minutes?: number;
    total_benefit_rider_minutes?: number;
    route_changed_stop_count?: number;
    route_changed_rider_count?: number;
    top_impacted_stops?: JobMapTimeImpactTopStop[];
};

export type JobMapPrivateLink = {
    id: string;
    access_type: string;
    address: string;
    pickup_address: string;
    pickup_route_id: string;
    drive_time_s: number;
    drive_distance_m: number;
    geometry: number[][];
};

export type JobMapRouteConnector = {
    id: string;
    route_id: string;
    route_index: number;
    from_node?: number | null;
    to_node?: number | null;
    connector_type: string;
    distance_m: number;
    geometry: number[][];
};

export type JobMapData = {
    job_id: string;
    scenario_key: string;
    scenario_name: string;
    service_direction?: string;
    traffic_profile_name?: string;
    bounds?: JobMapBounds | null;
    routes: JobMapRoute[];
    stops: JobMapStop[];
    route_connectors?: JobMapRouteConnector[];
    private_links: JobMapPrivateLink[];
    summary: {
        route_count: number;
        stop_count: number;
        passenger_count: number;
        distance_m: number;
        duration_s: number;
        time_impact?: JobMapTimeImpactSummary;
    };
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
    comfort_load_factor: number;
    traffic_profile_name: string;
    traffic_coefficient_mode: string;
    service_direction: string;
    to_school_arrival_time: string;
    from_school_departure_time: string;
    include_subway_aggregation_scenario: boolean;
    include_nearby_aggregation_scenario: boolean;
    operating_cost_per_km: number;
    revenue_rules: Array<{
        min_km: number;
        max_km: number | null;
        fee_per_person: number;
    }> | null;
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

export type DistanceCheckerHistorySummary = {
    run_id: string;
    tool_key: string;
    owner_email?: string;
    title: string;
    created_at?: string | null;
    summary: Record<string, unknown>;
};

export type DistanceCheckerHistoryRecord = DistanceCheckerHistorySummary & {
    scenario?: Record<string, unknown>;
    preview?: DistanceWorkbookPreview;
    reference_result?: ReferenceDistanceResponse;
    route_cost_result?: RouteCostResponse;
};

export type DistanceCheckerHistoryCreateResponse = {
    job: DistanceCheckerHistorySummary;
};

export type DistanceCheckerToolMode = "reference" | "route_cost";

export type FleetPlannerPreviewResponse = {
    summary: {
        market: string;
        mode: string;
        monitor_seats: number;
        max_route_duration_minutes?: number;
        group_count: number;
        total_riders: number;
        source: string;
        vehicle_catalog_source?: string;
        vehicle_catalog_count?: number;
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

export type FleetPlannerVehicleConfig = {
    vehicle_type?: string;
    display_name: string;
    listed_seats: number;
    category: string;
    propulsion: string;
    available_count: number;
    enabled: boolean;
    notes?: string;
};

export type FleetPlannerVehicleCatalogResponse = {
    summary: {
        market: string;
        monitor_seats: number;
        vehicle_count: number;
        source: string;
    };
    catalog: FleetPlannerVehicleConfig[];
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
        max_route_duration_minutes?: number;
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
        traffic_profile_name?: string;
        traffic_time_multiplier?: number;
        traffic_profile_context?: string;
        live_traffic_sample?: Record<string, unknown> | null;
    };
    school: Record<string, unknown>;
    routes: Array<Record<string, unknown>>;
    rows: Array<Record<string, unknown>>;
    stop_rows: Array<Record<string, unknown>>;
    map_html: string;
    map_data?: JobMapData;
    refinement_note: string;
    workbook_file_name?: string;
    workbook_base64?: string;
};

export type FleetPlannerHistorySummary = {
    run_id: string;
    tool_key: string;
    owner_email?: string;
    title: string;
    created_at?: string | null;
    shared_with_all?: boolean;
    seed?: boolean;
    seed_label?: string;
    summary: Record<string, unknown>;
};

export type FleetPlannerHistoryRecord = FleetPlannerHistorySummary & {
    scenario?: Record<string, unknown>;
    preview_result?: FleetPlannerPreviewResponse;
    geocode_result?: FleetPlannerGeocodeResponse;
    cluster_result?: FleetPlannerClusterResponse;
    route_preview_result?: FleetPlannerRoutePreviewResponse;
    global_plan_result?: FleetPlannerRoutePreviewResponse;
};

export type FleetPlannerHistoryCreateResponse = {
    job: FleetPlannerHistorySummary;
};

type JobsResponse = {
    jobs: JobSummary[];
};

type FleetPlannerHistoryResponse = {
    jobs: FleetPlannerHistorySummary[];
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

export function getAuthConfig() {
    return apiFetch<AuthConfig>("/auth/config");
}

export function getGoogleGeocodeUsage() {
    return apiFetch<GoogleGeocodeUsage>("/google-geocode-usage");
}

export function getDeploymentFeatures() {
    return apiFetch<DeploymentFeatures>("/deployment-features");
}

export function getTrafficRolloutStatus() {
    return apiFetch<TrafficRolloutStatusResponse>("/traffic-rollout/status");
}

export function getWorkbookTemplateUrl() {
    return `${API_BASE_URL}/workbooks/template`;
}

export function getDemandTemplateUrl() {
    return `${API_BASE_URL}/fleet-planner/demand-template`;
}

export async function listJobs() {
    const payload = await apiFetch<JobsResponse>("/jobs");
    return payload.jobs;
}

export function getJob(jobId: string) {
    return apiFetch<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`);
}

export function getJobArtifactUrl(
    jobId: string,
    artifactKey: string,
    options?: { download?: boolean; refresh?: boolean },
) {
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

export function getJobMapData(jobId: string, scenarioKey: string) {
    return apiFetch<JobMapData>(
        `/jobs/${encodeURIComponent(jobId)}/map-data/${encodeURIComponent(scenarioKey)}`,
    );
}

export function getJobTrafficAttribution(
    jobId: string,
    options: { routeEvidence?: boolean; topMatches?: boolean } = {},
) {
    const params = new URLSearchParams();
    if (options.routeEvidence) {
        params.set("route_evidence", "1");
    }
    if (options.topMatches) {
        params.set("top_matches", "1");
    }
    const query = params.toString();
    return apiFetch<JobTrafficAttributionResponse>(
        `/jobs/${encodeURIComponent(jobId)}/traffic-attribution${query ? `?${query}` : ""}`,
    );
}

export function getJobExportUrl(jobId: string, exportKey: string) {
    return `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/exports/${encodeURIComponent(exportKey)}`;
}

export function generateAiAudit(
    jobId: string,
    payload: { force?: boolean; language?: string } = {},
) {
    return apiFetch<AiAuditResponse>(
        `/jobs/${encodeURIComponent(jobId)}/ai-audit`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}

export function cancelJob(jobId: string) {
    return apiFetch<JobRecord>(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
    });
}

export function deleteJob(jobId: string) {
    return apiFetch<{ deleted: boolean; job_id: string }>(
        `/jobs/${encodeURIComponent(jobId)}`,
        {
            method: "DELETE",
        },
    );
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
    scheduled_job?: boolean;
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
    return apiFetch<DistanceWorkbookPreview>(
        "/distance-checker/workbook-preview",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
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

type DistanceCheckerHistoryResponse = {
    jobs: DistanceCheckerHistorySummary[];
};

function distanceCheckerHistoryPath(toolMode: DistanceCheckerToolMode) {
    return toolMode === "route_cost"
        ? "/distance-checker/route-cost-history"
        : "/distance-checker/reference-history";
}

export async function listDistanceCheckerHistory(
    toolMode: DistanceCheckerToolMode,
) {
    const payload = await apiFetch<DistanceCheckerHistoryResponse>(
        distanceCheckerHistoryPath(toolMode),
    );
    return payload.jobs;
}

export function getDistanceCheckerHistory(
    toolMode: DistanceCheckerToolMode,
    runId: string,
) {
    return apiFetch<DistanceCheckerHistoryRecord>(
        `${distanceCheckerHistoryPath(toolMode)}/${encodeURIComponent(runId)}`,
    );
}

export function deleteDistanceCheckerHistory(
    toolMode: DistanceCheckerToolMode,
    runId: string,
) {
    return apiFetch<{ deleted: boolean; run_id: string }>(
        `${distanceCheckerHistoryPath(toolMode)}/${encodeURIComponent(runId)}`,
        {
            method: "DELETE",
        },
    );
}

export function saveDistanceCheckerHistory(payload: {
    title: string;
    tool_mode: DistanceCheckerToolMode;
    scenario: Record<string, unknown>;
    preview?: DistanceWorkbookPreview | null;
    reference_result?: ReferenceDistanceResponse | null;
    route_cost_result?: RouteCostResponse | null;
}) {
    return apiFetch<DistanceCheckerHistoryCreateResponse>(
        distanceCheckerHistoryPath(payload.tool_mode),
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}

export function previewFleetPlanner(payload: {
    market: "KR" | "CN";
    mode: "balanced" | "cost_saver" | "comfort_saver";
    monitor_seats: number;
    max_route_duration_minutes?: number;
    vehicle_catalog?: FleetPlannerVehicleConfig[];
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

export function getFleetPlannerVehicleCatalog(payload: {
    market: "KR" | "CN";
    monitor_seats: number;
}) {
    const params = new URLSearchParams({
        market: payload.market,
        monitor_seats: String(payload.monitor_seats),
    });
    return apiFetch<FleetPlannerVehicleCatalogResponse>(
        `/fleet-planner/vehicle-catalog?${params.toString()}`,
    );
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
    max_route_duration_minutes?: number;
    vehicle_catalog?: FleetPlannerVehicleConfig[];
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
    vehicle_catalog?: FleetPlannerVehicleConfig[];
    cluster_result: {
        school: Record<string, unknown>;
        clusters: Array<Record<string, unknown>>;
        failed_points: Array<Record<string, unknown>>;
        summary: Record<string, unknown>;
    };
}) {
    return apiFetch<FleetPlannerRoutePreviewResponse>(
        "/fleet-planner/route-preview",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}

export function buildFleetPlannerGlobalPlan(payload: {
    market: "KR" | "CN";
    mode: "balanced" | "cost_saver" | "comfort_saver";
    monitor_seats: number;
    max_route_duration_minutes?: number;
    vehicle_catalog?: FleetPlannerVehicleConfig[];
    service_direction: "to_school" | "from_school";
    geocode_result: {
        school: Record<string, unknown>;
        demand_points: Array<Record<string, unknown>>;
        summary: Record<string, unknown>;
    };
}) {
    return apiFetch<FleetPlannerRoutePreviewResponse>(
        "/fleet-planner/global-plan",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}

export async function listFleetPlannerHistory() {
    const payload = await apiFetch<FleetPlannerHistoryResponse>(
        "/fleet-planner/history",
    );
    return payload.jobs;
}

export function getFleetPlannerHistory(runId: string) {
    return apiFetch<FleetPlannerHistoryRecord>(
        `/fleet-planner/history/${encodeURIComponent(runId)}`,
    );
}

export function deleteFleetPlannerHistory(runId: string) {
    return apiFetch<{ deleted: boolean; run_id: string }>(
        `/fleet-planner/history/${encodeURIComponent(runId)}`,
        {
            method: "DELETE",
        },
    );
}

export function saveFleetPlannerHistory(payload: {
    title?: string;
    scenario: Record<string, unknown>;
    preview_result: FleetPlannerPreviewResponse;
    geocode_result?: FleetPlannerGeocodeResponse;
    cluster_result?: FleetPlannerClusterResponse;
    route_preview_result?: FleetPlannerRoutePreviewResponse;
    global_plan_result: FleetPlannerRoutePreviewResponse;
}) {
    return apiFetch<FleetPlannerHistoryCreateResponse>(
        "/fleet-planner/history",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        },
    );
}
