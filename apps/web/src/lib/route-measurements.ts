export type RouteDisplayMetrics = {
  duration_s: number | null;
  distance_m: number | null;
  source: "measurement" | "historical_measurement" | "planning_reference" | "unavailable";
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function metric(value: unknown): number | null {
  if ((typeof value !== "number" && typeof value !== "string") || (typeof value === "string" && !value.trim())) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
}

export function routeDisplayMetrics(route: Record<string, unknown>): RouteDisplayMetrics {
  const unavailable: RouteDisplayMetrics = { duration_s: null, distance_m: null, source: "unavailable" };
  const display = record(route.display_metrics);
  if (display.source) {
    if (display.source === "unavailable") return unavailable;
    if (["measurement", "historical_measurement", "planning_reference"].includes(String(display.source))) {
      return { duration_s: metric(display.duration_s), distance_m: metric(display.distance_m), source: display.source as RouteDisplayMetrics["source"] };
    }
    return unavailable;
  }
  const evidence = record(route.route_evidence);
  const gate = record(route.final_route_traffic_gate || route.am_arrival_gate);
  if (Object.keys(evidence).length) {
    if (evidence.status !== "verified" || evidence.complete !== true || (Array.isArray(evidence.issues) && evidence.issues.length)) return unavailable;
    const drive = metric(evidence.duration_s), distance = metric(evidence.distance_m), dwell = metric(route.stop_service_time_s);
    if (drive === null || distance === null) return unavailable;
    const total = dwell === null ? null : drive + dwell;
    for (const [key, value] of [["verified_drive_duration_s", drive], ["verified_distance_m", distance], ["verified_total_duration_s", total]] as const) {
      const saved = metric(gate[key]);
      if (value !== null && saved !== null && Math.abs(value - saved) > .01) return unavailable;
    }
    return { duration_s: total, distance_m: distance, source: "measurement" };
  }
  if (gate.status === "unavailable" || ["needs_review", "unavailable"].includes(String(route.evidence_status))) return unavailable;
  if (["passed", "failed"].includes(String(gate.status))) {
    return { duration_s: metric(gate.verified_total_duration_s), distance_m: metric(gate.verified_distance_m), source: "historical_measurement" };
  }
  return { duration_s: metric(route.time_s) ?? metric(route.duration_s),
    distance_m: metric(route.distance_m) ?? metric(route.total_distance_m), source: "planning_reference" };
}

export function routeMeasurementLabel(route: Record<string, unknown>): string {
  const labels = { measurement: "Measured route", historical_measurement: "Historical measurement",
    planning_reference: "Planning reference", unavailable: "Needs review" };
  return labels[routeDisplayMetrics(route).source];
}

export function sumAvailable(values: Array<number | null>): number | null {
  return values.some(value => value === null || !Number.isFinite(value) || value < 0)
    ? null : metric((values as number[]).reduce((total, value) => total + value, 0));
}

export function maxAvailable(values: Array<number | null>): number | null {
  return values.some(value => value === null || !Number.isFinite(value) || value < 0)
    ? null : Math.max(0, ...(values as number[]));
}

export function measuredTotalDuration(routes: Record<string, unknown>[]): number | null {
  if (!routes.length) return null;
  return sumAvailable(routes.map(route => {
    const display = routeDisplayMetrics(route);
    return display.source === "measurement" || display.source === "historical_measurement"
      ? display.duration_s : null;
  }));
}

export function compareAvailableDurations(left: number | null, right: number | null): number {
  const a = metric(left), b = metric(right);
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return a - b;
}

export function summaryMeasurementValue(payload: Record<string, unknown>, key: string): unknown {
  const summary = record(payload.display_summary);
  return Object.prototype.hasOwnProperty.call(summary, key) ? summary[key] : payload[key];
}
