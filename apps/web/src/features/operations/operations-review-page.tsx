import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
    ArrowLeft,
    CalendarDays,
    CheckCircle2,
    CircleAlert,
    GitCompareArrows,
    Loader2,
    MapPinned,
} from "lucide-react";
import { InteractiveRouteMap } from "@/features/results/interactive-route-map";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatNumber } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n/context";
import {
    getJobMapData,
    previewOperationsReview,
    type OperationsReviewCandidate,
} from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
    operationally_ready: "Operationally ready",
    conditionally_viable: "Conditionally viable",
    review_reference: "Review reference",
    insufficient_evidence: "Insufficient evidence",
};

const REASON_LABELS: Record<string, string> = {
    job_not_succeeded: "Job did not succeed",
    no_recommended_plan: "No comparable plan evidence",
};

const COMPATIBILITY_LABELS: Record<string, string> = {
    input_stops: "Workbook stops",
    input_stops_unavailable: "Workbook stops unavailable",
    current_plan: "Current Plan",
    current_plan_unavailable: "Current plan unavailable",
    service_direction: "Service direction",
    market: "Market",
    time_window: "Time window",
    solver_inputs: "Solver inputs",
};

export function OperationsReviewPage() {
    const t = useT();
    const { jobIds: rawJobIds } = useParams({ from: "/operations-review/$jobIds" });
    const jobIds = useMemo(
        () => [...new Set(rawJobIds.split(",").map((value) => value.trim()).filter(Boolean))],
        [rawJobIds],
    );
    const reviewQuery = useQuery({
        queryKey: ["operations-review", jobIds],
        queryFn: () => previewOperationsReview(jobIds),
        enabled: jobIds.length >= 2,
    });
    const review = reviewQuery.data;
    const [selectedCandidateId, setSelectedCandidateId] = useState("");

    useEffect(() => {
        if (!review?.candidates.length) return;
        setSelectedCandidateId((current) =>
            review.candidates.some((candidate) => candidate.candidate_id === current)
                ? current
                : (review.recommendation?.candidate_id ?? review.candidates[0].candidate_id),
        );
    }, [review]);

    const selectedCandidate =
        review?.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId) ??
        review?.recommendation ??
        null;
    const mapQuery = useQuery({
        queryKey: [
            "operations-review-map",
            selectedCandidate?.representative_job_id,
            selectedCandidate?.scenario_key,
        ],
        queryFn: () =>
            getJobMapData(
                selectedCandidate?.representative_job_id ?? "",
                selectedCandidate?.scenario_key ?? "",
            ),
        enabled: Boolean(
            selectedCandidate?.representative_job_id && selectedCandidate?.scenario_key,
        ),
    });

    if (reviewQuery.isLoading) {
        return <LoadingState label={t("Building operations review")} />;
    }
    if (reviewQuery.error || !review) {
        return (
            <div className="space-y-4">
                <BackLink />
                <Card>
                    <CardContent className="flex items-start gap-3 text-sm text-danger">
                        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                        {(reviewQuery.error as Error | null)?.message ?? t("Operations review is unavailable")}
                    </CardContent>
                </Card>
            </div>
        );
    }

    const statusTone = reviewStatusTone(review.status);
    return (
        <div className="space-y-5 pb-16 lg:pb-0">
            <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
                <div>
                    <BackLink />
                    <div className="mt-4 flex items-center gap-2">
                        <GitCompareArrows className="h-5 w-5 text-primary" aria-hidden="true" />
                        <h1 className="text-2xl font-semibold tracking-normal">
                            {t("Operations Plan Review")}
                        </h1>
                    </div>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                        {t("Compare compatible completed runs, find repeated plans, and select an operator reference without rerunning the solver or provider APIs.")}
                    </p>
                </div>
                <Badge tone={statusTone}>{t(STATUS_LABELS[review.status] ?? review.status)}</Badge>
            </section>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label={t("Selected jobs")} value={formatNumber(review.selected_job_count)} />
                <Metric label={t("Qualified samples")} value={formatNumber(review.qualified_sample_count)} tone="success" />
                <Metric label={t("Excluded samples")} value={formatNumber(review.excluded_sample_count)} tone={review.excluded_sample_count ? "warning" : "neutral"} />
                <Metric
                    label={t("Repeated plan samples")}
                    value={formatNumber(review.recommendation?.sample_count ?? 0)}
                    tone={review.recommendation?.sample_count && review.recommendation.sample_count > 1 ? "success" : "warning"}
                />
            </div>

            {!review.compatibility.compatible ? (
                <Card className="border-warning">
                    <CardHeader>
                        <div className="flex items-center gap-2 text-warning-foreground">
                            <CircleAlert className="h-4 w-4" aria-hidden="true" />
                            <h2 className="text-sm font-semibold">{t("Selected jobs are not comparable")}</h2>
                        </div>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        {review.compatibility.issues.map((issue) => (
                            <div key={`${issue.job_id}-${issue.fields.join("-")}`}>
                                <span className="font-medium text-foreground">{issue.job_id}</span>: {issue.fields.map((field) => t(COMPATIBILITY_LABELS[field] ?? field)).join(", ")}
                            </div>
                        ))}
                    </CardContent>
                </Card>
            ) : null}

            {review.recommendation ? (
                <DecisionPanel candidate={review.recommendation} />
            ) : (
                <Card>
                    <CardContent className="text-sm text-muted-foreground">
                        {t("At least two compatible successful runs with comparable plan results are required before a reference plan can be selected.")}
                    </CardContent>
                </Card>
            )}

            {review.candidates.length > 1 ? (
                <section className="space-y-2">
                    <h2 className="text-sm font-semibold">{t("Candidate plans")}</h2>
                    <div className="flex flex-wrap gap-2">
                        {review.candidates.map((candidate) => (
                            <button
                                key={candidate.candidate_id}
                                type="button"
                                className={
                                    selectedCandidateId === candidate.candidate_id
                                        ? buttonClassName("primary")
                                        : buttonClassName("secondary")
                                }
                                onClick={() => setSelectedCandidateId(candidate.candidate_id)}
                            >
                                {t(candidate.scenario_name)} · {formatNumber(candidate.route_count)} {t("routes")} · {formatNumber(candidate.sample_count)} {t("samples")}
                            </button>
                        ))}
                    </div>
                </section>
            ) : null}

            <DailyEvidenceTable evidence={review.daily_evidence} />

            {selectedCandidate ? (
                <section className="space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                            <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
                            <h2 className="text-base font-semibold">{t("Representative plan map")}</h2>
                        </div>
                        <Link
                            to="/jobs/$jobId"
                            params={{ jobId: selectedCandidate.representative_job_id }}
                            className={buttonClassName("secondary")}
                        >
                            {t("Open source audit")}
                        </Link>
                    </div>
                    {mapQuery.isLoading ? (
                        <LoadingState label={t("Loading representative plan map")} compact />
                    ) : mapQuery.data ? (
                        <InteractiveRouteMap data={{
                            ...mapQuery.data,
                            scenario_name: t(mapQuery.data.scenario_name),
                        }} />
                    ) : (
                        <Card>
                            <CardContent className="text-sm text-muted-foreground">
                                {(mapQuery.error as Error | null)?.message ?? t("Representative plan map is unavailable")}
                            </CardContent>
                        </Card>
                    )}
                </section>
            ) : null}
        </div>
    );
}

