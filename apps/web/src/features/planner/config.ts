import type { PlannerConfigPayload } from "@/lib/api";

export const DEFAULT_PLANNER_CONFIG: PlannerConfigPayload = {
  large_bus_name: "Large Bus",
  mid_bus_name: "Mid Bus",
  small_bus_name: "Small Bus",
  large_bus_capacity: 42,
  mid_bus_capacity: 35,
  small_bus_capacity: 19,
  large_bus_max_count: 20,
  mid_bus_max_count: 15,
  small_bus_max_count: 10,
  free_baseline_large_bus_ratio: 20,
  free_baseline_mid_bus_ratio: 15,
  free_baseline_small_bus_ratio: 10,
  express_threshold_km: 15,
  reserved_express_buses: 4,
  express_skip_inner_km: 8,
  max_route_duration_minutes: 60,
  stop_service_minutes: 1,
  subway_search_radius_m: 1500,
  max_subway_walk_distance_m: 800,
  nearby_cluster_radius_m: 500,
  comfort_load_factor: 0.85,
  traffic_profile_name: "Off-Peak",
  service_direction: "From School",
  include_subway_aggregation_scenario: false,
  include_nearby_aggregation_scenario: false,
  operating_cost_per_km: 0,
  revenue_rules: [{ min_km: 0, max_km: null, fee_per_person: 0 }],
};

export const SERVICE_DIRECTION_OPTIONS = ["From School", "To School"];
export const TRAFFIC_PROFILE_OPTIONS = ["Off-Peak", "AM Peak", "PM Peak"];
