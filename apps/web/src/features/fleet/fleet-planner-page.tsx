import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleHelp, Download, FileSpreadsheet, History, Loader2, Map, MapPinned, RefreshCw, Route, Save, SlidersHorizontal, Upload, UsersRound, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  buildFleetPlannerClusters,
  buildFleetPlannerGlobalPlan,
  buildFleetPlannerRoutePreview,
  geocodeFleetPlannerDemand,
  getFleetPlannerHistory,
  getDemandTemplateUrl,
  listFleetPlannerHistory,
  previewFleetPlanner,
  saveFleetPlannerHistory,
  type FleetPlannerClusterResponse,
  type FleetPlannerGeocodeResponse,
  type FleetPlannerHistoryRecord,
  type FleetPlannerHistorySummary,
  type FleetPlannerPreviewResponse,
  type FleetPlannerRoutePreviewResponse,
  type FleetPlannerHistoryCreateResponse,
} from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/cn";

const fieldClassName =
  "h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none transition focus:border-primary";
const textareaClassName =
  "min-h-28 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm outline-none transition focus:border-primary";

type FleetResultView = "fleet" | "demand" | "geocode" | "optimized" | "maps" | "diagnostics";
type ToolMapOutput = {
  key: string;
  name: string;
  html: string;
};

export function FleetPlannerPage() {
  const queryClient = useQueryClient();
  const [market, setMarket] = useState<"KR" | "CN">("KR");
  const [mode, setMode] = useState<"balanced" | "cost_saver" | "comfort_saver">("balanced");
  const [monitorSeats, setMonitorSeats] = useState(1);
  const [riderCounts, setRiderCounts] = useState("8, 22, 34, 44");
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [sectorCount, setSectorCount] = useState<4 | 8 | 12>(8);
  const [routeDirection, setRouteDirection] = useState<"to_school" | "from_school">("to_school");
  const [globalDirection, setGlobalDirection] = useState<"to_school" | "from_school">("to_school");
  const [historyTitle, setHistoryTitle] = useState("");
  const [loadedHistoryRecord, setLoadedHistoryRecord] = useState<FleetPlannerHistoryRecord | null>(null);
  const [howToUseOpen, setHowToUseOpen] = useState(false);
  const [activeResultView, setActiveResultView] = useState<FleetResultView>("fleet");

  const historyQuery = useQuery({
    queryKey: ["fleet-planner-history"],
    queryFn: listFleetPlannerHistory,
    staleTime: 15_000,
  });

  const previewMutation = useMutation({
    mutationFn: async () =>
      previewFleetPlanner({
        market,
        mode,
        monitor_seats: monitorSeats,
        rider_counts: riderCounts,
        file_name: file?.name,
        file_base64: fileBase64 || undefined,
      }),
    onSuccess: () => {
      setLoadedHistoryRecord(null);
      setActiveResultView("fleet");
    },
  });

  const geocodeMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64) {
        throw new Error("Upload a demand workbook before running geocode preview.");
      }
      return geocodeFleetPlannerDemand({
        file_name: file.name,
        file_base64: fileBase64,
      });
    },
    onSuccess: () => {
      setLoadedHistoryRecord(null);
      setActiveResultView("geocode");
    },
  });

  const clusterMutation = useMutation({
    mutationFn: async () => {
      const geocodeResult = geocodeMutation.data;
      if (!geocodeResult) {
        throw new Error("Run demand geocode before building clusters.");
      }
      return buildFleetPlannerClusters({
        market,
        mode,
        monitor_seats: monitorSeats,
        sector_count: sectorCount,
        geocode_result: {
          school: geocodeResult.school,
          demand_points: geocodeResult.demand_points,
          summary: geocodeResult.summary,
        },
      });
    },
    onSuccess: () => {
      setLoadedHistoryRecord(null);
      setActiveResultView("diagnostics");
    },
  });

  const routePreviewMutation = useMutation({
    mutationFn: async () => {
      const clusterResult = clusterMutation.data;
      if (!clusterResult) {
        throw new Error("Build clusters before running route preview.");
      }
      return buildFleetPlannerRoutePreview({
        market,
        mode,
        monitor_seats: monitorSeats,
        service_direction: routeDirection,
        max_route_duration_minutes: Number(result?.assumptions.max_route_duration_minutes || 0) || undefined,
        cluster_result: {
          school: clusterResult.school,
          clusters: clusterResult.clusters,
          failed_points: clusterResult.failed_points,
          summary: clusterResult.summary,
        },
      });
    },
    onSuccess: () => {
      setLoadedHistoryRecord(null);
      setActiveResultView("diagnostics");
    },
  });

  const globalPlanMutation = useMutation({
    mutationFn: async () => {
      const geocodeResult = geocodeMutation.data;
      if (!geocodeResult) {
        throw new Error("Run demand geocode before building a global plan.");
      }
      return buildFleetPlannerGlobalPlan({
        market,
        mode,
        monitor_seats: monitorSeats,
        service_direction: globalDirection,
        geocode_result: {
          school: geocodeResult.school,
          demand_points: geocodeResult.demand_points,
          summary: geocodeResult.summary,
        },
      });
    },
    onSuccess: () => {
      setLoadedHistoryRecord(null);
      setActiveResultView("optimized");
    },
  });

  const saveHistoryMutation = useMutation({
    mutationFn: async () => {
      if (!result) {
        throw new Error("Run Fleet preview before saving history.");
      }
      if (!globalPlanResult) {
        throw new Error("Build an optimized plan before saving history.");
      }
      return saveFleetPlannerHistory({
        title: historyTitle.trim() || defaultFleetHistoryTitle(),
        scenario: {
          market,
          mode,
          monitor_seats: monitorSeats,
          service_direction: globalDirection,
        },
        preview_result: result,
        geocode_result: geocodeResult,
        cluster_result: clusterResult,
        route_preview_result: routePreviewResult,
        global_plan_result: globalPlanResult,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["fleet-planner-history"] });
    },
  });

  const loadHistoryMutation = useMutation({
    mutationFn: (runId: string) => getFleetPlannerHistory(runId),
    onSuccess: (record) => {
      previewMutation.reset();
      geocodeMutation.reset();
      clusterMutation.reset();
      routePreviewMutation.reset();
      globalPlanMutation.reset();
      saveHistoryMutation.reset();
      setLoadedHistoryRecord(record);
      setHistoryTitle(record.title || "");
      setActiveResultView("optimized");
    },
  });

  function resetScenarioResults() {
    previewMutation.reset();
    geocodeMutation.reset();
    clusterMutation.reset();
    routePreviewMutation.reset();
    globalPlanMutation.reset();
    saveHistoryMutation.reset();
    setLoadedHistoryRecord(null);
    setActiveResultView("fleet");
  }

  function resetClusterResults() {
    clusterMutation.reset();
    routePreviewMutation.reset();
    globalPlanMutation.reset();
    saveHistoryMutation.reset();
    setLoadedHistoryRecord(null);
    setActiveResultView("geocode");
  }

  function resetRoutePreviewResults() {
    routePreviewMutation.reset();
  }

  function resetGlobalPlanResults() {
    globalPlanMutation.reset();
    saveHistoryMutation.reset();
    setLoadedHistoryRecord(null);
    setActiveResultView("fleet");
  }

  async function handleFileChange(nextFile: File | null) {
    setFile(nextFile);
    setFileError("");
    setFileBase64("");
    resetScenarioResults();
    if (!nextFile) {
      return;
    }
    const suffix = nextFile.name.split(".").pop()?.toLowerCase();
    if (suffix !== "xlsx") {
      setFileError("Use an .xlsx demand workbook.");
      return;
    }
    try {
      setFileBase64(await fileToBase64(nextFile));
    } catch (error) {
      setFileError(error instanceof Error ? error.message : "Workbook could not be read.");
    }
  }

  const result = previewMutation.data || loadedHistoryRecord?.preview_result;
  const geocodeResult = geocodeMutation.data || loadedHistoryRecord?.geocode_result;
  const clusterResult = clusterMutation.data || loadedHistoryRecord?.cluster_result;
  const routePreviewResult = routePreviewMutation.data || loadedHistoryRecord?.route_preview_result;
  const globalPlanResult = globalPlanMutation.data || loadedHistoryRecord?.global_plan_result;
  const mapOutputs = useMemo(
    () => collectFleetMapOutputs({ geocodeResult, clusterResult, routePreviewResult, globalPlanResult }),
    [geocodeResult, clusterResult, routePreviewResult, globalPlanResult],
  );
  const mixRows = useMemo(
    () =>
      Object.entries(result?.mix_summary.vehicle_mix || {}).map(([vehicle, count]) => ({
        vehicle,
        count,
      })),
    [result],
  );

  function handleMarketChange(nextMarket: "KR" | "CN") {
    if (nextMarket === market) {
      return;
    }
    setMarket(nextMarket);
    resetScenarioResults();
  }

  function handleModeChange(nextMode: "balanced" | "cost_saver" | "comfort_saver") {
    if (nextMode === mode) {
      return;
    }
    setMode(nextMode);
    resetScenarioResults();
  }

  function handleMonitorSeatsChange(nextMonitorSeats: number) {
    setMonitorSeats(nextMonitorSeats);
    resetScenarioResults();
  }

  function handleRiderCountsChange(nextRiderCounts: string) {
    setRiderCounts(nextRiderCounts);
    previewMutation.reset();
  }

  function handleSectorCountChange(nextSectorCount: 4 | 8 | 12) {
    if (nextSectorCount === sectorCount) {
      return;
    }
    setSectorCount(nextSectorCount);
    resetClusterResults();
  }

  function handleRouteDirectionChange(nextDirection: "to_school" | "from_school") {
    if (nextDirection === routeDirection) {
      return;
    }
    setRouteDirection(nextDirection);
    resetRoutePreviewResults();
  }

  function handleGlobalDirectionChange(nextDirection: "to_school" | "from_school") {
    if (nextDirection === globalDirection) {
      return;
    }
    setGlobalDirection(nextDirection);
    resetGlobalPlanResults();
  }

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-primary">Side tools</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">Fleet Planner Preview</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Preview vehicle choices from rider groups or a demand workbook before running address clustering and routing.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={buttonClassName("secondary")} onClick={() => setHowToUseOpen(true)}>
            <CircleHelp className="h-4 w-4" aria-hidden="true" />
            How to use
          </button>
          <a href={getDemandTemplateUrl()} className={buttonClassName("secondary")}>
            <Download className="h-4 w-4" aria-hidden="true" />
            Demand template
          </a>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">Demand Input</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/60 px-4 py-6 text-center transition hover:border-primary/60 hover:bg-muted">
                <Upload className="mb-3 h-6 w-6 text-primary" aria-hidden="true" />
                <span className="text-sm font-medium">{file?.name || "Select demand workbook"}</span>
                <span className="mt-1 text-xs text-muted-foreground">Optional .xlsx demand template</span>
                <input
                  className="sr-only"
                  type="file"
                  accept=".xlsx"
                  onChange={(event) => void handleFileChange(event.target.files?.[0] || null)}
                />
              </label>
              {fileError ? <InlineError message={fileError} /> : null}
              <Field label="Manual Rider Groups">
                <textarea
                  className={textareaClassName}
                  value={riderCounts}
                  onChange={(event) => handleRiderCountsChange(event.target.value)}
                  disabled={Boolean(fileBase64)}
                />
              </Field>
            </CardContent>
          </Card>

          {result ? (
            <FleetPreviewResult
              result={result}
              mixRows={mixRows}
              geocodeResult={geocodeResult}
              clusterResult={clusterResult}
              routePreviewResult={routePreviewResult}
              globalPlanResult={globalPlanResult}
              mapOutputs={mapOutputs}
              historyTitle={historyTitle}
              onHistoryTitleChange={setHistoryTitle}
              onSaveHistory={() => saveHistoryMutation.mutate()}
              saveHistoryResult={saveHistoryMutation.data}
              saveHistoryError={saveHistoryMutation.error as Error | null}
              isSavingHistory={saveHistoryMutation.isPending}
              activeView={activeResultView}
              onActiveViewChange={setActiveResultView}
            />
          ) : null}
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">Scenario</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <Field label="Market">
                <div className="grid grid-cols-2 gap-2">
                  <ModeButton active={market === "KR"} onClick={() => handleMarketChange("KR")}>
                    KR
                  </ModeButton>
                  <ModeButton active={market === "CN"} onClick={() => handleMarketChange("CN")}>
                    CN
                  </ModeButton>
                </div>
              </Field>
              <Field label="Planning Mode">
                <select className={fieldClassName} value={mode} onChange={(event) => handleModeChange(event.target.value as typeof mode)}>
                  <option value="balanced">Balanced</option>
                  <option value="cost_saver">Cost Saver</option>
                  <option value="comfort_saver">Comfort Saver</option>
                </select>
              </Field>
              <Field label="Bus Monitor Seats">
                <input
                  className={fieldClassName}
                  type="number"
                  min="0"
                  max="10"
                  step="1"
                  value={monitorSeats}
                  onChange={(event) => handleMonitorSeatsChange(Number(event.target.value))}
                />
              </Field>
              <Field label="Service Direction">
                <div className="grid grid-cols-2 gap-2">
                  <ModeButton active={globalDirection === "to_school"} onClick={() => handleGlobalDirectionChange("to_school")}>
                    To School
                  </ModeButton>
                  <ModeButton active={globalDirection === "from_school"} onClick={() => handleGlobalDirectionChange("from_school")}>
                    From School
                  </ModeButton>
                </div>
              </Field>
              {previewMutation.error ? <InlineError message={(previewMutation.error as Error).message} /> : null}
              {geocodeMutation.error ? <InlineError message={(geocodeMutation.error as Error).message} /> : null}
              {clusterMutation.error ? <InlineError message={(clusterMutation.error as Error).message} /> : null}
              {routePreviewMutation.error ? <InlineError message={(routePreviewMutation.error as Error).message} /> : null}
              {globalPlanMutation.error ? <InlineError message={(globalPlanMutation.error as Error).message} /> : null}
              <div className="grid gap-2">
                <Button
                  type="button"
                  disabled={previewMutation.isPending || Boolean(file && !fileBase64)}
                  icon={previewMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <UsersRound className="h-4 w-4" />}
                  onClick={() => previewMutation.mutate()}
                >
                  Preview fleet
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!fileBase64 || geocodeMutation.isPending}
                  icon={geocodeMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <MapPinned className="h-4 w-4" />}
                  onClick={() => {
                    clusterMutation.reset();
                    routePreviewMutation.reset();
                    globalPlanMutation.reset();
                    saveHistoryMutation.reset();
                    geocodeMutation.mutate();
                  }}
                >
                  Validate & geocode
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!geocodeResult || globalPlanMutation.isPending}
                  icon={globalPlanMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Route className="h-4 w-4" />}
                  onClick={() => {
                    saveHistoryMutation.reset();
                    globalPlanMutation.mutate();
                  }}
                >
                  Build optimized plan
                </Button>
              </div>
              <details className="rounded-md border border-border bg-muted/40">
                <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">Advanced diagnostics</summary>
                <div className="space-y-3 border-t border-border p-3">
                  <p className="text-xs leading-5 text-muted-foreground">
                    Directional grouping is only a diagnostic preview. It is not used by the optimized plan.
                  </p>
                  <Field label="Grouping Granularity">
                    <select className={fieldClassName} value={sectorCount} onChange={(event) => handleSectorCountChange(Number(event.target.value) as 4 | 8 | 12)}>
                      <option value={4}>4 sectors</option>
                      <option value={8}>8 sectors</option>
                      <option value={12}>12 sectors</option>
                    </select>
                  </Field>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!geocodeResult || clusterMutation.isPending}
                    icon={clusterMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <MapPinned className="h-4 w-4" />}
                    onClick={() => {
                      routePreviewMutation.reset();
                      globalPlanMutation.reset();
                      saveHistoryMutation.reset();
                      clusterMutation.mutate();
                    }}
                  >
                    Preview groups
                  </Button>
                  <Field label="Grouped Route Direction">
                    <div className="grid grid-cols-2 gap-2">
                      <ModeButton active={routeDirection === "to_school"} onClick={() => handleRouteDirectionChange("to_school")}>
                        To School
                      </ModeButton>
                      <ModeButton active={routeDirection === "from_school"} onClick={() => handleRouteDirectionChange("from_school")}>
                        From School
                      </ModeButton>
                    </div>
                  </Field>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!clusterResult || routePreviewMutation.isPending}
                    icon={routePreviewMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Route className="h-4 w-4" />}
                    onClick={() => routePreviewMutation.mutate()}
                  >
                    Preview grouped routes
                  </Button>
                </div>
              </details>
            </CardContent>
          </Card>
          <FleetPlannerHistoryPanel
            jobs={historyQuery.data || []}
            activeRunId={loadedHistoryRecord?.run_id}
            isLoading={historyQuery.isLoading || loadHistoryMutation.isPending}
            error={(historyQuery.error || loadHistoryMutation.error) as Error | null}
            onRefresh={() => void historyQuery.refetch()}
            onOpen={(runId) => loadHistoryMutation.mutate(runId)}
          />
        </aside>
      </div>
      <FleetPlannerHowToUse open={howToUseOpen} onClose={() => setHowToUseOpen(false)} />
    </div>
  );
}