function BackLink() {
    const t = useT();
    return (
        <Link to="/jobs" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            {t("Audit History")}
        </Link>
    );
}

function DecisionPanel({ candidate }: { candidate: OperationsReviewCandidate }) {
    const t = useT();
    return (
        <Card>
            <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden="true" />
                        <h2 className="text-sm font-semibold">{t("Operator reference")}</h2>
                    </div>
                    <Badge tone={reviewStatusTone(candidate.status)}>
                        {t(STATUS_LABELS[candidate.status] ?? candidate.status)}
                    </Badge>
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                <div>
                    <div className="text-lg font-semibold">{t(candidate.scenario_name)}</div>
                    <div className="mt-1 text-sm text-muted-foreground">
                        {candidate.representative_job_name} · {candidate.representative_job_id}
                    </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                    <Metric label={t("Routes")} value={formatNumber(candidate.route_count)} />
                    <Metric label={t("Samples matched")} value={`${formatNumber(candidate.sample_count)} / ${formatNumber(candidate.valid_sample_count)}`} tone="success" />
                    <Metric label={t("Affected riders, worst day")} value={formatNumber(candidate.max_time_impact_affected_rider_count)} tone={candidate.max_time_impact_affected_rider_count ? "warning" : "success"} />
                    <Metric label={t("Maximum breach, worst day")} value={`${formatNumber(candidate.max_time_impact_adverse_minutes)} ${t("min")}`} tone={candidate.max_time_impact_adverse_minutes ? "warning" : "success"} />
                    <Metric label={t("Average excess rider-minutes")} value={formatNumber(candidate.average_excess_rider_minutes)} tone={candidate.average_excess_rider_minutes ? "warning" : "success"} />
                </div>
                <p className="text-sm leading-6 text-muted-foreground">
                    {candidate.status === "operationally_ready"
                        ? t("This exact plan repeated and passed all hard checks in every qualified sample.")
                        : candidate.status === "conditionally_viable"
                          ? t("This plan has useful evidence but did not repeat as a fully passing plan across all qualified samples.")
                          : t("This is the most stable least-harm reference, not an adoption-ready operating plan.")}
                </p>
            </CardContent>
        </Card>
    );
}

