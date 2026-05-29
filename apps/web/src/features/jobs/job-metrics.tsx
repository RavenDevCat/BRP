import type { JobRecord } from "@/lib/api";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatNumber } from "@/lib/format";

export function JobMetrics({ job }: { job: JobRecord }) {
  const summary = job.prepared_payload_summary || {};
  const result = job.result || {};
  const structured = result.structured_results as Record<string, unknown> | undefined;
  const currentPlan = structured?.current_plan_assessment as Record<string, unknown> | undefined;

  const metrics = [
    ["Input stops", formatNumber(summary.input_record_count)],
    ["Current routes", formatNumber(summary.current_plan_route_count)],
    ["Assignments", formatNumber(summary.current_plan_assignment_count)],
    ["Current distance", formatDistance(currentPlan?.total_route_distance_m)],
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

function formatDistance(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "Not available";
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value / 1000)} km`;
}
