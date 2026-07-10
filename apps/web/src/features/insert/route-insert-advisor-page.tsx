import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, MapPin, PlusCircle, ShieldCheck, Upload } from "lucide-react";
import { HistorySidebar } from "@/components/history-sidebar";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { InteractiveRouteMap } from "@/features/results/interactive-route-map";
import {
  getRouteInsertAdvisorCapabilities,
  deleteRouteInsertAdvisorHistory,
  getRouteInsertAdvisorHistory,
  listRouteInsertAdvisorHistory,
  requestRouteInsertAdvisorProposals,
  type JobMapData,
  type RouteInsertAdvisorProposalRequest,
  type RouteInsertAdvisorProposalResponse,
  type RouteInsertAdvisorHistorySummary,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useT } from "@/lib/i18n/context";

function asNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function minutes(value: unknown): string {
  return `${Math.round(asNumber(value) / 60)} min`;
}

function meters(value: unknown): string {
  const raw = asNumber(value);
  return raw >= 1000 ? `${(raw / 1000).toFixed(1)} km` : `${Math.round(raw)} m`;
}

function signed(value: unknown, formatter: (item: unknown) => string): string {
  const raw = asNumber(value);
  return `${raw > 0 ? "+" : ""}${formatter(raw)}`;
}

function text(value: unknown): string {
  return String(value ?? "").trim();
}

function checkLabel(value: unknown, t: (key: string) => string): string {
  const key = text(value);
  if (key === "capacity") return t("Capacity limit");
  if (key === "stop_limit") return t("Stop limit");
  if (key === "combined_capacity") return t("Combined capacity limit");
  if (key === "combined_stop_limit") return t("Combined stop limit");
  if (key === "combined_constraints") return t("No combined feasible option");
  if (key === "osrm_refine_failed") return t("Road estimate unavailable");
  return key;
}

function proposalNewStopAddress(proposal: Record<string, unknown>): string {
  const newStop = proposal.new_stop as Record<string, unknown> | undefined;
  return text(newStop?.address);
}

function proposalPosition(proposal: Record<string, unknown>, t: (key: string) => string): string {
  const newAddress = proposalNewStopAddress(proposal) || t("New stop");
  return text(proposal.type) === "walk_to_stop"
    ? `${t("Walk to")} ${String(proposal.target_stop_address || "-")}`
    : `${String(proposal.insert_after_address || "-")} -> ${newAddress} -> ${String(proposal.insert_before_address || "-")}`;
}

function recommendationList(result: RouteInsertAdvisorProposalResponse): Array<Record<string, unknown>> {
  if (Array.isArray(result.recommendations) && result.recommendations.length) return result.recommendations;
  const proposals = result.proposals ?? [];
  return Array.from(
    proposals
      .reduce((bestByAddress, proposal) => {
        const address = proposalNewStopAddress(proposal) || `${bestByAddress.size + 1}`;
        if (!bestByAddress.has(address)) {
          bestByAddress.set(address, {
            new_stop: proposal.new_stop,
            primary: proposal,
            alternates: [],
            option_count: 1,
          });
        }
        return bestByAddress;
      }, new Map<string, Record<string, unknown>>())
      .values(),
  );
}

function proposalChecks(proposal: Record<string, unknown>, t: (key: string) => string): string {
  const checks = Array.isArray(proposal.warnings) ? proposal.warnings.map(text).filter(Boolean) : [];
  return checks.length ? checks.map((item) => checkLabel(item, t)).join(", ") : t("No issues");
}

