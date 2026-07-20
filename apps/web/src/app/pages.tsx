import { lazy, Suspense, type ReactNode, useState } from "react";
import { Link, Outlet, useNavigate, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    Ban,
    CheckCircle2,
    Clock3,
    ListChecks,
    Loader2,
    Play,
    Trash2,
    XCircle,
} from "lucide-react";
import { AppShell } from "@/features/shell/app-shell";
import { HistorySidebar } from "@/components/history-sidebar";
import { JobMetrics } from "@/features/jobs/job-metrics";
import { JobTable } from "@/features/jobs/job-table";
import {
    cancelJob,
    deleteJob,
    getDeploymentFeatures,
    getJob,
    listJobs,
    releaseJob,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDateTime, formatNumber, formatRuntime } from "@/lib/format";
import { getJobName, getJobStatusTone } from "@/features/jobs/status";
import { jobInputStopCount } from "@/features/jobs/summary-metrics";
import { LanguageProvider, useT } from "@/lib/i18n/context";

const JobResultView = lazy(() =>
    import("@/features/results/job-result-view").then((module) => ({ default: module.JobResultView })),
);

export function RootLayout() {
    const featuresQuery = useQuery({
        queryKey: ["deployment-features"],
        queryFn: getDeploymentFeatures,
        staleTime: 60_000,
    });

    return (
        <LanguageProvider
            switchEnabled={featuresQuery.data?.language_switch_enabled ?? false}
            availableLanguages={featuresQuery.data?.available_languages}
        >
            <AppShell>
                <Outlet />
            </AppShell>
        </LanguageProvider>
    );
}

