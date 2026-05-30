import { Link } from "@tanstack/react-router";
import { ArrowRight, CircleAlert } from "lucide-react";
import type { JobSummary } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDateTime, formatNumber } from "@/lib/format";
import { getJobName, getJobStatusTone } from "@/features/jobs/status";
import { jobInputStopCount } from "@/features/jobs/summary-metrics";

export function JobTable({ jobs }: { jobs: JobSummary[] }) {
  if (!jobs.length) {
    return (
      <EmptyState
        title="No jobs yet"
        detail="Submitted planning jobs will appear here after workbook validation and queue submission."
      />
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface shadow-panel">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] border-collapse text-sm">
          <thead className="bg-muted text-left text-xs font-semibold uppercase text-muted-foreground">
            <tr>
              <th className="px-4 py-3">Job</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">Stops</th>
              <th className="px-4 py-3">Routes</th>
              <th className="px-4 py-3">Owner</th>
              <th className="px-4 py-3 text-right">Open</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const summary = job.prepared_payload_summary || {};
              return (
                <tr key={job.job_id} className="border-t border-border">
                  <td className="max-w-[260px] px-4 py-3">
                    <div className="truncate font-medium text-foreground">{getJobName(job)}</div>
                    <div className="mt-1 font-mono text-xs text-muted-foreground">{job.job_id}</div>
                    {job.error ? (
                      <div className="mt-2 flex items-start gap-1.5 text-xs text-destructive">
                        <CircleAlert className="mt-0.5 h-3.5 w-3.5 flex-none" aria-hidden="true" />
                        <span className="line-clamp-2">{job.error}</span>
                      </div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={getJobStatusTone(job.status)}>{job.status}</Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{formatDateTime(job.created_at)}</td>
                  <td className="px-4 py-3">{formatNumber(jobInputStopCount(summary))}</td>
                  <td className="px-4 py-3">{formatNumber(summary.current_plan_route_count)}</td>
                  <td className="max-w-[220px] truncate px-4 py-3 text-muted-foreground">
                    {job.owner_email || "Unknown"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      to="/jobs/$jobId"
                      params={{ jobId: job.job_id }}
                      className={buttonClassName("ghost")}
                      aria-label={`Open job ${job.job_id}`}
                    >
                      <ArrowRight className="h-4 w-4" aria-hidden="true" />
                      View
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