function beforeAfter(base: unknown, next: unknown, formatter: (value: unknown) => string): string {
  return `${formatter(base)} -> ${formatter(next)}`;
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

export function RouteInsertAdvisorPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [addresses, setAddresses] = useState("");
  const [country, setCountry] = useState("China");
  const [city, setCity] = useState("Shanghai");
  const [walkingThreshold, setWalkingThreshold] = useState("500");
  const [stopLimit, setStopLimit] = useState("");
  const [activeScenarioId, setActiveScenarioId] = useState("recommended");
  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const [activeHistoryId, setActiveHistoryId] = useState("");
  const [historyResult, setHistoryResult] = useState<RouteInsertAdvisorProposalResponse>();
  const capabilitiesQuery = useQuery({
    queryKey: ["route-insert-advisor-capabilities"],
    queryFn: getRouteInsertAdvisorCapabilities,
    staleTime: 60_000,
  });
  const proposalMutation = useMutation({
    mutationFn: requestRouteInsertAdvisorProposals,
    onSuccess: (data) => {
      setActiveScenarioId(data.scenarios?.[0]?.id || "recommended");
      setActiveHistoryId(data.history_job?.run_id || "");
      setHistoryResult(data);
      void queryClient.invalidateQueries({ queryKey: ["route-insert-history"] });
    },
  });
  const historyQuery = useQuery({
    queryKey: ["route-insert-history"],
    queryFn: listRouteInsertAdvisorHistory,
  });
  const openHistoryMutation = useMutation({
    mutationFn: getRouteInsertAdvisorHistory,
    onSuccess: (record) => {
      const restored = record.route_insert_result;
      if (!restored) return;
      setHistoryResult(restored);
      setActiveHistoryId(record.run_id);
      setActiveScenarioId(restored.scenarios?.[0]?.id || "recommended");
    },
  });
  const deleteHistoryMutation = useMutation({
    mutationFn: deleteRouteInsertAdvisorHistory,
    onSuccess: async (_response, runId) => {
      if (activeHistoryId === runId) {
        setActiveHistoryId("");
        setHistoryResult(undefined);
      }
      await queryClient.invalidateQueries({ queryKey: ["route-insert-history"] });
    },
  });
  const bulkDeleteHistoryMutation = useMutation({
    mutationFn: async (runIds: string[]) => {
      for (const runId of runIds) await deleteRouteInsertAdvisorHistory(runId);
      return runIds;
    },
    onSuccess: async (runIds) => {
      if (runIds.includes(activeHistoryId)) {
        setActiveHistoryId("");
        setHistoryResult(undefined);
      }
      await queryClient.invalidateQueries({ queryKey: ["route-insert-history"] });
    },
  });
  const capabilities = capabilitiesQuery.data;
  const sourceCount = capabilities?.supported_sources.length ?? 1;
  const checkCount = capabilities?.candidate_checks.length ?? 7;
  const result = historyResult;
  const canRun = Boolean(fileBase64 && addresses.trim() && !proposalMutation.isPending);
  const requestPayload = (): RouteInsertAdvisorProposalRequest => ({
    file_name: file?.name || "workbook.xlsx",
    file_base64: fileBase64,
    new_stops: addresses,
    constraints: {
      country,
      city,
      walking_threshold_m: Number(walkingThreshold) || 0,
      stop_limit: stopLimit ? Number(stopLimit) : null,
    },
  });

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-primary">{t("Planning Tools")}</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">
            {t("Route Insert Advisor")}
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            {t("Find the least disruptive way to add new students into an existing route plan.")}
          </p>
        </div>
        <Link to="/jobs" className={buttonClassName("secondary")}>
          <ListChecks className="h-4 w-4" aria-hidden="true" />
          {t("Audit History")}
        </Link>
      </section>

      <div
        className={[
          "grid gap-4",
          historyCollapsed
            ? "lg:grid-cols-[88px_minmax(0,1fr)]"
            : "lg:grid-cols-[320px_minmax(0,1fr)]",
        ].join(" ")}
      >
        <HistorySidebar
          items={historyQuery.data || []}
          itemId={(job) => job.run_id}
          activeId={activeHistoryId}
          title="Route Insert History"
          emptyMessage="Saved Route Insert runs will appear here."
          collapsed={historyCollapsed}
          onCollapsedChange={setHistoryCollapsed}
          isLoading={historyQuery.isLoading}
          isFetching={historyQuery.isFetching}
          error={
            (historyQuery.error ||
              openHistoryMutation.error ||
              deleteHistoryMutation.error ||
              bulkDeleteHistoryMutation.error) as Error | null
          }
          deletingId={
            deleteHistoryMutation.isPending
              ? deleteHistoryMutation.variables
              : undefined
          }
          bulkDeleting={bulkDeleteHistoryMutation.isPending}
          onRefresh={() => void historyQuery.refetch()}
          onOpen={(runId) => openHistoryMutation.mutate(runId)}
          onDelete={(runId) => deleteHistoryMutation.mutate(runId)}
          onBulkDelete={(runIds) => bulkDeleteHistoryMutation.mutate(runIds)}
          renderItem={(job, active) => (
            <RouteInsertHistoryItem job={job} active={active} />
          )}
          className="min-w-0 lg:sticky lg:top-20 lg:self-start"
        />

        <div className="min-w-0 space-y-6">
      <div className="grid gap-4 lg:grid-cols-3">
        <InfoCard
          icon={<PlusCircle className="h-4 w-4" aria-hidden="true" />}
          title={t("Advisor status")}
          value={capabilitiesQuery.isError ? t("Unavailable") : t("Ready")}
          tone={capabilitiesQuery.isError ? "warning" : "success"}
          detail={t("The original plan stays unchanged; the advisor only returns proposal candidates.")}
        />
        <InfoCard
          icon={<MapPin className="h-4 w-4" aria-hidden="true" />}
          title={t("Supported sources")}
          value={`${sourceCount}`}
          detail={t("Upload the same current-plan workbook used by Route Audit.")}
        />
        <InfoCard
          icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
          title={t("Candidate checks")}
          value={`${checkCount}`}
          detail={t("Capacity, stop limit, insertion impact, and walking-to-stop checks are included.")}
        />
      </div>

      <section className="rounded-md border border-border bg-surface shadow-sm">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{t("New student insertion")}</h2>
        </div>
        <form
          className="grid gap-4 p-4 lg:grid-cols-[1fr_320px]"
          onSubmit={(event) => {
            event.preventDefault();
            setActiveHistoryId("");
            setHistoryResult(undefined);
            proposalMutation.mutate(requestPayload());
          }}
        >
          <div className="space-y-4">
            <div className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{t("Current-plan workbook")}</span>
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/60 px-4 py-6 text-center transition hover:border-primary/60 hover:bg-muted">
                <Upload className="mb-3 h-6 w-6 text-primary" aria-hidden="true" />
                <span className="text-sm font-medium">{file?.name || t("Select workbook")}</span>
                <span className="mt-1 text-xs text-muted-foreground">{t("Upload the current-plan workbook used for Route Audit.")}</span>
                <input
                  className="sr-only"
                  type="file"
                  accept=".xlsx,.xlsm"
                  onChange={async (event) => {
                    const nextFile = event.target.files?.[0] ?? null;
                    event.currentTarget.value = "";
                    setFile(nextFile);
                    setFileBase64("");
                    setFileError("");
                    setActiveScenarioId("recommended");
                    setActiveHistoryId("");
                    setHistoryResult(undefined);
                    proposalMutation.reset();
                    if (!nextFile) return;
                    try {
                      setFileBase64(await fileToBase64(nextFile));
                    } catch (error) {
                      setFileError(error instanceof Error ? error.message : t("Workbook could not be read."));
                    }
                  }}
                />
              </label>
              {fileError ? <p className="text-xs text-warning-foreground">{fileError}</p> : null}
            </div>
            <Field label={t("New student addresses")}>
              <textarea
                className="min-h-32 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                value={addresses}
                onChange={(event) => setAddresses(event.target.value)}
                placeholder={t("One address per line")}
              />
            </Field>
          </div>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("Country")}>
                <input
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                  value={country}
                  onChange={(event) => setCountry(event.target.value)}
                />
              </Field>
              <Field label={t("City")}>
                <input
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                  value={city}
                  onChange={(event) => setCity(event.target.value)}
                />
              </Field>
            </div>
            <Field label={t("Walking threshold")}>
              <input
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                type="number"
                min="0"
                value={walkingThreshold}
                onChange={(event) => setWalkingThreshold(event.target.value)}
              />
            </Field>
            <Field label={t("Stop limit")}>
              <input
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                type="number"
                min="0"
                value={stopLimit}
                onChange={(event) => setStopLimit(event.target.value)}
                placeholder={t("Use route default")}
              />
            </Field>
            <button className={buttonClassName("primary")} disabled={!canRun} type="submit">
              <PlusCircle className="h-4 w-4" aria-hidden="true" />
              {proposalMutation.isPending ? t("Scoring...") : t("Find proposals")}
            </button>
          </div>
        </form>
      </section>

      {proposalMutation.isError ? (
        <section className="rounded-md border border-warning bg-warning/10 p-4 text-sm text-warning-foreground">
          {proposalMutation.error instanceof Error ? proposalMutation.error.message : t("Proposal request failed.")}
        </section>
      ) : null}

      {result?.history_error ? (
        <section className="rounded-md border border-warning bg-warning/10 p-4 text-sm text-warning-foreground">
          {t("History autosave failed")}: {result.history_error}
        </section>
      ) : null}

      {result ? (
        <ProposalResults
          result={result}
          activeScenarioId={activeScenarioId}
          onSelectScenario={setActiveScenarioId}
        />
      ) : null}
        </div>
      </div>
    </div>
  );
}

