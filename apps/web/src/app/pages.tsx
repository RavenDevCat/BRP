import type { ReactNode } from "react";
import { Link, Outlet, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Clock3, ListChecks, Loader2, XCircle } from "lucide-react";
import { AppShell } from "@/features/shell/app-shell";
import { JobMetrics } from "@/features/jobs/job-metrics";
import { JobTable } from "@/features/jobs/job-table";
import { getHealth, getJob, listJobs } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDateTime } from "@/lib/format";
import { getJobName, getJobStatusTone } from "@/features/jobs/status";

export function RootLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

export function DashboardPage() {
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: listJobs });
  const jobs = jobsQuery.data || [];
  const runningCount = jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
  const succeededCount = jobs.filter((job) => job.status === "succeeded").length;
  const failedCount = jobs.filter((job) => ["failed", "canceled"].includes(job.status)).length;

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-primary">React preview</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">
            Operations dashboard
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            A new frontend shell running beside Streamlit. It reads the same backend job API without changing the production client path.
          </p>
        </div>
        <Link to="/jobs" className={buttonClassName("primary")}>
          <ListChecks className="h-4 w-4" aria-hidden="true" />
          Open jobs
        </Link>
      </section>

      <div className="grid gap-3 md:grid-cols-4">
        <StatusPanel
          label="Backend"
          value={healthQuery.data?.status || "checking"}
          detail="Via /api/health"
          icon={<CheckCircle2 className="h-4 w-4" aria-hidden="true" />}
        />
        <StatusPanel
          label="Running"
          value={String(runningCount)}
          detail="Queued or active"
          icon={<Clock3 className="h-4 w-4" aria-hidden="true" />}
        />
        <StatusPanel
          label="Succeeded"
          value={String(succeededCount)}
          detail="Completed jobs"
          icon={<CheckCircle2 className="h-4 w-4" aria-hidden="true" />}
        />
        <StatusPanel
          label="Attention"
          value={String(failedCount)}
          detail="Failed or canceled"
          icon={<XCircle className="h-4 w-4" aria-hidden="true" />}
        />
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Recent jobs</h2>
          <Badge tone="info">{jobs.length} visible</Badge>
        </div>
        {jobsQuery.isLoading ? <LoadingState label="Loading jobs" /> : <JobTable jobs={jobs.slice(0, 5)} />}
      </section>
    </div>
  );
}

export function JobsPage() {
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: listJobs, refetchInterval: 15_000 });
  const jobs = jobsQuery.data || [];

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal">Jobs</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Read-only job history from the shared backend. Submission stays in Streamlit until the React upload flow is ready.
          </p>
        </div>
        <Button
          type="button"
          variant="secondary"
          icon={<Loader2 className={jobsQuery.isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} aria-hidden="true" />}
          onClick={() => void jobsQuery.refetch()}
        >
          Refresh
        </Button>
      </section>

      {jobsQuery.error ? (
        <EmptyState title="Jobs could not load" detail={(jobsQuery.error as Error).message} />
      ) : jobsQuery.isLoading ? (
        <LoadingState label="Loading jobs" />
      ) : (
        <JobTable jobs={jobs} />
      )}
    </div>
  );
}

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  const jobQuery = useQuery({
    queryKey: ["jobs", jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 5_000 : false;
    },
  });

  if (jobQuery.isLoading) {
    return <LoadingState label="Loading job" />;
  }

  if (jobQuery.error || !jobQuery.data) {
    return (
      <EmptyState
        title="Job could not load"
        detail={jobQuery.error instanceof Error ? jobQuery.error.message : "The backend did not return a job record."}
        action={
          <Link to="/jobs" className={buttonClassName("secondary")}>
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back to jobs
          </Link>
        }
      />
    );
  }

  const job = jobQuery.data;

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <Link to="/jobs" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Jobs
          </Link>
          <h1 className="mt-3 truncate text-2xl font-semibold tracking-normal">{getJobName(job)}</h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{job.job_id}</p>
        </div>
        <Badge tone={getJobStatusTone(job.status)}>{job.status}</Badge>
      </div>

      <JobMetrics job={job} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Result preview</h2>
          </CardHeader>
          <CardContent>
            {job.error ? (
              <pre className="max-h-[420px] overflow-auto rounded-md bg-red-50 p-4 text-xs leading-5 text-red-800">
                {job.error}
                {job.traceback ? `\n\n${job.traceback}` : ""}
              </pre>
            ) : job.result ? (
              <pre className="max-h-[520px] overflow-auto rounded-md bg-muted p-4 text-xs leading-5 text-muted-foreground">
                {JSON.stringify(job.result, null, 2)}
              </pre>
            ) : (
              <EmptyState
                title="No result payload yet"
                detail="Queued and running jobs update automatically. Completed jobs will expose structured results here first, before richer React result views are built."
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Timeline</h2>
          </CardHeader>
          <CardContent>
            <dl className="space-y-4 text-sm">
              <TimelineItem label="Created" value={formatDateTime(job.created_at)} />
              <TimelineItem label="Started" value={formatDateTime(job.started_at)} />
              <TimelineItem label="Finished" value={formatDateTime(job.finished_at)} />
              <TimelineItem label="Owner" value={job.owner_email || "Unknown"} />
            </dl>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatusPanel({
  label,
  value,
  detail,
  icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon: ReactNode;
}) {
  return (
    <Card>
      <CardContent className="flex min-h-28 flex-col justify-between">
        <div className="flex items-center justify-between gap-3 text-muted-foreground">
          <span className="text-xs font-medium uppercase">{label}</span>
          {icon}
        </div>
        <div>
          <div className="text-2xl font-semibold">{value}</div>
          <div className="mt-1 text-xs text-muted-foreground">{detail}</div>
        </div>
      </CardContent>
    </Card>
  );
}

function TimelineItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border pb-3 last:border-b-0 last:pb-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{value}</dd>
    </div>
  );
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex min-h-56 items-center justify-center rounded-lg border border-border bg-surface text-sm text-muted-foreground shadow-panel">
      <Loader2 className="mr-2 h-4 w-4 animate-spin text-primary" aria-hidden="true" />
      {label}
    </div>
  );
}
