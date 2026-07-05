import { type ReactNode, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowRight,
  BarChart3,
  Bot,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  FileWarning,
  GitCompareArrows,
  ListChecks,
  Loader2,
  Map,
  Maximize2,
  RefreshCw,
  Route,
  TriangleAlert,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { buttonClassName } from "@/components/ui/button-styles";
import { InteractiveRouteMap } from "@/features/results/interactive-route-map";
import {
  generateAiAudit,
  getJobArtifactUrl,
  getJobExportUrl,
  getJobMapData,
  type JobMapData,
  type JobMapRoute,
  type JobMapStop,
  type JobMapTimeImpactSummary,
  type JobMapTimeImpactTopStop,
  type JobRecord,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  formatDistanceKmFromMeters,
  formatDurationMinFromSeconds,
  formatNumber,
  formatPercent,
  toTitle,
} from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n/context";

type ResultTab = "summary" | "plans" | "impact" | "review";

const resultTabs: Array<{ key: ResultTab; label: string }> = [
  { key: "summary", label: "Summary" },
  { key: "plans", label: "Plans" },
  { key: "impact", label: "Impact" },
  { key: "review", label: "Review" },
];

export function JobResultView({ job }: { job: JobRecord }) {
  const t = useT();
  const [activeTab, setActiveTab] = useState<ResultTab>("summary");
  const result = asRecord(job.result);
  const currentPlan = asRecord(result.current_plan_assessment);
  const routeSummaries = asRecordArray(currentPlan.route_summaries);
  const currentComparison = asRecord(result.current_plan_comparison);
  const reallocation = asRecord(result.route_reallocation_analysis);
  const reallocationSummary = asRecord(reallocation.summary);
  const priorityActions = asRecordArray(reallocationSummary.priority_recommendations).slice(0, 4);
  const diagnostics = getDiagnostics(job);
  const mapOutputs = useMemo(() => collectMapOutputs(job.job_id, result), [job.job_id, result]);
  const jobDisplayName = getJobDisplayName(job);

  const scenarios = useMemo(() => buildScenarioRows(result), [result]);

  if (job.error) {
    return (
      <Card className="min-w-0">
        <CardHeader>
          <h2 className="text-sm font-semibold">{t("Run error")}</h2>
        </CardHeader>
        <CardContent>
          <pre className="max-h-[420px] overflow-auto rounded-md bg-red-50 p-4 text-xs leading-5 text-red-800">
            {job.error}
            {job.traceback ? `\n\n${job.traceback}` : ""}
          </pre>
        </CardContent>
      </Card>
    );
  }

  if (!job.result) {
    return (
      <EmptyState
        title={t("No result payload yet")}
        detail={t("Queued and running jobs update automatically. Completed jobs will expose audit results here.")}
      />
    );
  }

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap gap-2">
        {resultTabs.map((tab) => (
          <button
            key={tab.key}
            className={cn(
              "h-9 rounded-md border px-3 text-sm font-medium transition",
              activeTab === tab.key
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-surface text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            type="button"
            onClick={() => setActiveTab(tab.key)}
          >
            {t(tab.label)}
          </button>
        ))}
      </div>

      {activeTab === "summary" ? (
        <SummaryPanel
          job={job}
          result={result}
          currentPlan={currentPlan}
          currentComparison={currentComparison}
          diagnostics={diagnostics}
          priorityActions={priorityActions}
          reallocation={reallocation}
          reallocationSummary={reallocationSummary}
          scenarios={scenarios}
          onOpenReview={() => setActiveTab("review")}
        />
      ) : null}
      {activeTab === "plans" ? (
        <PlansPanel
          jobId={job.job_id}
          jobName={jobDisplayName}
          mapOutputs={mapOutputs}
          result={result}
          diagnostics={diagnostics}
          scenarios={scenarios}
          currentComparison={currentComparison}
        />
      ) : null}
      {activeTab === "impact" ? <TimeImpactPanel jobId={job.job_id} mapOutputs={mapOutputs} /> : null}
      {activeTab === "review" ? (
        <ReviewPanel
          currentPlan={currentPlan}
          currentComparison={currentComparison}
          routeSummaries={routeSummaries}
          result={result}
          diagnostics={diagnostics}
          mapOutputs={mapOutputs}
        />
      ) : null}
    </div>
  );
}

function SummaryPanel({
  job,
  result,
  currentPlan,
  currentComparison,
  diagnostics,
  priorityActions,
  reallocation,
  reallocationSummary,
  scenarios,
  onOpenReview,
}: {
  job: JobRecord;
  result: Record<string, unknown>;
  currentPlan: Record<string, unknown>;
  currentComparison: Record<string, unknown>;
  diagnostics: Diagnostics;
  priorityActions: Array<Record<string, unknown>>;
  reallocation: Record<string, unknown>;
  reallocationSummary: Record<string, unknown>;
  scenarios: ScenarioRow[];
  onOpenReview: () => void;
}) {
  const t = useT();
  const currentPlanRow = scenarios.find((scenario) => scenario.key === "current_plan");
  const timeConstrainedRow = scenarios.find((scenario) => scenario.key === "time_constrained");
  const exceptionPreservingRow = scenarios.find((scenario) => scenario.key === "exception_preserving");
  const ep15Row = scenarios.find((scenario) => scenario.key === "ep15min");
  const timeConstrained = timeConstrainedRow?.enabled ? timeConstrainedRow : undefined;
  const exceptionPreserving = exceptionPreservingRow?.enabled ? exceptionPreservingRow : undefined;
  const ep15 = ep15Row?.enabled ? ep15Row : undefined;
  const recommended = pickRecommendedScenario(scenarios);
  const reviewCount = diagnostics.inputAddressWarnings.length + diagnostics.geocodeWarnings.length + diagnostics.excludedStops.length;
  const solveProcessRows = buildSolveProcessRows(result);

  return (
    <div className="space-y-4">
      {diagnostics.inputAddressWarnings.length ? (
        <InputAddressWarningBanner count={diagnostics.inputAddressWarnings.length} onOpenReview={onOpenReview} />
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        <MetricCard
          label="Current plan"
          value={formatNumber(currentPlan.route_count)}
          tone={currentPlanRow ? scenarioTrafficTone(currentPlanRow, "neutral") : "neutral"}
          detail={currentPlanRow ? scenarioCardDetail(currentPlanRow, "Uploaded plan used as the control case.") : ""}
        />
        <MetricCard
          label="Recommended plan"
          value={recommended ? `${formatNumber(recommended.routeCount)} ${t("routes")}` : t("Needs review")}
          tone={recommended ? scenarioTrafficTone(recommended, "info") : "warning"}
          detail={recommended ? scenarioCardDetail(recommended, recommended.name) : "No adoption-ready optimized plan was produced."}
        />
        <MetricCard
          label={timeConstrained?.name || "Time-impact constrained"}
          value={timeConstrained ? formatNumber(timeConstrained.routeCount) : t("Skipped")}
          tone={timeConstrained ? scenarioTrafficTone(timeConstrained, "info") : "warning"}
          detail={
            timeConstrained
              ? scenarioCardDetail(timeConstrained, timeConstrained.detail)
              : scenarioSkippedDetail(
                  timeConstrainedRow,
                  "Adjust the time-impact limit, time window, stop limit, minimum saving, or fleet settings, then rerun.",
                )
          }
        />
        <MetricCard
          label="Protected route plan"
          value={exceptionPreserving ? formatNumber(exceptionPreserving.routeCount) : t("Skipped")}
          tone={exceptionPreserving ? scenarioTrafficTone(exceptionPreserving, "success") : "warning"}
          detail={
            exceptionPreserving
              ? scenarioCardDetail(exceptionPreserving, "Keeps current exception routes protected, then optimizes the remaining stops.")
              : scenarioSkippedDetail(
                  exceptionPreservingRow,
                  "Review current-plan failed routes or relax the optimization settings, then rerun.",
                )
          }
        />
        <MetricCard
          label={ep15?.name || "Protected time-impact plan"}
          value={ep15 ? formatNumber(ep15.routeCount) : t("Skipped")}
          tone={ep15 ? scenarioTrafficTone(ep15, "info") : "warning"}
          detail={
            ep15
              ? scenarioCardDetail(ep15, ep15.detail)
              : scenarioSkippedDetail(
                  ep15Row,
                  "Adjust protected-route assumptions, the time-impact limit, or fleet settings, then rerun.",
                )
          }
        />
        <MetricCard
          label="Data review"
          value={reviewCount ? `${formatNumber(reviewCount)} ${t("item(s)")}` : t("Clear")}
          tone={reviewCount ? "warning" : "success"}
        />
      </div>

      <SolveProcessCard rows={solveProcessRows} />

      <AiAuditPanel
        job={job}
        currentPlan={currentPlan}
        currentComparison={currentComparison}
        reallocationSummary={reallocationSummary}
        scenarios={scenarios}
      />

      <CollapsibleSection title="Detailed action signals">
        <ActionPanel
          priorityActions={priorityActions}
          reallocationSummary={reallocationSummary}
          reallocation={reallocation}
        />
      </CollapsibleSection>
    </div>
  );
}

function InputAddressWarningBanner({
  count,
  onOpenReview,
}: {
  count: number;
  onOpenReview: () => void;
}) {
  const t = useT();
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-4 shadow-panel">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="flex gap-3">
          <div className="flex h-10 w-10 flex-none items-center justify-center rounded-full bg-amber-100 text-amber-800">
            <FileWarning className="h-5 w-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-amber-950">{t("Input addresses need review")}</h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-amber-900">
              {formatNumber(count)} {t("accepted address(es) may be outside the expected service area, far from school, or unusual in the route sequence. The plan still ran, but review these workbook rows before sharing or operating it.")}
            </p>
          </div>
        </div>
        <Button type="button" variant="secondary" icon={<ListChecks className="h-4 w-4" />} onClick={onOpenReview}>
          {t("Review details")}
        </Button>
      </div>
    </div>
  );
}

function PlansPanel({
  jobId,
  jobName,
  mapOutputs,
  result,
  diagnostics,
  scenarios,
  currentComparison,
}: {
  jobId: string;
  jobName: string;
  mapOutputs: MapOutput[];
  result: Record<string, unknown>;
  diagnostics: Diagnostics;
  scenarios: ScenarioRow[];
  currentComparison: Record<string, unknown>;
}) {
  const [selectedMapKey, setSelectedMapKey] = useState("");
  const activeMapKey = mapOutputs.find((item) => item.key === selectedMapKey)?.key || mapOutputs[0]?.key || "";
  const arrivalReverseChecks = useMemo(
    () => buildArrivalReverseChecks(result, activeMapKey),
    [activeMapKey, result],
  );

  return (
    <div className="space-y-4">
      <MapsPanel
        jobId={jobId}
        jobName={jobName}
        mapOutputs={mapOutputs}
        result={result}
        diagnostics={diagnostics}
        selectedKey={activeMapKey}
        onSelectKey={setSelectedMapKey}
      />
      {arrivalReverseChecks.length ? <ArrivalReverseCheckPanel checks={arrivalReverseChecks} /> : null}
      <CollapsibleSection title="Scenario tables">
        <BaselinePanel scenarios={scenarios} currentComparison={currentComparison} />
      </CollapsibleSection>
    </div>
  );
}

function ReviewPanel({
  currentPlan,
  currentComparison,
  routeSummaries,
  result,
  diagnostics,
  mapOutputs,
}: {
  currentPlan: Record<string, unknown>;
  currentComparison: Record<string, unknown>;
  routeSummaries: Array<Record<string, unknown>>;
  result: Record<string, unknown>;
  diagnostics: Diagnostics;
  mapOutputs: MapOutput[];
}) {
  return (
    <div className="space-y-4">
      <AuditPanel
        currentPlan={currentPlan}
        currentComparison={currentComparison}
        routeSummaries={routeSummaries}
        result={result}
      />
      <DiagnosticsPanel diagnostics={diagnostics} mapOutputs={mapOutputs} result={result} />
    </div>
  );
}

function AuditPanel({
  currentPlan,
  currentComparison,
  routeSummaries,
  result,
}: {
  currentPlan: Record<string, unknown>;
  currentComparison: Record<string, unknown>;
  routeSummaries: Array<Record<string, unknown>>;
  result: Record<string, unknown>;
}) {
  const t = useT();
  const recommendations = asStringArray(currentComparison.recommendations);
  const benchmarkGateWarnings = buildBenchmarkGateWarnings(result);
  const plannerConfig = asRecord(result.planner_config);
  const trafficBasis = stringValue(result.traffic_profile_context || plannerConfig.traffic_profile_name);
  const arrivalGateSummary = formatArrivalGateSummary(result);
  const solveProcessRows = buildSolveProcessRows(result);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard label="Current routes" value={formatNumber(currentPlan.route_count)} />
        <MetricCard label="Service stops" value={formatNumber(assessmentServiceStopCount(currentPlan))} />
        <MetricCard label="Avg distance" value={formatDistanceKmFromMeters(currentPlan.avg_route_distance_m)} />
        <MetricCard label="Avg duration" value={formatDurationMinFromSeconds(currentPlan.avg_route_duration_s)} />
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard label="Average load" value={formatPercent(currentPlan.avg_load_factor, 100)} />
        <MetricCard label="Low-load routes" value={formatNumber(currentPlan.low_load_route_count)} tone="warning" />
        <MetricCard label="Overlong routes" value={formatNumber(currentPlan.overlong_route_count)} tone="warning" />
        <MetricCard label="Route gap" value={formatSignedNumber(currentComparison.route_gap)} tone="info" />
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Audit detail readout")}</h2>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 text-sm md:grid-cols-3">
            <ReadoutItem label="Service direction" value={stringValue(result.service_direction || plannerConfig.service_direction)} translateValue />
            <ReadoutItem label="Traffic profile" value={stringValue(result.traffic_profile_name || plannerConfig.traffic_profile_name)} translateValue />
            <ReadoutItem label="Traffic multiplier" value={formatTrafficMultiplier(result.traffic_time_multiplier)} />
            <ReadoutItem label="Traffic basis" value={trafficBasis} />
            {arrivalGateSummary ? <ReadoutItem label="AM arrival gate" value={arrivalGateSummary} /> : null}
            <ReadoutItem label="Current bus mix" value={formatBusMix(asRecord(currentPlan.bus_mix))} />
          </div>
          {benchmarkGateWarnings.length ? (
            <ul className="space-y-2 text-sm text-amber-800">
              {benchmarkGateWarnings.map((item) => (
                <li key={item} className="flex gap-2">
                  <TriangleAlert className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
                  <span>{t(item)}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {recommendations.length ? (
            <ul className="space-y-2 text-sm text-muted-foreground">
              {recommendations.map((item) => (
                <li key={item} className="flex gap-2">
                  <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-accent" aria-hidden="true" />
                  <span>{translateCurrentPlanRecommendation(item, t)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-sm text-muted-foreground">{t("No comparison recommendations were generated.")}</div>
          )}
        </CardContent>
      </Card>

      <SolveProcessCard rows={solveProcessRows} />

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Route className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Route diagnostics")}</h2>
          </div>
        </CardHeader>
        <CardContent>
          <RouteDiagnosticsTable routes={routeSummaries} />
        </CardContent>
      </Card>
    </div>
  );
}

function SolveProcessCard({ rows }: { rows: ReturnType<typeof buildSolveProcessRows> }) {
  const t = useT();
  return (
    <CollapsibleSection title="Solve process">
      <div className="space-y-3">
        {rows.map((row) => (
          <div key={row.label} className="rounded-lg border border-border bg-surface px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-semibold">{t(row.label)}</div>
              <Badge tone={row.passed ? "success" : row.neutral ? "info" : "warning"}>{t(row.status)}</Badge>
            </div>
            <div className="mt-2 text-sm leading-6 text-muted-foreground">{row.summary}</div>
            <ol className="mt-3 space-y-1 text-sm leading-6 text-muted-foreground">
              {row.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </CollapsibleSection>
  );
}

function AiAuditPanel({
  job,
  currentPlan,
  currentComparison,
  reallocationSummary,
  scenarios,
}: {
  job: JobRecord;
  currentPlan: Record<string, unknown>;
  currentComparison: Record<string, unknown>;
  reallocationSummary: Record<string, unknown>;
  scenarios: ScenarioRow[];
}) {
  const t = useT();
  const { lang } = useLanguage();
  const queryClient = useQueryClient();
  const requestedReportKey = lang === "ko" ? "ko" : lang === "zh" ? "zh" : "en";
  const requestedReportLanguage = lang === "ko" ? "Korean" : lang === "zh" ? "Chinese" : "English";
  const auditMutation = useMutation({
    mutationFn: ({ force }: { force: boolean }) => generateAiAudit(job.job_id, { force, language: requestedReportLanguage }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["jobs", job.job_id] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const reportsByLanguage = useMemo(
    () => ({
      ...collectAiAuditReports(job.ai_audit_reports, job.ai_audit_report),
      ...collectAiAuditReports(auditMutation.data?.ai_audit_reports, auditMutation.data?.ai_audit_report),
    }),
    [
      job.ai_audit_reports,
      job.ai_audit_report,
      auditMutation.data?.ai_audit_reports,
      auditMutation.data?.ai_audit_report,
    ],
  );
  const report = asRecord(reportsByLanguage[requestedReportKey]);
  const reportMarkdown = stringValue(report.report_markdown);
  const aiStatus = stringValue(auditMutation.data?.ai_audit_status || job.ai_audit_status).toLowerCase();
  const aiRunning = aiStatus === "running" || auditMutation.isPending;
  const generateReportIcon =
    auditMutation.isPending && !auditMutation.variables?.force ? (
      <Loader2 className="h-4 w-4 animate-spin" />
    ) : (
      <Bot className="h-4 w-4" />
    );
  const generateReport = () => auditMutation.mutate({ force: false });
  const scenarioRows = scenarios.filter((scenario) => scenario.enabled);
  const routeSummaries = asRecordArray(currentPlan.route_summaries);
  const priorityActions = asRecordArray(reallocationSummary.priority_recommendations).slice(0, 5);
  const downloadHtml = reportMarkdown
    ? buildAiReportHtml({
        job,
        report,
        currentPlan,
        currentComparison,
        reallocationSummary,
        scenarios: scenarioRows,
        priorityActions,
        translate: t,
      })
    : "";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">{t("AI audit briefing board")}</h2>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                disabled={aiRunning || Boolean(reportMarkdown)}
                icon={generateReportIcon}
                onClick={generateReport}
              >
                {t("Generate report")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={aiRunning}
                icon={auditMutation.isPending && auditMutation.variables?.force ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                onClick={() => auditMutation.mutate({ force: true })}
              >
                {t("Regenerate")}
              </Button>
              {reportMarkdown ? (
                <a
                  className={buttonClassName("secondary")}
                  href={`data:text/html;charset=utf-8,${encodeURIComponent(downloadHtml)}`}
                  download={`ai_audit_report_${job.job_id}_${requestedReportKey}.html`}
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  {t("Download HTML")}
                </a>
              ) : null}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-4">
            <MetricCard label="Current routes" value={formatNumber(currentPlan.route_count)} />
            <MetricCard label="Average load" value={formatPercent(currentPlan.avg_load_factor, 100)} />
            <MetricCard label="Average time" value={formatDurationMinFromSeconds(currentPlan.avg_route_duration_s)} />
            <MetricCard label="Action signals" value={formatNumber(reallocationSummary.actionable_weak_route_count)} tone="info" />
          </div>

          <div className="rounded-md border border-border bg-muted/50 px-3 py-3 text-sm leading-6 text-muted-foreground">
            {t("AI uses only deterministic route metrics, baseline comparisons, and recommendation summaries. Full address lists are excluded from the prompt.")}
          </div>

          {aiRunning ? (
            <div className="rounded-md border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
              {t("AI audit generation is running. The backend may take up to about a minute depending on the model response.")}
            </div>
          ) : null}
          {auditMutation.error ? <InlineError message={(auditMutation.error as Error).message} /> : null}
          {job.ai_audit_error && !auditMutation.error ? <InlineError message={job.ai_audit_error} /> : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">{t("Routes to review first")}</h2>
          </CardHeader>
          <CardContent>
            {routeSummaries.length ? (
              <RouteRiskTable routes={routeSummaries} />
            ) : (
              <div className="text-sm text-muted-foreground">{t("No route diagnostics were available for AI briefing.")}</div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">{t("Top suggested actions")}</h2>
          </CardHeader>
          <CardContent>
            {priorityActions.length ? (
              <div className="space-y-3">
                {priorityActions.map((action, index) => (
                  <div key={`${stringValue(action.from_route_id)}-${stringValue(action.to_route_id)}-${index}`} className="rounded-md border border-border px-3 py-2 text-sm">
                    <div className="font-medium">
                      {t("Move")} {formatNumber(action.stop_count)} {t("stop(s) from")} {stringValue(action.from_route_id) || t("N/A")} {t("to")}{" "}
                      {stringValue(action.to_route_id) || t("N/A")}
                    </div>
                    <div className="mt-1 text-muted-foreground">
                      {t("Save about")} {formatDurationMinFromSeconds(action.network_total_duration_saving_s)} {t("and")}{" "}
                      {formatDistanceKmFromMeters(action.network_total_distance_saving_m)}.
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">{t("No high-priority action signals were generated.")}</div>
            )}
          </CardContent>
        </Card>
      </div>

      {scenarioRows.length ? (
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">{t("Scenario evidence")}</h2>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] border-collapse text-sm">
                <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">{t("Scenario")}</th>
                    <th className="px-3 py-2">{t("Routes")}</th>
                    <th className="px-3 py-2">{t("Service stops")}</th>
                    <th className="px-3 py-2">{t("Avg time")}</th>
                    <th className="px-3 py-2">{t("Avg distance")}</th>
                    <th className="px-3 py-2">{t("Bus mix")}</th>
                  </tr>
                </thead>
                <tbody>
                  {scenarioRows.map((scenario) => (
                    <tr key={scenario.name} className="border-t border-border">
                      <td className="px-3 py-2 font-medium">{t(scenario.name)}</td>
                      <td className="px-3 py-2">{formatNumber(scenario.routeCount)}</td>
                      <td className="px-3 py-2">{formatNumber(scenario.stopCount)}</td>
                      <td className="px-3 py-2">{formatDurationMinFromSeconds(scenario.avgDurationS)}</td>
                      <td className="px-3 py-2">{formatDistanceKmFromMeters(scenario.avgDistanceM)}</td>
                      <td className="px-3 py-2">{formatBusMix(scenario.busMix)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold">{t("AI audit report")}</h2>
            {reportMarkdown ? (
              <Badge tone="success">{stringValue(report.model) || t("generated")}</Badge>
            ) : (
              <Badge tone="neutral">{t("not generated")}</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {reportMarkdown ? (
            <div className="space-y-4">
              <MarkdownReport markdown={reportMarkdown} />
              <div className="text-xs text-muted-foreground">
                {t("Generated")} {stringValue(report.generated_at) || t("unknown")} | {t("Input policy")}:{" "}
                {stringValue(report.input_policy) || t("aggregated facts only")}
              </div>
            </div>
          ) : (
            <EmptyState
              title={t("No AI report yet")}
              detail={t("History runs do not create this automatically. Click Generate report to create a bounded management-facing narrative from the deterministic audit outputs.")}
              action={
                <Button
                  type="button"
                  disabled={aiRunning || Boolean(reportMarkdown)}
                  icon={generateReportIcon}
                  onClick={generateReport}
                >
                  {t("Generate report")}
                </Button>
              }
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function BaselinePanel({
  scenarios,
  currentComparison,
}: {
  scenarios: ScenarioRow[];
  currentComparison: Record<string, unknown>;
}) {
  const t = useT();
  return (
    <div className="space-y-4">
      <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
        <div>
          <h2 className="text-sm font-semibold">{t("Optimization scenarios")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("Compare the uploaded current plan against the balanced and protected-route optimization results.")}
          </p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
        {scenarios.map((scenario) => (
          <Card key={scenario.name}>
            <CardContent className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold">{t(scenario.name)}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{t(scenario.detail)}</div>
                </div>
                <Badge tone={scenario.enabled ? scenarioTrafficTone(scenario, "success") : "neutral"}>
                  {scenario.enabled ? t(scenarioTrafficStatusLabel(scenario) || "ready") : t("skipped")}
                </Badge>
              </div>
              {scenario.enabled ? (
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <ReadoutItem label="Routes" value={formatNumber(scenario.routeCount)} />
                  <ReadoutItem label="Service stops" value={formatNumber(scenario.stopCount)} />
                  <ReadoutItem label="Avg distance" value={formatDistanceKmFromMeters(scenario.avgDistanceM)} />
                  <ReadoutItem label="Avg duration" value={formatDurationMinFromSeconds(scenario.avgDurationS)} />
                  <div className="col-span-2">
                    <ReadoutItem label="Bus mix" value={formatBusMix(scenario.busMix)} />
                  </div>
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">{scenario.skippedReason || t("Scenario was not enabled for this run.")}</div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {scenarios
          .filter((scenario) => scenario.enabled && scenario.routes.length > 0)
          .map((scenario) => (
            <Card key={`${scenario.name}-routes`} className="min-w-0">
              <CardHeader>
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-sm font-semibold">{t(scenario.name)} {t("route table")}</h2>
                  <Badge tone="info">{formatNumber(scenario.routes.length)} {t("routes")}</Badge>
                </div>
              </CardHeader>
              <CardContent>
                <BaselineRouteTable routes={scenario.routes} />
              </CardContent>
            </Card>
          ))}
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <GitCompareArrows className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Current plan comparison")}</h2>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-4">
          <MetricCard label="Current route count" value={formatNumber(currentComparison.current_route_count)} />
          <MetricCard label="Optimized route count" value={formatNumber(currentComparison.baseline_route_count)} />
          <MetricCard label="Distance gap" value={formatPercent(currentComparison.avg_distance_gap_pct)} tone="info" />
          <MetricCard label="Duration gap" value={formatPercent(currentComparison.avg_duration_gap_pct)} tone="info" />
        </CardContent>
      </Card>
    </div>
  );
}

function ArrivalReverseCheckPanel({ checks }: { checks: ArrivalReverseCheck[] }) {
  const t = useT();
  const routes = checks.flatMap((check) => check.routes);
  const warningCount = checks.reduce((total, check) => total + check.warningRouteCount, 0);
  const checkedCount = checks.reduce((total, check) => total + check.checkedRouteCount, 0);
  const maxOverrun = Math.max(...checks.map((check) => check.maxDepartureWindowOverrunMinutes), 0);
  const earliestRequired = checks
    .map((check) => check.earliestRequiredDepartureMinutes)
    .filter((value): value is number => Number.isFinite(value));
  const earliestRequiredLabel = earliestRequired.length ? formatClockMinutes(Math.min(...earliestRequired)) : "";
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {warningCount > 0 ? (
              <TriangleAlert className="h-4 w-4 text-amber-600" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
            )}
            <h2 className="text-sm font-semibold">{t("Arrival reverse check")}</h2>
          </div>
          <Badge tone={warningCount > 0 ? "warning" : "success"}>
            {warningCount > 0 ? `${formatNumber(warningCount)} ${t("warning routes")}` : t("Within window")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-4">
          <MetricCard label="Checked routes" value={formatNumber(checkedCount)} />
          <MetricCard label="Warning routes" value={formatNumber(warningCount)} tone={warningCount > 0 ? "warning" : "success"} />
          <MetricCard label="Earliest required departure" value={earliestRequiredLabel || t("Not available")} tone={warningCount > 0 ? "warning" : "neutral"} />
          <MetricCard label="Max early departure gap" value={`${formatNumber(Math.round(maxOverrun))} ${t("min")}`} tone={maxOverrun > 0 ? "warning" : "success"} />
        </div>
        <div className="max-h-[360px] overflow-auto">
          <table className="w-full min-w-[820px] border-collapse text-sm">
            <thead className="sticky top-0 bg-muted text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2">{t("Scenario")}</th>
                <th className="px-3 py-2">{t("Route")}</th>
                <th className="px-3 py-2">{t("Bus")}</th>
                <th className="px-3 py-2">{t("Passengers")}</th>
                <th className="px-3 py-2">{t("Service stops")}</th>
                <th className="px-3 py-2">{t("Total time")}</th>
                <th className="px-3 py-2">{t("Required departure")}</th>
                <th className="px-3 py-2">{t("Status")}</th>
              </tr>
            </thead>
            <tbody>
              {routes.map((route, index) => (
                <tr
                  key={`${route.scenarioName}-${route.routeId}-${index}`}
                  className={cn("border-t border-border", route.beforeEarliestDeparture ? "bg-amber-50/60" : "")}
                >
                  <td className="px-3 py-2">{t(route.scenarioName)}</td>
                  <td className="px-3 py-2 font-medium">{route.routeId}</td>
                  <td className="px-3 py-2">{route.busType || t("Not available")}</td>
                  <td className="px-3 py-2">{formatNumber(route.riderCount)}</td>
                  <td className="px-3 py-2">{formatNumber(route.serviceStopCount)}</td>
                  <td className="px-3 py-2">{formatDurationMinFromSeconds(route.verifiedTotalDurationS)}</td>
                  <td className="px-3 py-2">{route.requiredDepartureLabel || t("Not available")}</td>
                  <td className="px-3 py-2">
                    <Badge tone={route.available ? (route.beforeEarliestDeparture ? "warning" : "success") : "neutral"}>
                      {route.available
                        ? route.beforeEarliestDeparture
                          ? t("Before earliest departure")
                          : t("Within window")
                        : t("Unverified")}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function ActionPanel({
  priorityActions,
  reallocationSummary,
  reallocation,
}: {
  priorityActions: Array<Record<string, unknown>>;
  reallocationSummary: Record<string, unknown>;
  reallocation: Record<string, unknown>;
}) {
  const t = useT();
  const routeProfiles = asRecordArray(reallocationSummary.priority_route_profiles || reallocation.route_opportunity_profiles);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard label="Weak routes" value={formatNumber(reallocationSummary.weak_route_count)} />
        <MetricCard label="Actionable" value={formatNumber(reallocationSummary.actionable_weak_route_count)} />
        <MetricCard label="Removable now" value={formatNumber(reallocationSummary.route_removable_now_count)} tone="warning" />
        <MetricCard label="Best time saving" value={formatDurationMinFromSeconds(reallocationSummary.best_network_time_saving_s)} tone="success" />
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Priority route-to-route actions")}</h2>
          </div>
        </CardHeader>
        <CardContent>
          {priorityActions.length ? (
            <div className="space-y-3">
              {priorityActions.map((action, index) => (
                <div key={`${stringValue(action.from_route_id)}-${stringValue(action.to_route_id)}-${index}`} className="rounded-md border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="info">{stringValue(action.route_action_label) || t("Local improvement")}</Badge>
                    <span className="text-sm font-semibold">{stringValue(action.from_route_id)}</span>
                    <ArrowRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                    <span className="text-sm font-semibold">{stringValue(action.to_route_id)}</span>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">{stringValue(action.explanation)}</p>
                  <div className="mt-3 grid gap-2 text-sm md:grid-cols-4">
                    <ReadoutItem label="Stops moved" value={formatNumber(action.stop_count)} />
                    <ReadoutItem label="Passengers" value={formatNumber(action.moved_passenger_count)} />
                    <ReadoutItem label="Time saving" value={formatDurationMinFromSeconds(action.network_total_duration_saving_s)} />
                    <ReadoutItem label="Distance saving" value={formatDistanceKmFromMeters(action.network_total_distance_saving_m)} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title={t("No priority actions")} detail={t("No route-to-route adjustment met the current filters.")} />
          )}
        </CardContent>
      </Card>

      {routeProfiles.length ? (
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">{t("Route-level action signals")}</h2>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-sm">
                <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">{t("Route")}</th>
                    <th className="px-3 py-2">{t("Signal")}</th>
                    <th className="px-3 py-2">{t("Best target")}</th>
                    <th className="px-3 py-2">{t("Move support")}</th>
                    <th className="px-3 py-2">{t("Best saving")}</th>
                  </tr>
                </thead>
                <tbody>
                  {routeProfiles.map((profile) => (
                    <tr key={stringValue(profile.route_id)} className="border-t border-border">
                      <td className="px-3 py-2 font-medium">{stringValue(profile.route_id)}</td>
                      <td className="px-3 py-2">{stringValue(profile.route_action_label)}</td>
                      <td className="px-3 py-2">{stringValue(profile.best_to_route_id)}</td>
                      <td className="px-3 py-2">{formatNumber(profile.supporting_move_count)}</td>
                      <td className="px-3 py-2">{formatDurationMinFromSeconds(profile.best_network_time_saving_s)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function DiagnosticsPanel({
  diagnostics,
  mapOutputs,
  result,
}: {
  diagnostics: Diagnostics;
  mapOutputs: MapOutput[];
  result: Record<string, unknown>;
}) {
  const t = useT();
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard label="Client prep" value={formatDurationSeconds(diagnostics.clientPrepElapsedSeconds)} />
        <MetricCard label="Backend compute" value={formatDurationSeconds(result.elapsed_seconds)} />
        <MetricCard label="Input review" value={formatNumber(diagnostics.inputAddressWarnings.length)} tone="warning" />
        <MetricCard label="Geocode warnings" value={formatNumber(diagnostics.geocodeWarnings.length)} tone="warning" />
        <MetricCard label="Excluded stops" value={formatNumber(diagnostics.excludedStops.length)} tone="warning" />
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FileWarning className="h-4 w-4 text-amber-700" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Input address review")}</h2>
          </div>
        </CardHeader>
        <CardContent>
          {diagnostics.inputAddressWarnings.length ? (
            <SimpleObjectTable rows={diagnostics.inputAddressWarnings} />
          ) : (
            <div className="text-sm text-muted-foreground">{t("No accepted input addresses were flagged for review.")}</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FileWarning className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Coordinate warnings")}</h2>
          </div>
        </CardHeader>
        <CardContent>
          {diagnostics.geocodeWarnings.length ? (
            <SimpleObjectTable rows={diagnostics.geocodeWarnings} />
          ) : (
            <div className="text-sm text-muted-foreground">{t("No geocode warnings were recorded.")}</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Map className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Map outputs")}</h2>
          </div>
        </CardHeader>
        <CardContent>
          {mapOutputs.length ? (
            <div className="space-y-2">
              {mapOutputs.map((item) => (
                <div key={item.key} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm">
                  <span className="font-medium">{t(item.name)}</span>
                  <Badge tone="success">{t("available")}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">{t("No map outputs were included in this payload.")}</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

type TimeImpactFilter = "all" | "worse" | "over_acceptance" | "high_risk" | "route_changed" | "unavailable";

function TimeImpactPanel({
  jobId,
  mapOutputs,
}: {
  jobId: string;
  mapOutputs: MapOutput[];
}) {
  const t = useT();
  const scenarioOptions = useMemo(
    () => mapOutputs.filter((output) => output.key !== "current_plan"),
    [mapOutputs],
  );
  const [selectedKey, setSelectedKey] = useState("");
  const [filter, setFilter] = useState<TimeImpactFilter>("worse");
  const [search, setSearch] = useState("");
  const [selectedRouteId, setSelectedRouteId] = useState("");
  const selected =
    scenarioOptions.find((item) => item.key === selectedKey) ||
    scenarioOptions.find((item) => item.key === "time_constrained") ||
    scenarioOptions[0];
  const impactQuery = useQuery({
    queryKey: ["job-map-data", jobId, "time-impact", selected?.key],
    queryFn: () => getJobMapData(jobId, selected?.key || ""),
    enabled: Boolean(selected),
  });

  if (!scenarioOptions.length || !selected) {
    return (
      <EmptyState
        title={t("No optimized scenarios available")}
        detail={t("Time impact review needs an optimized scenario to compare against the current plan.")}
      />
    );
  }

  const data = impactQuery.data;
  const summary = data?.summary.time_impact;
  const recommendation = summary?.available ? buildTimeImpactRecommendation(summary, selected.name, t) : null;
  const acceptanceThresholdLabel = formatImpactMinutes(summary?.acceptance_threshold_minutes ?? 15);
  const routeRows = data ? buildTimeImpactRouteRows(data) : [];
  const stopRows = data ? buildTimeImpactStopRows(data, { filter, search, selectedRouteId }) : [];
  const comparedStops = data ? data.stops.filter((stop) => stop.time_impact?.comparison_available) : [];
  const unavailableStops = data
    ? data.stops.filter((stop) => !stop.is_depot && stop.time_impact?.comparison_available === false)
    : [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-2">
              <GitCompareArrows className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">{t("Time impact review")}</h2>
            </div>
            <div className="flex flex-wrap gap-2">
              {scenarioOptions.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  className={cn(
                    "h-9 rounded-md border px-3 text-sm font-medium transition",
                    selected.key === option.key
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-surface text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                  onClick={() => {
                    setSelectedKey(option.key);
                    setSelectedRouteId("");
                  }}
                >
                  {t(option.name)}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {impactQuery.isLoading ? (
            <div className="flex h-32 items-center justify-center rounded-md border border-border bg-muted text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
              {t("Loading time impact model")}
            </div>
          ) : null}
          {impactQuery.isError ? (
            <InlineError message={t("Time impact data is not available for this scenario yet.")} />
          ) : null}
          {data && !summary?.available ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <AlertCircle className="mr-2 inline h-4 w-4 align-text-bottom" aria-hidden="true" />
              {t("No comparable stop timing was found for this scenario.")}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {data && summary?.available ? (
        <>
          {recommendation ? (
            <TimeImpactDecisionCard
              recommendation={recommendation}
              summary={summary}
              scenarioName={selected.name}
              serviceDirection={data.service_direction || ""}
              topStops={summary.top_impacted_stops || []}
              onReviewOverAcceptance={() => {
                setFilter("over_acceptance");
                setSelectedRouteId("");
              }}
              onReviewHighRisk={() => {
                setFilter("high_risk");
                setSelectedRouteId("");
              }}
              onFocusRoute={(routeId) => {
                setSelectedRouteId(routeId);
                setFilter("all");
              }}
            />
          ) : null}

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <ImpactSummaryCard
              label="Within acceptance"
              value={formatPercent(summary.acceptance_rider_ratio, 100)}
              detail={`${formatNumber(summary.compared_rider_count)} ${t("riders")} / ${formatNumber(summary.compared_stop_count)} ${t("stops compared")}`}
              tone={Number(summary.over_acceptance_rider_count || 0) ? "warning" : "success"}
            />
            <ImpactSummaryCard
              label={`Over ${acceptanceThresholdLabel}`}
              value={`${formatNumber(summary.over_acceptance_rider_count)} ${t("riders")}`}
              detail={`${formatNumber(summary.over_acceptance_stop_count)} ${t("stops")}; ${t("max over by")} ${formatImpactMinutes(summary.max_over_acceptance_delta_minutes)}`}
              tone={Number(summary.over_acceptance_rider_count || 0) ? "warning" : "success"}
            />
            <ImpactSummaryCard
              label="Typical adverse"
              value={formatImpactMinutes(summary.weighted_avg_adverse_delta_minutes)}
              detail={`${formatNumber(summary.worse_rider_count)} ${t("riders worse")}; P90 ${formatImpactMinutes(summary.p90_adverse_delta_minutes)}`}
              tone="info"
            />
            <ImpactSummaryCard
              label="Worst adverse"
              value={formatImpactMinutes(summary.max_adverse_delta_minutes)}
              detail={`${formatNumber(summary.high_risk_stop_count)} ${t("high-risk stops")}`}
              tone={Number(summary.high_risk_stop_count || 0) ? "warning" : "success"}
            />
          </div>

          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex items-center gap-2">
                  <Route className="h-4 w-4 text-primary" aria-hidden="true" />
                  <h2 className="text-sm font-semibold">{t("Route impact")}</h2>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selectedRouteId ? (
                    <button
                      type="button"
                      className={cn(buttonClassName("secondary"), "h-8")}
                      onClick={() => setSelectedRouteId("")}
                    >
                      {t("All routes")}
                    </button>
                  ) : null}
                  <a
                    href={getJobExportUrl(jobId, `time-impact-${selected.key}`)}
                    className={cn(buttonClassName("secondary"), "h-8")}
                  >
                    <Download className="h-4 w-4" aria-hidden="true" />
                    {t("Export Excel")}
                  </a>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <TimeImpactRouteTable
                routes={routeRows}
                selectedRouteId={selectedRouteId}
                onSelectRoute={setSelectedRouteId}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex items-center gap-2">
                  <ListChecks className="h-4 w-4 text-primary" aria-hidden="true" />
                  <h2 className="text-sm font-semibold">{t("Stop impact")}</h2>
                  <Badge tone="neutral">{formatNumber(stopRows.length)} {t("shown")}</Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                  {(["all", "worse", "over_acceptance", "high_risk", "route_changed", "unavailable"] as const).map((key) => (
                    <button
                      key={key}
                      type="button"
                      className={cn(
                        "h-8 rounded-md border px-2.5 text-xs font-semibold transition",
                        filter === key
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border bg-surface text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                      onClick={() => setFilter(key)}
                    >
                      {t(timeImpactFilterLabel(key))}
                    </button>
                  ))}
                  <input
                    className="h-8 min-w-[220px] rounded-md border border-border bg-surface px-3 text-sm outline-none transition placeholder:text-muted-foreground focus:border-primary"
                    placeholder={t("Search stop or route")}
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <TimeImpactStopTable stops={stopRows} serviceDirection={data.service_direction || ""} />
              {unavailableStops.length ? (
                <div className="mt-3 text-xs text-muted-foreground">
                  {formatNumber(unavailableStops.length)} {t("stop(s) could not be matched to the current plan timing model.")}
                </div>
              ) : null}
            </CardContent>
          </Card>
        </>
      ) : null}

      {data && !comparedStops.length && unavailableStops.length ? (
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">{t("Unmatched stops")}</h2>
          </CardHeader>
          <CardContent>
            <TimeImpactStopTable stops={unavailableStops} serviceDirection={data.service_direction || ""} />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

type TimeImpactRecommendationLevel = "acceptable" | "review" | "high_risk" | "incomplete";

type TimeImpactRecommendation = {
  level: TimeImpactRecommendationLevel;
  title: string;
  detail: string;
  tone: "success" | "warning" | "danger" | "info";
  evidence: string[];
};

function buildTimeImpactRecommendation(
  summary: JobMapTimeImpactSummary,
  scenarioName: string,
  t: (key: string, fallback?: string) => string,
): TimeImpactRecommendation {
  const comparedRiders = Number(summary.compared_rider_count || 0);
  const comparedStops = Number(summary.compared_stop_count || 0);
  const overRiders = Number(summary.over_acceptance_rider_count || 0);
  const overStops = Number(summary.over_acceptance_stop_count || 0);
  const highRiskStops = Number(summary.high_risk_stop_count || 0);
  const highRiskRiders = Number(summary.high_risk_rider_count || 0);
  const unavailableStops = Number(summary.unavailable_stop_count || 0);
  const routeChangedRiders = Number(summary.route_changed_rider_count || 0);
  const maxOverMinutes = Number(summary.max_over_acceptance_delta_minutes || 0);
  const overRiderRatio = comparedRiders ? overRiders / comparedRiders : 0;
  const acceptanceRatio = Number(summary.acceptance_rider_ratio || 0);
  const evidence = [
    `${formatPercent(acceptanceRatio, 100)} ${t("of compared riders are within the acceptance window")}`,
    `${formatNumber(overRiders)} ${t("rider(s) across")} ${formatNumber(overStops)} ${t("stop(s) are over threshold")}`,
    `${formatNumber(highRiskRiders)} ${t("rider(s) at")} ${formatNumber(highRiskStops)} ${t("high-risk stop(s)")}`,
    `${formatNumber(routeChangedRiders)} ${t("rider(s) change route")}`,
  ];
  if (unavailableStops) {
    evidence.push(`${formatNumber(unavailableStops)} ${t("stop(s) could not be matched to current-plan timing")}`);
  }

  if (!comparedRiders || !comparedStops) {
    return {
      level: "incomplete",
      tone: "info",
      title: `${t(scenarioName)} ${t("needs more timing data")}`,
      detail: t("No comparable pickup/dropoff timing was found, so this scenario should not be accepted from time impact alone."),
      evidence,
    };
  }

  if (highRiskStops > 0 || overRiderRatio >= 0.1 || maxOverMinutes >= 10) {
    return {
      level: "high_risk",
      tone: "danger",
      title: `${t(scenarioName)} ${t("needs operations review before adoption")}`,
      detail: t("The optimized plan creates material pickup/dropoff disruption for some families. Review the highlighted stops and routes before sharing this plan."),
      evidence,
    };
  }

  if (overRiders > 0 || unavailableStops > 0 || routeChangedRiders > comparedRiders * 0.35) {
    return {
      level: "review",
      tone: "warning",
      title: `${t(scenarioName)} ${t("is plausible but needs targeted review")}`,
      detail: t("Most riders are within the acceptance window, but a small set of stops or route changes should be checked by operations first."),
      evidence,
    };
  }

  return {
    level: "acceptable",
    tone: "success",
    title: `${t(scenarioName)} ${t("looks operationally acceptable")}`,
    detail: `${t("All compared riders are within the")} ${formatImpactMinutes(summary.acceptance_threshold_minutes ?? 15)} ${t("acceptance window. Review route changes and cost tradeoffs before final approval.")}`,
    evidence,
  };
}

function TimeImpactDecisionCard({
  recommendation,
  summary,
  scenarioName,
  serviceDirection,
  topStops,
  onReviewOverAcceptance,
  onReviewHighRisk,
  onFocusRoute,
}: {
  recommendation: TimeImpactRecommendation;
  summary: JobMapTimeImpactSummary;
  scenarioName: string;
  serviceDirection: string;
  topStops: JobMapTimeImpactTopStop[];
  onReviewOverAcceptance: () => void;
  onReviewHighRisk: () => void;
  onFocusRoute: (routeId: string) => void;
}) {
  const t = useT();
  const overRiders = Number(summary.over_acceptance_rider_count || 0);
  const highRiskStops = Number(summary.high_risk_stop_count || 0);
  const visibleTopStops = topStops.slice(0, 5);
  const iconClassName = cn(
    "h-5 w-5",
    recommendation.level === "acceptable" ? "text-emerald-700" : recommendation.level === "high_risk" ? "text-rose-700" : "text-amber-700",
  );
  const Icon = recommendation.level === "acceptable" ? CheckCircle2 : recommendation.level === "high_risk" ? TriangleAlert : AlertCircle;

  return (
    <Card className={cn(
      "border-l-4",
      recommendation.level === "acceptable" && "border-l-emerald-500",
      recommendation.level === "review" && "border-l-amber-500",
      recommendation.level === "high_risk" && "border-l-rose-500",
      recommendation.level === "incomplete" && "border-l-sky-500",
    )}>
      <CardHeader>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex min-w-0 gap-3">
            <div className="mt-0.5 flex h-9 w-9 flex-none items-center justify-center rounded-md bg-muted">
              <Icon className={iconClassName} aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold">{recommendation.title}</h2>
                <Badge tone={recommendation.tone}>{t(timeImpactRecommendationLabel(recommendation.level))}</Badge>
              </div>
              <p className="mt-1 max-w-4xl text-sm leading-6 text-muted-foreground">{recommendation.detail}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {overRiders ? (
              <Button type="button" variant="secondary" onClick={onReviewOverAcceptance}>{t("Review over-threshold")}</Button>
            ) : null}
            {highRiskStops ? (
              <Button type="button" variant="secondary" onClick={onReviewHighRisk}>{t("Review high-risk")}</Button>
            ) : null}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {recommendation.evidence.map((item) => (
            <div key={item} className="rounded-md border border-border bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
              {item}
            </div>
          ))}
        </div>

        {visibleTopStops.length ? (
          <div className="rounded-md border border-border bg-surface">
            <div className="border-b border-border px-3 py-2 text-xs font-semibold uppercase text-muted-foreground">
              {t("Review first")} · {t(scenarioName)}
            </div>
            <div className="divide-y divide-border">
              {visibleTopStops.map((stop) => (
                <div key={stop.stop_id} className="grid gap-2 px-3 py-3 text-sm md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-semibold">{stop.address || t("Unknown stop")}</span>
                      <Badge tone={timeImpactBadgeTone(stop.level, stop.impact_direction)}>{t(timeImpactLabel(stop.level, stop.impact_direction))}</Badge>
                      {stop.within_acceptance === false ? <Badge tone="warning">{t("Over threshold")}</Badge> : null}
                    </div>
                    <div className="mt-1 text-xs leading-5 text-muted-foreground">
                      {timeImpactTopStopReason(stop, serviceDirection, t)}
                    </div>
                  </div>
                  <button
                    type="button"
                    className={cn(buttonClassName("secondary"), "h-8 text-xs")}
                    onClick={() => onFocusRoute(stop.route_id)}
                  >
                    {t("Focus route")}
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function timeImpactRecommendationLabel(level: TimeImpactRecommendationLevel) {
  if (level === "acceptable") {
    return "Acceptable";
  }
  if (level === "high_risk") {
    return "High risk";
  }
  if (level === "incomplete") {
    return "Incomplete";
  }
  return "Review needed";
}

function timeImpactTopStopReason(
  stop: JobMapTimeImpactTopStop,
  serviceDirection: string,
  t: (key: string, fallback?: string) => string,
) {
  const adverseMinutes = Number(stop.adverse_delta_minutes || 0);
  const threshold = Number(stop.acceptance_threshold_minutes ?? 15);
  const overMinutes = Number(stop.over_acceptance_minutes || 0);
  const riders = Number(stop.affected_rider_count || 0);
  const familyEffect = timeImpactAdversePhrase(serviceDirection, adverseMinutes, t);
  const routeChange = stop.route_changed ? ` ${t("Route changes from")} ${stop.current_route_id || t("current plan")} ${t("to")} ${stop.new_route_id || stop.route_id}.` : "";
  const thresholdText = stop.within_acceptance === false
    ? ` ${t("It is")} ${formatImpactMinutes(overMinutes)} ${t("over the")} ${formatImpactMinutes(threshold)} ${t("window.")}`
    : ` ${t("It remains within the")} ${formatImpactMinutes(threshold)} ${t("window.")}`;
  return `${formatNumber(riders)} ${t("rider(s)")}; ${familyEffect}.${thresholdText}${routeChange}`;
}

type TimeImpactRouteRow = JobMapRoute & {
  time_impact?: JobMapTimeImpactSummary;
};

function buildTimeImpactRouteRows(data: JobMapData): TimeImpactRouteRow[] {
  return [...data.routes]
    .filter((route) => route.time_impact?.available)
    .sort(
      (left, right) =>
        Number(right.time_impact?.over_acceptance_rider_count || 0) -
          Number(left.time_impact?.over_acceptance_rider_count || 0) ||
        Number(right.time_impact?.high_risk_rider_count || 0) -
          Number(left.time_impact?.high_risk_rider_count || 0) ||
        Number(right.time_impact?.weighted_avg_adverse_delta_minutes || 0) -
          Number(left.time_impact?.weighted_avg_adverse_delta_minutes || 0) ||
        Number(right.time_impact?.worse_rider_count || 0) -
          Number(left.time_impact?.worse_rider_count || 0),
    );
}

function buildTimeImpactStopRows(
  data: JobMapData,
  {
    filter,
    search,
    selectedRouteId,
  }: {
    filter: TimeImpactFilter;
    search: string;
    selectedRouteId: string;
  },
): JobMapStop[] {
  const normalizedSearch = search.trim().toLowerCase();
  return data.stops
    .filter((stop) => !stop.is_depot)
    .filter((stop) => {
      if (selectedRouteId && stop.route_id !== selectedRouteId) {
        return false;
      }
      if (!normalizedSearch) {
        return true;
      }
      const impact = stop.time_impact || {};
      const haystack = [
        stop.route_id,
        stop.address,
        stop.requested_address,
        impact.current_route_id,
        impact.new_route_id,
        impact.current_time_label,
        impact.new_time_label,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(normalizedSearch);
    })
    .filter((stop) => {
      const impact = stop.time_impact || {};
      if (filter === "worse") {
        return impact.impact_direction === "worse";
      }
      if (filter === "over_acceptance") {
        return Boolean(impact.comparison_available) && impact.within_acceptance === false;
      }
      if (filter === "high_risk") {
        return impact.level === "severe" || impact.level === "critical";
      }
      if (filter === "route_changed") {
        return Boolean(impact.route_changed);
      }
      if (filter === "unavailable") {
        return impact.comparison_available === false;
      }
      return true;
    })
    .sort(
      (left, right) =>
        stopAdverseMinutes(right) - stopAdverseMinutes(left) ||
        stopAffectedRiders(right) - stopAffectedRiders(left) ||
        stopAbsoluteMinutes(right) - stopAbsoluteMinutes(left),
    );
}

function TimeImpactRouteTable({
  routes,
  selectedRouteId,
  onSelectRoute,
}: {
  routes: TimeImpactRouteRow[];
  selectedRouteId: string;
  onSelectRoute: (routeId: string) => void;
}) {
  const t = useT();
  if (!routes.length) {
    return <div className="text-sm text-muted-foreground">{t("No route-level impact rows were generated.")}</div>;
  }

  return (
    <div className="max-h-[360px] overflow-auto">
      <table className="w-full min-w-[940px] border-collapse text-sm">
        <thead className="sticky top-0 bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">{t("Route")}</th>
            <th className="px-3 py-2">{t("Bus")}</th>
            <th className="px-3 py-2">{t("Riders")}</th>
            <th className="px-3 py-2">{t("Over threshold")}</th>
            <th className="px-3 py-2">{t("Worse riders")}</th>
            <th className="px-3 py-2">{t("High risk")}</th>
            <th className="px-3 py-2">{t("Weighted adverse")}</th>
            <th className="px-3 py-2">{t("Max adverse")}</th>
            <th className="px-3 py-2">{t("Changed riders")}</th>
          </tr>
        </thead>
        <tbody>
          {routes.map((route) => {
            const impact = route.time_impact || {};
            const active = selectedRouteId === route.id;
            return (
              <tr key={route.id} className={cn("border-t border-border", active ? "bg-primary/10" : "")}>
                <td className="px-3 py-2">
                  <button
                    type="button"
                    className="font-semibold text-primary hover:underline"
                    onClick={() => onSelectRoute(active ? "" : route.id)}
                  >
                    {route.id}
                  </button>
                </td>
                <td className="px-3 py-2">{route.bus_type_name || t("N/A")}</td>
                <td className="px-3 py-2">{formatNumber(route.load)}</td>
                <td className="px-3 py-2">{formatNumber(impact.over_acceptance_rider_count)}</td>
                <td className="px-3 py-2">{formatNumber(impact.worse_rider_count)}</td>
                <td className="px-3 py-2">
                  <Badge tone={Number(impact.high_risk_stop_count || 0) ? "warning" : "neutral"}>
                    {formatNumber(impact.high_risk_stop_count)}
                  </Badge>
                </td>
                <td className="px-3 py-2">{formatImpactMinutes(impact.weighted_avg_adverse_delta_minutes)}</td>
                <td className="px-3 py-2">{formatImpactMinutes(impact.max_adverse_delta_minutes)}</td>
                <td className="px-3 py-2">{formatNumber(impact.route_changed_rider_count)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TimeImpactStopTable({
  stops,
  serviceDirection,
}: {
  stops: JobMapStop[];
  serviceDirection: string;
}) {
  const t = useT();
  if (!stops.length) {
    return <div className="text-sm text-muted-foreground">{t("No stops match the current filter.")}</div>;
  }

  return (
    <div className="max-h-[520px] overflow-auto">
      <table className="w-full min-w-[980px] border-collapse text-sm">
        <thead className="sticky top-0 bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">{t("Impact")}</th>
            <th className="px-3 py-2">{t("Stop")}</th>
            <th className="px-3 py-2">{t("Riders")}</th>
            <th className="px-3 py-2">{t("Current")}</th>
            <th className="px-3 py-2">{t("Optimized")}</th>
            <th className="px-3 py-2">{t("Delta")}</th>
            <th className="px-3 py-2">{t("Route")}</th>
            <th className="px-3 py-2">{t("Match")}</th>
          </tr>
        </thead>
        <tbody>
          {stops.map((stop) => {
            const impact = stop.time_impact || {};
            const comparisonAvailable = Boolean(impact.comparison_available);
            const overAcceptance = comparisonAvailable && impact.within_acceptance === false;
            return (
              <tr key={stop.id} className="border-t border-border">
                <td className="px-3 py-2">
                  {comparisonAvailable ? (
                    <Badge tone={overAcceptance ? "warning" : timeImpactBadgeTone(impact.level, impact.impact_direction)}>
                      {overAcceptance
                        ? `${t("Over")} ${formatImpactMinutes(impact.acceptance_threshold_minutes ?? 15)}`
                        : t(timeImpactLabel(impact.level, impact.impact_direction))}
                    </Badge>
                  ) : (
                    <Badge tone="warning">{t("Unmatched")}</Badge>
                  )}
                </td>
                <td className="max-w-[320px] px-3 py-2">
                  <div className="truncate font-medium">{stop.address || stop.requested_address || t("Unknown address")}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {stop.route_id} · {t("Stop")} {formatNumber(stop.order)}
                  </div>
                </td>
                <td className="px-3 py-2">{formatNumber(impact.affected_rider_count ?? stop.passenger_count)}</td>
                <td className="px-3 py-2">
                  <div className="font-medium">{impact.current_time_label || t("N/A")}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{impact.current_route_id || t("Current plan")}</div>
                </td>
                <td className="px-3 py-2">
                  <div className="font-medium">{impact.new_time_label || stop.scheduled_time_label || t("N/A")}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{impact.new_route_id || stop.route_id}</div>
                </td>
                <td className="px-3 py-2">
                  {comparisonAvailable ? (
                    <>
                      <div className={cn("font-semibold", timeImpactDeltaClassName(impact.impact_direction))}>
                        {formatImpactMinutes(impact.delta_minutes, { signed: true })}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {timeImpactAdversePhrase(serviceDirection, Number(impact.adverse_delta_minutes || 0), t)}
                      </div>
                    </>
                  ) : (
                    <span className="text-muted-foreground">{t("N/A")}</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {impact.route_changed ? (
                    <Badge tone="info">{t("Changed")}</Badge>
                  ) : (
                    <Badge tone="neutral">{t("Same")}</Badge>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-muted-foreground">
                  {impact.comparison_status || t("matched")}
                  {impact.matched_key ? <div>{impact.matched_key}</div> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function timeImpactFilterLabel(filter: TimeImpactFilter) {
  if (filter === "worse") {
    return "Worse";
  }
  if (filter === "over_acceptance") {
    return "Over 15m";
  }
  if (filter === "high_risk") {
    return "High risk";
  }
  if (filter === "route_changed") {
    return "Route changed";
  }
  if (filter === "unavailable") {
    return "Unmatched";
  }
  return "All";
}

function timeImpactBadgeTone(level: unknown, direction: unknown): "neutral" | "success" | "warning" | "danger" | "info" {
  if (level === "critical" || level === "severe") {
    return "danger";
  }
  if (level === "elevated" || level === "notice") {
    return "warning";
  }
  if (direction === "better") {
    return "success";
  }
  return "neutral";
}

function timeImpactLabel(level: unknown, direction: unknown) {
  if (level === "critical") {
    return "Critical";
  }
  if (level === "severe") {
    return "Severe";
  }
  if (level === "elevated") {
    return "Elevated";
  }
  if (level === "notice") {
    return "Notice";
  }
  if (direction === "better") {
    return "Better";
  }
  return "Neutral";
}

function timeImpactDeltaClassName(direction: unknown) {
  if (direction === "worse") {
    return "text-amber-800";
  }
  if (direction === "better") {
    return "text-emerald-700";
  }
  return "text-foreground";
}

function timeImpactAdversePhrase(
  serviceDirection: string,
  adverseMinutes: number,
  t: (key: string, fallback?: string) => string = (key) => key,
) {
  if (!Number.isFinite(adverseMinutes) || adverseMinutes <= 0.5) {
    return t("No adverse shift");
  }
  const label = serviceDirection === "To School" ? t("earlier pickup") : t("later dropoff");
  return `${formatImpactMinutes(adverseMinutes)} ${label}`;
}

function formatImpactMinutes(value: unknown, options: { signed?: boolean } = {}) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return "0 min";
  }
  const rounded = Math.round(numericValue);
  const prefix = options.signed && rounded > 0 ? "+" : "";
  return `${prefix}${formatNumber(rounded)} min`;
}

function stopAdverseMinutes(stop: JobMapStop) {
  return Number(stop.time_impact?.adverse_delta_minutes || 0);
}

function stopAbsoluteMinutes(stop: JobMapStop) {
  return Number(stop.time_impact?.absolute_delta_minutes || 0);
}

function stopAffectedRiders(stop: JobMapStop) {
  return Number(stop.time_impact?.affected_rider_count ?? stop.passenger_count ?? 0);
}

function MapsPanel({
  jobId,
  jobName,
  mapOutputs,
  result,
  diagnostics,
  selectedKey,
  onSelectKey,
}: {
  jobId: string;
  jobName: string;
  mapOutputs: MapOutput[];
  result: Record<string, unknown>;
  diagnostics: Diagnostics;
  selectedKey: string;
  onSelectKey: (key: string) => void;
}) {
  const t = useT();
  const [isMapFullscreenOpen, setIsMapFullscreenOpen] = useState(false);
  const selected = mapOutputs.find((item) => item.key === selectedKey) || mapOutputs[0];
  const scenarioSummaries = useMemo(() => buildMapScenarioSummaries(result, mapOutputs), [mapOutputs, result]);
  const excludedStopCount = diagnostics.excludedStops.length;
  const geocodeWarningCount = diagnostics.geocodeWarnings.length;
  const interactiveQuery = useQuery({
    queryKey: ["job-map-data", jobId, selected?.key],
    queryFn: () => getJobMapData(jobId, selected.key),
    enabled: Boolean(selected),
  });
  const displayScenarioSummaries = useMemo(
    () =>
      interactiveQuery.data && selected
        ? scenarioSummaries.map((summary) =>
            summary.key === selected.key ? mapDataScenarioSummary(summary, interactiveQuery.data as JobMapData) : summary,
          )
        : scenarioSummaries,
    [interactiveQuery.data, scenarioSummaries, selected],
  );
  const workbookExportUrl = selected?.key
    ? getJobExportUrl(jobId, `scenario-template-${selected.key}`)
    : "";

  if (!mapOutputs.length || !selected) {
    return <EmptyState title={t("No maps available")} detail={t("This job did not include rendered route map artifacts.")} />;
  }

  const downloadInteractiveMap = () => {
    if (!interactiveQuery.data) {
      return;
    }
    downloadInteractiveMapHtml(interactiveQuery.data, jobName, selected.name);
  };

  const renderMapSurface = (fullscreen = false) => {
    if (interactiveQuery.isLoading) {
      return (
        <div
          className={cn(
            "flex items-center justify-center border border-border bg-muted text-sm text-muted-foreground",
            fullscreen ? "h-full rounded-none" : "h-[560px] rounded-md",
          )}
        >
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
          {t("Loading interactive map")}
        </div>
      );
    }
    if (interactiveQuery.isError) {
      return (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          {t("Interactive map data is not available for this scenario yet. Try another scenario or refresh this job.")}
        </div>
      );
    }
    return interactiveQuery.data ? <InteractiveRouteMap data={interactiveQuery.data} fullscreen={fullscreen} /> : null;
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2">
            <Map className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Route maps")}</h2>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {excludedStopCount || geocodeWarningCount ? (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <FileWarning className="h-4 w-4 flex-none" aria-hidden="true" />
            <span className="font-medium">{t("Input geocode review")}:</span>
            {excludedStopCount ? <span>{formatNumber(excludedStopCount)} {t("excluded stop(s)")}</span> : null}
            {excludedStopCount && geocodeWarningCount ? <span aria-hidden="true">·</span> : null}
            {geocodeWarningCount ? <span>{formatNumber(geocodeWarningCount)} {t("warning(s)")}</span> : null}
          </div>
        ) : null}
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {displayScenarioSummaries.map((summary) => (
            <button
              key={summary.key}
              type="button"
              className={cn(
                "rounded-md border p-3 text-left transition",
                selected.key === summary.key
                  ? "border-primary bg-primary/10"
                  : "border-border bg-surface hover:bg-muted",
              )}
              onClick={() => onSelectKey(summary.key)}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="truncate text-sm font-semibold">{t(summary.name)}</div>
                <Badge tone={selected.key === summary.key ? "info" : "neutral"}>{formatNumber(summary.routeCount)} {t("routes")}</Badge>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <div>{t("Stops")}: {formatNumber(summary.stopCount)}</div>
                <div>{t("Riders")}: {formatNumber(summary.passengerCount)}</div>
                <div>{t("Total")}: {formatDistanceKmFromMeters(summary.totalDistanceM)}</div>
                <div>{t("Longest")}: {formatDurationMinFromSeconds(summary.longestDurationS)}</div>
                {summary.trafficStatusLabel ? (
                  <div
                    className={cn(
                      "col-span-2 font-medium",
                      summary.trafficStatusTone === "success" ? "text-emerald-700" : "text-amber-700",
                    )}
                  >
                    {t(summary.trafficStatusLabel)}
                  </div>
                ) : null}
                {excludedStopCount ? <div className="col-span-2 text-amber-700">{t("Excluded")}: {formatNumber(excludedStopCount)} {t("stop(s)")}</div> : null}
              </div>
            </button>
          ))}
        </div>
        <div className="relative">
          {renderMapSurface()}
          <div className="absolute right-3 top-3 z-20 flex flex-wrap justify-end gap-2">
            <button
              type="button"
              className={cn(buttonClassName("secondary"), "border-slate-300 bg-white shadow-lg hover:bg-slate-50")}
              title={t("Open map")}
              aria-label={t("Open map")}
              onClick={() => setIsMapFullscreenOpen(true)}
            >
              <Maximize2 className="h-4 w-4" aria-hidden="true" />
              {t("Open")}
            </button>
            <button
              type="button"
              className={cn(buttonClassName("secondary"), "border-slate-300 bg-white shadow-lg hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60")}
              disabled={!interactiveQuery.data}
              title={t("Download map")}
              aria-label={t("Download map")}
              onClick={downloadInteractiveMap}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              {t("Map")}
            </button>
            {workbookExportUrl ? (
              <a
                className={cn(buttonClassName("secondary"), "border-slate-300 bg-white shadow-lg hover:bg-slate-50")}
                href={workbookExportUrl}
                title={t("Download workbook")}
                aria-label={t("Download workbook")}
              >
                <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />
                {t("Workbook")}
              </a>
            ) : null}
          </div>
        </div>
        {isMapFullscreenOpen ? (
          <div
            className="fixed inset-0 z-50 bg-slate-950/42 p-2 backdrop-blur-sm sm:p-4 lg:p-6"
            role="dialog"
            aria-modal="true"
            aria-label={t("Fullscreen route map")}
            onClick={() => setIsMapFullscreenOpen(false)}
          >
            <div
              className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-white/55 bg-surface/92 shadow-2xl ring-1 ring-slate-950/10 backdrop-blur-xl"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex min-h-14 flex-col gap-3 border-b border-white/45 bg-surface/82 px-4 py-3 shadow-sm backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{t(selected.name)}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {t("Interactive route map")} · {formatNumber(dataRouteCountForSummary(scenarioSummaries, selected.key))} {t("routes")}
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
                  <button
                    type="button"
                    className={cn(buttonClassName("secondary"), "bg-white/70 backdrop-blur hover:bg-white disabled:cursor-not-allowed disabled:opacity-60")}
                    disabled={!interactiveQuery.data}
                    onClick={downloadInteractiveMap}
                    title={t("Download map")}
                    aria-label={t("Download map")}
                  >
                    <Download className="h-4 w-4" aria-hidden="true" />
                    {t("Map")}
                  </button>
                  {workbookExportUrl ? (
                    <a
                      className={cn(buttonClassName("secondary"), "bg-white/70 backdrop-blur hover:bg-white")}
                      href={workbookExportUrl}
                    title={t("Download workbook")}
                    aria-label={t("Download workbook")}
                    >
                      <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />
                      {t("Workbook")}
                    </a>
                  ) : null}
                  <button
                    type="button"
                    className={cn(buttonClassName("secondary"), "border-red-300 bg-red-50/90 text-red-700 backdrop-blur hover:border-red-400 hover:bg-red-100 hover:text-red-800")}
                    onClick={() => setIsMapFullscreenOpen(false)}
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                    {t("Close")}
                  </button>
                </div>
              </div>
              <div className="min-h-0 flex-1 bg-muted/70">{renderMapSurface(true)}</div>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RouteDiagnosticsTable({ routes }: { routes: Array<Record<string, unknown>> }) {
  const t = useT();
  if (!routes.length) {
    return <div className="text-sm text-muted-foreground">{t("No route-level diagnostics were included.")}</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[780px] border-collapse text-sm">
        <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">{t("Route")}</th>
            <th className="px-3 py-2">{t("Bus")}</th>
            <th className="px-3 py-2">{t("Service stops")}</th>
            <th className="px-3 py-2">{t("Passengers")}</th>
            <th className="px-3 py-2">{t("Load")}</th>
            <th className="px-3 py-2">{t("Distance")}</th>
            <th className="px-3 py-2">{t("Duration")}</th>
          </tr>
        </thead>
        <tbody>
          {routes.map((route) => (
            <tr key={stringValue(route.route_id)} className="border-t border-border">
              <td className="px-3 py-2 font-medium">{stringValue(route.route_id)}</td>
              <td className="px-3 py-2">{stringValue(route.bus_type)}</td>
              <td className="px-3 py-2">{formatNumber(routeServiceStopCount(route))}</td>
              <td className="px-3 py-2">{formatNumber(route.passenger_count)}</td>
              <td className="px-3 py-2">{formatPercent(route.load_factor, 100)}</td>
              <td className="px-3 py-2">{formatDistanceKmFromMeters(route.distance_m)}</td>
              <td className="px-3 py-2">{formatDurationMinFromSeconds(route.duration_s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BaselineRouteTable({ routes }: { routes: Array<Record<string, unknown>> }) {
  const t = useT();
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead className="sticky top-0 bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">{t("Route")}</th>
            <th className="px-3 py-2">{t("Bus")}</th>
            <th className="px-3 py-2">{t("Service stops")}</th>
            <th className="px-3 py-2">{t("Passengers")}</th>
            <th className="px-3 py-2">{t("Capacity")}</th>
            <th className="px-3 py-2">{t("Load")}</th>
            <th className="px-3 py-2">{t("Distance")}</th>
            <th className="px-3 py-2">{t("Duration")}</th>
          </tr>
        </thead>
        <tbody>
          {routes.map((route, index) => (
            <tr key={`${stringValue(route.route_id || route.vehicle_id)}-${index}`} className="border-t border-border">
              <td className="px-3 py-2 font-medium">{stringValue(route.route_id || route.vehicle_id || index + 1)}</td>
              <td className="px-3 py-2">{stringValue(route.bus_type_name || route.bus_type)}</td>
              <td className="px-3 py-2">{formatNumber(routeStopCount(route))}</td>
              <td className="px-3 py-2">{formatNumber(routePassengerCount(route))}</td>
              <td className="px-3 py-2">{formatNumber(route.bus_capacity || route.capacity)}</td>
              <td className="px-3 py-2">{formatPercent(routeLoadFactor(route), 100)}</td>
              <td className="px-3 py-2">{formatDistanceKmFromMeters(route.distance_m || route.total_distance_m)}</td>
              <td className="px-3 py-2">{formatDurationMinFromSeconds(route.time_s || route.duration_s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SimpleObjectTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  const t = useT();
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 6);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] border-collapse text-sm">
        <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-3 py-2">{t(toTitle(column))}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-t border-border">
              {columns.map((column) => (
                <td key={column} className="max-w-[260px] truncate px-3 py-2">{stringValue(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RouteRiskTable({ routes }: { routes: Array<Record<string, unknown>> }) {
  const t = useT();
  const rows = routes
    .map((route) => {
      const durationMin = Number(route.duration_s) / 60;
      const loadPct = Number(route.load_factor) * 100;
      const reasons = [];
      if (durationMin >= 70) {
            reasons.push("long ride");
      } else if (durationMin >= 60) {
        reasons.push("near limit");
      }
      if (loadPct < 50) {
        reasons.push("low utilization");
      } else if (loadPct >= 90) {
        reasons.push("very full");
      }
      return {
        route,
        reasons: reasons.length ? reasons : ["balanced"],
        score:
          (durationMin >= 70 ? 3 : durationMin >= 60 ? 1 : 0) +
          (loadPct < 50 ? 2 : 0) +
          (loadPct >= 90 ? 2 : 0),
      };
    })
    .sort((left, right) => right.score - left.score)
    .slice(0, 8);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] border-collapse text-sm">
        <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">{t("Route")}</th>
            <th className="px-3 py-2">{t("Duration")}</th>
            <th className="px-3 py-2">{t("Load")}</th>
            <th className="px-3 py-2">{t("Passengers")}</th>
            <th className="px-3 py-2">{t("What to notice")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ route, reasons }) => (
            <tr key={stringValue(route.route_id)} className="border-t border-border">
              <td className="px-3 py-2 font-medium">{stringValue(route.route_id)}</td>
              <td className="px-3 py-2">{formatDurationMinFromSeconds(route.duration_s)}</td>
              <td className="px-3 py-2">{formatPercent(route.load_factor, 100)}</td>
              <td className="px-3 py-2">{formatNumber(route.passenger_count)}</td>
              <td className="px-3 py-2">{reasons.map((reason) => t(reason)).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MarkdownReport({ markdown }: { markdown: string }) {
  const nodes = parseReportMarkdown(markdown);
  return (
    <div className="space-y-4 text-sm leading-6 text-foreground">
      {nodes.map((node, nodeIndex) => {
        if (node.type === "heading") {
          return (
            <h3 key={`${nodeIndex}-${node.text}`} className="text-base font-semibold text-foreground">
              {node.text}
            </h3>
          );
        }
        if (node.type === "list") {
          return (
            <ul key={`${nodeIndex}-${node.items[0]}`} className="space-y-2 text-muted-foreground">
              {node.items.map((item, itemIndex) => (
                <li key={`${itemIndex}-${item}`} className="flex gap-2">
                  <span className="mt-2 h-1.5 w-1.5 flex-none rounded-full bg-primary" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={`${nodeIndex}-${node.text}`} className="text-muted-foreground">
            {node.text}
          </p>
        );
      })}
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{message}</div>;
}

function CollapsibleSection({ title, children }: { title: string; children: ReactNode }) {
  const t = useT();
  return (
    <details className="rounded-lg border border-border bg-surface shadow-panel">
      <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-foreground transition hover:bg-muted">
        {t(title)}
      </summary>
      <div className="border-t border-border p-4">{children}</div>
    </details>
  );
}

function ImpactSummaryCard({
  label,
  value,
  detail,
  tone = "neutral",
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "neutral" | "success" | "warning" | "info";
}) {
  const t = useT();
  return (
    <div className={cn("min-w-0 rounded-lg border bg-surface p-4 shadow-panel", metricToneClassName(tone))}>
      <div className="text-xs font-medium uppercase text-muted-foreground">{t(label)}</div>
      <div className={cn("mt-3 text-2xl font-semibold", metricValueClassName(tone))}>{value}</div>
      <div className="mt-2 text-xs leading-5 text-muted-foreground">{detail}</div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  tone = "neutral",
  detail = "",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "warning" | "info";
  detail?: string;
}) {
  const t = useT();
  return (
    <div className={cn("min-w-0 rounded-lg border bg-surface p-4 shadow-panel", metricToneClassName(tone))}>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 text-xs font-medium uppercase text-muted-foreground">{t(label)}</div>
      </div>
      <div className={cn("mt-3 text-2xl font-semibold", metricValueClassName(tone))}>{value}</div>
      {detail ? <div className="mt-2 text-xs leading-5 text-muted-foreground">{t(detail)}</div> : null}
    </div>
  );
}

function metricToneClassName(tone: "neutral" | "success" | "warning" | "info") {
  if (tone === "success") {
    return "border-emerald-200 bg-emerald-50/35";
  }
  if (tone === "warning") {
    return "border-amber-200 bg-amber-50/35";
  }
  if (tone === "info") {
    return "border-cyan-200 bg-cyan-50/35";
  }
  return "border-border";
}

function metricValueClassName(tone: "neutral" | "success" | "warning" | "info") {
  if (tone === "success") {
    return "text-emerald-800";
  }
  if (tone === "warning") {
    return "text-amber-800";
  }
  if (tone === "info") {
    return "text-cyan-800";
  }
  return "text-foreground";
}

function ReadoutItem({
  label,
  value,
  translateValue = false,
}: {
  label: string;
  value: string;
  translateValue?: boolean;
}) {
  const t = useT();
  const displayValue = value ? (translateValue ? t(value) : value) : t("Not available");
  return (
    <div>
      <div className="text-xs font-medium uppercase text-muted-foreground">{t(label)}</div>
      <div className="mt-1 break-words font-medium">{displayValue}</div>
    </div>
  );
}

function translateCurrentPlanRecommendation(
  text: string,
  t: (key: string, fallback?: string) => string,
) {
  const normalized = text.trim();
  const moreRoutes = normalized.match(/^The current plan uses ([\d,.]+) more routes than the (?:free-optimization baseline|optimized plan)\.$/);
  if (moreRoutes) {
    return `${t("The current plan uses")} ${moreRoutes[1]} ${t("more routes than the optimized plan.")}`;
  }

  const routeDistanceGap = normalized.match(/^The current plan average route distance is ([\d,.]+)% above the (?:free-optimization baseline|optimized plan)\.$/);
  if (routeDistanceGap) {
    return `${t("The current plan average route distance is")} ${routeDistanceGap[1]}% ${t("above the optimized plan.")}`;
  }

  const routeDurationGap = normalized.match(/^The current plan average route duration is ([\d,.]+)% above the (?:free-optimization baseline|optimized plan)\.$/);
  if (routeDurationGap) {
    return `${t("The current plan average route duration is")} ${routeDurationGap[1]}% ${t("above the optimized plan.")}`;
  }

  const moreVehicles = normalized.match(/^The current plan uses ([\d,.]+) more (.+) vehicles than the (?:free-optimization baseline|optimized plan)\.$/);
  if (moreVehicles) {
    return `${t("The current plan uses")} ${moreVehicles[1]} ${t("more")} ${moreVehicles[2]} ${t("vehicles than the optimized plan.")}`;
  }

  const routeCountDiff = normalized.match(/^The current plan route count differs from the (.+) by ([+-]?[\d,.]+)\.$/);
  if (routeCountDiff) {
    return `${t("The current plan route count differs from the")} ${t(routeCountDiff[1])} ${t("by")} ${routeCountDiff[2]}.`;
  }

  const comparedDistance = normalized.match(/^Compared with the (.+), (.+) can reduce average route distance by about ([\d,.]+)%\.$/);
  if (comparedDistance) {
    return `${t("Compared with the")} ${t(comparedDistance[1])}, ${t(comparedDistance[2])} ${t("can reduce average route distance by about")} ${comparedDistance[3]}%.`;
  }

  const comparedTime = normalized.match(/^Compared with the (.+), (.+) can reduce average route time by about ([\d,.]+)%\.$/);
  if (comparedTime) {
    return `${t("Compared with the")} ${t(comparedTime[1])}, ${t(comparedTime[2])} ${t("can reduce average route time by about")} ${comparedTime[3]}%.`;
  }

  const unchangedLoad = normalized.match(/^Load factor remains unchanged in the (.+), which confirms that this comparison isolates network design quality rather than vehicle capacity\.$/);
  if (unchangedLoad) {
    return `${t("Load factor remains unchanged in the")} ${t(unchangedLoad[1])}${t(", which confirms that this comparison isolates network design quality rather than vehicle capacity.")}`;
  }

  const removablePackages = normalized.match(/^The (?:constrained-improvement baseline|optimized improvement plan) includes ([\d,.]+) package\(s\) that fully empty a route, creating immediate route-removal candidates\.$/);
  if (removablePackages) {
    return `${t("The optimized improvement plan includes")} ${removablePackages[1]} ${t("package(s) that fully empty a route, creating immediate route-removal candidates.")}`;
  }

  const limitedDemandPackages = normalized.match(/^The (?:constrained-improvement baseline|optimized improvement plan) includes ([\d,.]+) package\(s\) that leave a route with very limited residual demand, creating a strong removal path\.$/);
  if (limitedDemandPackages) {
    return `${t("The optimized improvement plan includes")} ${limitedDemandPackages[1]} ${t("package(s) that leave a route with very limited residual demand, creating a strong removal path.")}`;
  }

  const consolidationPackages = normalized.match(/^The (?:constrained-improvement baseline|optimized improvement plan) includes ([\d,.]+) package\(s\) that move a route materially closer to consolidation\.$/);
  if (consolidationPackages) {
    return `${t("The optimized improvement plan includes")} ${consolidationPackages[1]} ${t("package(s) that move a route materially closer to consolidation.")}`;
  }

  const practicalMerge = normalized.match(/^The leading constrained package is a practical merge candidate because the receiving route still lands near ([\d,.]+) minutes and ([\d,.]+)% load\.$/);
  if (practicalMerge) {
    return `${t("The leading constrained package is a practical merge candidate because the receiving route still lands near")} ${practicalMerge[1]} ${t("minutes and")} ${practicalMerge[2]}% ${t("load.")}`;
  }

  const tightMerge = normalized.match(/^The leading constrained package is feasible but tight because the receiving route rises to about ([\d,.]+) minutes and ([\d,.]+)% load\.$/);
  if (tightMerge) {
    return `${t("The leading constrained package is feasible but tight because the receiving route rises to about")} ${tightMerge[1]} ${t("minutes and")} ${tightMerge[2]}% ${t("load.")}`;
  }

  const notCleanMerge = normalized.match(/^The leading constrained package is not yet a clean merge because the receiving route would be pushed to about ([\d,.]+) minutes and ([\d,.]+)% load\.$/);
  if (notCleanMerge) {
    return `${t("The leading constrained package is not yet a clean merge because the receiving route would be pushed to about")} ${notCleanMerge[1]} ${t("minutes and")} ${notCleanMerge[2]}% ${t("load.")}`;
  }

  return t(normalized, normalized);
}

function collectAiAuditReports(
  reports: Record<string, Record<string, unknown>> | null | undefined,
  legacyReport: Record<string, unknown> | null | undefined,
): Record<string, Record<string, unknown>> {
  const collected: Record<string, Record<string, unknown>> = {};
  if (reports && typeof reports === "object") {
    Object.entries(reports).forEach(([key, value]) => {
      const report = asRecord(value);
      if (Object.keys(report).length) {
        collected[getAiAuditLanguageKey(key)] = report;
      }
    });
  }
  const legacy = asRecord(legacyReport);
  if (Object.keys(legacy).length) {
    collected[getAiAuditLanguageKey(legacy.language || "English")] ??= legacy;
  }
  return collected;
}

function getAiAuditLanguageKey(language: unknown): "en" | "ko" | "zh" {
  const normalized = stringValue(language).trim().toLowerCase();
  if (
    normalized.startsWith("zh")
    || normalized.startsWith("cn")
    || normalized.includes("chinese")
    || normalized.includes("中文")
    || normalized.includes("汉语")
    || normalized.includes("漢語")
  ) {
    return "zh";
  }
  if (
    normalized.startsWith("ko")
    || normalized.startsWith("kr")
    || normalized.includes("korean")
    || normalized.includes("한국")
    || normalized.includes("한글")
  ) {
    return "ko";
  }
  return "en";
}

type ScenarioRow = {
  key: string;
  name: string;
  detail: string;
  enabled: boolean;
  skippedReason: string;
  trafficGate: Record<string, unknown>;
  exceptionAccepted: boolean;
  routeCount: unknown;
  stopCount: unknown;
  avgDistanceM: unknown;
  avgDurationS: unknown;
  busMix: Record<string, unknown>;
  routes: Array<Record<string, unknown>>;
};

type ArrivalReverseCheck = {
  scenarioName: string;
  checkedRouteCount: number;
  warningRouteCount: number;
  unavailableRouteCount: number;
  maxDepartureWindowOverrunMinutes: number;
  earliestRequiredDepartureMinutes: number | null;
  routes: ArrivalReverseRoute[];
};

type ArrivalReverseRoute = {
  scenarioName: string;
  routeId: string;
  busType: string;
  riderCount: unknown;
  serviceStopCount: unknown;
  verifiedTotalDurationS: unknown;
  requiredDepartureLabel: string;
  departureWindowOverrunMinutes: number;
  beforeEarliestDeparture: boolean;
  available: boolean;
};

function formatTimeImpactLimitMinutes(value: unknown): string {
  const rawValue = Number(value === null || value === undefined || value === "" ? 15 : value);
  const numeric = Math.max(0, rawValue);
  if (!Number.isFinite(numeric)) return "15";
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1).replace(/\.0$/, "");
}

function scenarioDisplayName(fallback: string, scenario: Record<string, unknown>): string {
  return normalizeScenarioDisplayName(stringValue(scenario.display_name || scenario.scenario_label) || fallback);
}

function normalizeScenarioDisplayName(label: string): string {
  if (label.endsWith("-Minute Constrained")) {
    return `${label.slice(0, -"Minute Constrained".length)}Minute Balanced Plan`;
  }
  if (label === "Exception Preserving") {
    return "Protected Route Plan";
  }
  if (label.startsWith("EP ") && label.endsWith("-Minute")) {
    const limit = label.slice(3, -"Minute".length).trim();
    return `Protected ${limit}Minute Plan`;
  }
  return label;
}

function timeImpactLimitFromScenario(result: Record<string, unknown>, scenario: Record<string, unknown>): string {
  const constraint = asRecord(scenario.time_constraint);
  const plannerConfig = asRecord(result.planner_config);
  return formatTimeImpactLimitMinutes(
    scenario.time_impact_limit_minutes ??
      constraint.time_impact_limit_minutes ??
      constraint.threshold_minutes ??
      plannerConfig.time_impact_limit_minutes ??
      15,
  );
}

function timeImpactScenarioName(
  result: Record<string, unknown>,
  scenario: Record<string, unknown>,
  kind: "time_constrained" | "ep15min",
): string {
  const fallback =
    kind === "ep15min"
      ? `Protected ${timeImpactLimitFromScenario(result, scenario)}-Minute Plan`
      : `${timeImpactLimitFromScenario(result, scenario)}-Minute Balanced Plan`;
  return scenarioDisplayName(fallback, scenario);
}

function buildScenarioRows(result: Record<string, unknown>): ScenarioRow[] {
  const structured = asRecord(result.structured_results);
  const currentPlanScenario = asRecord(structured.current_plan || result.current_plan_scenario);
  const timeConstrainedScenario = asRecord(result.time_constrained_optimization || structured.time_constrained);
  const ep15Scenario = asRecord(result.ep15min_optimization || structured.ep15min);
  const currentPlanRow = Object.keys(currentPlanScenario).length
    ? scenarioFromScenario("current_plan", "Current Plan", "Imported supplier route order", currentPlanScenario)
    : scenarioFromAssessment("current_plan", "Current Plan", "Imported supplier route order", asRecord(result.current_plan_assessment));
  return [
    currentPlanRow,
    scenarioFromScenario("time_constrained", timeImpactScenarioName(result, timeConstrainedScenario, "time_constrained"), `Balances vehicle saving, time window, and a ${timeImpactLimitFromScenario(result, timeConstrainedScenario)}-minute time-impact limit.`, timeConstrainedScenario),
    scenarioFromScenario("exception_preserving", "Protected Route Plan", "Keeps current exception routes protected, then optimizes the remaining stops.", asRecord(result.exception_preserving_optimization || structured.exception_preserving)),
    scenarioFromScenario("ep15min", timeImpactScenarioName(result, ep15Scenario, "ep15min"), `Keeps current exception routes protected and applies the ${timeImpactLimitFromScenario(result, ep15Scenario)}-minute time-impact limit.`, ep15Scenario),
  ];
}

function buildArrivalReverseChecks(result: Record<string, unknown>, selectedScenarioKey = ""): ArrivalReverseCheck[] {
  const structured = asRecord(result.structured_results);
  const timeConstrainedScenario = asRecord(result.time_constrained_optimization || structured.time_constrained);
  const ep15Scenario = asRecord(result.ep15min_optimization || structured.ep15min);
  const scenarios: Record<string, [string, Record<string, unknown>]> = {
    current_plan: ["Current Plan", asRecord(structured.current_plan || result.current_plan_scenario)],
    time_constrained: [timeImpactScenarioName(result, timeConstrainedScenario, "time_constrained"), timeConstrainedScenario],
    exception_preserving: ["Protected Route Plan", asRecord(result.exception_preserving_optimization || structured.exception_preserving)],
    ep15min: [timeImpactScenarioName(result, ep15Scenario, "ep15min"), ep15Scenario],
    subway: ["Subway Aggregated", asRecord(structured.subway || result.subway)],
    nearby: ["Nearby Aggregated", asRecord(structured.nearby || result.nearby)],
  };
  const selected = scenarios[selectedScenarioKey];
  return (selected ? [selected] : Object.values(scenarios))
    .map(([scenarioName, scenario]) => buildArrivalReverseCheck(scenarioName, scenario))
    .filter((check): check is ArrivalReverseCheck => Boolean(check));
}

function buildArrivalReverseCheck(scenarioName: string, scenario: Record<string, unknown>): ArrivalReverseCheck | null {
  if (!Object.keys(scenario).length || scenario.enabled === false) {
    return null;
  }
  const explicit = asRecord(scenario.arrival_reverse_check);
  const explicitRoutes = asRecordArray(explicit.routes);
  const sourceRoutes = explicitRoutes.length ? explicitRoutes : asRecordArray(scenario.routes);
  const routes = sourceRoutes
    .map((route, index) => normalizeArrivalReverseRoute(scenarioName, route, index, Boolean(explicitRoutes.length)))
    .filter((route): route is ArrivalReverseRoute => Boolean(route));
  if (!routes.length) {
    return null;
  }
  const warningRouteCount = routes.filter((route) => route.beforeEarliestDeparture).length;
  const checkedRouteCount = routes.filter((route) => route.available).length;
  const unavailableRouteCount = routes.length - checkedRouteCount;
  const overrunMinutes = Number(explicit.max_departure_window_overrun_minutes);
  const earliestRequiredMinutes = Number(explicit.earliest_required_departure_minutes);
  const derivedEarliestRequired = routes
    .map((route) => parseClockMinutes(route.requiredDepartureLabel))
    .filter((value): value is number => value !== null);
  return {
    scenarioName,
    checkedRouteCount: Number.isFinite(Number(explicit.checked_route_count)) ? Number(explicit.checked_route_count) : checkedRouteCount,
    warningRouteCount: Number.isFinite(Number(explicit.warning_route_count)) ? Number(explicit.warning_route_count) : warningRouteCount,
    unavailableRouteCount: Number.isFinite(Number(explicit.unavailable_route_count)) ? Number(explicit.unavailable_route_count) : unavailableRouteCount,
    maxDepartureWindowOverrunMinutes: Number.isFinite(overrunMinutes)
      ? overrunMinutes
      : Math.max(...routes.map((route) => route.departureWindowOverrunMinutes), 0),
    earliestRequiredDepartureMinutes: Number.isFinite(earliestRequiredMinutes)
      ? earliestRequiredMinutes
      : derivedEarliestRequired.length
        ? Math.min(...derivedEarliestRequired)
        : null,
    routes,
  };
}

function normalizeArrivalReverseRoute(
  scenarioName: string,
  route: Record<string, unknown>,
  index: number,
  routeIsExplicitCheck: boolean,
): ArrivalReverseRoute | null {
  const explicit = routeIsExplicitCheck ? route : asRecord(route.arrival_reverse_check);
  if (Object.keys(explicit).length) {
    return {
      scenarioName,
      routeId: stringValue(explicit.route_id || route.route_id || route.vehicle_id || `Bus ${index + 1}`),
      busType: stringValue(explicit.bus_type || route.bus_type_name || route.bus_type),
      riderCount: explicit.rider_count ?? routePassengerCount(route),
      serviceStopCount: explicit.service_stop_count ?? routeStopCount(route),
      verifiedTotalDurationS: explicit.verified_total_duration_s,
      requiredDepartureLabel: stringValue(explicit.required_departure_label),
      departureWindowOverrunMinutes: Number(explicit.departure_window_overrun_minutes || 0),
      beforeEarliestDeparture: Boolean(explicit.before_earliest_departure),
      available: explicit.available !== false && stringValue(explicit.status) !== "unavailable",
    };
  }
  const gate = asRecord(route.final_route_traffic_gate || route.am_arrival_gate);
  if (!Object.keys(gate).length || trafficGateType(gate) !== "arrival_window") {
    return null;
  }
  const latestArrivalMinutes = Number(gate.latest_arrival_minutes ?? gate.target_arrival_minutes);
  const earliestDepartureMinutes = Number(gate.earliest_departure_minutes);
  const verifiedTotalDurationS = Number(gate.verified_total_duration_s);
  const requiredDepartureMinutes =
    Number.isFinite(latestArrivalMinutes) && Number.isFinite(verifiedTotalDurationS)
      ? latestArrivalMinutes - verifiedTotalDurationS / 60
      : Number(gate.verified_departure_minutes);
  const available = stringValue(gate.status) !== "unavailable" && Number.isFinite(requiredDepartureMinutes);
  return {
    scenarioName,
    routeId: stringValue(route.route_id || route.vehicle_id || gate.route_id || `Bus ${index + 1}`),
    busType: stringValue(route.bus_type_name || route.bus_type),
    riderCount: routePassengerCount(route),
    serviceStopCount: routeStopCount(route),
    verifiedTotalDurationS: gate.verified_total_duration_s,
    requiredDepartureLabel: Number.isFinite(requiredDepartureMinutes) ? formatClockMinutes(requiredDepartureMinutes) : "",
    departureWindowOverrunMinutes: Number(gate.time_window_overrun_minutes || gate.estimated_arrival_delay_minutes || 0),
    beforeEarliestDeparture:
      Number.isFinite(requiredDepartureMinutes) &&
      Number.isFinite(earliestDepartureMinutes) &&
      requiredDepartureMinutes < earliestDepartureMinutes,
    available,
  };
}

function scenarioFromAssessment(key: string, name: string, detail: string, assessment: Record<string, unknown>): ScenarioRow {
  return {
    key,
    name,
    detail,
    enabled: Object.keys(assessment).length > 0,
    skippedReason: "",
    trafficGate: {},
    exceptionAccepted: false,
    routeCount: assessment.route_count,
    stopCount: assessmentServiceStopCount(assessment),
    avgDistanceM: assessment.avg_route_distance_m,
    avgDurationS: assessment.avg_route_duration_s,
    busMix: asRecord(assessment.bus_mix),
    routes: asRecordArray(assessment.route_summaries),
  };
}

function scenarioFromScenario(key: string, name: string, detail: string, scenario: Record<string, unknown>): ScenarioRow {
  return {
    key,
    name,
    detail,
    enabled: Object.keys(scenario).length > 0 && scenario.enabled !== false,
    skippedReason: stringValue(scenario.skipped_reason),
    trafficGate: asRecord(scenario.traffic_gate),
    exceptionAccepted: Boolean(asRecord(scenario.exception_preserving).accepted || scenario.exception_feasible),
    routeCount: scenario.route_count || scenario.bus_count,
    stopCount: scenarioServiceStopCount(scenario),
    avgDistanceM: scenario.avg_route_distance_m,
    avgDurationS: scenario.avg_route_duration_s,
    busMix: asRecord(scenario.bus_mix),
    routes: asRecordArray(scenario.routes),
  };
}

function trafficGateType(gate: Record<string, unknown>): "route_duration" | "arrival_window" {
  const gateType = stringValue(gate.gate_type);
  if (gateType === "route_duration" || stringValue(gate.service_direction) === "From School") {
    return "route_duration";
  }
  return "arrival_window";
}

function trafficGateCheckName(gate: Record<string, unknown>): string {
  return trafficGateType(gate) === "route_duration" ? "route-duration check" : "time-window check";
}

function trafficGateFailureText(gate: Record<string, unknown>): string {
  return trafficGateType(gate) === "route_duration" ? "route(s) over route-duration budget" : "route(s) outside final time window";
}

function scenarioTrafficStatusLabel(scenario: Pick<ScenarioRow, "trafficGate" | "exceptionAccepted">): string {
  const status = stringValue(scenario.trafficGate.status);
  const savingTarget = asRecord(scenario.trafficGate.vehicle_saving_target);
  const savingStatus = stringValue(savingTarget.status);
  const checkName = trafficGateCheckName(scenario.trafficGate);
  if (status === "failed" && scenario.exceptionAccepted) {
    return "Exception contained";
  }
  if (status === "passed" && savingStatus === "failed") {
    return "Vehicle saving target failed";
  }
  if (status === "passed") {
    return trafficGateType(scenario.trafficGate) === "route_duration" ? "Route duration passed" : "Time window passed";
  }
  if (status === "failed") {
    return trafficGateType(scenario.trafficGate) === "route_duration" ? "Route duration failed" : "Time window failed";
  }
  if (status === "unavailable") {
    return `AMap ${checkName} incomplete`;
  }
  return "";
}

function scenarioCardDetail(scenario: Pick<ScenarioRow, "trafficGate" | "exceptionAccepted">, detail: string): string {
  const status = scenarioTrafficStatusLabel(scenario);
  return status ? `${status}. ${detail}` : detail;
}

function scenarioSkippedDetail(scenario: Pick<ScenarioRow, "skippedReason"> | undefined, adjustmentHint: string): string {
  const reason = scenario?.skippedReason.trim();
  return `Skipped: ${reason || "Scenario was not enabled for this run."} ${adjustmentHint}`;
}

function scenarioTrafficTone(
  scenario: Pick<ScenarioRow, "trafficGate" | "exceptionAccepted">,
  fallback: "neutral" | "success" | "warning" | "info",
): "neutral" | "success" | "warning" | "info" {
  const status = stringValue(scenario.trafficGate.status);
  const savingStatus = stringValue(asRecord(scenario.trafficGate.vehicle_saving_target).status);
  if (status === "failed" && scenario.exceptionAccepted) {
    return "info";
  }
  if (status === "passed" && savingStatus === "failed") {
    return "warning";
  }
  if (status === "passed") {
    return "success";
  }
  if (status === "failed" || status === "unavailable") {
    return "warning";
  }
  return fallback;
}

function scenarioIsAdoptionReady(scenario: ScenarioRow): boolean {
  const status = stringValue(scenario.trafficGate.status);
  return status === "passed" || (status === "failed" && scenario.exceptionAccepted);
}

function pickRecommendedScenario(scenarios: ScenarioRow[]): ScenarioRow | undefined {
  const optimized = ["time_constrained", "ep15min", "exception_preserving"];
  return (
    optimized
      .map((key) => scenarios.find((scenario) => scenario.key === key && scenario.enabled))
      .find((scenario): scenario is ScenarioRow => Boolean(scenario && scenarioIsAdoptionReady(scenario))) ||
    optimized
      .map((key) => scenarios.find((scenario) => scenario.key === key && scenario.enabled))
      .find((scenario): scenario is ScenarioRow => Boolean(scenario))
  );
}

function buildBenchmarkGateWarnings(result: Record<string, unknown>): string[] {
  const timeConstrainedScenario = asRecord(result.time_constrained_optimization);
  const ep15Scenario = asRecord(result.ep15min_optimization);
  const scenarios: Array<[string, Record<string, unknown>]> = [
    [timeImpactScenarioName(result, timeConstrainedScenario, "time_constrained"), asRecord(timeConstrainedScenario.traffic_gate)],
    [timeImpactScenarioName(result, ep15Scenario, "ep15min"), asRecord(ep15Scenario.traffic_gate)],
  ];
  return scenarios.flatMap(([label, gate]) => {
    const status = stringValue(gate.status);
    const checkName = trafficGateCheckName(gate);
    if (status === "failed") {
      return [`${label} failed the final ${checkName}; do not treat its vehicle saving as adoption-ready.`];
    }
    if (status === "unavailable") {
      return [`${label} did not complete AMap final ${checkName}; review before adoption.`];
    }
    return [];
  });
}


function getJobDisplayName(job: JobRecord) {
  const metadata = asRecord(job.metadata);
  const result = asRecord(job.result);
  return (
    stringValue(metadata.job_name) ||
    stringValue(metadata.title) ||
    stringValue(metadata.source_label) ||
    stringValue(result.source_label) ||
    job.job_id
  );
}

export function downloadInteractiveMapHtml(data: JobMapData, jobName: string, mapName: string) {
  const filename = `${sanitizeDownloadFilename(jobName)} - ${sanitizeDownloadFilename(mapName)}.html`;
  const blob = new Blob([buildStandaloneInteractiveMapHtml(data, jobName, mapName)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function sanitizeDownloadFilename(value: string) {
  const cleaned = value
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (cleaned || "BRP Map").slice(0, 120).trim();
}

function buildStandaloneInteractiveMapHtml(data: JobMapData, jobName: string, mapName: string) {
  const payload = JSON.stringify(data).replace(/</g, "\\u003c");
  const title = `${jobName} - ${mapName}`;
  const summaryRecord = asRecord(data.summary);
  const amGate = asRecord(summaryRecord.am_arrival_gate);
  const amStatus = String(amGate.status || "");
  const amWindowSummary =
    amStatus === "failed"
      ? `Time window failed (${formatNumber(amGate.failed_route_count)} routes)`
      : amStatus === "unavailable"
        ? `Time window unverified (${formatNumber(amGate.unavailable_route_count)} routes)`
        : amStatus === "passed"
          ? "Time window passed"
          : "";
  const mapSummary = `${formatNumber(data.summary.route_count)} routes · ${formatNumber(data.summary.stop_count)} stops · ${formatNumber(data.summary.passenger_count)} riders${amWindowSummary ? ` · ${amWindowSummary}` : ""}`;
  const standaloneTileUrls = ["https://tile.openstreetmap.de/{z}/{x}/{y}.png"];
  const routeColors = [
    "#0f766e", "#2563eb", "#c2410c", "#7c3aed", "#15803d", "#be123c", "#0891b2", "#a16207",
    "#4338ca", "#db2777", "#047857", "#b45309", "#0369a1", "#9333ea", "#4d7c0f", "#dc2626",
    "#0e7490", "#6d28d9", "#ca8a04", "#1d4ed8", "#9f1239", "#166534",
  ];
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${htmlEscape(title)}</title>
  <link href="https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.css" rel="stylesheet" />
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; background: #0f172a; }
    .app { height: 100vh; min-height: 640px; padding: 18px; background: radial-gradient(circle at top left, rgba(15,118,110,.22), transparent 32%), linear-gradient(135deg, #0f172a, #1e293b 55%, #0f172a); }
    .viewer { height: 100%; min-height: 0; overflow: hidden; border: 1px solid rgba(255,255,255,.55); border-radius: 14px; background: rgba(255,255,255,.72); box-shadow: 0 28px 70px rgba(15,23,42,.38); backdrop-filter: blur(18px); }
    .toolbar { min-height: 58px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,.55); background: rgba(255,255,255,.72); backdrop-filter: blur(18px); }
    .title { min-width: 0; }
    .title h1 { margin: 0; font-size: 15px; line-height: 1.25; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .title p { margin: 3px 0 0; color: #64748b; font-size: 12px; }
    .toolbar-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    button { font: inherit; }
    .button { height: 36px; border-radius: 8px; border: 1px solid #cbd5e1; background: rgba(255,255,255,.78); padding: 0 12px; font-size: 14px; font-weight: 650; color: #334155; cursor: pointer; backdrop-filter: blur(12px); }
    .button:hover { background: white; }
    .button.close { border-color: #fca5a5; background: rgba(254,242,242,.92); color: #b91c1c; }
    .body { position: relative; height: calc(100% - 58px); min-height: 0; }
    #map { position: absolute; inset: 0; }
    .sidebar { position: absolute; z-index: 3; inset: 12px auto 12px 12px; width: 360px; display: flex; min-height: 0; flex-direction: column; overflow: hidden; border: 1px solid rgba(255,255,255,.48); border-radius: 14px; background: rgba(255,255,255,.30); box-shadow: 0 24px 55px rgba(15,23,42,.24); backdrop-filter: blur(26px); }
    .sidebar-head { padding: 14px; border-bottom: 1px solid rgba(255,255,255,.42); background: rgba(255,255,255,.18); backdrop-filter: blur(22px); }
    .sidebar-title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .sidebar h2 { margin: 0; font-size: 18px; }
    .summary { margin-top: 6px; color: #64748b; font-size: 13px; }
    .fit { height: 34px; border: 1px solid #0f766e; color: #0f766e; background: rgba(255,255,255,.72); }
    .search { width: 100%; height: 40px; margin-top: 14px; border: 1px solid rgba(255,255,255,.48); border-radius: 9px; padding: 0 12px; background: rgba(255,255,255,.42); color: #111827; outline: none; font-size: 14px; backdrop-filter: blur(12px); }
    .chips { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
    .chip { height: 30px; border-radius: 8px; border: 1px solid rgba(255,255,255,.45); background: rgba(255,255,255,.42); color: #64748b; padding: 0 10px; font-size: 12px; font-weight: 700; cursor: pointer; }
    .chip.active { border-color: #0f766e; background: #0f766e; color: white; }
    .showing { margin-top: 10px; color: #64748b; font-size: 12px; }
    .routes { min-height: 0; flex: 1; overflow: auto; padding: 9px; display: flex; flex-direction: column; gap: 8px; }
    .route-card { flex: 0 0 auto; overflow: hidden; border: 1px solid rgba(255,255,255,.38); border-radius: 12px; background: rgba(255,255,255,.28); box-shadow: 0 4px 16px rgba(15,23,42,.10); backdrop-filter: blur(20px); }
    .route-card.active { background: rgba(255,255,255,.58); }
    .route-main { width: 100%; min-height: 76px; display: flex; gap: 12px; align-items: flex-start; border: 0; border-left: 3px solid transparent; background: transparent; padding: 13px 12px; text-align: left; cursor: pointer; color: #111827; }
    .dot { width: 12px; height: 12px; margin-top: 5px; flex: none; border-radius: 999px; box-shadow: 0 0 0 2px rgba(255,255,255,.9); }
    .route-text { display: block; min-width: 0; flex: 1; }
    .route-title { display: flex; align-items: center; gap: 8px; font-weight: 800; font-size: 15px; line-height: 1.3; }
    .route-title span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badge { flex: 0 0 auto; border: 1px solid #bae6fd; background: #eff6ff; color: #0369a1; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 800; text-transform: uppercase; }
    .badge.capacity { border-color: #fecdd3; background: #fff1f2; color: #be123c; }
    .badge.high { border-color: #fde68a; background: #fffbeb; color: #b45309; }
    .route-meta { display: block; margin-top: 7px; color: #64748b; font-size: 13px; line-height: 1.45; }
    .chevron { width: 26px; height: 26px; margin-top: 2px; display: flex; align-items: center; justify-content: center; border-radius: 999px; background: rgba(255,255,255,.48); color: #475569; flex: none; }
    .stops { max-height: 300px; overflow: auto; border-top: 1px solid rgba(255,255,255,.32); background: rgba(255,255,255,.18); padding: 9px; }
    .stops-label { margin: 0 0 6px; color: #64748b; font-size: 11px; font-weight: 800; text-transform: uppercase; }
    .stop-row { width: 100%; display: grid; grid-template-columns: 28px minmax(0,1fr); gap: 8px; border: 0; border-radius: 8px; background: transparent; padding: 8px; text-align: left; cursor: pointer; }
    .stop-row:hover { background: rgba(255,255,255,.46); }
    .stop-row.active { background: #0f766e; color: white; }
    .stop-address { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 650; }
    .stop-meta { margin-top: 3px; color: #64748b; font-size: 12px; }
    .stop-row.active .stop-meta { color: rgba(255,255,255,.82); }
    .maplibregl-popup-content { border-radius: 10px; box-shadow: 0 14px 40px rgba(15,23,42,.25); }
    .popup { max-width: 260px; font-size: 12px; }
    .popup strong { display: block; margin-bottom: 4px; }
    @media (max-width: 760px) { .app { padding: 8px; } .toolbar { align-items: flex-start; flex-direction: column; } .body { height: calc(100% - 106px); } .sidebar { inset: 10px; width: auto; max-height: 45%; } }
  </style>
</head>
<body>
  <div class="app">
    <div class="viewer">
      <div class="toolbar">
        <div class="title">
          <h1>${htmlEscape(title)}</h1>
          <p>${htmlEscape(data.scenario_name)} · ${htmlEscape(mapSummary)}</p>
        </div>
        <div class="toolbar-actions">
          <button class="button" type="button" onclick="fitAll()">Fit all</button>
          <button class="button close" type="button" onclick="window.close()">Close</button>
        </div>
      </div>
      <div class="body">
        <div id="map"></div>
        <aside class="sidebar">
          <div class="sidebar-head">
            <div class="sidebar-title-row">
              <div><h2>${htmlEscape(data.scenario_name)}</h2><div class="summary">${htmlEscape(mapSummary)}</div></div>
              <button class="button fit" type="button" onclick="fitAll()">Fit all</button>
            </div>
            <input id="search" class="search" placeholder="Search route, bus, vehicle" />
            <div class="chips"><button class="chip active" data-filter="all">All</button><button class="chip" data-filter="long">Long</button><button class="chip" data-filter="high">High load</button><button class="chip" data-filter="many">Many stops</button></div>
            <div id="showing" class="showing"></div>
          </div>
          <div id="routes" class="routes"></div>
        </aside>
      </div>
    </div>
  </div>
  <script src="https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.js"></script>
  <script>window.BRP_MAP_DATA = ${payload};</script>
  <script>
    const data = window.BRP_MAP_DATA;
    const colors = ${JSON.stringify(routeColors)};
    const bounds = normalizeBounds(data.bounds);
    let selectedRouteId = "";
    let selectedStopId = "";
    let hoverPopup = null;
    let filter = "all";
    let search = "";
    const routesById = new Map(data.routes.map(route => [route.id, route]));
    const stopsByRouteId = new Map();
    for (const stop of data.stops) { const list = stopsByRouteId.get(stop.route_id) || []; list.push(stop); stopsByRouteId.set(stop.route_id, list); }
    for (const list of stopsByRouteId.values()) list.sort((a,b) => a.order - b.order);
    const routeConnectors = Array.isArray(data.route_connectors) ? data.route_connectors : [];
    const longThreshold = percentile(data.routes.map(route => route.duration_s), 0.75);
    const map = new maplibregl.Map({ container: "map", style: { version: 8, sources: { osm: { type: "raster", tiles: ${JSON.stringify(standaloneTileUrls)}, tileSize: 256, attribution: "OpenStreetMap contributors" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] }, center: bounds ? [(bounds.min_lng + bounds.max_lng) / 2, (bounds.min_lat + bounds.max_lat) / 2] : [121.4737,31.2304], zoom: 11 });
    map.addControl(new maplibregl.NavigationControl(), "bottom-right");
    map.on("load", () => { addLayers(); fitAll(); renderRoutes(); });
    document.getElementById("search").addEventListener("input", event => { search = event.target.value.toLowerCase().trim(); renderRoutes(); });
    document.querySelectorAll(".chip").forEach(button => button.addEventListener("click", () => { filter = button.dataset.filter; document.querySelectorAll(".chip").forEach(item => item.classList.toggle("active", item === button)); renderRoutes(); }));
    renderRoutes();
    function addLayers() {
      map.addSource("route-connectors", { type: "geojson", data: connectorGeojson(false) });
      map.addLayer({ id: "route-connectors-line", type: "line", source: "route-connectors", paint: { "line-color": ["get","color"], "line-width": 2, "line-opacity": .58, "line-dasharray": [1.5,1.5] } });
      map.addSource("routes", { type: "geojson", data: routeGeojson(false) });
      map.addLayer({ id: "route-casing", type: "line", source: "routes", paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": .72 } });
      map.addLayer({ id: "route-lines", type: "line", source: "routes", paint: { "line-color": ["get","color"], "line-width": 4, "line-opacity": .78 } });
      map.addSource("selected-route-connectors", { type: "geojson", data: selectedConnectorGeojson() });
      map.addLayer({ id: "selected-route-connectors-line", type: "line", source: "selected-route-connectors", paint: { "line-color": ["get","color"], "line-width": 3, "line-opacity": .86, "line-dasharray": [1.5,1.5] } });
      map.addSource("selected-route", { type: "geojson", data: selectedRouteGeojson() });
      map.addLayer({ id: "selected-route-casing", type: "line", source: "selected-route", paint: { "line-color": "#ffffff", "line-width": 13, "line-opacity": .98 } });
      map.addLayer({ id: "selected-route-line", type: "line", source: "selected-route", paint: { "line-color": ["get","color"], "line-width": 8, "line-opacity": .98 } });
      map.addSource("stops", { type: "geojson", data: stopGeojson() });
      map.addLayer({ id: "stops-circle", type: "circle", source: "stops", paint: { "circle-color": ["get","color"], "circle-radius": ["case",["get","is_depot"],6,4], "circle-opacity": .72, "circle-stroke-color": "#111827", "circle-stroke-width": 1.5 } });
      map.on("click", "route-lines", event => { const id = event.features && event.features[0] && event.features[0].properties.route_id; if (id) selectRoute(String(id)); });
      map.on("click", "selected-route-line", event => { const id = event.features && event.features[0] && event.features[0].properties.route_id; if (id) selectRoute(String(id)); });
      map.on("click", "stops-circle", event => { const id = event.features && event.features[0] && event.features[0].properties.stop_id; if (id) selectStop(String(id)); });
      map.on("mousemove", "route-lines", showRouteHover);
      map.on("mousemove", "selected-route-line", showRouteHover);
      map.on("mousemove", "stops-circle", showStopHover);
      map.on("mouseenter", "route-lines", () => map.getCanvas().style.cursor = "pointer");
      map.on("mouseenter", "selected-route-line", () => map.getCanvas().style.cursor = "pointer");
      map.on("mouseenter", "stops-circle", () => map.getCanvas().style.cursor = "pointer");
      map.on("mouseleave", "route-lines", clearHover);
      map.on("mouseleave", "selected-route-line", clearHover);
      map.on("mouseleave", "stops-circle", clearHover);
    }
    function showRouteHover(event) { const feature = event.features && event.features[0]; if (!feature) return; const route = routesById.get(String(feature.properties.route_id)); if (!route) return; showHover(event.lngLat, '<div class="popup"><strong>' + esc(route.id || ('Bus ' + (route.vehicle_id || route.route_index + 1))) + '</strong><div>' + fmt(route.load) + ' riders · ' + fmt(route.stop_count) + ' stops</div><div>' + duration(route.duration_s) + ' · ' + distance(route.distance_m) + '</div>' + (route.bus_type_name ? '<div>' + esc(route.bus_type_name) + '</div>' : '') + '</div>'); }
    function showStopHover(event) { const feature = event.features && event.features[0]; if (!feature) return; const stop = data.stops.find(item => item.id === String(feature.properties.stop_id)); if (!stop) return; showHover(event.lngLat, '<div class="popup"><strong>' + esc(stop.is_depot ? 'School / Start' : 'Stop ' + stop.order) + '</strong><div>' + esc(stop.address || stop.requested_address || 'Unknown address') + '</div><div>' + esc(stop.route_id) + ' · ' + fmt(stop.passenger_count) + ' riders</div><div>' + stopTiming(stop) + ' · ' + distance(stop.cumulative_distance_m) + '</div></div>'); }
    function showHover(lngLat, html) { if (!hoverPopup) hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 }); hoverPopup.setLngLat(lngLat).setHTML(html).addTo(map); }
    function clearHover() { map.getCanvas().style.cursor = "grab"; if (hoverPopup) { hoverPopup.remove(); hoverPopup = null; } }
    function routeGeometry(route) { return route && Array.isArray(route.display_geometry) && route.display_geometry.length >= 2 ? route.display_geometry : (route && Array.isArray(route.geometry) ? route.geometry : []); }
    function connectorGeojson(dimmed) { return { type: "FeatureCollection", features: routeConnectors.filter(connector => connector.geometry && connector.geometry.length >= 2 && (!dimmed || connector.route_id !== selectedRouteId)).map(connector => ({ type: "Feature", properties: { connector_id: connector.id, route_id: connector.route_id, color: color(connector.route_index || 0) }, geometry: { type: "LineString", coordinates: connector.geometry } })) }; }
    function selectedConnectorGeojson() { return { type: "FeatureCollection", features: routeConnectors.filter(connector => connector.route_id === selectedRouteId && connector.geometry && connector.geometry.length >= 2).map(connector => ({ type: "Feature", properties: { connector_id: connector.id, route_id: connector.route_id, color: color(connector.route_index || 0) }, geometry: { type: "LineString", coordinates: connector.geometry } })) }; }
    function routeGeojson(dimmed) { return { type: "FeatureCollection", features: data.routes.filter(route => routeGeometry(route).length >= 2 && (!dimmed || route.id !== selectedRouteId)).map(route => ({ type: "Feature", properties: { route_id: route.id, color: color(route.route_index) }, geometry: { type: "LineString", coordinates: routeGeometry(route) } })) }; }
    function selectedRouteGeojson() { const route = routesById.get(selectedRouteId); return { type: "FeatureCollection", features: route && routeGeometry(route).length >= 2 ? [{ type: "Feature", properties: { route_id: route.id, color: color(route.route_index) }, geometry: { type: "LineString", coordinates: routeGeometry(route) } }] : [] }; }
    function stopGeojson() { const stops = selectedRouteId ? data.stops.filter(stop => stop.route_id === selectedRouteId) : data.stops; return { type: "FeatureCollection", features: stops.map(stop => ({ type: "Feature", properties: { stop_id: stop.id, route_id: stop.route_id, color: color(stop.route_index), is_depot: stop.is_depot }, geometry: { type: "Point", coordinates: [stop.lng, stop.lat] } })) }; }
    function updateMapSources() { if (!map.getSource("routes")) return; map.getSource("route-connectors").setData(connectorGeojson(Boolean(selectedRouteId))); map.getSource("routes").setData(routeGeojson(Boolean(selectedRouteId))); map.getSource("selected-route-connectors").setData(selectedConnectorGeojson()); map.getSource("selected-route").setData(selectedRouteGeojson()); map.getSource("stops").setData(stopGeojson()); }
    function renderRoutes() { const routes = data.routes.filter(route => { const hay = [route.id, route.bus_type_name, route.vehicle_id, route.route_index + 1].join(" ").toLowerCase(); if (search && !hay.includes(search)) return false; if (filter === "long") return route.duration_s >= longThreshold; if (filter === "high") return loadRatio(route) >= .85; if (filter === "many") return route.stop_count >= 8; return true; }); document.getElementById("showing").textContent = "Showing " + routes.length + " of " + data.routes.length + " routes"; document.getElementById("routes").innerHTML = routes.map(routeCard).join(""); document.querySelectorAll("[data-route]").forEach(btn => btn.addEventListener("click", () => selectRoute(btn.dataset.route))); document.querySelectorAll("[data-stop]").forEach(btn => btn.addEventListener("click", event => { event.stopPropagation(); selectStop(btn.dataset.stop); })); }
    function routeCard(route) { const active = route.id === selectedRouteId; const stops = stopsByRouteId.get(route.id) || []; return '<div class="route-card ' + (active ? 'active' : '') + '"><button class="route-main" data-route="' + escAttr(route.id) + '" style="border-left-color:' + color(route.route_index) + '"><span class="dot" style="background:' + color(route.route_index) + '"></span><span class="route-text"><span class="route-title"><span>' + esc(route.id || ('Bus ' + (route.vehicle_id || route.route_index + 1))) + '</span>' + statusBadge(route) + '</span><span class="route-meta">' + fmt(route.load) + ' riders · ' + fmt(route.stop_count) + ' stops · ' + duration(route.duration_s) + '<br />' + distance(route.distance_m) + (route.bus_type_name ? ' · ' + esc(route.bus_type_name) : '') + '</span></span><span class="chevron">' + (active ? '⌃' : '⌄') + '</span></button>' + (active ? '<div class="stops"><p class="stops-label">Stop sequence</p>' + stops.map(stop => '<button class="stop-row ' + (stop.id === selectedStopId ? 'active' : '') + '" data-stop="' + escAttr(stop.id) + '"><span><strong>' + (stop.is_depot ? 'S' : stop.order) + '</strong></span><span><span class="stop-address">' + esc(stop.address || stop.requested_address || 'Unknown address') + '</span><span class="stop-meta">' + fmt(stop.passenger_count) + ' riders · ' + stopTiming(stop) + '</span></span></button>').join('') + '</div>' : '') + '</div>'; }
    function selectRoute(routeId) { if (selectedRouteId === routeId) { selectedRouteId = ''; selectedStopId = ''; updateMapSources(); renderRoutes(); fitAll(); return; } selectedRouteId = routeId; selectedStopId = ''; updateMapSources(); renderRoutes(); fitRoute(routesById.get(routeId)); }
    function selectStop(stopId) { const stop = data.stops.find(item => item.id === stopId); if (!stop) return; selectedStopId = stopId; selectedRouteId = stop.route_id; updateMapSources(); renderRoutes(); map.flyTo({ center: [stop.lng, stop.lat], zoom: Math.max(map.getZoom(), 14), duration: 450 }); new maplibregl.Popup().setLngLat([stop.lng, stop.lat]).setHTML('<div class="popup"><strong>' + esc(stop.is_depot ? 'School / Start' : 'Stop ' + stop.order) + '</strong><div>' + esc(stop.address || stop.requested_address || 'Unknown address') + '</div><div>' + esc(stop.route_id) + ' · ' + fmt(stop.passenger_count) + ' riders</div><div>' + stopTiming(stop) + ' · ' + distance(stop.cumulative_distance_m) + '</div></div>').addTo(map); }
    function fitAll() { if (!bounds) return; map.fitBounds([[bounds.min_lng, bounds.min_lat], [bounds.max_lng, bounds.max_lat]], { padding: 72, duration: 500 }); }
    function normalizeBounds(value) {
      if (!value) return null;
      const normalized = {
        min_lng: Number(value.min_lng ?? value.west),
        min_lat: Number(value.min_lat ?? value.south),
        max_lng: Number(value.max_lng ?? value.east),
        max_lat: Number(value.max_lat ?? value.north),
      };
      return [normalized.min_lng, normalized.min_lat, normalized.max_lng, normalized.max_lat].every(Number.isFinite) ? normalized : null;
    }
    function fitRoute(route) { const geometry = routeGeometry(route); if (!route || geometry.length < 2) return; const connectorPoints = routeConnectors.filter(connector => connector.route_id === route.id).flatMap(connector => connector.geometry || []); const points = geometry.concat(connectorPoints); const lngs = points.map(p => p[0]).filter(Number.isFinite); const lats = points.map(p => p[1]).filter(Number.isFinite); map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 96, duration: 500 }); }
    function statusBadge(route) { const gate = route.am_arrival_gate || {}; const status = gate.status === 'failed' ? 'TIME WINDOW' : gate.status === 'unavailable' ? 'UNVERIFIED' : route.load / (route.bus_capacity || 0) >= 1 ? 'CAPACITY' : route.load / (route.bus_capacity || 1) >= .85 ? 'HIGH LOAD' : route.duration_s >= 3600 ? 'LONG' : ''; if (!status) return ''; return '<span class="badge ' + (status === 'CAPACITY' || status === 'TIME WINDOW' ? 'capacity' : status === 'HIGH LOAD' || status === 'UNVERIFIED' ? 'high' : '') + '">' + status + '</span>'; }
    function stopTiming(stop) { return (stop.scheduled_time_label ? esc(stop.scheduled_time_label) + ' · ' : '') + duration(stop.cumulative_duration_s); }
    function color(index) { return colors[index % colors.length]; }
    function loadRatio(route) { return route.bus_capacity ? route.load / route.bus_capacity : 0; }
    function percentile(values, ratio) { const sorted = values.filter(Number.isFinite).sort((a,b)=>a-b); return sorted.length ? sorted[Math.max(0, Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio)))] : 0; }
    function fmt(value) { return new Intl.NumberFormat().format(Number(value || 0)); }
    function duration(seconds) { const m = Math.round(Number(seconds || 0) / 60); return m >= 60 ? Math.floor(m / 60) + 'h ' + (m % 60) + 'm' : m + ' min'; }
    function distance(meters) { const km = Number(meters || 0) / 1000; return (Math.round(km * 10) / 10) + ' km'; }
    function esc(value) { return String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    function escAttr(value) { return esc(value).split(String.fromCharCode(96)).join("&#96;"); }
  </script>
</body>
</html>`;
}

type Diagnostics = {
  inputAddressWarnings: Array<Record<string, unknown>>;
  geocodeWarnings: Array<Record<string, unknown>>;
  excludedStops: Array<Record<string, unknown>>;
  clientPrepElapsedSeconds: unknown;
};

function getDiagnostics(job: JobRecord): Diagnostics {
  const metadata = asRecord(job.metadata);
  const clientPrep = asRecord(metadata.client_prep);
  const result = asRecord(job.result);
  const structured = asRecord(result.structured_results);
  const inputAddressReview = asRecord(result.input_address_review || structured.input_address_review);
  return {
    inputAddressWarnings: asRecordArray(inputAddressReview.warnings),
    geocodeWarnings: asRecordArray(clientPrep.geocode_warnings),
    excludedStops: asRecordArray(clientPrep.excluded_stops),
    clientPrepElapsedSeconds: clientPrep.elapsed_seconds,
  };
}

type MapOutput = {
  key: string;
  name: string;
  path: string;
  hasRenderableMap: boolean;
  url: string;
  downloadUrl: string;
};

type MapScenarioSummary = {
  key: string;
  name: string;
  routeCount: number;
  stopCount: number;
  passengerCount: number;
  totalDistanceM: number;
  longestDurationS: number;
  exceptionAccepted: boolean;
  trafficStatusLabel: string;
  trafficStatusTone: "neutral" | "success" | "warning" | "info";
};

function dataRouteCountForSummary(summaries: MapScenarioSummary[], key: string) {
  return summaries.find((summary) => summary.key === key)?.routeCount ?? 0;
}

function scenarioForMapSurface(result: Record<string, unknown>, key: string): Record<string, unknown> {
  const structured = asRecord(result.structured_results);
  return asRecord(structured[key]);
}

function collectMapOutputs(jobId: string, result: Record<string, unknown>): MapOutput[] {
  const structured = asRecord(result.structured_results);
  const timeConstrainedScenario = asRecord(result.time_constrained_optimization || structured.time_constrained);
  const ep15Scenario = asRecord(result.ep15min_optimization || structured.ep15min);
  const keys = [
    ["current_plan", "Current Plan"],
    ["time_constrained", timeImpactScenarioName(result, timeConstrainedScenario, "time_constrained")],
    ["exception_preserving", "Protected Route Plan"],
    ["ep15min", timeImpactScenarioName(result, ep15Scenario, "ep15min")],
    ["subway", "Subway Aggregated"],
    ["nearby", "Nearby Aggregated"],
  ] as const;
  return keys
    .map(([key, name]) => {
      const scenario = scenarioForMapSurface(result, key);
      const path = stringValue(scenario.output_html);
      const hasRenderableMap = Boolean(
        scenario.enabled !== false &&
        path &&
        asRecordArray(scenario.points).length > 0 &&
        asRecordArray(scenario.routes).length > 0,
      );
      return {
        key,
        name,
        path,
        hasRenderableMap,
        url: getJobArtifactUrl(jobId, key, { refresh: true }),
        downloadUrl: getJobArtifactUrl(jobId, key, { download: true, refresh: true }),
      };
    })
    .filter((item) => item.hasRenderableMap);
}

function buildMapScenarioSummaries(result: Record<string, unknown>, mapOutputs: MapOutput[]): MapScenarioSummary[] {
  return mapOutputs.map((output) => {
    const scenario = scenarioForMapSurface(result, output.key);
    const routes = asRecordArray(scenario.routes);
    const exceptionAccepted = Boolean(asRecord(scenario.exception_preserving).accepted || scenario.exception_feasible);
    return {
      key: output.key,
      name: output.name,
      routeCount: Number(scenario.route_count || scenario.bus_count || routes.length || 0),
      stopCount: Number(scenarioServiceStopCount(scenario) || 0),
      passengerCount: routes.reduce((total, route) => total + Number(routePassengerCount(route) || 0), 0),
      totalDistanceM: routes.reduce((total, route) => total + Number(route.distance_m || 0), 0),
      longestDurationS: routes.reduce((maxDuration, route) => Math.max(maxDuration, mapRouteDurationSeconds(route)), 0),
      exceptionAccepted,
      trafficStatusLabel: scenarioTrafficStatusLabel({ trafficGate: asRecord(scenario.traffic_gate), exceptionAccepted }),
      trafficStatusTone: scenarioTrafficTone({ trafficGate: asRecord(scenario.traffic_gate), exceptionAccepted }, "neutral"),
    };
  });
}

function mapDataScenarioSummary(base: MapScenarioSummary, data: JobMapData): MapScenarioSummary {
  const routes = Array.isArray(data.routes) ? data.routes : [];
  const summary = data.summary || {};
  return {
    ...base,
    routeCount: Number(summary.route_count || routes.length || base.routeCount),
    stopCount: Number(summary.stop_count || base.stopCount),
    passengerCount: Number(
      summary.passenger_count || routes.reduce((total, route) => total + Number(route.load || 0), 0) || base.passengerCount,
    ),
    totalDistanceM: Number(
      summary.distance_m || routes.reduce((total, route) => total + Number(route.distance_m || 0), 0) || base.totalDistanceM,
    ),
    longestDurationS: routes.reduce((maxDuration, route) => Math.max(maxDuration, Number(route.duration_s || 0)), 0) || base.longestDurationS,
  };
}

function mapRouteDurationSeconds(route: Record<string, unknown>) {
  return Number(route.traffic_api_duration_s || route.traffic_adjusted_drive_time_s || route.time_s || 0);
}

function buildAiReportHtml({
  job,
  report,
  currentPlan,
  currentComparison,
  reallocationSummary,
  scenarios,
  priorityActions,
  translate,
}: {
  job: JobRecord;
  report: Record<string, unknown>;
  currentPlan: Record<string, unknown>;
  currentComparison: Record<string, unknown>;
  reallocationSummary: Record<string, unknown>;
  scenarios: ScenarioRow[];
  priorityActions: Array<Record<string, unknown>>;
  translate?: (key: string, fallback?: string) => string;
}) {
  const t = translate ?? ((key: string) => key);
  const metadata = asRecord(job.metadata);
  const title = stringValue(metadata.job_name) || job.job_id;
  const generatedAt = stringValue(report.generated_at) || new Date().toISOString();
  const scenarioRows = scenarios
    .map(
      (scenario) => `
        <tr>
          <td>${htmlEscape(t(scenario.name))}</td>
          <td>${htmlEscape(formatNumber(scenario.routeCount))}</td>
          <td>${htmlEscape(t(scenarioTrafficStatusLabel(scenario) || "Not applicable"))}</td>
          <td>${htmlEscape(formatDurationMinFromSeconds(scenario.avgDurationS))}</td>
          <td>${htmlEscape(formatDistanceKmFromMeters(scenario.avgDistanceM))}</td>
          <td>${htmlEscape(formatBusMix(scenario.busMix))}</td>
        </tr>`,
    )
    .join("");
  const actionRows = priorityActions
    .map(
      (action) => `
        <li>
          ${htmlEscape(t("Move"))} ${htmlEscape(formatNumber(action.stop_count))} ${htmlEscape(t("stop(s) from"))} ${htmlEscape(stringValue(action.from_route_id) || t("N/A"))}
          ${htmlEscape(t("to"))} ${htmlEscape(stringValue(action.to_route_id) || t("N/A"))};
          ${htmlEscape(t("estimated saving"))} ${htmlEscape(formatDurationMinFromSeconds(action.network_total_duration_saving_s))}
          ${htmlEscape(t("and"))} ${htmlEscape(formatDistanceKmFromMeters(action.network_total_distance_saving_m))}.
        </li>`,
    )
    .join("");

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${htmlEscape(t("AI Audit Report"))} - ${htmlEscape(title)}</title>
  <style>
    body { font-family: Inter, Arial, sans-serif; color: #18212f; margin: 40px; line-height: 1.55; }
    .eyebrow { color: #0f766e; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    h1 { font-size: 28px; margin: 8px 0 4px; }
    h2 { border-top: 1px solid #d8dee8; margin-top: 28px; padding-top: 18px; font-size: 18px; }
    .meta, .policy { color: #697386; font-size: 12px; }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 22px 0; }
    .metric { border: 1px solid #d8dee8; border-radius: 8px; padding: 12px; }
    .metric span { color: #697386; display: block; font-size: 11px; font-weight: 700; text-transform: uppercase; }
    .metric strong { display: block; font-size: 22px; margin-top: 8px; }
    table { border-collapse: collapse; margin-top: 12px; width: 100%; }
    th, td { border-bottom: 1px solid #d8dee8; padding: 8px; text-align: left; vertical-align: top; }
    th { background: #f5f7fa; color: #697386; font-size: 11px; text-transform: uppercase; }
    li { margin: 6px 0; }
  </style>
</head>
<body>
  <div class="eyebrow">${htmlEscape(t("BRP AI Audit Report"))}</div>
  <h1>${htmlEscape(title)}</h1>
  <div class="meta">${htmlEscape(t("Job"))} ${htmlEscape(job.job_id)} | ${htmlEscape(t("Generated"))} ${htmlEscape(generatedAt)} | ${htmlEscape(t("Model"))} ${htmlEscape(stringValue(report.model) || t("N/A"))}</div>
  <div class="grid">
    <div class="metric"><span>${htmlEscape(t("Current Routes"))}</span><strong>${htmlEscape(formatNumber(currentPlan.route_count))}</strong></div>
    <div class="metric"><span>${htmlEscape(t("Average Load"))}</span><strong>${htmlEscape(formatPercent(currentPlan.avg_load_factor, 100))}</strong></div>
    <div class="metric"><span>${htmlEscape(t("Average Time"))}</span><strong>${htmlEscape(formatDurationMinFromSeconds(currentPlan.avg_route_duration_s))}</strong></div>
    <div class="metric"><span>${htmlEscape(t("Route Gap"))}</span><strong>${htmlEscape(formatSignedNumber(currentComparison.route_gap))}</strong></div>
  </div>
  ${markdownToHtml(stringValue(report.report_markdown))}
  <h2>${htmlEscape(t("Scenario Evidence"))}</h2>
  <table>
    <thead><tr><th>${htmlEscape(t("Scenario"))}</th><th>${htmlEscape(t("Routes"))}</th><th>${htmlEscape(t("Time Window"))}</th><th>${htmlEscape(t("Avg Time"))}</th><th>${htmlEscape(t("Avg Distance"))}</th><th>${htmlEscape(t("Bus Mix"))}</th></tr></thead>
    <tbody>${scenarioRows}</tbody>
  </table>
  <h2>${htmlEscape(t("Top Suggested Actions"))}</h2>
  ${actionRows ? `<ul>${actionRows}</ul>` : `<p>${htmlEscape(t("No high-priority action signals were generated."))}</p>`}
  <p class="policy">${htmlEscape(t("Input policy"))}: ${htmlEscape(stringValue(report.input_policy) || t("Aggregated facts only; full address list excluded."))}</p>
  <p class="policy">${htmlEscape(t("Action signals"))}: ${htmlEscape(formatNumber(reallocationSummary.actionable_weak_route_count))}</p>
</body>
</html>`;
}

function markdownToHtml(markdown: string) {
  const lines = sanitizeReportMarkdown(markdown).split(/\n/);
  let html = "";
  let inList = false;
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    const heading = parseMarkdownHeading(line);
    const bullet = parseMarkdownBullet(line);
    if (heading) {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      html += `<h2>${htmlEscape(heading)}</h2>`;
    } else if (bullet) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${htmlEscape(cleanMarkdownText(bullet))}</li>`;
    } else {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      html += `<p>${htmlEscape(cleanMarkdownText(line))}</p>`;
    }
  }
  if (inList) {
    html += "</ul>";
  }
  return html;
}

type ReportMarkdownNode =
  | { type: "heading"; text: string }
  | { type: "list"; items: string[] }
  | { type: "paragraph"; text: string };

function parseReportMarkdown(markdown: string): ReportMarkdownNode[] {
  const nodes: ReportMarkdownNode[] = [];
  let pendingItems: string[] = [];
  const flushList = () => {
    if (pendingItems.length) {
      nodes.push({ type: "list", items: pendingItems });
      pendingItems = [];
    }
  };

  const lines = sanitizeReportMarkdown(markdown).split(/\n/);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      continue;
    }

    const heading = parseMarkdownHeading(line) || parseReportTitle(line);
    if (heading) {
      flushList();
      nodes.push({ type: "heading", text: heading });
      continue;
    }

    const bullet = parseMarkdownBullet(line);
    if (bullet) {
      pendingItems.push(cleanMarkdownText(bullet));
      continue;
    }

    flushList();
    nodes.push({ type: "paragraph", text: cleanMarkdownText(line) });
  }

  flushList();
  return nodes;
}

function parseMarkdownHeading(line: string): string {
  const hashHeading = line.match(/^#{1,6}\s+(.+)$/);
  const numberedHeading = line.match(/^\d+\.\s+(.+)$/);
  return cleanMarkdownText(hashHeading?.[1] || numberedHeading?.[1] || "");
}

function parseMarkdownBullet(line: string): string {
  return line.match(/^[-*•]\s+(.+)$/)?.[1] || "";
}

function cleanMarkdownText(value: string) {
  return value.replace(/\*\*/g, "").replace(/__/g, "").replace(/`/g, "").replace(/^>\s*/, "").trim();
}

function parseReportTitle(line: string) {
  const cleaned = cleanMarkdownText(line);
  return /^(audit report:|.+operations audit report$)/i.test(cleaned) ? cleaned : "";
}

function sanitizeReportMarkdown(markdown: string) {
  return markdown
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !/^(---|\*\*\*|___|>)$/.test(line))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function htmlEscape(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

function stringValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function parseClockMinutes(value: unknown): number | null {
  const text = stringValue(value).trim();
  const match = text.match(/^(\d{1,2}):(\d{2})$/);
  if (!match) {
    return null;
  }
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes) || minutes > 59) {
    return null;
  }
  return (hours * 60 + minutes) % (24 * 60);
}

function formatClockMinutes(value: unknown): string {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return "";
  }
  const total = ((Math.round(numericValue) % (24 * 60)) + 24 * 60) % (24 * 60);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function formatTrafficMultiplier(value: unknown): string {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || numericValue <= 0) {
    return "";
  }
  return `${numericValue.toFixed(2).replace(/\.?0+$/, "")}x`;
}

function formatArrivalGateSummary(result: Record<string, unknown>): string {
  const timeConstrainedScenario = asRecord(result.time_constrained_optimization);
  const ep15Scenario = asRecord(result.ep15min_optimization);
  const scenarios: Array<[string, Record<string, unknown>]> = [
    [timeImpactScenarioName(result, timeConstrainedScenario, "time_constrained"), asRecord(timeConstrainedScenario.traffic_gate)],
    [timeImpactScenarioName(result, ep15Scenario, "ep15min"), asRecord(ep15Scenario.traffic_gate)],
  ];
  return scenarios
    .filter(([, gate]) => Object.keys(gate).length > 0 && stringValue(gate.status) !== "not_applicable")
    .map(([label, gate]) => {
      const status = stringValue(gate.status) || "unknown";
      const failed = Number(gate.failed_route_count || 0);
      const delay = Number(gate.max_estimated_arrival_delay_minutes || 0);
      const attempts = asRecordArray(gate.replan_attempts).length;
      const failureText = trafficGateFailureText(gate);
      const details = [
        failed > 0 ? `${formatNumber(failed)} ${failureText}` : "",
        delay > 0 ? `max ${formatNumber(Math.round(delay))} min` : "",
        attempts > 0 ? `replanned ${formatNumber(attempts)}` : "",
      ].filter(Boolean);
      return `${label}: ${status}${details.length ? ` (${details.join(", ")})` : ""}`;
    })
    .join(" | ");
}

function buildSolveProcessRows(result: Record<string, unknown>) {
  const timeConstrainedScenario = asRecord(result.time_constrained_optimization);
  const ep15Scenario = asRecord(result.ep15min_optimization);
  const scenarios: Array<[string, Record<string, unknown>]> = [
    [timeImpactScenarioName(result, timeConstrainedScenario, "time_constrained"), timeConstrainedScenario],
    ["Protected Route Plan", asRecord(result.exception_preserving_optimization)],
    [timeImpactScenarioName(result, ep15Scenario, "ep15min"), ep15Scenario],
  ];
  return scenarios.map(([label, scenario]) => buildSolveProcessRow(label, scenario));
}

function buildSolveProcessRow(label: string, scenario: Record<string, unknown>) {
  const gate = asRecord(scenario.traffic_gate);
  const status = stringValue(gate.status);
  const exceptionAttempts = asRecordArray(asRecord(scenario.exception_preserving).attempts);
  if (!status || status === "not_applicable") {
    if (exceptionAttempts.length) {
      const acceptedAttempt = exceptionAttempts.find((attempt) => Boolean(attempt.accepted));
      const steps = exceptionAttempts.map((attempt, index) => {
        const remainingLimit = Number(attempt.remaining_vehicle_limit || 0);
        const routeCount = Number(attempt.route_count || 0);
        const frozen = Number(attempt.frozen_route_count || 0);
        const accepted = Boolean(attempt.accepted);
        return accepted
          ? `EP ladder ${index + 1}: froze ${formatNumber(frozen)} current route(s), tested ${formatNumber(remainingLimit)} remainder route(s), accepted ${formatNumber(routeCount)} total route(s).`
          : `EP ladder ${index + 1}: froze ${formatNumber(frozen)} current route(s), tested ${formatNumber(remainingLimit)} remainder route(s), not accepted.`;
      });
      return {
        label,
        status: acceptedAttempt ? "Passed" : "Failed",
        passed: Boolean(acceptedAttempt),
        neutral: false,
        summary: `${formatNumber(exceptionAttempts.length)} EP ladder attempt(s); ${acceptedAttempt ? "accepted a candidate" : "no accepted candidate"}.`,
        steps,
      };
    }
    return {
      label,
      status: status === "not_applicable" ? "Not applicable" : "No record",
      passed: true,
      neutral: true,
      summary: status === "not_applicable" ? "This scenario did not require AM arrival-gate replanning." : "This job was created before solve-process tracking, or no optimization scenario was recorded.",
      steps: ["No replan rounds were recorded."],
    };
  }
  const attempts = asRecordArray(gate.replan_attempts);
  const vehicleAttempts = asRecordArray(gate.vehicle_search_attempts);
  const ladder = asRecord(gate.vehicle_ladder_search);
  const ladderAttempts = asRecordArray(ladder.attempts);
  const savingTarget = asRecord(gate.vehicle_saving_target);
  const savingStatus = stringValue(savingTarget.status);
  const checkName = trafficGateCheckName(gate);
  const failureText = trafficGateFailureText(gate);
  const finalFailed = Number(gate.failed_route_count || 0);
  const finalDelay = Number(gate.max_estimated_arrival_delay_minutes || 0);
  const steps = attempts.map((attempt, index) => {
    const failed = Number(attempt.failed_route_count || 0);
    const delay = Number(attempt.max_estimated_arrival_delay_minutes || 0);
    const fromTarget = Number(attempt.from_route_duration_minutes || 0);
    const toTarget = Number(attempt.to_route_duration_minutes || 0);
    const routeIds = asStringArray(attempt.failed_route_ids);
    const shownRoutes = routeIds.slice(0, 6).join(", ");
    const routeText = shownRoutes ? `; affected routes: ${shownRoutes}${routeIds.length > 6 ? "..." : ""}` : "";
    return `Round ${index + 1}: ${formatNumber(failed)} ${failureText}, max ${formatNumber(Math.round(delay))} min over; target ${formatNumber(Math.round(fromTarget))} -> ${formatNumber(Math.round(toTarget))} min${routeText}.`;
  });
  vehicleAttempts.forEach((attempt, index) => {
    const targetBusCount = Number(attempt.target_bus_count || 0);
    const busCount = Number(attempt.bus_count || 0);
    const failed = Number(attempt.failed_route_count || 0);
    const delay = Number(attempt.max_estimated_arrival_delay_minutes || 0);
    const attemptStatus = stringValue(attempt.status);
    const passed = attemptStatus === "passed";
    steps.push(
      passed
        ? `Vehicle search ${index + 1}: ${formatNumber(targetBusCount || busCount)} route(s) passed the final ${checkName}.`
        : `Vehicle search ${index + 1}: ${formatNumber(targetBusCount)} route(s) failed; ${formatNumber(failed)} ${failureText}, max ${formatNumber(Math.round(delay))} min over.`
    );
  });
  ladderAttempts.forEach((attempt, index) => {
    const target = Number(attempt.target_vehicle_count || 0);
    const routeCount = Number(attempt.route_count || 0);
    const saved = Number(attempt.saved_route_count || 0);
    const attemptStatus = stringValue(attempt.status);
    const failed = Number(attempt.failed_route_count || 0);
    const delay = Number(attempt.max_overrun_minutes || 0);
    steps.push(
      attemptStatus === "passed"
        ? `Vehicle ladder ${index + 1}: tested ${formatNumber(target || routeCount)} route(s), passed, saved ${formatNumber(saved)}.`
        : `Vehicle ladder ${index + 1}: tested ${formatNumber(target)} route(s), failed; ${formatNumber(failed)} ${failureText}, max ${formatNumber(Math.round(delay))} min over.`
    );
  });
  exceptionAttempts.forEach((attempt, index) => {
    const remainingLimit = Number(attempt.remaining_vehicle_limit || 0);
    const routeCount = Number(attempt.route_count || 0);
    const frozen = Number(attempt.frozen_route_count || 0);
    const accepted = Boolean(attempt.accepted);
    steps.push(
      accepted
        ? `EP ladder ${index + 1}: froze ${formatNumber(frozen)} current route(s), tested ${formatNumber(remainingLimit)} remainder route(s), accepted ${formatNumber(routeCount)} total route(s).`
        : `EP ladder ${index + 1}: froze ${formatNumber(frozen)} current route(s), tested ${formatNumber(remainingLimit)} remainder route(s), not accepted.`
    );
  });
  steps.push(
    status === "passed" && savingStatus !== "failed"
      ? `Final check passed: all checked routes passed the final ${checkName}.`
      : status === "passed" && savingStatus === "failed"
        ? `Final check failed: saved ${formatNumber(Number(savingTarget.saved_route_count || 0))} route(s), below the required ${formatNumber(Number(savingTarget.minimum_vehicle_reduction || 0))}.`
      : `Final check failed: ${formatNumber(finalFailed)} ${failureText}, max ${formatNumber(Math.round(finalDelay))} min over.`
  );
  const effectivePassed = status === "passed" && savingStatus !== "failed";
  return {
    label,
    status: effectivePassed ? "Passed" : savingStatus === "failed" ? "Failed" : toTitle(status.replace(/_/g, " ")),
    passed: effectivePassed,
    neutral: false,
    summary:
      effectivePassed
        ? `${formatNumber(Math.max(1, attempts.length + 1))} solve round(s), ${formatNumber(ladderAttempts.length)} ladder attempt(s); final ${checkName} passed.`
        : savingStatus === "failed"
          ? `${formatNumber(ladderAttempts.length + exceptionAttempts.length)} ladder attempt(s); saved ${formatNumber(Number(savingTarget.saved_route_count || 0))}, required ${formatNumber(Number(savingTarget.minimum_vehicle_reduction || 0))}.`
        : `${formatNumber(Math.max(1, attempts.length + 1))} solve round(s), ${formatNumber(vehicleAttempts.length + exceptionAttempts.length)} vehicle-search attempt(s); ${formatNumber(finalFailed)} ${failureText}, max ${formatNumber(Math.round(finalDelay))} min over.`,
    steps,
  };
}

function formatSignedNumber(value: unknown): string {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return "0";
  }
  return `${numericValue > 0 ? "+" : ""}${formatNumber(numericValue)}`;
}

function formatBusMix(value: Record<string, unknown>) {
  const items = Object.entries(value)
    .filter(([, count]) => Number(count) > 0)
    .map(([name, count]) => `${name}: ${formatNumber(count)}`);
  return items.length ? items.join(" | ") : "Not available";
}

function routeStopCount(route: Record<string, unknown>) {
  return routeServiceStopCount(route);
}

function routeServiceStopCount(route: Record<string, unknown>) {
  const explicitServiceStopCount = Number(route.service_stop_count ?? route.student_stop_count);
  if (Number.isFinite(explicitServiceStopCount)) {
    return explicitServiceStopCount;
  }
  const explicitStopCount = Number(route.stop_count ?? route.stops);
  if (Number.isFinite(explicitStopCount)) {
    const scheduledStopCount = Number(route.scheduled_stop_count ?? route.all_stop_count);
    if (Number.isFinite(scheduledStopCount)) {
      return explicitStopCount;
    }
    const nodes = Array.isArray(route.nodes) ? route.nodes : [];
    if (nodes.length) {
      return Math.max(0, nodes.length - 1);
    }
    return Math.max(0, explicitStopCount - 1);
  }
  const nodeCount = Array.isArray(route.nodes) ? route.nodes.length : 0;
  return Math.max(0, nodeCount - 1);
}

function assessmentServiceStopCount(assessment: Record<string, unknown>) {
  const explicitServiceStopCount = Number(assessment.service_stop_count ?? assessment.student_stop_count);
  if (Number.isFinite(explicitServiceStopCount)) {
    return explicitServiceStopCount;
  }
  const routeSummaries = asRecordArray(assessment.route_summaries);
  if (routeSummaries.length) {
    return routeSummaries.reduce((total, route) => total + Number(routeServiceStopCount(route) || 0), 0);
  }
  const explicitStopCount = Number(assessment.stop_count);
  if (Number.isFinite(explicitStopCount)) {
    const routeCount = Number(assessment.route_count);
    return Math.max(0, explicitStopCount - (Number.isFinite(routeCount) ? routeCount : 0));
  }
  return explicitStopCount;
}

function scenarioServiceStopCount(scenario: Record<string, unknown>) {
  const explicitServiceStopCount = Number(scenario.service_stop_count ?? scenario.student_stop_count);
  if (Number.isFinite(explicitServiceStopCount)) {
    return explicitServiceStopCount;
  }
  const points = asRecordArray(scenario.points);
  if (points.length) {
    return points.filter((point) => !Boolean(point.is_depot)).length;
  }
  const explicitStopCount = Number(scenario.stop_count);
  if (Number.isFinite(explicitStopCount)) {
    return explicitStopCount;
  }
  return explicitStopCount;
}

function routePassengerCount(route: Record<string, unknown>) {
  return route.passenger_count ?? route.passengers ?? route.load;
}

function routeLoadFactor(route: Record<string, unknown>) {
  const explicitLoad = Number(route.load_factor);
  if (Number.isFinite(explicitLoad)) {
    return explicitLoad;
  }
  const passengers = Number(routePassengerCount(route));
  const capacity = Number(route.bus_capacity ?? route.capacity);
  if (!Number.isFinite(passengers) || !Number.isFinite(capacity) || capacity <= 0) {
    return null;
  }
  return passengers / capacity;
}

function formatDurationSeconds(value: unknown) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return "Not available";
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(numericValue)}s`;
}
