import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowRight,
  BarChart3,
  Bot,
  Download,
  FileWarning,
  GitCompareArrows,
  ListChecks,
  Loader2,
  Map,
  Maximize2,
  RefreshCw,
  Route,
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

type ResultTab = "ai" | "audit" | "impact" | "baselines" | "maps" | "actions" | "diagnostics";

const resultTabs: Array<{ key: ResultTab; label: string }> = [
  { key: "ai", label: "AI Audit" },
  { key: "audit", label: "Audit Detail" },
  { key: "impact", label: "Time Impact" },
  { key: "actions", label: "Actions" },
  { key: "baselines", label: "Baselines" },
  { key: "maps", label: "Maps" },
  { key: "diagnostics", label: "Diagnostics" },
];

export function JobResultView({ job }: { job: JobRecord }) {
  const [activeTab, setActiveTab] = useState<ResultTab>("ai");
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
          <h2 className="text-sm font-semibold">Run error</h2>
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
        title="No result payload yet"
        detail="Queued and running jobs update automatically. Completed jobs will expose audit results here."
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
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "audit" ? (
        <AuditPanel
          currentPlan={currentPlan}
          currentComparison={currentComparison}
          routeSummaries={routeSummaries}
          result={result}
        />
      ) : null}
      {activeTab === "ai" ? (
        <AiAuditPanel
          job={job}
          currentPlan={currentPlan}
          currentComparison={currentComparison}
          reallocationSummary={reallocationSummary}
          scenarios={scenarios}
        />
      ) : null}
      {activeTab === "baselines" ? (
        <BaselinePanel
          jobId={job.job_id}
          scenarios={scenarios}
          currentComparison={currentComparison}
        />
      ) : null}
      {activeTab === "impact" ? <TimeImpactPanel jobId={job.job_id} mapOutputs={mapOutputs} /> : null}
      {activeTab === "maps" ? <MapsPanel jobId={job.job_id} jobName={jobDisplayName} mapOutputs={mapOutputs} result={result} diagnostics={diagnostics} /> : null}
      {activeTab === "actions" ? (
        <ActionPanel
          priorityActions={priorityActions}
          reallocationSummary={reallocationSummary}
          reallocation={reallocation}
        />
      ) : null}
      {activeTab === "diagnostics" ? <DiagnosticsPanel diagnostics={diagnostics} mapOutputs={mapOutputs} result={result} /> : null}
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
  const recommendations = asStringArray(currentComparison.recommendations);
  const plannerConfig = asRecord(result.planner_config);

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
            <h2 className="text-sm font-semibold">Audit detail readout</h2>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 text-sm md:grid-cols-3">
            <ReadoutItem label="Service direction" value={stringValue(result.service_direction || plannerConfig.service_direction)} />
            <ReadoutItem label="Traffic profile" value={stringValue(result.traffic_profile_name || plannerConfig.traffic_profile_name)} />
            <ReadoutItem label="Current bus mix" value={formatBusMix(asRecord(currentPlan.bus_mix))} />
          </div>
          {recommendations.length ? (
            <ul className="space-y-2 text-sm text-muted-foreground">
              {recommendations.map((item) => (
                <li key={item} className="flex gap-2">
                  <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-accent" aria-hidden="true" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-sm text-muted-foreground">No comparison recommendations were generated.</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Route className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Route diagnostics</h2>
          </div>
        </CardHeader>
        <CardContent>
          <RouteDiagnosticsTable routes={routeSummaries} />
        </CardContent>
      </Card>
    </div>
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
  const queryClient = useQueryClient();
  const auditMutation = useMutation({
    mutationFn: ({ force }: { force: boolean }) => generateAiAudit(job.job_id, { force, language: "English" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["jobs", job.job_id] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const report = asRecord(auditMutation.data?.ai_audit_report || job.ai_audit_report);
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
      })
    : "";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">AI audit briefing board</h2>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                disabled={aiRunning || Boolean(reportMarkdown)}
                icon={generateReportIcon}
                onClick={generateReport}
              >
                Generate report
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={aiRunning}
                icon={auditMutation.isPending && auditMutation.variables?.force ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                onClick={() => auditMutation.mutate({ force: true })}
              >
                Regenerate
              </Button>
              {reportMarkdown ? (
                <a
                  className={buttonClassName("secondary")}
                  href={`data:text/html;charset=utf-8,${encodeURIComponent(downloadHtml)}`}
                  download={`ai_audit_report_${job.job_id}.html`}
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  Download HTML
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
            AI uses only deterministic route metrics, baseline comparisons, and recommendation summaries. Full address
            lists are excluded from the prompt.
          </div>

          {aiRunning ? (
            <div className="rounded-md border border-cyan-200 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
              AI audit generation is running. The backend may take up to about a minute depending on the model response.
            </div>
          ) : null}
          {auditMutation.error ? <InlineError message={(auditMutation.error as Error).message} /> : null}
          {job.ai_audit_error && !auditMutation.error ? <InlineError message={job.ai_audit_error} /> : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Routes to review first</h2>
          </CardHeader>
          <CardContent>
            {routeSummaries.length ? (
              <RouteRiskTable routes={routeSummaries} />
            ) : (
              <div className="text-sm text-muted-foreground">No route diagnostics were available for AI briefing.</div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Top suggested actions</h2>
          </CardHeader>
          <CardContent>
            {priorityActions.length ? (
              <div className="space-y-3">
                {priorityActions.map((action, index) => (
                  <div key={`${stringValue(action.from_route_id)}-${stringValue(action.to_route_id)}-${index}`} className="rounded-md border border-border px-3 py-2 text-sm">
                    <div className="font-medium">
                      Move {formatNumber(action.stop_count)} stop(s) from {stringValue(action.from_route_id) || "N/A"} to{" "}
                      {stringValue(action.to_route_id) || "N/A"}
                    </div>
                    <div className="mt-1 text-muted-foreground">
                      Save about {formatDurationMinFromSeconds(action.network_total_duration_saving_s)} and{" "}
                      {formatDistanceKmFromMeters(action.network_total_distance_saving_m)}.
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">No high-priority action signals were generated.</div>
            )}
          </CardContent>
        </Card>
      </div>

      {scenarioRows.length ? (
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Scenario evidence</h2>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] border-collapse text-sm">
                <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">Scenario</th>
                    <th className="px-3 py-2">Routes</th>
                    <th className="px-3 py-2">Service stops</th>
                    <th className="px-3 py-2">Avg time</th>
                    <th className="px-3 py-2">Avg distance</th>
                    <th className="px-3 py-2">Bus mix</th>
                  </tr>
                </thead>
                <tbody>
                  {scenarioRows.map((scenario) => (
                    <tr key={scenario.name} className="border-t border-border">
                      <td className="px-3 py-2 font-medium">{scenario.name}</td>
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
            <h2 className="text-sm font-semibold">AI audit report</h2>
            {reportMarkdown ? (
              <Badge tone="success">{stringValue(report.model) || "generated"}</Badge>
            ) : (
              <Badge tone="neutral">not generated</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {reportMarkdown ? (
            <div className="space-y-4">
              <MarkdownReport markdown={reportMarkdown} />
              <div className="text-xs text-muted-foreground">
                Generated {stringValue(report.generated_at) || "unknown"} | Input policy:{" "}
                {stringValue(report.input_policy) || "aggregated facts only"}
              </div>
            </div>
          ) : (
            <EmptyState
              title="No AI report yet"
              detail="History runs do not create this automatically. Click Generate report to create a bounded management-facing narrative from the deterministic audit outputs."
              action={
                <Button
                  type="button"
                  disabled={aiRunning || Boolean(reportMarkdown)}
                  icon={generateReportIcon}
                  onClick={generateReport}
                >
                  Generate report
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
  jobId,
  scenarios,
  currentComparison,
}: {
  jobId: string;
  scenarios: ScenarioRow[];
  currentComparison: Record<string, unknown>;
}) {
  const freeOptimization = scenarios.find((scenario) => scenario.name === "Free Optimization" && scenario.enabled);

  return (
    <div className="space-y-4">
      <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
        <div>
          <h2 className="text-sm font-semibold">Baseline scenarios</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Compare the imported supplier plan against constrained, like-for-like, and upper-bound optimization evidence.
          </p>
        </div>
        {freeOptimization ? (
          <a className={buttonClassName("secondary", "whitespace-nowrap")} href={getJobExportUrl(jobId, "free-optimization-template")}>
            <Download className="h-4 w-4" aria-hidden="true" />
            Download workbook
          </a>
        ) : null}
      </div>

      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
        {scenarios.map((scenario) => (
          <Card key={scenario.name}>
            <CardContent className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold">{scenario.name}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{scenario.detail}</div>
                </div>
                <Badge tone={scenario.enabled ? "success" : "neutral"}>{scenario.enabled ? "ready" : "skipped"}</Badge>
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
                <div className="text-sm text-muted-foreground">{scenario.skippedReason || "Scenario was not enabled for this run."}</div>
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
                  <h2 className="text-sm font-semibold">{scenario.name} route table</h2>
                  <Badge tone="info">{formatNumber(scenario.routes.length)} routes</Badge>
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
            <h2 className="text-sm font-semibold">Current plan vs free optimization</h2>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-4">
          <MetricCard label="Current route count" value={formatNumber(currentComparison.current_route_count)} />
          <MetricCard label="Baseline route count" value={formatNumber(currentComparison.baseline_route_count)} />
          <MetricCard label="Distance gap" value={formatPercent(currentComparison.avg_distance_gap_pct)} tone="info" />
          <MetricCard label="Duration gap" value={formatPercent(currentComparison.avg_duration_gap_pct)} tone="info" />
        </CardContent>
      </Card>
    </div>
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
            <h2 className="text-sm font-semibold">Priority route-to-route actions</h2>
          </div>
        </CardHeader>
        <CardContent>
          {priorityActions.length ? (
            <div className="space-y-3">
              {priorityActions.map((action, index) => (
                <div key={`${stringValue(action.from_route_id)}-${stringValue(action.to_route_id)}-${index}`} className="rounded-md border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="info">{stringValue(action.route_action_label) || "Local improvement"}</Badge>
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
            <EmptyState title="No priority actions" detail="No route-to-route adjustment met the current filters." />
          )}
        </CardContent>
      </Card>

      {routeProfiles.length ? (
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Route-level action signals</h2>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-sm">
                <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2">Route</th>
                    <th className="px-3 py-2">Signal</th>
                    <th className="px-3 py-2">Best target</th>
                    <th className="px-3 py-2">Move support</th>
                    <th className="px-3 py-2">Best saving</th>
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
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard label="Client prep" value={formatDurationSeconds(diagnostics.clientPrepElapsedSeconds)} />
        <MetricCard label="Backend compute" value={formatDurationSeconds(result.elapsed_seconds)} />
        <MetricCard label="Geocode warnings" value={formatNumber(diagnostics.geocodeWarnings.length)} tone="warning" />
        <MetricCard label="Excluded stops" value={formatNumber(diagnostics.excludedStops.length)} tone="warning" />
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FileWarning className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Coordinate warnings</h2>
          </div>
        </CardHeader>
        <CardContent>
          {diagnostics.geocodeWarnings.length ? (
            <SimpleObjectTable rows={diagnostics.geocodeWarnings} />
          ) : (
            <div className="text-sm text-muted-foreground">No geocode warnings were recorded.</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Map className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Map outputs</h2>
          </div>
        </CardHeader>
        <CardContent>
          {mapOutputs.length ? (
            <div className="space-y-2">
              {mapOutputs.map((item) => (
                <div key={item.key} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm">
                  <span className="font-medium">{item.name}</span>
                  <Badge tone="success">available</Badge>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">No map outputs were included in this payload.</div>
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
    scenarioOptions.find((item) => item.key === "original") ||
    scenarioOptions[0];
  const impactQuery = useQuery({
    queryKey: ["job-map-data", jobId, "time-impact", selected?.key],
    queryFn: () => getJobMapData(jobId, selected?.key || ""),
    enabled: Boolean(selected),
  });

  if (!scenarioOptions.length || !selected) {
    return (
      <EmptyState
        title="No optimized scenarios available"
        detail="Time impact review needs an optimized scenario to compare against the current plan."
      />
    );
  }

  const data = impactQuery.data;
  const summary = data?.summary.time_impact;
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
              <h2 className="text-sm font-semibold">Time impact review</h2>
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
                  {option.name}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {impactQuery.isLoading ? (
            <div className="flex h-32 items-center justify-center rounded-md border border-border bg-muted text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
              Loading time impact model
            </div>
          ) : null}
          {impactQuery.isError ? (
            <InlineError message="Time impact data is not available for this scenario yet." />
          ) : null}
          {data && !summary?.available ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <AlertCircle className="mr-2 inline h-4 w-4 align-text-bottom" aria-hidden="true" />
              No comparable stop timing was found for this scenario.
            </div>
          ) : null}
        </CardContent>
      </Card>

      {data && summary?.available ? (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Acceptance rate"
              value={formatPercent(summary.acceptance_rider_ratio, 100)}
              tone={Number(summary.over_acceptance_rider_count || 0) ? "warning" : "success"}
            />
            <MetricCard
              label={`Over ${acceptanceThresholdLabel} riders`}
              value={formatNumber(summary.over_acceptance_rider_count)}
              tone={Number(summary.over_acceptance_rider_count || 0) ? "warning" : "success"}
            />
            <MetricCard
              label={`Over ${acceptanceThresholdLabel} stops`}
              value={formatNumber(summary.over_acceptance_stop_count)}
              tone={Number(summary.over_acceptance_stop_count || 0) ? "warning" : "success"}
            />
            <MetricCard
              label="Max over threshold"
              value={formatImpactMinutes(summary.max_over_acceptance_delta_minutes)}
              tone={Number(summary.max_over_acceptance_delta_minutes || 0) ? "warning" : "success"}
            />
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Worse riders" value={formatNumber(summary.worse_rider_count)} tone="warning" />
            <MetricCard label="High-risk stops" value={formatNumber(summary.high_risk_stop_count)} tone="warning" />
            <MetricCard label="Weighted adverse" value={formatImpactMinutes(summary.weighted_avg_adverse_delta_minutes)} tone="info" />
            <MetricCard label="Route changed" value={formatNumber(summary.route_changed_rider_count)} tone="neutral" />
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Compared stops" value={formatNumber(summary.compared_stop_count)} />
            <MetricCard label="Compared riders" value={formatNumber(summary.compared_rider_count)} />
            <MetricCard label="P90 adverse" value={formatImpactMinutes(summary.p90_adverse_delta_minutes)} />
            <MetricCard label="Max adverse" value={formatImpactMinutes(summary.max_adverse_delta_minutes)} tone="warning" />
          </div>

          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex items-center gap-2">
                  <Route className="h-4 w-4 text-primary" aria-hidden="true" />
                  <h2 className="text-sm font-semibold">Route impact</h2>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selectedRouteId ? (
                    <button
                      type="button"
                      className={cn(buttonClassName("secondary"), "h-8")}
                      onClick={() => setSelectedRouteId("")}
                    >
                      All routes
                    </button>
                  ) : null}
                  <a
                    href={getJobExportUrl(jobId, `time-impact-${selected.key}`)}
                    className={cn(buttonClassName("secondary"), "h-8")}
                  >
                    <Download className="h-4 w-4" aria-hidden="true" />
                    Export Excel
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
                  <h2 className="text-sm font-semibold">Stop impact</h2>
                  <Badge tone="neutral">{formatNumber(stopRows.length)} shown</Badge>
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
                      {timeImpactFilterLabel(key)}
                    </button>
                  ))}
                  <input
                    className="h-8 min-w-[220px] rounded-md border border-border bg-surface px-3 text-sm outline-none transition placeholder:text-muted-foreground focus:border-primary"
                    placeholder="Search stop or route"
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
                  {formatNumber(unavailableStops.length)} stop(s) could not be matched to the current plan timing model.
                </div>
              ) : null}
            </CardContent>
          </Card>
        </>
      ) : null}

      {data && !comparedStops.length && unavailableStops.length ? (
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Unmatched stops</h2>
          </CardHeader>
          <CardContent>
            <TimeImpactStopTable stops={unavailableStops} serviceDirection={data.service_direction || ""} />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
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
  if (!routes.length) {
    return <div className="text-sm text-muted-foreground">No route-level impact rows were generated.</div>;
  }

  return (
    <div className="max-h-[360px] overflow-auto">
      <table className="w-full min-w-[940px] border-collapse text-sm">
        <thead className="sticky top-0 bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Route</th>
            <th className="px-3 py-2">Bus</th>
            <th className="px-3 py-2">Riders</th>
            <th className="px-3 py-2">Over threshold</th>
            <th className="px-3 py-2">Worse riders</th>
            <th className="px-3 py-2">High risk</th>
            <th className="px-3 py-2">Weighted adverse</th>
            <th className="px-3 py-2">Max adverse</th>
            <th className="px-3 py-2">Changed riders</th>
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
                <td className="px-3 py-2">{route.bus_type_name || "N/A"}</td>
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
  if (!stops.length) {
    return <div className="text-sm text-muted-foreground">No stops match the current filter.</div>;
  }

  return (
    <div className="max-h-[520px] overflow-auto">
      <table className="w-full min-w-[980px] border-collapse text-sm">
        <thead className="sticky top-0 bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Impact</th>
            <th className="px-3 py-2">Stop</th>
            <th className="px-3 py-2">Riders</th>
            <th className="px-3 py-2">Current</th>
            <th className="px-3 py-2">Optimized</th>
            <th className="px-3 py-2">Delta</th>
            <th className="px-3 py-2">Route</th>
            <th className="px-3 py-2">Match</th>
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
                        ? `Over ${formatImpactMinutes(impact.acceptance_threshold_minutes ?? 15)}`
                        : timeImpactLabel(impact.level, impact.impact_direction)}
                    </Badge>
                  ) : (
                    <Badge tone="warning">Unmatched</Badge>
                  )}
                </td>
                <td className="max-w-[320px] px-3 py-2">
                  <div className="truncate font-medium">{stop.address || stop.requested_address || "Unknown address"}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {stop.route_id} · Stop {formatNumber(stop.order)}
                  </div>
                </td>
                <td className="px-3 py-2">{formatNumber(impact.affected_rider_count ?? stop.passenger_count)}</td>
                <td className="px-3 py-2">
                  <div className="font-medium">{impact.current_time_label || "N/A"}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{impact.current_route_id || "Current plan"}</div>
                </td>
                <td className="px-3 py-2">
                  <div className="font-medium">{impact.new_time_label || stop.scheduled_time_label || "N/A"}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{impact.new_route_id || stop.route_id}</div>
                </td>
                <td className="px-3 py-2">
                  {comparisonAvailable ? (
                    <>
                      <div className={cn("font-semibold", timeImpactDeltaClassName(impact.impact_direction))}>
                        {formatImpactMinutes(impact.delta_minutes, { signed: true })}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {timeImpactAdversePhrase(serviceDirection, Number(impact.adverse_delta_minutes || 0))}
                      </div>
                    </>
                  ) : (
                    <span className="text-muted-foreground">N/A</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {impact.route_changed ? (
                    <Badge tone="info">Changed</Badge>
                  ) : (
                    <Badge tone="neutral">Same</Badge>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-muted-foreground">
                  {impact.comparison_status || "matched"}
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

function timeImpactAdversePhrase(serviceDirection: string, adverseMinutes: number) {
  if (!Number.isFinite(adverseMinutes) || adverseMinutes <= 0.5) {
    return "No adverse shift";
  }
  const label = serviceDirection === "To School" ? "earlier pickup" : "later dropoff";
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
}: {
  jobId: string;
  jobName: string;
  mapOutputs: MapOutput[];
  result: Record<string, unknown>;
  diagnostics: Diagnostics;
}) {
  const [selectedKey, setSelectedKey] = useState("");
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

  if (!mapOutputs.length || !selected) {
    return <EmptyState title="No maps available" detail="This job did not include rendered route map artifacts." />;
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
          Loading interactive map
        </div>
      );
    }
    if (interactiveQuery.isError) {
      return (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          Interactive map data is not available for this scenario yet. Download the map artifact if you need the original generated HTML.
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
            <h2 className="text-sm font-semibold">Route maps</h2>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {excludedStopCount || geocodeWarningCount ? (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <FileWarning className="h-4 w-4 flex-none" aria-hidden="true" />
            <span className="font-medium">Input geocode review:</span>
            {excludedStopCount ? <span>{formatNumber(excludedStopCount)} excluded stop(s)</span> : null}
            {excludedStopCount && geocodeWarningCount ? <span aria-hidden="true">·</span> : null}
            {geocodeWarningCount ? <span>{formatNumber(geocodeWarningCount)} warning(s)</span> : null}
          </div>
        ) : null}
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {scenarioSummaries.map((summary) => (
            <button
              key={summary.key}
              type="button"
              className={cn(
                "rounded-md border p-3 text-left transition",
                selected.key === summary.key
                  ? "border-primary bg-primary/10"
                  : "border-border bg-surface hover:bg-muted",
              )}
              onClick={() => setSelectedKey(summary.key)}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="truncate text-sm font-semibold">{summary.name}</div>
                <Badge tone={selected.key === summary.key ? "info" : "neutral"}>{formatNumber(summary.routeCount)} routes</Badge>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <div>Stops: {formatNumber(summary.stopCount)}</div>
                <div>Riders: {formatNumber(summary.passengerCount)}</div>
                <div>Total: {formatDistanceKmFromMeters(summary.totalDistanceM)}</div>
                <div>Longest: {formatDurationMinFromSeconds(summary.longestDurationS)}</div>
                {excludedStopCount ? <div className="col-span-2 text-amber-700">Excluded: {formatNumber(excludedStopCount)} stop(s)</div> : null}
              </div>
            </button>
          ))}
        </div>
        <div className="relative">
          {renderMapSurface()}
          <div className="absolute right-3 top-3 z-20 flex flex-wrap justify-end gap-2">
            <button type="button" className={cn(buttonClassName("secondary"), "border-slate-300 bg-white shadow-lg hover:bg-slate-50")} onClick={() => setIsMapFullscreenOpen(true)}>
              <Maximize2 className="h-4 w-4" aria-hidden="true" />
              Open
            </button>
            <button
              type="button"
              className={cn(buttonClassName("secondary"), "border-slate-300 bg-white shadow-lg hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60")}
              disabled={!interactiveQuery.data}
              onClick={downloadInteractiveMap}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              Download
            </button>
          </div>
        </div>
        {isMapFullscreenOpen ? (
          <div
            className="fixed inset-0 z-50 bg-slate-950/42 p-2 backdrop-blur-sm sm:p-4 lg:p-6"
            role="dialog"
            aria-modal="true"
            aria-label="Fullscreen route map"
            onClick={() => setIsMapFullscreenOpen(false)}
          >
            <div
              className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-white/55 bg-surface/92 shadow-2xl ring-1 ring-slate-950/10 backdrop-blur-xl"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex min-h-14 flex-col gap-3 border-b border-white/45 bg-surface/82 px-4 py-3 shadow-sm backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{selected.name}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    Interactive route map · {formatNumber(dataRouteCountForSummary(scenarioSummaries, selected.key))} routes
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
                  <button
                    type="button"
                    className={cn(buttonClassName("secondary"), "bg-white/70 backdrop-blur hover:bg-white disabled:cursor-not-allowed disabled:opacity-60")}
                    disabled={!interactiveQuery.data}
                    onClick={downloadInteractiveMap}
                  >
                    <Download className="h-4 w-4" aria-hidden="true" />
                    Download
                  </button>
                  <button
                    type="button"
                    className={cn(buttonClassName("secondary"), "border-red-300 bg-red-50/90 text-red-700 backdrop-blur hover:border-red-400 hover:bg-red-100 hover:text-red-800")}
                    onClick={() => setIsMapFullscreenOpen(false)}
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                    Close
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
  if (!routes.length) {
    return <div className="text-sm text-muted-foreground">No route-level diagnostics were included.</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[780px] border-collapse text-sm">
        <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Route</th>
            <th className="px-3 py-2">Bus</th>
            <th className="px-3 py-2">Service stops</th>
            <th className="px-3 py-2">Passengers</th>
            <th className="px-3 py-2">Load</th>
            <th className="px-3 py-2">Distance</th>
            <th className="px-3 py-2">Duration</th>
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
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead className="sticky top-0 bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Route</th>
            <th className="px-3 py-2">Bus</th>
            <th className="px-3 py-2">Service stops</th>
            <th className="px-3 py-2">Passengers</th>
            <th className="px-3 py-2">Capacity</th>
            <th className="px-3 py-2">Load</th>
            <th className="px-3 py-2">Distance</th>
            <th className="px-3 py-2">Duration</th>
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
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 6);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] border-collapse text-sm">
        <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-3 py-2">{toTitle(column)}</th>
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
            <th className="px-3 py-2">Route</th>
            <th className="px-3 py-2">Duration</th>
            <th className="px-3 py-2">Load</th>
            <th className="px-3 py-2">Passengers</th>
            <th className="px-3 py-2">What to notice</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ route, reasons }) => (
            <tr key={stringValue(route.route_id)} className="border-t border-border">
              <td className="px-3 py-2 font-medium">{stringValue(route.route_id)}</td>
              <td className="px-3 py-2">{formatDurationMinFromSeconds(route.duration_s)}</td>
              <td className="px-3 py-2">{formatPercent(route.load_factor, 100)}</td>
              <td className="px-3 py-2">{formatNumber(route.passenger_count)}</td>
              <td className="px-3 py-2">{reasons.join(", ")}</td>
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

function MetricCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "warning" | "info";
}) {
  return (
    <div className={cn("min-w-0 rounded-lg border bg-surface p-4 shadow-panel", metricToneClassName(tone))}>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 text-xs font-medium uppercase text-muted-foreground">{label}</div>
      </div>
      <div className={cn("mt-3 text-2xl font-semibold", metricValueClassName(tone))}>{value}</div>
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

function ReadoutItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 break-words font-medium">{value || "Not available"}</div>
    </div>
  );
}

type ScenarioRow = {
  name: string;
  detail: string;
  enabled: boolean;
  skippedReason: string;
  routeCount: unknown;
  stopCount: unknown;
  avgDistanceM: unknown;
  avgDurationS: unknown;
  busMix: Record<string, unknown>;
  routes: Array<Record<string, unknown>>;
};

function buildScenarioRows(result: Record<string, unknown>): ScenarioRow[] {
  const structured = asRecord(result.structured_results);
  return [
    scenarioFromAssessment("Current Plan", "Imported supplier route order", asRecord(result.current_plan_assessment)),
    scenarioFromAssessment("Like-for-Like", "Same route allocation, improved order", asRecord(result.like_for_like_baseline)),
    scenarioFromAssessment("Constrained", "High-confidence transfer packages", asRecord(result.constrained_improvement_baseline)),
    scenarioFromScenario("Free Optimization", "Upper-bound regrouping benchmark", asRecord(result.free_optimization_baseline || structured.free_optimization_baseline || structured.original)),
    scenarioFromScenario("15-Minute Constrained", "Optimized with a 15-minute time-impact limit", asRecord(result.time_constrained_optimization || structured.time_constrained)),
  ];
}

function scenarioFromAssessment(name: string, detail: string, assessment: Record<string, unknown>): ScenarioRow {
  return {
    name,
    detail,
    enabled: Object.keys(assessment).length > 0,
    skippedReason: "",
    routeCount: assessment.route_count,
    stopCount: assessmentServiceStopCount(assessment),
    avgDistanceM: assessment.avg_route_distance_m,
    avgDurationS: assessment.avg_route_duration_s,
    busMix: asRecord(assessment.bus_mix),
    routes: asRecordArray(assessment.route_summaries),
  };
}

function scenarioFromScenario(name: string, detail: string, scenario: Record<string, unknown>): ScenarioRow {
  return {
    name,
    detail,
    enabled: Object.keys(scenario).length > 0 && scenario.enabled !== false,
    skippedReason: stringValue(scenario.skipped_reason),
    routeCount: scenario.route_count || scenario.bus_count,
    stopCount: scenarioServiceStopCount(scenario),
    avgDistanceM: scenario.avg_route_distance_m,
    avgDurationS: scenario.avg_route_duration_s,
    busMix: asRecord(scenario.bus_mix),
    routes: asRecordArray(scenario.routes),
  };
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

function downloadInteractiveMapHtml(data: JobMapData, jobName: string, mapName: string) {
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
  const tileUrl = `${window.location.origin}/api/map-tiles/{z}/{x}/{y}.png`;
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
          <p>${htmlEscape(data.scenario_name)} · ${htmlEscape(formatNumber(data.summary.route_count))} routes · ${htmlEscape(formatNumber(data.summary.stop_count))} stops · ${htmlEscape(formatNumber(data.summary.passenger_count))} riders</p>
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
              <div><h2>${htmlEscape(data.scenario_name)}</h2><div class="summary">${htmlEscape(formatNumber(data.summary.route_count))} routes · ${htmlEscape(formatNumber(data.summary.stop_count))} stops · ${htmlEscape(formatNumber(data.summary.passenger_count))} riders</div></div>
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
    let selectedRouteId = "";
    let selectedStopId = "";
    let hoverPopup = null;
    let filter = "all";
    let search = "";
    const routesById = new Map(data.routes.map(route => [route.id, route]));
    const stopsByRouteId = new Map();
    for (const stop of data.stops) { const list = stopsByRouteId.get(stop.route_id) || []; list.push(stop); stopsByRouteId.set(stop.route_id, list); }
    for (const list of stopsByRouteId.values()) list.sort((a,b) => a.order - b.order);
    const longThreshold = percentile(data.routes.map(route => route.duration_s), 0.75);
    const map = new maplibregl.Map({ container: "map", style: { version: 8, sources: { osm: { type: "raster", tiles: [${JSON.stringify(tileUrl)}], tileSize: 256, attribution: "OpenStreetMap contributors" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] }, center: data.bounds ? [(data.bounds.min_lng + data.bounds.max_lng) / 2, (data.bounds.min_lat + data.bounds.max_lat) / 2] : [121.4737,31.2304], zoom: 11 });
    map.addControl(new maplibregl.NavigationControl(), "bottom-right");
    map.on("load", () => { addLayers(); fitAll(); renderRoutes(); });
    document.getElementById("search").addEventListener("input", event => { search = event.target.value.toLowerCase().trim(); renderRoutes(); });
    document.querySelectorAll(".chip").forEach(button => button.addEventListener("click", () => { filter = button.dataset.filter; document.querySelectorAll(".chip").forEach(item => item.classList.toggle("active", item === button)); renderRoutes(); }));
    function addLayers() {
      map.addSource("routes", { type: "geojson", data: routeGeojson(false) });
      map.addLayer({ id: "route-casing", type: "line", source: "routes", paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": .72 } });
      map.addLayer({ id: "route-lines", type: "line", source: "routes", paint: { "line-color": ["get","color"], "line-width": 4, "line-opacity": .78 } });
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
    function showStopHover(event) { const feature = event.features && event.features[0]; if (!feature) return; const stop = data.stops.find(item => item.id === String(feature.properties.stop_id)); if (!stop) return; showHover(event.lngLat, '<div class="popup"><strong>' + esc(stop.is_depot ? 'School / Start' : 'Stop ' + stop.order) + '</strong><div>' + esc(stop.address || stop.requested_address || 'Unknown address') + '</div><div>' + esc(stop.route_id) + ' · ' + fmt(stop.passenger_count) + ' riders</div><div>' + duration(stop.cumulative_duration_s) + ' · ' + distance(stop.cumulative_distance_m) + '</div></div>'); }
    function showHover(lngLat, html) { if (!hoverPopup) hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 }); hoverPopup.setLngLat(lngLat).setHTML(html).addTo(map); }
    function clearHover() { map.getCanvas().style.cursor = "grab"; if (hoverPopup) { hoverPopup.remove(); hoverPopup = null; } }
    function routeGeojson(dimmed) { return { type: "FeatureCollection", features: data.routes.filter(route => route.geometry && route.geometry.length >= 2 && (!dimmed || route.id !== selectedRouteId)).map(route => ({ type: "Feature", properties: { route_id: route.id, color: color(route.route_index) }, geometry: { type: "LineString", coordinates: route.geometry } })) }; }
    function selectedRouteGeojson() { const route = routesById.get(selectedRouteId); return { type: "FeatureCollection", features: route && route.geometry && route.geometry.length >= 2 ? [{ type: "Feature", properties: { route_id: route.id, color: color(route.route_index) }, geometry: { type: "LineString", coordinates: route.geometry } }] : [] }; }
    function stopGeojson() { const stops = selectedRouteId ? data.stops.filter(stop => stop.route_id === selectedRouteId) : data.stops; return { type: "FeatureCollection", features: stops.map(stop => ({ type: "Feature", properties: { stop_id: stop.id, route_id: stop.route_id, color: color(stop.route_index), is_depot: stop.is_depot }, geometry: { type: "Point", coordinates: [stop.lng, stop.lat] } })) }; }
    function updateMapSources() { if (!map.getSource("routes")) return; map.getSource("routes").setData(routeGeojson(Boolean(selectedRouteId))); map.getSource("selected-route").setData(selectedRouteGeojson()); map.getSource("stops").setData(stopGeojson()); }
    function renderRoutes() { const routes = data.routes.filter(route => { const hay = [route.id, route.bus_type_name, route.vehicle_id, route.route_index + 1].join(" ").toLowerCase(); if (search && !hay.includes(search)) return false; if (filter === "long") return route.duration_s >= longThreshold; if (filter === "high") return loadRatio(route) >= .85; if (filter === "many") return route.stop_count >= 8; return true; }); document.getElementById("showing").textContent = "Showing " + routes.length + " of " + data.routes.length + " routes"; document.getElementById("routes").innerHTML = routes.map(routeCard).join(""); document.querySelectorAll("[data-route]").forEach(btn => btn.addEventListener("click", () => selectRoute(btn.dataset.route))); document.querySelectorAll("[data-stop]").forEach(btn => btn.addEventListener("click", event => { event.stopPropagation(); selectStop(btn.dataset.stop); })); }
    function routeCard(route) { const active = route.id === selectedRouteId; const stops = stopsByRouteId.get(route.id) || []; return '<div class="route-card ' + (active ? 'active' : '') + '"><button class="route-main" data-route="' + escAttr(route.id) + '" style="border-left-color:' + color(route.route_index) + '"><span class="dot" style="background:' + color(route.route_index) + '"></span><span class="route-text"><span class="route-title"><span>' + esc(route.id || ('Bus ' + (route.vehicle_id || route.route_index + 1))) + '</span>' + statusBadge(route) + '</span><span class="route-meta">' + fmt(route.load) + ' riders · ' + fmt(route.stop_count) + ' stops · ' + duration(route.duration_s) + '<br />' + distance(route.distance_m) + (route.bus_type_name ? ' · ' + esc(route.bus_type_name) : '') + '</span></span><span class="chevron">' + (active ? '⌃' : '⌄') + '</span></button>' + (active ? '<div class="stops"><p class="stops-label">Stop sequence</p>' + stops.map(stop => '<button class="stop-row ' + (stop.id === selectedStopId ? 'active' : '') + '" data-stop="' + escAttr(stop.id) + '"><span><strong>' + (stop.is_depot ? 'S' : stop.order) + '</strong></span><span><span class="stop-address">' + esc(stop.address || stop.requested_address || 'Unknown address') + '</span><span class="stop-meta">' + fmt(stop.passenger_count) + ' riders · ' + duration(stop.cumulative_duration_s) + '</span></span></button>').join('') + '</div>' : '') + '</div>'; }
    function selectRoute(routeId) { if (selectedRouteId === routeId) { selectedRouteId = ''; selectedStopId = ''; updateMapSources(); renderRoutes(); fitAll(); return; } selectedRouteId = routeId; selectedStopId = ''; updateMapSources(); renderRoutes(); fitRoute(routesById.get(routeId)); }
    function selectStop(stopId) { const stop = data.stops.find(item => item.id === stopId); if (!stop) return; selectedStopId = stopId; selectedRouteId = stop.route_id; updateMapSources(); renderRoutes(); map.flyTo({ center: [stop.lng, stop.lat], zoom: Math.max(map.getZoom(), 14), duration: 450 }); new maplibregl.Popup().setLngLat([stop.lng, stop.lat]).setHTML('<div class="popup"><strong>' + esc(stop.is_depot ? 'School / Start' : 'Stop ' + stop.order) + '</strong><div>' + esc(stop.address || stop.requested_address || 'Unknown address') + '</div><div>' + esc(stop.route_id) + ' · ' + fmt(stop.passenger_count) + ' riders</div><div>' + duration(stop.cumulative_duration_s) + ' · ' + distance(stop.cumulative_distance_m) + '</div></div>').addTo(map); }
    function fitAll() { if (!data.bounds) return; map.fitBounds([[data.bounds.min_lng, data.bounds.min_lat], [data.bounds.max_lng, data.bounds.max_lat]], { padding: 72, duration: 500 }); }
    function fitRoute(route) { if (!route || !route.geometry || route.geometry.length < 2) return; const lngs = route.geometry.map(p => p[0]).filter(Number.isFinite); const lats = route.geometry.map(p => p[1]).filter(Number.isFinite); map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 96, duration: 500 }); }
    function statusBadge(route) { const status = route.load / (route.bus_capacity || 0) >= 1 ? 'CAPACITY' : route.load / (route.bus_capacity || 1) >= .85 ? 'HIGH LOAD' : route.duration_s >= 3600 ? 'LONG' : ''; if (!status) return ''; return '<span class="badge ' + (status === 'CAPACITY' ? 'capacity' : status === 'HIGH LOAD' ? 'high' : '') + '">' + status + '</span>'; }
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
  geocodeWarnings: Array<Record<string, unknown>>;
  excludedStops: Array<Record<string, unknown>>;
  clientPrepElapsedSeconds: unknown;
};

function getDiagnostics(job: JobRecord): Diagnostics {
  const metadata = asRecord(job.metadata);
  const clientPrep = asRecord(metadata.client_prep);
  return {
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
};

function dataRouteCountForSummary(summaries: MapScenarioSummary[], key: string) {
  return summaries.find((summary) => summary.key === key)?.routeCount ?? 0;
}

function collectMapOutputs(jobId: string, result: Record<string, unknown>): MapOutput[] {
  const structured = asRecord(result.structured_results);
  const keys = [
    ["current_plan", "Current Plan"],
    ["original", "Free Optimization Baseline"],
    ["time_constrained", "15-Minute Constrained"],
    ["subway", "Subway Aggregated"],
    ["nearby", "Nearby Aggregated"],
    ["further_most", "Further Most"],
    ["further_most_nearby", "Further Most + Nearby Aggregate"],
  ] as const;
  return keys
    .map(([key, name]) => {
      const scenario = asRecord(structured[key]);
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
  const structured = asRecord(result.structured_results);
  return mapOutputs.map((output) => {
    const scenario = asRecord(structured[output.key]);
    const routes = asRecordArray(scenario.routes);
    return {
      key: output.key,
      name: output.name,
      routeCount: Number(scenario.route_count || scenario.bus_count || routes.length || 0),
      stopCount: Number(scenarioServiceStopCount(scenario) || 0),
      passengerCount: routes.reduce((total, route) => total + Number(routePassengerCount(route) || 0), 0),
      totalDistanceM: routes.reduce((total, route) => total + Number(route.distance_m || 0), 0),
      longestDurationS: routes.reduce((maxDuration, route) => Math.max(maxDuration, mapRouteDurationSeconds(route)), 0),
    };
  });
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
}: {
  job: JobRecord;
  report: Record<string, unknown>;
  currentPlan: Record<string, unknown>;
  currentComparison: Record<string, unknown>;
  reallocationSummary: Record<string, unknown>;
  scenarios: ScenarioRow[];
  priorityActions: Array<Record<string, unknown>>;
}) {
  const metadata = asRecord(job.metadata);
  const title = stringValue(metadata.job_name) || job.job_id;
  const generatedAt = stringValue(report.generated_at) || new Date().toISOString();
  const scenarioRows = scenarios
    .map(
      (scenario) => `
        <tr>
          <td>${htmlEscape(scenario.name)}</td>
          <td>${htmlEscape(formatNumber(scenario.routeCount))}</td>
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
          Move ${htmlEscape(formatNumber(action.stop_count))} stop(s) from ${htmlEscape(stringValue(action.from_route_id) || "N/A")}
          to ${htmlEscape(stringValue(action.to_route_id) || "N/A")};
          estimated saving ${htmlEscape(formatDurationMinFromSeconds(action.network_total_duration_saving_s))}
          and ${htmlEscape(formatDistanceKmFromMeters(action.network_total_distance_saving_m))}.
        </li>`,
    )
    .join("");

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AI Audit Report - ${htmlEscape(title)}</title>
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
  <div class="eyebrow">BRP AI Audit Report</div>
  <h1>${htmlEscape(title)}</h1>
  <div class="meta">Job ${htmlEscape(job.job_id)} | Generated ${htmlEscape(generatedAt)} | Model ${htmlEscape(stringValue(report.model) || "N/A")}</div>
  <div class="grid">
    <div class="metric"><span>Current Routes</span><strong>${htmlEscape(formatNumber(currentPlan.route_count))}</strong></div>
    <div class="metric"><span>Average Load</span><strong>${htmlEscape(formatPercent(currentPlan.avg_load_factor, 100))}</strong></div>
    <div class="metric"><span>Average Time</span><strong>${htmlEscape(formatDurationMinFromSeconds(currentPlan.avg_route_duration_s))}</strong></div>
    <div class="metric"><span>Route Gap</span><strong>${htmlEscape(formatSignedNumber(currentComparison.route_gap))}</strong></div>
  </div>
  ${markdownToHtml(stringValue(report.report_markdown))}
  <h2>Scenario Evidence</h2>
  <table>
    <thead><tr><th>Scenario</th><th>Routes</th><th>Avg Time</th><th>Avg Distance</th><th>Bus Mix</th></tr></thead>
    <tbody>${scenarioRows}</tbody>
  </table>
  <h2>Top Suggested Actions</h2>
  ${actionRows ? `<ul>${actionRows}</ul>` : `<p>No high-priority action signals were generated.</p>`}
  <p class="policy">Input policy: ${htmlEscape(stringValue(report.input_policy) || "Aggregated facts only; full address list excluded.")}</p>
  <p class="policy">Action signals: ${htmlEscape(formatNumber(reallocationSummary.actionable_weak_route_count))}</p>
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
