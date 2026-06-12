import type { JobRecord } from "@/lib/api";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatDistanceKmFromMeters, formatNumber } from "@/lib/format";
import { currentPlanAssignmentCount, jobInputStopCount } from "@/features/jobs/summary-metrics";

export function JobMetrics({ job }: { job: JobRecord }) {
  const summary = job.prepared_payload_summary || {};
  const result = asRecord(job.result);
  const structured = asRecord(result.structured_results);
  const currentPlan = asOptionalRecord(result.current_plan_assessment) ?? asRecord(structured.current_plan_assessment);
  const currentDistanceM = currentPlan.total_distance_m ?? currentPlan.total_route_distance_m;

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
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map((metric) => (
        <Card key={metric.label}>
          <CardHeader>
            <div className="text-xs font-medium uppercase text-muted-foreground" title={metric.detail}>
              {metric.label}
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold text-foreground">{metric.value}</div>
            <div className="mt-1 min-h-8 text-xs leading-4 text-muted-foreground">{metric.detail}</div>
          </CardContent>
        </Card>
      ))}
    </div>
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