export function DashboardPage() {
    const t = useT();
    const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: listJobs });
    const jobs = jobsQuery.data || [];
    const runningCount = jobs.filter((job) =>
        ["scheduled", "queued", "running"].includes(job.status),
    ).length;
    const succeededCount = jobs.filter(
        (job) => job.status === "succeeded",
    ).length;
    const failedCount = jobs.filter((job) =>
        ["failed", "canceled"].includes(job.status),
    ).length;

    return (
        <div className="space-y-6 pb-16 lg:pb-0">
            <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
                <div>
                    <p className="text-sm font-medium text-primary">
                        {t("Route planning")}
                    </p>
                    <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">
                        {t("Planning dashboard")}
                    </h1>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                        {t(
                            "Run current-plan audits, compare baseline scenarios, review maps, and collect report outputs from one workspace.",
                        )}
                    </p>
                </div>
                <Link to="/jobs" className={buttonClassName("primary")}>
                    <ListChecks className="h-4 w-4" aria-hidden="true" />
                    {t("Open Route Audit")}
                </Link>
            </section>

            <div className="grid gap-3 md:grid-cols-3">
                <StatusPanel
                    label={t("Running")}
                    value={String(runningCount)}
                    detail={t("Queued or active")}
                    icon={<Clock3 className="h-4 w-4" aria-hidden="true" />}
                />
                <StatusPanel
                    label={t("Succeeded")}
                    value={String(succeededCount)}
                    detail={t("Completed audits")}
                    icon={
                        <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                    }
                />
                <StatusPanel
                    label={t("Attention")}
                    value={String(failedCount)}
                    detail={t("Failed or canceled")}
                    icon={<XCircle className="h-4 w-4" aria-hidden="true" />}
                />
            </div>

            <section className="space-y-3">
                <div className="flex items-center justify-between">
                    <h2 className="text-base font-semibold">
                        {t("Recent audits")}
                    </h2>
                    <Badge tone="info">
                        {jobs.length} {t("visible")}
                    </Badge>
                </div>
                {jobsQuery.isLoading ? (
                    <LoadingState label={t("Loading jobs")} />
                ) : (
                    <JobTable jobs={jobs.slice(0, 5)} />
                )}
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
    const t = useT();
    const [desktopHistoryCollapsed, setDesktopHistoryCollapsed] =
        useState(true);
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const jobsQuery = useQuery({
        queryKey: ["jobs"],
        queryFn: listJobs,
        refetchInterval: (query) => jobsRefreshInterval(query.state.data),
        refetchIntervalInBackground: false,
    });
    const jobs = jobsQuery.data || [];
    const resolvedJobId = selectedJobId || "";

    const historyDeleteMutation = useMutation({
        mutationFn: (jobId: string) => deleteJob(jobId),
        onSuccess: async (_data, deletedJobId) => {
            const deletedJobIndex = jobs.findIndex(
                (job) => job.job_id === deletedJobId,
            );
            const replacementJobId =
                deletedJobIndex >= 0
                    ? (jobs[deletedJobIndex + 1]?.job_id ??
                        jobs[deletedJobIndex - 1]?.job_id ??
                        "")
                    : "";
            const shouldReplaceSelection =
                deletedJobId === selectedJobId || deletedJobId === resolvedJobId;
            queryClient.removeQueries({ queryKey: ["jobs", deletedJobId] });
            await queryClient.invalidateQueries({ queryKey: ["jobs"] });
            if (shouldReplaceSelection) {
                if (replacementJobId) {
                    await navigate({
                        to: "/jobs/$jobId",
                        params: { jobId: replacementJobId },
                    });
                } else {
                    await navigate({ to: "/jobs" });
                }
            }
        },
    });
    const bulkHistoryDeleteMutation = useMutation({
        mutationFn: async (jobIds: string[]) => {
            for (const jobId of jobIds) {
                await deleteJob(jobId);
            }
            return jobIds;
        },
        onSuccess: async (deletedJobIds) => {
            const deletedSet = new Set(deletedJobIds);
            for (const jobId of deletedJobIds) {
                queryClient.removeQueries({ queryKey: ["jobs", jobId] });
            }
            await queryClient.invalidateQueries({ queryKey: ["jobs"] });
            if (deletedSet.has(resolvedJobId)) {
                const replacementJobId =
                    jobs.find((job) => !deletedSet.has(job.job_id))?.job_id ??
                    "";
                if (replacementJobId) {
                    await navigate({
                        to: "/jobs/$jobId",
                        params: { jobId: replacementJobId },
                    });
                } else {
                    await navigate({ to: "/jobs" });
                }
            }
        },
    });
    const deletingJobId = historyDeleteMutation.isPending
        ? (historyDeleteMutation.variables ?? null)
        : null;
    const deleteError =
        (historyDeleteMutation.error as Error | null) ||
        (bulkHistoryDeleteMutation.error as Error | null);

    return (
        <div className="space-y-4 pb-16 lg:pb-0">
            <section className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
                <div>
                    <h1 className="text-2xl font-semibold tracking-normal">
                        {t("Route Audit")}
                    </h1>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                        {t(
                            "Select an audit run from history and review its metrics, maps, actions, and reports in the same workspace.",
                        )}
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Link to="/new" className={buttonClassName("secondary")}>
                        {t("New audit")}
                    </Link>
                    <Button
                        type="button"
                        variant="secondary"
                        icon={
                            <Loader2
                                className={
                                    jobsQuery.isFetching
                                        ? "h-4 w-4 animate-spin"
                                        : "h-4 w-4"
                                }
                                aria-hidden="true"
                            />
                        }
                        onClick={() => void jobsQuery.refetch()}
                    >
                        {t("Refresh")}
                    </Button>
                </div>
            </section>

            <div
                className={[
                    "grid gap-4",
                    desktopHistoryCollapsed
                        ? "lg:grid-cols-[88px_minmax(0,1fr)]"
                        : "lg:grid-cols-[320px_minmax(0,1fr)]",
                ].join(" ")}
            >
                <HistorySidebar
                    items={jobs}
                    itemId={(job) => job.job_id}
                    activeId={resolvedJobId}
                    title="Audit History"
                    emptyMessage="Submitted audit runs will appear here after workbook validation and queue submission."
                    collapsed={desktopHistoryCollapsed}
                    onCollapsedChange={setDesktopHistoryCollapsed}
                    isLoading={jobsQuery.isLoading}
                    isFetching={jobsQuery.isFetching}
                    error={(jobsQuery.error as Error | null) || deleteError}
                    deletingId={deletingJobId || undefined}
                    bulkDeleting={bulkHistoryDeleteMutation.isPending}
                    onRefresh={() => void jobsQuery.refetch()}
                    onOpen={(jobId) => {
                        void navigate({
                            to: "/jobs/$jobId",
                            params: { jobId },
                        });
                    }}
                    onDelete={(jobId) => historyDeleteMutation.mutate(jobId)}
                    onBulkDelete={(jobIds) => bulkHistoryDeleteMutation.mutate(jobIds)}
                    selectionActionLabel="Compare operations"
                    selectionActionMin={2}
                    groupScope="route_audit"
                    onSelectionAction={(jobIds) => {
                        void navigate({
                            to: "/operations-review/$jobIds",
                            params: { jobIds: jobIds.join(",") },
                        });
                    }}
                    renderItem={(job, active) => (
                        <AuditHistoryItem job={job} active={active} />
                    )}
                    className="min-w-0 lg:sticky lg:top-20 lg:self-start"
                />

                {resolvedJobId ? (
                    <JobDetailPanel jobId={resolvedJobId} />
                ) : (
                    <EmptyState
                        title={t("Select an audit")}
                        detail={t(
                            "Choose a run from history to inspect its result.",
                        )}
                        action={
                            <Link
                                to="/new"
                                className={buttonClassName("primary")}
                            >
                                {t("New audit")}
                            </Link>
                        }
                    />
                )}
            </div>
        </div>
    );
}

