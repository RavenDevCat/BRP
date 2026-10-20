import type { JobMapData, JobMapStop } from "@/lib/api";
import type { ReturnResult, ReturnRoute } from "@/lib/return-types";

export function underReturnLimit(route: ReturnRoute, minutes: number) {
  return route.status === "resolved" && route.total_duration_s != null &&
    Number.isFinite(route.total_duration_s) && route.total_duration_s >= 0 && route.total_duration_s < minutes * 60;
}

export function returnMapData(result: ReturnResult, label: string, minutes: number): JobMapData {
  const stops: JobMapStop[] = [];
  const routes = result.routes.map((route, index) => {
    const routeIndex = route.route_index ?? index;
    const displayId = route.display_route_id || route.stops[0]?.source_display_route_id || route.route_id;
    const stopIds: string[] = [];
    for (const stop of route.stops) {
      if (!Number.isFinite(stop.lat) || !Number.isFinite(stop.lng)) continue;
      const id = `${route.route_id}__${stop.stop_sequence}`;
      stopIds.push(id);
      stops.push({
        id, route_id: displayId, route_index: routeIndex, order: stop.stop_sequence,
        node_index: stop.source_node_id ?? stop.source_stop_sequence, address: stop.address, passenger_count: stop.passenger_count,
        is_depot: stop.is_depot, lat: stop.lat!, lng: stop.lng!,
        cumulative_duration_s: stop.ride_duration_s ?? null, cumulative_distance_m: null,
        scheduled_time_label: stop.arrival_at ? new Intl.DateTimeFormat(undefined, {
          hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai",
        }).format(new Date(stop.arrival_at)) : undefined,
      });
    }
    return {
      id: displayId, source_route_id: route.source_route_id || route.route_id,
      route_index: routeIndex, vehicle_id: route.vehicle_id,
      exception_role: route.exception_role, route_change_type: route.route_change_type,
      bus_type_name: route.bus_type_name || "", bus_capacity: route.bus_capacity,
      load: route.riders, stop_count: route.stop_count,
      distance_m: route.status === "resolved" ? route.distance_m ?? null : null,
      duration_s: route.status === "resolved" ? route.total_duration_s ?? null : null,
      raw_duration_s: route.total_duration_s ?? 0, stop_service_time_s: route.dwell_duration_s,
      traffic_time_source: route.provider_route_source,
      geometry: route.geometry || route.geometry_segments?.flat() || [],
      geometry_segments: route.geometry_segments, stop_ids: stopIds,
    };
  });
  const coordinates = stops.map(stop => [stop.lng, stop.lat]);
  const measured = routes.filter(route => route.duration_s !== null);
  return {
    job_id: "", scenario_key: result.scenario_key || "current_plan", scenario_name: label,
    service_direction: "From School", duration_limit_s: minutes * 60,
    routes, stops, private_links: [],
    bounds: coordinates.length ? {
      min_lng: Math.min(...coordinates.map(p => p[0])), max_lng: Math.max(...coordinates.map(p => p[0])),
      min_lat: Math.min(...coordinates.map(p => p[1])), max_lat: Math.max(...coordinates.map(p => p[1])),
    } : null,
    summary: {
      route_count: result.summary.route_count, stop_count: result.routes.reduce((n, route) => n + route.stop_count, 0),
      passenger_count: result.summary.rider_count,
      distance_m: measured.length ? measured.reduce((n, route) => n + (route.distance_m || 0), 0) : null,
      duration_s: measured.length ? Math.max(...measured.map(route => route.duration_s!)) : null,
      measurement_unavailable_route_count: routes.length - measured.length,
    },
  };
}