function DailyEvidenceTable({ evidence }: { evidence: Array<{
    job_id: string;
    job_name: string;
    sample_at?: string | null;
    qualified: boolean;
    exclusion_reasons: string[];
    scenario_name?: string | null;
    route_count?: number | null;
    affected_rider_count?: number | null;
    worst_over_limit_minutes?: number | null;
    time_impact_affected_rider_count?: number | null;
    time_impact_max_adverse_minutes?: number | null;
}> }) {
    const t = useT();
    const { lang } = useLanguage();
    return (
        <Card>
            <CardHeader>
                <div className="flex items-center gap-2">
                    <CalendarDays className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Run evidence")}</h2>
                </div>
            </CardHeader>
            <CardContent className="overflow-x-auto p-0">
                <table className="w-full min-w-[820px] border-collapse text-sm">
                    <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
                        <tr>
                            <th className="px-4 py-3">{t("Run time")}</th>
                            <th className="px-4 py-3">{t("Job")}</th>
                            <th className="px-4 py-3">{t("Plan")}</th>
                            <th className="px-4 py-3">{t("Routes")}</th>
                            <th className="px-4 py-3">{t("Affected riders")}</th>
                            <th className="px-4 py-3">{t("Maximum breach")}</th>
                            <th className="px-4 py-3">{t("Evidence status")}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {evidence.map((item) => (
                            <tr key={item.job_id} className="border-t border-border">
                                <td className="px-4 py-3">{item.sample_at ? formatRunTime(item.sample_at, lang) : t("Not available")}</td>
                                <td className="px-4 py-3">
                                    <div className="font-medium">{item.job_name}</div>
                                    <div className="mt-1 text-xs text-muted-foreground">{item.job_id}</div>
                                </td>
                                <td className="px-4 py-3">{item.scenario_name ? t(item.scenario_name) : t("Not available")}</td>
                                <td className="px-4 py-3">{formatNumber(item.route_count)}</td>
                                <td className="px-4 py-3">{formatNumber(item.time_impact_affected_rider_count)}</td>
                                <td className="px-4 py-3">{formatNumber(item.time_impact_max_adverse_minutes)} {t("min")}</td>
                                <td className="px-4 py-3">
                                    <Badge tone={item.qualified ? "success" : "neutral"}>
                                        {t(item.qualified ? "Qualified" : "Excluded")}
                                    </Badge>
                                    {!item.qualified ? (
                                        <div className="mt-1 max-w-xs text-xs text-muted-foreground">
                                            {item.exclusion_reasons.map((reason) => t(REASON_LABELS[reason] ?? reason)).join("; ")}
                                        </div>
                                    ) : null}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </CardContent>
        </Card>
    );
}

function Metric({
    label,
    value,
    tone = "neutral",
}: {
    label: string;
    value: string;
    tone?: "neutral" | "success" | "warning";
}) {
    return (
        <div className={[
            "rounded-md border px-4 py-3",
            tone === "success"
                ? "border-emerald-200 bg-emerald-50/60"
                : tone === "warning"
                  ? "border-amber-200 bg-amber-50/60"
                  : "border-border bg-surface",
        ].join(" ")}>
            <div className="text-xs uppercase text-muted-foreground">{label}</div>
            <div className="mt-2 text-xl font-semibold">{value}</div>
        </div>
    );
}

function LoadingState({ label, compact = false }: { label: string; compact?: boolean }) {
    return (
        <div className={`flex items-center justify-center text-sm text-muted-foreground ${compact ? "h-40" : "h-[420px]"}`}>
            <Loader2 className="mr-2 h-4 w-4 animate-spin text-primary" aria-hidden="true" />
            {label}
        </div>
    );
}

function formatRunTime(value: string, lang: "en" | "ko" | "zh") {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const locale = {
        en: "en-US",
        ko: "ko-KR",
        zh: "zh-CN",
    }[lang];
    return new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(date);
}

function reviewStatusTone(status: string): "success" | "warning" | "neutral" | "info" {
    if (status === "operationally_ready") return "success";
    if (status === "review_reference") return "warning";
    if (status === "conditionally_viable") return "info";
    return "neutral";
}