function AuditHistoryItem({
    job,
    active,
}: {
    job: Awaited<ReturnType<typeof listJobs>>[number];
    active: boolean;
}) {
    const t = useT();
    const summary = job.prepared_payload_summary || {};
    const scheduledStartAt = getScheduledStartAt(job);
    const secondaryClass = active
        ? "text-primary-foreground/80"
        : "text-muted-foreground";
    return (
        <div className="min-w-0 px-1 py-1">
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="truncate text-sm font-semibold">
                        {getJobName(job)}
                    </div>
                    <div className={`mt-1 text-xs ${secondaryClass}`}>
                        {formatDateTime(job.created_at)}
                    </div>
                    {scheduledStartAt ? (
                        <div className={`mt-1 text-xs ${secondaryClass}`}>
                            {t("Scheduled for")} {formatDateTime(scheduledStartAt)}
                        </div>
                    ) : null}
                </div>
                <Badge tone={active ? "neutral" : getJobStatusTone(job.status)}>
                    {t(job.status)}
                </Badge>
            </div>
            <div className={`mt-2 grid grid-cols-2 gap-2 text-xs ${secondaryClass}`}>
                <span>
                    {formatNumber(jobInputStopCount(summary))} {t("stops")}
                </span>
                <span>
                    {formatNumber(summary.current_plan_route_count)} {t("routes")}
                </span>
            </div>
        </div>
    );
}

function getScheduledStartAt(job: {
    scheduled_start_at?: string | null;
    metadata?: Record<string, unknown>;
}): string | null {
    if (typeof job.scheduled_start_at === "string" && job.scheduled_start_at.trim()) {
        return job.scheduled_start_at;
    }
    const metadataValue = job.metadata?.scheduled_start_at;
    return typeof metadataValue === "string" && metadataValue.trim() ? metadataValue : null;
}

function scheduledRefreshInterval(job: Parameters<typeof getScheduledStartAt>[0]) {
    const scheduledStartAt = getScheduledStartAt(job);
    const scheduledTime = scheduledStartAt ? Date.parse(scheduledStartAt) : Number.NaN;
    return Number.isFinite(scheduledTime) && scheduledTime - Date.now() <= 60_000
        ? 5_000
        : 60_000;
}

function jobsRefreshInterval(jobs?: Awaited<ReturnType<typeof listJobs>>) {
    if (!jobs?.length) {
        return false;
    }
    if (jobs.some((job) => job.status === "queued" || job.status === "running")) {
        return 15_000;
    }
    const scheduledJobs = jobs.filter((job) => job.status === "scheduled");
    return scheduledJobs.length
        ? Math.min(...scheduledJobs.map(scheduledRefreshInterval))
        : false;
}

function jobRefreshInterval(job?: Awaited<ReturnType<typeof getJob>>) {
    if (!job) {
        return false;
    }
    if (job.status === "queued" || job.status === "running") {
        return 5_000;
    }
    return job.status === "scheduled" ? scheduledRefreshInterval(job) : false;
}

