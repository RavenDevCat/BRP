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
    ["Input stops", formatNumber(jobInputStopCount(summary))],
    ["Current routes", formatNumber(summary.current_plan_route_count)],
    ["Assignments", formatNumber(currentPlanAssignmentCount(summary))],
    ["Current distance", formatDistanceKmFromMeters(currentDistanceM)],
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map(([label, value]) => (
        <Card key={label}>
          <CardHeader>
            <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold text-foreground">{value}</div>
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
