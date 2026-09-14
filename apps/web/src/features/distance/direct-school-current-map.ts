import type { DirectSchoolAnalysisResult, JobMapData, JobMapStop } from "@/lib/api";

const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const line = (value: unknown): number[][] => Array.isArray(value) && value.every(p => Array.isArray(p) && p.length >= 2 && finite(p[0]) && finite(p[1])) ? value as number[][] : [];

export function currentMapStopId(routeId: string, sequence: number, stopKey: string) {
  return JSON.stringify([routeId, sequence, stopKey]);
}

// Adapt saved measurements only. Never request routes or infer a line between stops.
export function buildDirectSchoolCurrentMap(result: DirectSchoolAnalysisResult, routeId: string): JobMapData {
  const saved = result.routes.find(route => route.route_id === routeId);
  const data: JobMapData = {
    job_id: "", scenario_key: "current", scenario_name: routeId,
    service_direction: result.service_direction, routes: [], stops: [], private_links: [],
    summary: { route_count: 0, stop_count: 0, passenger_count: 0, distance_m: null, duration_s: null },
  };
  if (!saved) return data;
  const evidence = object(saved.route_evidence);
  const segmentValue = evidence.geometry_segments ?? saved.geometry_segments;
  const segments = Array.isArray(segmentValue) ? segmentValue.map(line).filter(s => s.length >= 2) : undefined;
  const geometry = line(saved.geometry ?? evidence.geometry);
  const entries = result.stops.flatMap(stop => (stop.route_contexts || [])
    .filter(context => context.route_id === routeId && finite(context.stop_sequence))
    .map(context => ({ stop, context, sequence: context.stop_sequence as number })))
    .sort((a, b) => a.sequence - b.sequence);
  const fromSchool = result.service_direction === "From School";
  const cumulative = (values: unknown, count: number): number | null => {
    if (!Array.isArray(values) || values.length !== entries.length || !values.every(finite)) return null;
    return values.slice(0, count).reduce((total: number, value: number) => total + value, 0);
  };
  const stops: JobMapStop[] = entries.flatMap(({ stop, context, sequence }, index) => {
    if (!finite(stop.lng) || !finite(stop.lat)) return [];
    const offset = fromSchool ? index + 1 : index;
    return [{ id: currentMapStopId(routeId, sequence, stop.stop_key), route_id: routeId,
      route_index: 0, order: sequence, display_label: String(sequence), node_index: index,
      address: stop.address, passenger_count: context.riders ?? 0, is_depot: false,
      lng: stop.lng, lat: stop.lat,
      cumulative_duration_s: cumulative(evidence.leg_durations_s, offset),
      cumulative_distance_m: cumulative(evidence.leg_distances_m, offset) }];
  });
  const school = result.school;
  if (finite(school.lng) && finite(school.lat)) {
    stops.push({ id: JSON.stringify([routeId, "school"]), route_id: routeId, route_index: 0,
      order: fromSchool ? 0 : Math.max(0, ...entries.map(e => e.sequence)) + 1,
      display_label: "S", node_index: entries.length, address: String(school.address || ""),
      passenger_count: 0, is_depot: true, lat: school.lat, lng: school.lng,
      cumulative_duration_s: fromSchool ? 0 : cumulative(evidence.leg_durations_s, entries.length),
      cumulative_distance_m: fromSchool ? 0 : cumulative(evidence.leg_distances_m, entries.length) });
  }
  const duration = finite(saved.total_duration_min) ? saved.total_duration_min * 60 : null;
  const distance = finite(saved.provider_distance_km) ? saved.provider_distance_km * 1000 : null;
  const riders = finite(saved.riders) ? saved.riders : entries.reduce((sum, e) => sum + (e.context.riders || 0), 0);
  data.routes = [{ id: routeId, source_route_id: routeId, route_index: 0, bus_type_name: "",
    load: riders, stop_count: finite(saved.stop_count) ? saved.stop_count : entries.length,
    duration_s: duration, distance_m: distance,
    raw_duration_s: finite(saved.provider_duration_min) ? saved.provider_duration_min * 60 : 0,
    stop_service_time_s: result.parameters.stop_service_minutes * 60,
    geometry: segments ? segments.flat() : geometry, geometry_segments: segments,
    stop_ids: stops.map(stop => stop.id),
    route_evidence: saved.route_evidence as JobMapData["routes"][number]["route_evidence"],
    traffic_time_source: typeof saved.provider_route_source === "string" ? saved.provider_route_source : undefined }];
  data.stops = stops;
  data.summary = { route_count: 1, stop_count: data.routes[0].stop_count,
    passenger_count: riders, distance_m: distance, duration_s: duration };
  return data;
}
