import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { Link, Outlet, useNavigate, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, ChevronDown, ChevronUp, Clock3, ListChecks, Loader2, Trash2, XCircle } from "lucide-react";
import { AppShell } from "@/features/shell/app-shell";
import { JobMetrics } from "@/features/jobs/job-metrics";
import { JobTable } from "@/features/jobs/job-table";
import { JobResultView } from "@/features/results/job-result-view";
import { cancelJob, deleteJob, getHealth, getJob, listJobs } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDateTime, formatNumber } from "@/lib/format";
import { getJobName, getJobStatusTone } from "@/features/jobs/status";
import { jobInputStopCount } from "@/features/jobs/summary-metrics";

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
          <p className="text-sm font-medium text-primary">Route planning</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">
            Planning dashboard
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Run current-plan audits, compare baseline scenarios, review maps, and collect report outputs from one workspace.
          </p>
        </div>
        <Link to="/jobs" className={buttonClassName("primary")}>
          <ListChecks className="h-4 w-4" aria-hidden="true" />
          Open Route Audit
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
          detail="Completed audits"
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
          <h2 className="text-base font-semibold">Recent audits</h2>
          <Badge tone="info">{jobs.length} visible</Badge>
        </div>
        {jobsQuery.isLoading ? <LoadingState label="Loading jobs" /> : <JobTable jobs={jobs.slice(0, 5)} />}
      </section>
    </div>
  );
}

export function JobsPage() {
  return <JobsWorkspace />;
}

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  return <JobsWorkspace selectedJobId={jobId} />;
}

function JobsWorkspace({ selectedJobId }: { selectedJobId?: string }) {
  const [historyOpen, setHistoryOpen] = useState(!selectedJobId);
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: listJobs, refetchInterval: 15_000 });
  const jobs = jobsQuery.data || [];
  const resolvedJobId = selectedJobId || jobs[0]?.job_id || "";
  const selectedJob = jobs.find((job) => job.job_id === resolvedJobId);

  useEffect(() => {
    if (selectedJobId) {
      setHistoryOpen(false);
    }
  }, [selectedJobId]);

  return (
    <div className="space-y-4 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal">Route Audit</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Select an audit run from history and review its metrics, maps, actions, and reports in the same workspace.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/new" className={buttonClassName("secondary")}>
            New audit
          </Link>
          <Button
            type="button"
            variant="secondary"
            icon={<Loader2 className={jobsQuery.isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} aria-hidden="true" />}
            onClick={() => void jobsQuery.refetch()}
          >
            Refresh
          </Button>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <Card className="min-w-0 xl:sticky xl:top-20 xl:self-start">
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold">History</h2>
                  <Badge tone="info">{formatNumber(jobs.length)}</Badge>
                </div>
                {selectedJob ? (
                  <div className="mt-1 truncate text-xs text-muted-foreground xl:hidden">
                    {getJobName(selectedJob)}
                  </div>
                ) : null}
              </div>
              <Button
                type="button"
                variant="ghost"
                className="xl:hidden"
                icon={
                  historyOpen ? (
                    <ChevronUp className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <ChevronDown className="h-4 w-4" aria-hidden="true" />
                  )
                }
                onClick={() => setHistoryOpen((open) => !open)}
              >
                {historyOpen ? "Hide" : "Show"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className={historyOpen ? "block" : "hidden xl:block"}>
            {jobsQuery.error ? (
              <InlineError message={(jobsQuery.error as Error).message} />
            ) : jobsQuery.isLoading ? (
              <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin text-primary" aria-hidden="true" />
                Loading jobs
              </div>
            ) : jobs.length ? (
              <JobHistorySubList jobs={jobs} selectedJobId={resolvedJobId} />
            ) : (
              <EmptyState
                title="No audits yet"
                detail="Submitted audit runs will appear here after workbook validation and queue submission."
              />
            )}
          </CardContent>
        </Card>

        {resolvedJobId ? (
          <JobDetailPanel jobId={resolvedJobId} />
        ) : (
          <EmptyState
            title="Select an audit"
            detail="Choose a run from history to inspect its result."
            action={
              <Link to="/new" className={buttonClassName("primary")}>
                New audit
              </Link>
            }
          />
        )}
      </div>
    </div>
  );
}

function JobHistorySubList({
  jobs,
  selectedJobId,
}: {
  jobs: Awaited<ReturnType<typeof listJobs>>;
  selectedJobId: string;
}) {
  return (
    <div className="max-h-72 space-y-2 overflow-auto pr-1 xl:max-h-[calc(100vh-220px)]">
      {jobs.map((job) => {
        const active = job.job_id === selectedJobId;
        const summary = job.prepared_payload_summary || {};
        return (
          <Link
            key={job.job_id}
            to="/jobs/$jobId"
            params={{ jobId: job.job_id }}
            className={[
              "block rounded-md border px-3 py-3 text-sm transition",
              active
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-surface text-foreground hover:border-primary/50 hover:bg-muted",
            ].join(" ")}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate font-semibold">{getJobName(job)}</div>
                <div className={active ? "mt-1 text-xs text-primary-foreground/75" : "mt-1 text-xs text-muted-foreground"}>
                  {formatDateTime(job.created_at)}
                </div>
              </div>
              <Badge tone={active ? "neutral" : getJobStatusTone(job.status)}>{job.status}</Badge>
            </div>
            <div className={active ? "mt-3 grid grid-cols-2 gap-2 text-xs text-primary-foreground/80" : "mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground"}>
              <span>{formatNumber(jobInputStopCount(summary))} stops</span>
              <span>{formatNumber(summary.current_plan_route_count)} routes</span>
            </div>
          </Link>
        );
      })}
    </div>
  );
}

function JobDetailPanel({ jobId }: { jobId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const jobQuery = useQuery({
    queryKey: ["jobs", jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 5_000 : false;
    },
  });
  const cancelMutation = useMutation({
    mutationFn: () => cancelJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["jobs", jobId] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await navigate({ to: "/jobs" });
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
      />
    );
  }

  const job = jobQuery.data;
  const jobIsActive = job.status === "queued" || job.status === "running";

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="break-words text-2xl font-semibold tracking-normal">{getJobName(job)}</h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{job.job_id}</p>
        </div>
        <Badge tone={getJobStatusTone(job.status)}>{job.status}</Badge>
      </div>

      <JobMetrics job={job} />

      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
        <JobResultView job={job} />

        <aside className="space-y-4">
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

          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold">Job actions</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                disabled={!jobIsActive || cancelMutation.isPending || deleteMutation.isPending}
                icon={cancelMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Ban className="h-4 w-4" />}
                onClick={() => {
                  if (window.confirm("Cancel this queued or running job?")) {
                    cancelMutation.mutate();
                  }
                }}
              >
                Cancel job
              </Button>
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                disabled={deleteMutation.isPending || cancelMutation.isPending}
                icon={deleteMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                onClick={() => {
                  if (window.confirm("Delete this job from local history? This cannot be undone.")) {
                    deleteMutation.mutate();
                  }
                }}
              >
                Delete job
              </Button>
              {!jobIsActive ? (
                <div className="text-xs leading-5 text-muted-foreground">Cancel is available only while a job is queued or running.</div>
              ) : null}
              {cancelMutation.error ? <InlineError message={(cancelMutation.error as Error).message} /> : null}
              {deleteMutation.error ? <InlineError message={(deleteMutation.error as Error).message} /> : null}
            </CardContent>
          </Card>
        </aside>
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

function InlineError({ message }: { message: string }) {
  return <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{message}</div>;
}
