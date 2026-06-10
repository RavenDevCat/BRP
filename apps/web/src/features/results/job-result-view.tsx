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
import { generateAiAudit, getJobArtifactUrl, getJobExportUrl, getJobMapData, type JobRecord } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  formatDistanceKmFromMeters,
  formatDurationMinFromSeconds,
  formatNumber,
  formatPercent,
  toTitle,
} from "@/lib/format";

type ResultTab = "ai" | "audit" | "baselines" | "maps" | "actions" | "diagnostics";

const resultTabs: Array<{ key: ResultTab; label: string }> = [
  { key: "ai", label: "AI Audit" },
  { key: "audit", label: "Audit Detail" },
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
      {activeTab === "maps" ? <MapsPanel jobId={job.job_id} mapOutputs={mapOutputs} result={result} diagnostics={diagnostics} /> : null}
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

function MapsPanel({
  jobId,
  mapOutputs,
  result,
  diagnostics,
}: {
  jobId: string;
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
            <button type="button" className={cn(buttonClassName("secondary"), "bg-white/88 shadow-lg backdrop-blur hover:bg-white")} onClick={() => setIsMapFullscreenOpen(true)}>
              <Maximize2 className="h-4 w-4" aria-hidden="true" />
              Open
            </button>
            <a href={selected.downloadUrl} className={cn(buttonClassName("secondary"), "bg-white/88 shadow-lg backdrop-blur hover:bg-white")}>
              <Download className="h-4 w-4" aria-hidden="true" />
              Download
            </a>
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
                  <a href={selected.downloadUrl} className={cn(buttonClassName("secondary"), "bg-white/70 backdrop-blur hover:bg-white")}>
                    <Download className="h-4 w-4" aria-hidden="true" />
                    Download
                  </a>
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
