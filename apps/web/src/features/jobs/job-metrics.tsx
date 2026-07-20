import type { JobRecord } from "@/lib/api";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatDistanceKmFromMeters, formatNumber, formatPercent } from "@/lib/format";
import { currentPlanAssignmentCount, jobInputStopCount } from "@/features/jobs/summary-metrics";
import { useT } from "@/lib/i18n/context";

export function JobMetrics({ job }: { job: JobRecord }) {
  const t = useT();
  const summary = job.prepared_payload_summary || {};
  const result = asRecord(job.result);
  const structured = asRecord(result.structured_results);
  const currentPlan = asOptionalRecord(result.current_plan_assessment) ?? asRecord(structured.current_plan_assessment);
  const currentDistanceM = currentPlan.total_distance_m ?? currentPlan.total_route_distance_m;
  const runParameters = buildRunParameterItems(job);

  const metrics = [
    {
      label: "Effective stops",
      value: formatNumber(jobInputStopCount(summary)),
      detail: "Unique stop points used for route planning.",
    },
    {
      label: "Current routes",
      value: formatNumber(summary.current_plan_route_count),
      detail: "Routes in the uploaded current plan.",
    },
    {
      label: "Student assignments",
      value: formatNumber(currentPlanAssignmentCount(summary)),
      detail: "Rider records from the workbook; this can exceed stops when students share a pickup point.",
    },
    {
      label: "Current distance",
      value: formatDistanceKmFromMeters(currentDistanceM),
      detail: "Total distance in the uploaded current plan.",
    },
  ];

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <Card key={metric.label}>
            <CardHeader>
              <div className="text-xs font-medium uppercase text-muted-foreground" title={t(metric.detail)}>
                {t(metric.label)}
              </div>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold text-foreground">{metric.value}</div>
              <div className="mt-1 min-h-8 text-xs leading-4 text-muted-foreground">{t(metric.detail)}</div>
            </CardContent>
          </Card>
        ))}
      </div>
      {runParameters.length ? <RunParametersCard items={runParameters} /> : null}
    </div>
  );
}

type RunParameterItem = {
  label: string;
  value: string;
  detail?: string;
  translateValue?: boolean;
};

function RunParametersCard({ items }: { items: RunParameterItem[] }) {
  const t = useT();
  return (
    <Card>
      <CardHeader>
        <div className="text-xs font-medium uppercase text-muted-foreground">{t("Run parameters")}</div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
          {items.map((item) => (
            <div key={item.label} className="min-w-0">
              <div className="text-xs font-medium uppercase text-muted-foreground">{t(item.label)}</div>
              <div className="mt-1 break-words font-medium text-foreground">
                {item.translateValue ? t(item.value) : item.value}
              </div>
              {item.detail ? <div className="mt-1 text-xs leading-4 text-muted-foreground">{t(item.detail)}</div> : null}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return asOptionalRecord(value) ?? {};
}

function asOptionalRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function buildRunParameterItems(job: JobRecord): RunParameterItem[] {
  const result = asRecord(job.result);
  const metadata = asRecord(job.metadata);
  const config = {
    ...asRecord(metadata.planner_config),
    ...asRecord(result.config),
    ...asRecord(result.planner_config),
  };
  const items: RunParameterItem[] = [];

  const serviceDirection = stringValue(result.service_direction || config.service_direction);
  if (serviceDirection) {
    items.push({ label: "Service direction", value: serviceDirection, translateValue: true });
  }

  const comfortLoadFactor = finiteNumber(config.comfort_load_factor);
  if (comfortLoadFactor !== null) {
    const comfortEnabled = comfortLoadFactor < 0.999;
    items.push({
      label: "Comfort",
      value: comfortEnabled ? formatPercent(comfortLoadFactor, 100) : "Off",
      detail: comfortEnabled ? "Comfort capacity cap" : "Full capacity allowed",
      translateValue: !comfortEnabled,
    });
  }

  const trafficProfile = stringValue(result.traffic_profile_name || config.traffic_profile_name || config.traffic_assumption);
  if (trafficProfile) {
    items.push({ label: "Traffic profile", value: trafficProfile, translateValue: true });
  }

  const windowStart = stringValue(config.time_window_start);
  const windowEnd = stringValue(config.time_window_end);
  if (windowStart || windowEnd) {
    items.push({ label: "Time window", value: [windowStart, windowEnd].filter(Boolean).join(" - ") });
  }

  const fromSchoolDeparture = stringValue(config.from_school_departure_time);
  if (serviceDirection === "From School" && fromSchoolDeparture) {
    items.push({ label: "PM departure", value: fromSchoolDeparture });
  }

  const dwellMinutes = finiteNumber(config.stop_service_minutes);
  if (dwellMinutes !== null) {
    items.push({ label: "Stop dwell", value: `${formatNumber(dwellMinutes)} min / stop` });
  }

  const stopLimit = finiteNumber(config.route_stop_limit);
  items.push({
    label: "Stops Limit",
    value: stopLimit === null ? "No limit" : formatNumber(stopLimit),
    translateValue: stopLimit === null,
  });

  const minimumSaving = finiteNumber(config.minimum_vehicle_reduction);
  if (minimumSaving !== null) {
    items.push({
      label: "Minimum Saving",
      value: formatNumber(minimumSaving),
      detail: "Required vehicle reduction versus current plan.",
    });
  }

  const timeImpactLimit = finiteNumber(config.time_impact_limit_minutes);
  if (timeImpactLimit !== null) {
    items.push({
      label: "Time Impact Limit",
      value: `${formatNumber(timeImpactLimit)} min`,
      detail: "Used by the X-minute time-impact scenarios.",
    });
  }

  const fleetLimits = formatFleetLimits(config);
  if (fleetLimits) {
    items.push({ label: "Fleet limits", value: fleetLimits });
  }

  return items;
}

function formatFleetLimits(config: Record<string, unknown>): string {
  const entries = [
    [config.large_bus_name || "Large Bus", config.large_bus_max_count],
    [config.mid_bus_name || "Mid Bus", config.mid_bus_max_count],
    [config.small_bus_name || "Small Bus", config.small_bus_max_count],
  ]
    .map(([name, count]) => {
      const numericCount = finiteNumber(count);
      return numericCount === null ? "" : `${formatNumber(numericCount)} ${stringValue(name)}`;
    })
    .filter(Boolean);
  return entries.join(" | ");
}

function finiteNumber(value: unknown): number | null {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : null;
}

function stringValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