function FleetPreviewResult({
  result,
  mixRows,
  geocodeResult,
  clusterResult,
  routePreviewResult,
  globalPlanResult,
  mapOutputs,
  historyTitle,
  onHistoryTitleChange,
  onSaveHistory,
  saveHistoryResult,
  saveHistoryError,
  isSavingHistory,
  activeView,
  onActiveViewChange,
}: {
  result: FleetPlannerPreviewResponse;
  mixRows: Array<Record<string, unknown>>;
  geocodeResult?: FleetPlannerGeocodeResponse;
  clusterResult?: FleetPlannerClusterResponse;
  routePreviewResult?: FleetPlannerRoutePreviewResponse;
  globalPlanResult?: FleetPlannerRoutePreviewResponse;
  mapOutputs: ToolMapOutput[];
  historyTitle: string;
  onHistoryTitleChange: (value: string) => void;
  onSaveHistory: () => void;
  saveHistoryResult?: FleetPlannerHistoryCreateResponse;
  saveHistoryError?: Error | null;
  isSavingHistory: boolean;
  activeView: FleetResultView;
  onActiveViewChange: (view: FleetResultView) => void;
}) {
  const assumptions = result.assumptions;
  const tabs: Array<{ key: FleetResultView; label: string; badge?: string; available: boolean }> = [
    { key: "fleet", label: "Fleet preview", badge: `${formatNumber(result.summary.total_riders)} riders`, available: true },
    {
      key: "demand",
      label: "Demand input",
      badge: result.demand_workbook ? `${formatNumber(result.demand_workbook.summary.student_count)} students` : undefined,
      available: Boolean(result.demand_workbook),
    },
    {
      key: "geocode",
      label: "Address validation",
      badge: geocodeResult ? `${formatNumber(geocodeResult.summary.resolved_student_rows)} resolved` : undefined,
      available: Boolean(geocodeResult),
    },
    {
      key: "optimized",
      label: "Optimized plan",
      badge: globalPlanResult ? `${formatNumber(globalPlanResult.summary.route_count)} routes` : undefined,
      available: Boolean(globalPlanResult),
    },
    {
      key: "maps",
      label: "Maps",
      badge: mapOutputs.length ? `${formatNumber(mapOutputs.length)} maps` : undefined,
      available: mapOutputs.length > 0,
    },
    {
      key: "diagnostics",
      label: "Diagnostics",
      badge: clusterResult ? `${formatNumber(clusterResult.summary.cluster_count)} groups` : undefined,
      available: true,
    },
  ];

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Results workspace</h2>
            <p className="mt-1 text-xs text-muted-foreground">Each tab shows one stage of output so previews and final plans stay separate.</p>
          </div>
          <Badge tone={globalPlanResult ? "success" : "info"}>{globalPlanResult ? "Plan ready" : "Preview mode"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2 border-b border-border pb-3">
          {tabs.map((tab) => (
            <ResultTabButton
              key={tab.key}
              active={activeView === tab.key}
              available={tab.available}
              label={tab.label}
              badge={tab.badge}
              onClick={() => onActiveViewChange(tab.key)}
            />
          ))}
        </div>

        {activeView === "fleet" ? (
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-4">
              <Metric label="Max Route Time" value={`${formatNumber(assumptions.max_route_duration_minutes)} min`} />
              <Metric label="Max Stops" value={formatNumber(assumptions.max_stops_per_route)} />
              <Metric label="Target Load" value={formatPercent(assumptions.target_load_factor, 100)} />
              <Metric label="Min Load" value={formatPercent(assumptions.min_reasonable_load_factor, 100)} />
            </div>
            <ResultTable
              rows={result.recommendations}
              columns={["riders", "recommended_vehicle", "student_capacity", "load_factor", "empty_seats", "feasible_options", "rejected_options"]}
            />
            {mixRows.length ? (
              <div>
                <h3 className="mb-2 text-sm font-semibold">Estimated Mix</h3>
                <ResultTable rows={mixRows} columns={["vehicle", "count"]} />
              </div>
            ) : null}
          </div>
        ) : null}

        {activeView === "demand" ? (
          result.demand_workbook ? (
            <div className="space-y-4">
              <div className="grid gap-3 md:grid-cols-4">
                <Metric label="Rows" value={formatNumber(result.demand_workbook.summary.row_count)} />
                <Metric label="Students" value={formatNumber(result.demand_workbook.summary.student_count)} />
                <Metric label="Unique Addresses" value={formatNumber(result.demand_workbook.summary.unique_address_count)} />
                <Metric label="City" value={String(result.demand_workbook.summary.city || "N/A")} />
              </div>
              {result.demand_workbook.warnings.map((warning) => (
                <InlineError key={warning} message={warning} />
              ))}
              <ResultTable rows={result.demand_workbook.riders} columns={["Country", "City", "School", "Student Address", "Students", "Notes"]} />
            </div>
          ) : (
            <EmptyResultState title="No workbook preview" detail="Upload a demand workbook and run Preview fleet to review parsed rows here." />
          )
        ) : null}

        {activeView === "geocode" ? (
          geocodeResult ? (
            <DemandGeocodePreview result={geocodeResult} framed={false} />
          ) : (
            <EmptyResultState title="Address validation not run" detail="Use Validate & geocode after uploading a demand workbook." />
          )
        ) : null}

        {activeView === "optimized" ? (
          globalPlanResult ? (
            <div className="space-y-4">
              <RoutePreviewResult result={globalPlanResult} title="Optimized Plan" framed={false} />
              <FleetHistorySavePanel
                title={historyTitle}
                onTitleChange={onHistoryTitleChange}
                onSave={onSaveHistory}
                saveResult={saveHistoryResult}
                saveError={saveHistoryError}
                isSaving={isSavingHistory}
              />
            </div>
          ) : (
            <EmptyResultState title="Optimized plan not built" detail="Validate addresses first, then use Build optimized plan." />
          )
        ) : null}

        {activeView === "maps" ? (
          mapOutputs.length ? (
            <ToolMapsPanel mapOutputs={mapOutputs} />
          ) : (
            <EmptyResultState title="No maps available" detail="Run address validation or build an optimized plan to render maps here." />
          )
        ) : null}

        {activeView === "diagnostics" ? (
          <div className="space-y-4">
            {clusterResult ? <DemandClusterPreview result={clusterResult} framed={false} /> : null}
            {routePreviewResult ? <RoutePreviewResult result={routePreviewResult} title="Grouped Route Preview" framed={false} /> : null}
            {!clusterResult && !routePreviewResult ? (
              <EmptyResultState title="No diagnostic preview yet" detail="Open Advanced diagnostics and run Preview groups if you need spatial grouping diagnostics." />
            ) : null}
            <div>
              <h3 className="mb-2 text-sm font-semibold">Vehicle Catalog</h3>
              <ResultTable rows={result.catalog} columns={["vehicle", "category", "propulsion", "listed_seats", "monitor_seats", "student_capacity", "notes"]} />
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function DemandGeocodePreview({ result, framed = true }: { result: FleetPlannerGeocodeResponse; framed?: boolean }) {
  const content = (
    <>
      {framed ? (
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">Address Validation</h2>
            </div>
            <Badge tone={result.summary.failed_student_rows ? "warning" : "success"}>
              {formatNumber(result.summary.resolved_student_rows)} resolved
            </Badge>
          </div>
        </CardHeader>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Address Validation</h2>
          </div>
          <Badge tone={result.summary.failed_student_rows ? "warning" : "success"}>
            {formatNumber(result.summary.resolved_student_rows)} resolved
          </Badge>
        </div>
      )}
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-5">
          <Metric label="School" value={String(result.summary.school_status || "unknown")} />
          <Metric label="Resolved Rows" value={formatNumber(result.summary.resolved_student_rows)} />
          <Metric label="Failed Rows" value={formatNumber(result.summary.failed_student_rows)} />
          <Metric label="Resolved Students" value={formatNumber(result.summary.resolved_students)} />
          <Metric label="Cache Hits" value={formatNumber(result.summary.cache_hits)} />
        </div>
        <ResultTable
          rows={result.rows}
          columns={["Role", "Row", "Address", "Students", "Status", "Cache Hit", "Provider", "Formatted Address", "Lat", "Lng", "Warning"]}
        />
      </div>
    </>
  );
  return framed ? <Card>{content}</Card> : <div className="space-y-4">{content}</div>;
}

function DemandClusterPreview({ result, framed = true }: { result: FleetPlannerClusterResponse; framed?: boolean }) {
  const content = (
    <>
      {framed ? (
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">Demand Grouping Diagnostics</h2>
            </div>
            <Badge tone="success">{formatNumber(result.summary.cluster_count)} groups</Badge>
          </div>
        </CardHeader>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Demand Grouping Diagnostics</h2>
          </div>
          <Badge tone="success">{formatNumber(result.summary.cluster_count)} groups</Badge>
        </div>
      )}
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-5">
          <Metric label="Groups" value={formatNumber(result.summary.cluster_count)} />
          <Metric label="Resolved Points" value={formatNumber(result.summary.resolved_points)} />
          <Metric label="Resolved Students" value={formatNumber(result.summary.resolved_students)} />
          <Metric label="Failed Points" value={formatNumber(result.summary.failed_points)} />
          <Metric label="Max Capacity" value={formatNumber(result.summary.max_vehicle_student_capacity)} />
        </div>
        <ResultTable
          rows={result.rows}
          columns={[
            "Cluster",
            "Sector",
            "Students",
            "Stops",
            "Recommended Vehicle",
            "Student Capacity",
            "Load Factor",
            "Empty Seats",
            "Avg School Distance km",
            "Max School Distance km",
            "Warnings",
          ]}
        />
        {result.stop_rows.length ? (
          <details className="rounded-md border border-border bg-muted/40">
            <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">Group stop detail</summary>
            <div className="border-t border-border">
              <ResultTable
                rows={result.stop_rows}
                columns={["Cluster", "Sector", "Students", "Address", "Formatted Address", "Distance From School km", "Lat", "Lng"]}
              />
            </div>
          </details>
        ) : null}
      </div>
    </>
  );
  return framed ? <Card>{content}</Card> : <div className="space-y-4">{content}</div>;
}

function RoutePreviewResult({
  result,
  title,
  framed = true,
}: {
  result: FleetPlannerRoutePreviewResponse;
  title: string;
  framed?: boolean;
}) {
  const content = (
    <>
      {framed ? (
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Route className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">{title}</h2>
            </div>
            <Badge tone={result.refinement_note ? "warning" : "success"}>{formatNumber(result.summary.route_count)} routes</Badge>
          </div>
        </CardHeader>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Route className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{title}</h2>
          </div>
          <Badge tone={result.refinement_note ? "warning" : "success"}>{formatNumber(result.summary.route_count)} routes</Badge>
        </div>
      )}
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-5">
          <Metric label="Routes" value={formatNumber(result.summary.route_count)} />
          <Metric label="Distance" value={`${formatNumber(result.summary.total_distance_km)} km`} />
          <Metric label="Time" value={`${formatNumber(result.summary.total_duration_min)} min`} />
          <Metric label="Direction" value={result.summary.service_direction === "from_school" ? "From School" : "To School"} />
          <Metric label="Target" value={result.summary.max_route_duration_minutes ? `${formatNumber(result.summary.max_route_duration_minutes)} min` : "N/A"} />
        </div>
        {result.summary.candidate_vehicle_count || result.summary.solver ? (
          <div className="grid gap-3 md:grid-cols-2">
            <Metric label="Candidates" value={formatNumber(result.summary.candidate_vehicle_count)} />
            <Metric label="Solver" value={String(result.summary.solver || "global_ortools")} />
          </div>
        ) : null}
        {result.refinement_note ? <InlineError message={result.refinement_note} /> : null}
        {result.workbook_base64 ? (
          <Button
            type="button"
            variant="secondary"
            icon={<Download className="h-4 w-4" />}
            onClick={() => downloadBase64Workbook(result.workbook_base64 || "", result.workbook_file_name || "fleet_planner_generated_plan.xlsx")}
          >
            Download workbook
          </Button>
        ) : null}
        <ResultTable
          rows={result.rows}
          columns={[
            "cluster_id",
            "solver",
            "service_direction",
            "students",
            "stops",
            "vehicle",
            "distance_km",
            "duration_min",
            "load_factor_pct",
            "warnings",
          ]}
        />
        {result.stop_rows.length ? (
          <details className="rounded-md border border-border bg-muted/40">
            <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">Route stop detail</summary>
            <div className="border-t border-border">
              <ResultTable
                rows={result.stop_rows}
                columns={[
                  "route_id",
                  "stop_sequence",
                  "bus_type",
                  "country",
                  "city",
                  "address",
                  "formatted_address",
                  "passenger_count",
                  "lat",
                  "lng",
                ]}
              />
            </div>
          </details>
        ) : null}
      </div>
    </>
  );
  return framed ? <Card>{content}</Card> : <div className="space-y-4">{content}</div>;
}

function FleetHistorySavePanel({
  title,
  onTitleChange,
  onSave,
  saveResult,
  saveError,
  isSaving,
}: {
  title: string;
  onTitleChange: (value: string) => void;
  onSave: () => void;
  saveResult?: FleetPlannerHistoryCreateResponse;
  saveError?: Error | null;
  isSaving: boolean;
}) {
  return (
    <div className="space-y-3 rounded-md border border-border bg-muted/40 p-3">
      <Field label="History Name">
        <input
          className={fieldClassName}
          value={title}
          placeholder={defaultFleetHistoryTitle()}
          onChange={(event) => onTitleChange(event.target.value)}
        />
      </Field>
      {saveError ? <InlineError message={saveError.message} /> : null}
      {saveResult?.job ? <Badge tone="success">Saved to Fleet Planner History</Badge> : null}
      <Button
        type="button"
        disabled={isSaving}
        icon={isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        onClick={onSave}
      >
        Save to Fleet Planner History
      </Button>
    </div>
  );
}

function FleetPlannerHistoryPanel({
  jobs,
  activeRunId,
  isLoading,
  error,
  onRefresh,
  onOpen,
}: {
  jobs: FleetPlannerHistorySummary[];
  activeRunId?: string;
  isLoading: boolean;
  error?: Error | null;
  onRefresh: () => void;
  onOpen: (runId: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Fleet Planner History</h2>
          </div>
          <button type="button" className={buttonClassName("ghost")} aria-label="Refresh Fleet Planner history" onClick={onRefresh}>
            <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} aria-hidden="true" />
          </button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {error ? <InlineError message={error.message} /> : null}
        {!jobs.length && !isLoading ? (
          <div className="rounded-md border border-dashed border-border bg-muted/40 px-3 py-4 text-sm text-muted-foreground">
            Saved Fleet Planner runs will appear here.
          </div>
        ) : null}
        <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
          {jobs.map((job) => {
            const summary = job.summary || {};
            return (
              <button
                key={job.run_id}
                type="button"
                className={cn(
                  "w-full rounded-md border px-3 py-3 text-left transition",
                  activeRunId === job.run_id ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface hover:bg-muted",
                )}
                onClick={() => onOpen(job.run_id)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold">{job.title || "Fleet Planner Run"}</div>
                    <div className={cn("mt-1 text-xs", activeRunId === job.run_id ? "text-primary-foreground/80" : "text-muted-foreground")}>
                      {formatDateTime(job.created_at)}
                    </div>
                  </div>
                  <Badge tone={activeRunId === job.run_id ? "neutral" : "success"}>{formatNumber(summary.routes)} routes</Badge>
                </div>
                <div className={cn("mt-2 grid grid-cols-2 gap-1 text-xs", activeRunId === job.run_id ? "text-primary-foreground/80" : "text-muted-foreground")}>
                  <span>{formatNumber(summary.students)} students</span>
                  <span>{formatNumber(summary.total_distance_km)} km</span>
                </div>
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function ToolMapsPanel({ mapOutputs }: { mapOutputs: ToolMapOutput[] }) {
  const [selectedKey, setSelectedKey] = useState("");
  const selected = mapOutputs.find((item) => item.key === selectedKey) || mapOutputs[0];

  if (!selected) {
    return <EmptyResultState title="No maps available" detail="This Fleet Planner run has no rendered maps." />;
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Map className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{selected.name}</h2>
          </div>
          {mapOutputs.length > 1 ? <Badge tone="info">{formatNumber(mapOutputs.length)} maps</Badge> : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {mapOutputs.length > 1 ? (
          <div className="flex flex-wrap gap-2">
            {mapOutputs.map((item) => (
              <button
                key={item.key}
                type="button"
                className={cn(
                  "h-9 rounded-md border px-3 text-sm font-medium transition",
                  selected.key === item.key
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-surface text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                onClick={() => setSelectedKey(item.key)}
              >
                {item.name}
              </button>
            ))}
          </div>
        ) : null}
        <div className="overflow-hidden rounded-md border border-border bg-muted">
          <iframe
            key={selected.key}
            title={selected.name}
            srcDoc={selected.html}
            sandbox="allow-scripts allow-same-origin"
            className="h-[720px] min-h-[560px] max-h-[75vh] w-full border-0"
          />
        </div>
      </CardContent>
    </Card>
  );
}

function FleetPlannerHowToUse({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) {
    return null;
  }
  return (
    <div className="fixed inset-0 z-40">
      <button type="button" className="absolute inset-0 bg-slate-950/20" aria-label="Close how to use" onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col border-l border-border bg-surface shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">How to use Fleet Planner</h2>
            <p className="mt-1 text-xs text-muted-foreground">Use the main actions first; diagnostics are optional.</p>
          </div>
          <button type="button" className={buttonClassName("ghost")} aria-label="Close how to use" onClick={onClose}>
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="space-y-5 overflow-y-auto px-4 py-4 text-sm leading-6 text-muted-foreground">
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">Main flow</h3>
            <ol className="list-decimal space-y-2 pl-5">
              <li>Upload a demand workbook or enter manual rider groups.</li>
              <li>Use Preview fleet to check vehicle recommendations and active assumptions.</li>
              <li>Use Validate & geocode to resolve school and pickup locations.</li>
              <li>Use Build optimized plan to run the full solver and generate routes.</li>
            </ol>
          </section>
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">Scenario settings</h3>
            <p>Market selects vehicle catalog and local routing assumptions. Planning Mode changes how tightly vehicles are filled. Bus Monitor Seats reserves adult seats before student capacity is calculated.</p>
            <p>Service Direction controls pickup/drop-off order for the optimized plan.</p>
          </section>
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">Advanced diagnostics</h3>
            <p>Directional grouping previews demand distribution around the school. It is not used by the optimized plan.</p>
            <p>Grouping Granularity only affects that diagnostic preview. If most students are in one direction, the optimized solver can still split them into multiple routes based on capacity, travel time, and distance.</p>
          </section>
        </div>
      </aside>
    </div>
  );
}

function Field({ label, description, children }: { label: string; description?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {description ? <span className="block text-xs leading-5 text-muted-foreground">{description}</span> : null}
      {children}
    </label>
  );
}

function ModeButton({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      className={cn(
        "h-9 rounded-md border px-3 text-sm font-medium transition",
        active ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface text-foreground hover:bg-muted",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function ResultTabButton({
  active,
  available,
  label,
  badge,
  onClick,
}: {
  active: boolean;
  available: boolean;
  label: string;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-medium transition",
        active ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface text-foreground hover:bg-muted",
        !available && "opacity-70",
      )}
      onClick={onClick}
    >
      <span>{label}</span>
      {badge ? (
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[11px] leading-none",
            active ? "border-primary-foreground/50 text-primary-foreground" : "border-border text-muted-foreground",
          )}
        >
          {badge}
        </span>
      ) : null}
    </button>
  );
}

function EmptyResultState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-md border border-dashed border-border bg-muted/40 px-4 py-8 text-center">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground">{detail}</p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-muted/50 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function ResultTable({ rows, columns }: { rows: Array<Record<string, unknown>>; columns: string[] }) {
  if (!rows.length || !columns.length) {
    return null;
  }
  return (
    <div className="overflow-auto rounded-md border border-border">
      <table className="min-w-full divide-y divide-border text-left text-sm">
        <thead className="bg-muted text-xs text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.slice(0, 80).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="max-w-80 truncate px-3 py-2">
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{message}</div>;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "number") {
    if (value > 0 && value < 1) {
      return formatPercent(value, 100);
    }
    return formatNumber(value);
  }
  return String(value);
}

async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = reader.result;
      if (typeof value !== "string") {
        reject(new Error("Workbook could not be read as base64."));
        return;
      }
      resolve(value);
    };
    reader.onerror = () => reject(reader.error || new Error("Workbook could not be read."));
    reader.readAsDataURL(file);
  });
}

function collectFleetMapOutputs({
  geocodeResult,
  clusterResult,
  routePreviewResult,
  globalPlanResult,
}: {
  geocodeResult?: FleetPlannerGeocodeResponse;
  clusterResult?: FleetPlannerClusterResponse;
  routePreviewResult?: FleetPlannerRoutePreviewResponse;
  globalPlanResult?: FleetPlannerRoutePreviewResponse;
}) {
  return [
    { key: "address-validation", name: "Address Validation", html: geocodeResult?.map_html || "" },
    { key: "demand-groups", name: "Demand Groups", html: clusterResult?.map_html || "" },
    { key: "grouped-routes", name: "Grouped Route Preview", html: routePreviewResult?.map_html || "" },
    { key: "optimized-plan", name: "Optimized Plan", html: globalPlanResult?.map_html || "" },
  ].filter((item) => item.html.trim()) as ToolMapOutput[];
}

function formatDateTime(value: unknown) {
  const text = String(value || "").trim();
  if (!text) {
    return "Unknown time";
  }
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) {
    return text;
  }
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function defaultFleetHistoryTitle() {
  const now = new Date();
  const date = now.toISOString().slice(0, 10);
  const time = `${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}`;
  return `Fleet Planner Run - ${date} ${time}`;
}

function downloadBase64Workbook(base64Value: string, fileName: string) {
  const binary = atob(base64Value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const blob = new Blob([bytes], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}