function JobDetailPanel({ jobId }: { jobId: string }) {
    const t = useT();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const jobQuery = useQuery({
        queryKey: ["jobs", jobId],
        queryFn: () => getJob(jobId),
        refetchInterval: (query) => jobRefreshInterval(query.state.data),
        refetchIntervalInBackground: false,
    });
    const cancelMutation = useMutation({
        mutationFn: () => cancelJob(jobId),
        onSuccess: async () => {
            await queryClient.invalidateQueries({ queryKey: ["jobs", jobId] });
            await queryClient.invalidateQueries({ queryKey: ["jobs"] });
        },
    });
    const releaseMutation = useMutation({
        mutationFn: () => releaseJob(jobId),
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
        return <LoadingState label={t("Loading job")} />;
    }

    if (jobQuery.error || !jobQuery.data) {
        return (
            <EmptyState
                title={t("Job could not load")}
                detail={
                    jobQuery.error instanceof Error
                        ? jobQuery.error.message
                        : t("The backend did not return a job record.")
                }
            />
        );
    }

    const job = jobQuery.data;
    const jobIsActive = job.status === "scheduled" || job.status === "queued" || job.status === "running";
    const jobIsScheduled = job.status === "scheduled";
    const scheduledStartAt = getScheduledStartAt(job);

    return (
        <div className="min-w-0 space-y-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0">
                    <h1 className="break-words text-2xl font-semibold tracking-normal">
                        {getJobName(job)}
                    </h1>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span className="font-mono">{job.job_id}</span>
                        <span>
                            {t("Submitted by")}{" "}
                            {job.owner_email || t("Unknown")}
                        </span>
                    </div>
                </div>
                <Badge tone={getJobStatusTone(job.status)}>{t(job.status)}</Badge>
            </div>

            <JobMetrics job={job} />

            <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
                <Suspense fallback={<LoadingState label={t("Loading job")} />}>
                    <JobResultView job={job} />
                </Suspense>

                <aside className="space-y-4">
                    <Card>
                        <CardHeader>
                            <h2 className="text-sm font-semibold">
                                {t("Timeline")}
                            </h2>
                        </CardHeader>
                        <CardContent>
                            <dl className="space-y-4 text-sm">
                                <TimelineItem
                                    label={t("Created")}
                                    value={formatDateTime(job.created_at)}
                                />
                                {scheduledStartAt ? (
                                    <TimelineItem
                                        label={t("Scheduled for")}
                                        value={formatDateTime(scheduledStartAt)}
                                    />
                                ) : null}
                                <TimelineItem
                                    label={t("Started")}
                                    value={formatDateTime(job.started_at)}
                                />
                                <TimelineItem
                                    label={t("Finished")}
                                    value={formatDateTime(job.finished_at)}
                                />
                                <TimelineItem
                                    label={t("Runtime")}
                                    value={formatRuntime(job.started_at, job.finished_at)}
                                />
                                <TimelineItem
                                    label={t("Owner")}
                                    value={job.owner_email || t("Unknown")}
                                />
                            </dl>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <h2 className="text-sm font-semibold">
                                {t("Job actions")}
                            </h2>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {jobIsScheduled ? (
                                <Button
                                    type="button"
                                    variant="primary"
                                    className="w-full"
                                    disabled={
                                        releaseMutation.isPending ||
                                        cancelMutation.isPending ||
                                        deleteMutation.isPending
                                    }
                                    icon={
                                        releaseMutation.isPending ? (
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                        ) : (
                                            <Play className="h-4 w-4" />
                                        )
                                    }
                                    onClick={() => {
                                        if (window.confirm(t("Release this scheduled job now?"))) {
                                            releaseMutation.mutate();
                                        }
                                    }}
                                >
                                    {t("Release now")}
                                </Button>
                            ) : null}
                            <Button
                                type="button"
                                variant="secondary"
                                className="w-full"
                                disabled={
                                    !jobIsActive ||
                                    releaseMutation.isPending ||
                                    cancelMutation.isPending ||
                                    deleteMutation.isPending
                                }
                                icon={
                                    cancelMutation.isPending ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <Ban className="h-4 w-4" />
                                    )
                                }
                                onClick={() => {
                                    if (window.confirm(t("Cancel this job?"))) {
                                        cancelMutation.mutate();
                                    }
                                }}
                            >
                                {t("Cancel job")}
                            </Button>
                            <Button
                                type="button"
                                variant="secondary"
                                className="w-full"
                                disabled={
                                    deleteMutation.isPending ||
                                    releaseMutation.isPending ||
                                    cancelMutation.isPending
                                }
                                icon={
                                    deleteMutation.isPending ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <Trash2 className="h-4 w-4" />
                                    )
                                }
                                onClick={() => {
                                    if (
                                        window.confirm(
                                            t(
                                                "Delete this job from local history? This cannot be undone.",
                                            ),
                                        )
                                    ) {
                                        deleteMutation.mutate();
                                    }
                                }}
                            >
                                {t("Delete job")}
                            </Button>
                            {!jobIsActive ? (
                                <div className="text-xs leading-5 text-muted-foreground">
                                {t(
                                    "Cancel is available only while a job is scheduled, queued, or running.",
                                )}
                                </div>
                            ) : null}
                            {cancelMutation.error ? (
                                <InlineError
                                    message={
                                        (cancelMutation.error as Error).message
                                    }
                                />
                            ) : null}
                            {releaseMutation.error ? (
                                <InlineError
                                    message={
                                        (releaseMutation.error as Error).message
                                    }
                                />
                            ) : null}
                            {deleteMutation.error ? (
                                <InlineError
                                    message={
                                        (deleteMutation.error as Error).message
                                    }
                                />
                            ) : null}
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
                    <span className="text-xs font-medium uppercase">
                        {label}
                    </span>
                    {icon}
                </div>
                <div>
                    <div className="text-2xl font-semibold">{value}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                        {detail}
                    </div>
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
            <Loader2
                className="mr-2 h-4 w-4 animate-spin text-primary"
                aria-hidden="true"
            />
            {label}
        </div>
    );
}

function InlineError({ message }: { message: string }) {
    return (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {message}
        </div>
    );
}