function RouteInsertHistoryItem({
  job,
  active,
}: {
  job: RouteInsertAdvisorHistorySummary;
  active: boolean;
}) {
  const t = useT();
  const summary = job.summary || {};
  const secondaryClass = active
    ? "text-primary-foreground/80"
    : "text-muted-foreground";
  return (
    <div className="min-w-0 px-1 py-1">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">
            {job.title || t("Route Insert Run")}
          </div>
          <div className={`mt-1 text-xs ${secondaryClass}`}>
            {formatDateTime(job.created_at)}
          </div>
          <div className={`mt-1 truncate text-xs ${secondaryClass}`}>
            {t("Submitted by")} {job.owner_email || t("Unknown")}
          </div>
        </div>
        <Badge tone={active ? "neutral" : summary.feasible ? "success" : "warning"}>
          {summary.feasible ? t("Feasible") : t("Needs review")}
        </Badge>
      </div>
      <div className={`mt-2 grid grid-cols-2 gap-1 text-xs ${secondaryClass}`}>
        <span>{String(summary.new_stop_count ?? 0)} {t("new stops")}</span>
        <span>{String(summary.affected_route_count ?? 0)} {t("routes")}</span>
        <span>{signed(summary.total_added_duration_s, minutes)}</span>
        <span>{String(summary.scenario_count ?? 0)} {t("scenarios")}</span>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-2">
      <span className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function ProposalResults({
  result,
  activeScenarioId,
  onSelectScenario,
}: {
  result: RouteInsertAdvisorProposalResponse;
  activeScenarioId: string;
  onSelectScenario: (scenarioId: string) => void;
}) {
  const t = useT();
  const proposals = result.proposals ?? [];
  const warnings = Array.isArray(result.geocode_warnings) ? result.geocode_warnings : [];
  const summary = result.summary ?? {};
  const scenarios = result.scenarios?.length
    ? result.scenarios
    : [
        {
          id: "recommended",
          is_recommended: true,
          recommendations: result.recommendations,
          selected_plan: result.selected_plan,
          selected_map_data: result.selected_map_data ?? result.map_data,
        },
      ];
  const activeScenario =
    scenarios.find((scenario) => scenario.id === activeScenarioId) ?? scenarios[0];
  const selectedPlan = (activeScenario.selected_plan ?? {}) as Record<string, unknown>;
  const affectedRoutes = Array.isArray(selectedPlan.affected_routes)
    ? (selectedPlan.affected_routes as Array<Record<string, unknown>>)
    : [];
  const mapData = activeScenario.selected_map_data as JobMapData | undefined;
  const localizedMapData = mapData
    ? { ...mapData, scenario_name: t("Selected insert plan") }
    : undefined;
  const recommendations = activeScenario.recommendations?.length
    ? activeScenario.recommendations
    : recommendationList(result);
  const planFeasible = Boolean(selectedPlan.feasible);
  return (
    <section className="rounded-md border border-border bg-surface shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">{t("Selected insert plan")}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("All new students are combined into one plan and one active map.")}
          </p>
        </div>
        <Badge tone={planFeasible ? "success" : "warning"}>
          {planFeasible ? t("Ready to review") : t("Needs review")}
        </Badge>
      </div>
      <div className="overflow-auto p-4">
        <div className="mb-4">
          <div className="flex items-end justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold">{t("Cached scenarios")}</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {t("Choose a cached whole-plan scenario. Switching does not rerun geocoding, routing, or AMap.")}
              </p>
            </div>
            <Badge tone="info">{scenarios.length}</Badge>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
            {scenarios.map((scenario, index) => {
              const plan = (scenario.selected_plan ?? {}) as Record<string, unknown>;
              const active = scenario.id === activeScenario.id;
              return (
                <button
                  key={scenario.id}
                  type="button"
                  aria-pressed={active}
                  className={[
                    "min-w-0 rounded-md border p-3 text-left transition focus:outline-none focus:ring-2 focus:ring-primary/30",
                    active
                      ? "border-primary bg-primary/10"
                      : "border-border bg-surface hover:border-primary/50 hover:bg-muted/50",
                  ].join(" ")}
                  onClick={() => onSelectScenario(scenario.id)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="truncate text-sm font-semibold">
                      {index === 0 ? t("Recommended plan") : `${t("Alternative plan")} ${index}`}
                    </span>
                    <Badge tone={plan.feasible ? "success" : "warning"}>
                      {plan.feasible ? t("Feasible") : t("Needs review")}
                    </Badge>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                    <span>{signed(plan.total_added_duration_s, minutes)}</span>
                    <span>{signed(plan.total_added_distance_m, meters)}</span>
                    <span>{String(plan.affected_route_count ?? 0)} {t("routes")}</span>
                    <span>{String(plan.inserted_stop_count ?? 0)} {t("stops")}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
        <div className="mb-4 grid gap-3 md:grid-cols-4">
          <Metric label={t("New students")} value={String(summary.new_stop_count ?? 0)} />
          <Metric label={t("Affected routes")} value={String(selectedPlan.affected_route_count ?? affectedRoutes.length)} />
          <Metric label={t("Added time")} value={signed(selectedPlan.total_added_duration_s, minutes)} />
          <Metric label={t("Added distance")} value={signed(selectedPlan.total_added_distance_m, meters)} />
          <Metric
            label={t("Geocode warnings")}
            value={String(summary.geocode_warning_count ?? warnings.length)}
            tone={warnings.length ? "warning" : "success"}
          />
        </div>
        {warnings.length ? (
          <div className="mb-4 rounded-md border border-warning bg-warning/10 p-3 text-sm text-warning-foreground">
            <div className="font-semibold">{t("Some addresses could not be resolved.")}</div>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {warnings.map((warning, index) => (
                <li key={`${text(warning.address)}-${index}`}>
                  {text(warning.address) || `${t("Row")} ${index + 1}`}: {text(warning.reason)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {recommendations.length ? (
          <div className="mb-4 space-y-3">
            <div>
              <h3 className="text-base font-semibold">{t("Student actions")}</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {t("Choose an alternate only when operations prefers another reasonable route; the map refreshes as one combined plan.")}
              </p>
            </div>
            <div className="space-y-4">
              {recommendations.map((recommendation, index) => {
                const selected =
                  recommendation.selected as Record<string, unknown> | null | undefined;
                const newStop =
                  (recommendation.new_stop as Record<string, unknown> | undefined) ??
                  (selected?.new_stop as Record<string, unknown> | undefined);
                return (
                  <article key={`${text(newStop?.address)}-${index}`} className="rounded-md border border-border bg-muted/20 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{t("New stop")}</div>
                        <div className="mt-1 text-lg font-semibold">{text(newStop?.address) || `${t("New stop")} ${index + 1}`}</div>
                      </div>
                      <Badge tone={selected?.feasible ? "success" : "warning"}>
                        {selected ? (selected.feasible ? t("Feasible") : t("Needs review")) : t("No candidates")}
                      </Badge>
                    </div>
                    {selected ? <RecommendationCard proposal={selected} title={t("Selected action")} /> : null}
                  </article>
                );
              })}
            </div>
          </div>
        ) : null}
        {localizedMapData ? (
          <div className="relative mb-4 overflow-hidden rounded-md border border-border">
            <div className="border-b border-border px-4 py-3">
              <div className="text-sm font-semibold">{t("Selected plan map")}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {t("Colored lines are the selected routes; grey dashed lines show the original affected routes.")}
              </div>
            </div>
            <div className="h-[560px]">
              <InteractiveRouteMap data={localizedMapData} focusKey={`insert-${localizedMapData.job_id}-${activeScenario.id}`} />
            </div>
          </div>
        ) : null}
        {affectedRoutes.length ? (
          <div className="mb-4">
            <h3 className="text-base font-semibold">{t("Affected route comparison")}</h3>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              {affectedRoutes.map((route) => (
                <article key={text(route.route_id)} className="rounded-md border border-border p-4">
                  <div className="flex items-center justify-between gap-3">
                    <h4 className="font-semibold">{text(route.route_id)}</h4>
                    <Badge tone={route.feasible ? "success" : "warning"}>
                      {route.feasible ? t("Within constraints") : t("Needs review")}
                    </Badge>
                  </div>
                  <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
                    <Metric label={t("Route duration")} value={beforeAfter(route.base_duration_s, route.selected_duration_s, minutes)} />
                    <Metric label={t("Route distance")} value={beforeAfter(route.base_distance_m, route.selected_distance_m, meters)} />
                    <Metric label={t("Capacity")} value={`${String(route.capacity_before ?? "-")} -> ${String(route.capacity_after ?? "-")}${route.capacity_limit ? ` / ${String(route.capacity_limit)}` : ""}`} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge tone={route.time_window_ok ? "success" : "warning"}>
                      {route.time_window_ok ? t("Time window passed") : t("Outside time window")}
                    </Badge>
                    <Badge tone={route.provider_verified ? "success" : "warning"}>
                      {route.provider_verified ? t("AMap verified") : t("Road estimate only")}
                    </Badge>
                    <Badge tone="info">{signed(route.delta_duration_s, minutes)}</Badge>
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : null}
        {!proposals.length ? (
          <p className="text-sm text-muted-foreground">{t("No insertion candidates were returned.")}</p>
        ) : null}
      </div>
    </section>
  );
}

function RecommendationCard({
  proposal,
  title,
}: {
  proposal: Record<string, unknown>;
  title: string;
}) {
  const t = useT();
  const feasible = Boolean(proposal.feasible);
  const isWalking = text(proposal.type) === "walk_to_stop";
  return (
    <div className="mt-3 rounded-md border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-center justify-between gap-3">
        <h4 className="font-semibold">{title}</h4>
        <Badge tone={feasible ? "success" : "warning"}>{feasible ? t("Feasible") : t("Needs review")}</Badge>
      </div>
      <div className="mt-3 text-lg font-semibold">
        {isWalking ? t("Walk to existing stop") : `${t("Insert into")} ${String(proposal.route_id || "-")}`}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{proposalPosition(proposal, t)}</p>
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-4">
        <Metric
          label={t("Route duration")}
          value={
            isWalking
              ? t("No route change")
              : beforeAfter(proposal.base_route_duration_s, proposal.estimated_route_duration_s, minutes)
          }
        />
        <Metric
          label={t("Route distance")}
          value={
            isWalking
              ? meters(proposal.walking_distance_m)
              : beforeAfter(proposal.base_route_distance_m, proposal.estimated_route_distance_m, meters)
          }
        />
        <Metric
          label={t("Capacity")}
          value={`${String(proposal.capacity_before ?? "-")} -> ${String(proposal.capacity_after ?? "-")}${
            proposal.capacity_limit ? ` / ${String(proposal.capacity_limit)}` : ""
          }`}
        />
        <Metric
          label={t("Stop count")}
          value={`${String(proposal.base_stop_count ?? "-")} -> ${String(proposal.stop_count_after ?? "-")}${
            proposal.stop_limit ? ` / ${String(proposal.stop_limit)}` : ""
          }`}
        />
      </div>
      {!isWalking ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {t("Extra impact")}: {minutes(proposal.delta_duration_s)} / {meters(proposal.delta_distance_m)}
        </p>
      ) : null}
      <p className="mt-2 text-sm text-muted-foreground">{proposalChecks(proposal, t)}</p>
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "info",
}: {
  label: string;
  value: string;
  tone?: "success" | "warning" | "info";
}) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{label}</div>
      <Badge tone={tone}>{value}</Badge>
    </div>
  );
}

function InfoCard({
  icon,
  title,
  value,
  tone = "success",
  detail,
}: {
  icon: ReactNode;
  title: string;
  value: string;
  tone?: "success" | "warning" | "info";
  detail: string;
}) {
  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-normal text-muted-foreground">
          {icon}
          {title}
        </div>
        <Badge tone={tone}>{value}</Badge>
      </div>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">{detail}</p>
    </section>
  );
}
