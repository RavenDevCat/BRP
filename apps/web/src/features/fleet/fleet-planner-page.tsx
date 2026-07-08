import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Bus, CircleHelp, Download, FileSpreadsheet, History, Loader2, Map, MapPinned, Maximize2, Plus, RefreshCw, RotateCcw, Route, SlidersHorizontal, Trash2, Upload, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  buildFleetPlannerClusters,
  buildFleetPlannerGlobalPlan,
  buildFleetPlannerRoutePreview,
  deleteFleetPlannerHistory,
  geocodeFleetPlannerDemand,
  getFleetPlannerHistory,
  getFleetPlannerVehicleCatalog,
  getCurrentUser,
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
  type FleetPlannerVehicleConfig,
  type JobMapData,
} from "@/lib/api";
import { InteractiveRouteMap } from "@/features/results/interactive-route-map";
import { downloadInteractiveMapHtml } from "@/features/results/job-result-view";
import { formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/cn";
import { useT } from "@/lib/i18n/context";

const fieldClassName =
  "h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none transition focus:border-primary";
const ROUTE_TIME_TARGET_PRESETS = [30, 45, 60, 75] as const;
const VEHICLE_CATEGORIES = ["van", "mini_bus", "mid_bus", "large_bus"] as const;
const VEHICLE_PROPULSIONS = ["diesel", "electric"] as const;

type FleetResultView = "plan" | "map" | "review";
type FleetMarket = "KR" | "CN";
type FleetMode = "balanced" | "cost_saver" | "comfort_saver";
type FleetServiceDirection = "to_school" | "from_school";
type FleetVehicleCategory = (typeof VEHICLE_CATEGORIES)[number];
type FleetVehiclePropulsion = (typeof VEHICLE_PROPULSIONS)[number];
type FleetVehicleConfigDraft = FleetPlannerVehicleConfig & { id: string };
type VehicleProfileMode = "default" | "manual";
type FleetVehicleCatalogInput = Partial<FleetPlannerVehicleConfig> & { vehicle?: unknown; [key: string]: unknown };
type ToolMapOutput = {
  key: string;
  name: string;
  mapData?: JobMapData;
};
type PreviewVariables = {
  file?: File | null;
  fileBase64?: string;
  market?: FleetMarket;
};

export function FleetPlannerPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const [market, setMarket] = useState<FleetMarket>("KR");
  const [mode, setMode] = useState<FleetMode>("balanced");
  const [monitorSeats, setMonitorSeats] = useState(1);
  const [routeTimeTargetMinutes, setRouteTimeTargetMinutes] = useState(60);
  const riderCounts = "8, 22, 34, 44";
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [sectorCount, setSectorCount] = useState<4 | 8 | 12>(8);
  const [routeDirection, setRouteDirection] = useState<FleetServiceDirection>("to_school");
  const [globalDirection, setGlobalDirection] = useState<FleetServiceDirection>("to_school");
  const [historyTitle, setHistoryTitle] = useState("");
  const [defaultVehicleConfigsDraft, setDefaultVehicleConfigsDraft] = useState<FleetVehicleConfigDraft[]>([]);
  const [defaultVehicleCatalogEdited, setDefaultVehicleCatalogEdited] = useState(false);
  const [manualVehicleConfigs, setManualVehicleConfigs] = useState<FleetVehicleConfigDraft[]>([]);
  const [vehicleProfileMode, setVehicleProfileMode] = useState<VehicleProfileMode>("default");
  const [vehicleConfigOpen, setVehicleConfigOpen] = useState(false);
  const [loadedHistoryRecord, setLoadedHistoryRecord] = useState<FleetPlannerHistoryRecord | null>(null);
  const [howToUseOpen, setHowToUseOpen] = useState(false);
  const [fleetSettingHelpOpen, setFleetSettingHelpOpen] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const [activeResultView, setActiveResultView] = useState<FleetResultView>("review");
  const historyPanelRef = useRef<HTMLDivElement | null>(null);

  const currentUserQuery = useQuery({ queryKey: ["me"], queryFn: getCurrentUser, staleTime: 60_000 });

  const historyQuery = useQuery({
    queryKey: ["fleet-planner-history"],
    queryFn: listFleetPlannerHistory,
    staleTime: 15_000,
  });

  const vehicleCatalogQuery = useQuery({
    queryKey: ["fleet-planner-vehicle-catalog", market, monitorSeats],
    queryFn: () => getFleetPlannerVehicleCatalog({ market, monitor_seats: monitorSeats }),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (historyCollapsed) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      if (window.innerWidth < 1024) {
        return;
      }
      const target = event.target;
      if (target instanceof Node && historyPanelRef.current?.contains(target)) {
        return;
      }
      setHistoryCollapsed(true);
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [historyCollapsed]);

  const defaultVehicleConfigs = useMemo(
    () => normalizeVehicleConfigDrafts(vehicleCatalogQuery.data?.catalog || [], market),
    [market, vehicleCatalogQuery.data],
  );
  const defaultModeVehicleConfigs = defaultVehicleCatalogEdited ? defaultVehicleConfigsDraft : defaultVehicleConfigs;
  const activeVehicleConfigs = vehicleProfileMode === "manual" ? manualVehicleConfigs : defaultModeVehicleConfigs;
  const activeVehicleCatalogPayload = useMemo(
    () => fleetVehiclePayloadFromDrafts(activeVehicleConfigs),
    [activeVehicleConfigs],
  );
  const enabledVehicleCatalogCount = activeVehicleCatalogPayload.filter((vehicle) => vehicle.enabled && vehicle.available_count > 0).length;
  const isCustomVehicleCatalog = vehicleProfileMode === "manual" || defaultVehicleCatalogEdited;
  const customVehicleCatalogPayload = isCustomVehicleCatalog ? activeVehicleCatalogPayload : undefined;

  const previewMutation = useMutation({
    mutationFn: async (variables?: PreviewVariables) => {
      const selectedFile = variables?.file === undefined ? file : variables.file;
      const selectedFileBase64 = variables?.fileBase64 ?? fileBase64;
      const selectedMarket = variables?.market ?? market;
      return (
      previewFleetPlanner({
        market: selectedMarket,
        mode,
        monitor_seats: monitorSeats,
        max_route_duration_minutes: routeTimeTargetMinutes,
        vehicle_catalog: customVehicleCatalogPayload,
        rider_counts: riderCounts,
        file_name: selectedFile?.name,
        file_base64: selectedFileBase64 || undefined,
      })
      );
    },
    onSuccess: (preview, variables) => {
      const inferredMarket = inferMarketFromDemandWorkbook(preview.demand_workbook);
      const selectedMarket = variables?.market ?? market;
      if (inferredMarket && inferredMarket !== selectedMarket) {
        setMarket(inferredMarket);
        setVehicleProfileMode("default");
        setDefaultVehicleCatalogEdited(false);
        setDefaultVehicleConfigsDraft([]);
        setManualVehicleConfigs([]);
        geocodeMutation.reset();
        clusterMutation.reset();
        routePreviewMutation.reset();
        globalPlanMutation.reset();
        saveHistoryMutation.reset();
        setLoadedHistoryRecord(null);
        previewMutation.mutate({
          ...variables,
          file: variables?.file === undefined ? file : variables.file,
          fileBase64: variables?.fileBase64 ?? fileBase64,
          market: inferredMarket,
        });
        return;
      }
      setLoadedHistoryRecord(null);
      setActiveResultView("review");
    },
  });

  const geocodeMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64) {
        throw new Error(t("Upload a demand workbook before running geocode preview."));
      }
      return geocodeFleetPlannerDemand({
        file_name: file.name,
        file_base64: fileBase64,
      });
    },
    onSuccess: () => {
      setLoadedHistoryRecord(null);
      setActiveResultView("review");
    },
  });

  const clusterMutation = useMutation({
    mutationFn: async () => {
      const geocodeResult = geocodeMutation.data;
      if (!geocodeResult) {
        throw new Error(t("Run demand geocode before building clusters."));
      }
      return buildFleetPlannerClusters({
        market,
        mode,
        monitor_seats: monitorSeats,
        max_route_duration_minutes: routeTimeTargetMinutes,
        vehicle_catalog: customVehicleCatalogPayload,
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
      setActiveResultView("review");
    },
  });

  const routePreviewMutation = useMutation({
    mutationFn: async () => {
      const clusterResult = clusterMutation.data;
      if (!clusterResult) {
        throw new Error(t("Build clusters before running route preview."));
      }
      return buildFleetPlannerRoutePreview({
        market,
        mode,
        monitor_seats: monitorSeats,
        service_direction: routeDirection,
        max_route_duration_minutes: routeTimeTargetMinutes,
        vehicle_catalog: customVehicleCatalogPayload,
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
      setActiveResultView("review");
    },
  });

  const globalPlanMutation = useMutation({
    mutationFn: async () => {
      const geocodeResult = geocodeMutation.data;
      if (!geocodeResult) {
        throw new Error(t("Run demand geocode before building a global plan."));
      }
      return buildFleetPlannerGlobalPlan({
        market,
        mode,
        monitor_seats: monitorSeats,
        max_route_duration_minutes: routeTimeTargetMinutes,
        vehicle_catalog: customVehicleCatalogPayload,
        service_direction: globalDirection,
        geocode_result: {
          school: geocodeResult.school,
          demand_points: geocodeResult.demand_points,
          summary: geocodeResult.summary,
        },
      });
    },
    onSuccess: (globalPlan) => {
      setLoadedHistoryRecord(null);
      setActiveResultView("map");
      saveHistoryMutation.reset();
      saveHistoryMutation.mutate(globalPlan);
    },
  });

  const saveHistoryMutation = useMutation({
    mutationFn: async (globalPlanOverride?: FleetPlannerRoutePreviewResponse) => {
      if (!result) {
        throw new Error(t("Run Fleet preview before saving history."));
      }
      const planToSave = globalPlanOverride || globalPlanResult;
      if (!planToSave) {
        throw new Error(t("Build an optimized plan before saving history."));
      }
      return saveFleetPlannerHistory({
        title: historyTitle.trim() || defaultFleetHistoryTitle(),
        scenario: {
          market,
          mode,
          monitor_seats: monitorSeats,
          max_route_duration_minutes: routeTimeTargetMinutes,
          vehicle_catalog_source: isCustomVehicleCatalog ? "custom" : "default",
          vehicle_catalog_count: enabledVehicleCatalogCount,
          vehicle_catalog_snapshot: activeVehicleCatalogPayload,
          service_direction: globalDirection,
        },
        preview_result: result,
        geocode_result: geocodeResult,
        cluster_result: clusterResult,
        route_preview_result: routePreviewResult,
        global_plan_result: planToSave,
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
      applyHistoryScenario(record, {
        setMarket,
        setMode,
        setMonitorSeats,
        setRouteTimeTargetMinutes,
        setVehicleProfileMode,
        setDefaultVehicleCatalogEdited,
        setDefaultVehicleConfigsDraft,
        setManualVehicleConfigs,
        setRouteDirection,
        setGlobalDirection,
      });
      setActiveResultView("map");
    },
  });

  const deleteHistoryMutation = useMutation({
    mutationFn: (runId: string) => deleteFleetPlannerHistory(runId),
    onSuccess: async (payload) => {
      if (loadedHistoryRecord?.run_id === payload.run_id || saveHistoryMutation.data?.job.run_id === payload.run_id) {
        setLoadedHistoryRecord(null);
        saveHistoryMutation.reset();
        setHistoryTitle(defaultFleetHistoryTitle());
        setActiveResultView("review");
      }
      await queryClient.invalidateQueries({ queryKey: ["fleet-planner-history"] });
    },
  });
  const bulkDeleteHistoryMutation = useMutation({
    mutationFn: async (runIds: string[]) => {
      for (const runId of runIds) {
        await deleteFleetPlannerHistory(runId);
      }
      return runIds;
    },
    onSuccess: async (runIds) => {
      const deletedSet = new Set(runIds);
      if (
        deletedSet.has(loadedHistoryRecord?.run_id || "") ||
        deletedSet.has(saveHistoryMutation.data?.job.run_id || "")
      ) {
        setLoadedHistoryRecord(null);
        saveHistoryMutation.reset();
        setHistoryTitle(defaultFleetHistoryTitle());
        setActiveResultView("review");
      }
      await queryClient.invalidateQueries({ queryKey: ["fleet-planner-history"] });
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
    setActiveResultView("review");
  }

  function resetClusterResults() {
    clusterMutation.reset();
    routePreviewMutation.reset();
    globalPlanMutation.reset();
    saveHistoryMutation.reset();
    setLoadedHistoryRecord(null);
    setActiveResultView("review");
  }

  function resetRoutePreviewResults() {
    routePreviewMutation.reset();
  }

  function resetGlobalPlanResults() {
    globalPlanMutation.reset();
    saveHistoryMutation.reset();
    setLoadedHistoryRecord(null);
    setActiveResultView("review");
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
      setFileError(t("Use an .xlsx demand workbook."));
      return;
    }
    try {
      const encoded = await fileToBase64(nextFile);
      setFileBase64(encoded);
      previewMutation.mutate({ file: nextFile, fileBase64: encoded });
    } catch {
      setFileError(t("Workbook could not be read."));
    }
  }

  const result = previewMutation.data || loadedHistoryRecord?.preview_result;
  const demandWorkbook = result?.demand_workbook || null;
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

  function handleMarketChange(nextMarket: FleetMarket) {
    if (nextMarket === market) {
      return;
    }
    setMarket(nextMarket);
    setVehicleProfileMode("default");
    setDefaultVehicleCatalogEdited(false);
    setDefaultVehicleConfigsDraft([]);
    setManualVehicleConfigs([]);
    resetScenarioResults();
  }

  function handleModeChange(nextMode: FleetMode) {
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

  function handleRouteTimeTargetChange(nextMinutes: number) {
    const normalizedTarget = normalizeRouteTimeTarget(nextMinutes);
    if (normalizedTarget === routeTimeTargetMinutes) {
      return;
    }
    setRouteTimeTargetMinutes(normalizedTarget);
    resetScenarioResults();
  }

  function handleVehicleConfigsChange(nextConfigs: FleetVehicleConfigDraft[]) {
    if (vehicleProfileMode === "manual") {
      setManualVehicleConfigs(nextConfigs);
    } else {
      setDefaultVehicleConfigsDraft(nextConfigs);
      setDefaultVehicleCatalogEdited(true);
    }
    resetScenarioResults();
  }

  function handleVehicleProfileModeChange(nextMode: VehicleProfileMode) {
    if (nextMode === vehicleProfileMode) {
      return;
    }
    setVehicleProfileMode(nextMode);
    resetScenarioResults();
  }

  function handleVehicleCatalogReset() {
    if (vehicleProfileMode === "manual") {
      setManualVehicleConfigs([]);
    } else {
      setDefaultVehicleCatalogEdited(false);
      setDefaultVehicleConfigsDraft([]);
    }
    resetScenarioResults();
  }

  function handleSectorCountChange(nextSectorCount: 4 | 8 | 12) {
    if (nextSectorCount === sectorCount) {
      return;
    }
    setSectorCount(nextSectorCount);
    resetClusterResults();
  }

  function handleRouteDirectionChange(nextDirection: FleetServiceDirection) {
    if (nextDirection === routeDirection) {
      return;
    }
    setRouteDirection(nextDirection);
    resetRoutePreviewResults();
  }

  function handleGlobalDirectionChange(nextDirection: FleetServiceDirection) {
    if (nextDirection === globalDirection) {
      return;
    }
    setGlobalDirection(nextDirection);
    resetGlobalPlanResults();
  }

  return (
    <div className="pb-16 lg:pb-0">
      <div
        className={cn(
          "grid gap-4 lg:items-start",
          historyCollapsed ? "lg:grid-cols-[88px_minmax(0,1fr)]" : "lg:grid-cols-[320px_minmax(0,1fr)]",
        )}
      >
        <div ref={historyPanelRef} className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <FleetPlannerHistoryPanel
            className="min-w-0"
            jobs={historyQuery.data || []}
            activeRunId={loadedHistoryRecord?.run_id || saveHistoryMutation.data?.job.run_id}
            isLoading={historyQuery.isLoading || loadHistoryMutation.isPending || deleteHistoryMutation.isPending || bulkDeleteHistoryMutation.isPending}
            error={(historyQuery.error || loadHistoryMutation.error || deleteHistoryMutation.error || bulkDeleteHistoryMutation.error) as Error | null}
            collapsed={historyCollapsed}
            onCollapsedChange={setHistoryCollapsed}
            onRefresh={() => void historyQuery.refetch()}
            onOpen={(runId) => {
              setHistoryCollapsed(true);
              loadHistoryMutation.mutate(runId);
            }}
            onDelete={(runId) => deleteHistoryMutation.mutate(runId)}
            onBulkDelete={(runIds) => bulkDeleteHistoryMutation.mutate(runIds)}
            deletingRunId={deleteHistoryMutation.variables}
            bulkDeleting={bulkDeleteHistoryMutation.isPending}
            canDeleteShared={Boolean(currentUserQuery.data?.is_admin)}
          />
        </div>

        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-start">
                <div>
                  <p className="text-sm font-medium text-primary">{t("Side tools")}</p>
                  <h1 className="mt-1 text-2xl font-semibold tracking-normal text-foreground">{t("Fleet Planner Preview")}</h1>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                    {t("Create a demand-based fleet plan, validate addresses, and save the optimized result to Fleet Planner History.")}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <button type="button" className={buttonClassName("secondary")} onClick={() => setHowToUseOpen(true)}>
                    <CircleHelp className="h-4 w-4" aria-hidden="true" />
                    {t("How to use")}
                  </button>
                  <a href={getDemandTemplateUrl()} className={buttonClassName("secondary")}>
                    <Download className="h-4 w-4" aria-hidden="true" />
                    {t("Demand template")}
                  </a>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_28px_minmax(0,1fr)_28px_minmax(0,1fr)]">
                <section className="flex h-full flex-col rounded-md border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Demand source")}</h2>
                  </div>
                  <div className="flex flex-1 flex-col justify-center space-y-3 pt-3">
                    <label className="flex min-h-20 cursor-pointer items-center justify-center gap-3 rounded-md border border-dashed border-border bg-surface px-4 py-4 text-center transition hover:border-primary/60 hover:bg-muted">
                      <Upload className="h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
                      <span className="min-w-0 text-left">
                        <span className="block truncate text-sm font-medium">{file?.name || t("Select demand workbook")}</span>
                        <span className="mt-1 block text-xs text-muted-foreground">{t("Upload an .xlsx demand workbook to parse city, school, address count, and students.")}</span>
                      </span>
                      <input
                        className="sr-only"
                        type="file"
                        accept=".xlsx"
                        onChange={(event) => void handleFileChange(event.target.files?.[0] || null)}
                      />
                    </label>
                    {fileError ? <InlineError message={fileError} /> : null}
                    <DemandWorkbookSummaryCard workbook={demandWorkbook} fileName={file?.name} isLoading={previewMutation.isPending && Boolean(file)} />
                    <Field label="Job Name">
                      <input
                        className={fieldClassName}
                        value={historyTitle}
                        placeholder={defaultFleetHistoryTitle()}
                        onChange={(event) => setHistoryTitle(event.target.value)}
                      />
                    </Field>
                    <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-1">
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
                            {t("To School")}
                          </ModeButton>
                          <ModeButton active={globalDirection === "from_school"} onClick={() => handleGlobalDirectionChange("from_school")}>
                            {t("From School")}
                          </ModeButton>
                        </div>
                      </Field>
                    </div>
                  </div>
                </section>

                <SetupFlowArrow />

                <section className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <SlidersHorizontal className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Run settings")}</h2>
                  </div>
                  <div className="block space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-muted-foreground">{t("Fleet Setting")}</span>
                      <button
                        type="button"
                        className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface text-muted-foreground transition hover:border-primary/50 hover:text-primary"
                        aria-label={t("Fleet Setting guide")}
                        title={t("Fleet Setting guide")}
                        onClick={() => setFleetSettingHelpOpen(true)}
                      >
                        <CircleHelp className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <ModeButton active={market === "KR"} onClick={() => handleMarketChange("KR")}>
                        KR
                      </ModeButton>
                      <ModeButton active={market === "CN"} onClick={() => handleMarketChange("CN")}>
                        CN
                      </ModeButton>
                    </div>
                  </div>
                  <VehicleProfileSummary
                    configs={activeVehicleConfigs}
                    mode={vehicleProfileMode}
                    edited={isCustomVehicleCatalog}
                    isLoading={vehicleCatalogQuery.isLoading}
                    error={vehicleCatalogQuery.error as Error | null}
                    onManage={() => setVehicleConfigOpen(true)}
                    onReset={handleVehicleCatalogReset}
                  />
                  <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-1">
                    <Field label="Planning Mode">
                      <select className={fieldClassName} value={mode} onChange={(event) => handleModeChange(event.target.value as typeof mode)}>
                        <option value="balanced">{t("Balanced")}</option>
                        <option value="cost_saver">{t("Cost Saver")}</option>
                        <option value="comfort_saver">{t("Comfort Saver")}</option>
                      </select>
                    </Field>
                    <Field label="Route Time Target">
                      <RouteTimeTargetControl value={routeTimeTargetMinutes} onChange={handleRouteTimeTargetChange} />
                    </Field>
                  </div>
                </section>

                <SetupFlowArrow />

                <section className="flex h-full flex-col rounded-md border border-border bg-muted/30 p-3">
                  <div className="flex items-center gap-2">
                    <Route className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Run workflow")}</h2>
                  </div>
                  <div className="flex flex-1 flex-col justify-center space-y-3 pt-3">
                    {previewMutation.error ? <InlineError message={(previewMutation.error as Error).message} /> : null}
                    {geocodeMutation.error ? <InlineError message={(geocodeMutation.error as Error).message} /> : null}
                    {clusterMutation.error ? <InlineError message={(clusterMutation.error as Error).message} /> : null}
                    {routePreviewMutation.error ? <InlineError message={(routePreviewMutation.error as Error).message} /> : null}
                    {globalPlanMutation.error ? <InlineError message={(globalPlanMutation.error as Error).message} /> : null}
                    <WorkflowAction
                      step="1"
                      title="Preview fleet"
                      description="Check vehicle choices from the uploaded workbook."
                      disabled={!fileBase64 || previewMutation.isPending}
                      pending={previewMutation.isPending}
                      icon={<Bus className="h-4 w-4" />}
                      onClick={() => previewMutation.mutate(undefined)}
                    />
                    <WorkflowAction
                      step="2"
                      title="Validate & geocode"
                      description="Resolve addresses and prepare map-ready demand points."
                      disabled={!fileBase64 || geocodeMutation.isPending}
                      pending={geocodeMutation.isPending}
                      icon={<MapPinned className="h-4 w-4" />}
                      variant="secondary"
                      onClick={() => {
                        clusterMutation.reset();
                        routePreviewMutation.reset();
                        globalPlanMutation.reset();
                        saveHistoryMutation.reset();
                        geocodeMutation.mutate();
                      }}
                    />
                    <WorkflowAction
                      step="3"
                      title="Build optimized plan"
                      description="Create routes within the time target and auto-save the run to history."
                      disabled={!geocodeResult || !result || globalPlanMutation.isPending || saveHistoryMutation.isPending}
                      pending={globalPlanMutation.isPending || saveHistoryMutation.isPending}
                      icon={<Route className="h-4 w-4" />}
                      variant="secondary"
                      onClick={() => {
                        saveHistoryMutation.reset();
                        globalPlanMutation.mutate();
                      }}
                    />
                  </div>
                </section>
              </div>

              <details className="rounded-md border border-border bg-muted/40">
                <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">{t("Advanced diagnostics")}</summary>
                <div className="grid gap-3 border-t border-border p-3 lg:grid-cols-[minmax(0,1fr)_minmax(220px,280px)_minmax(220px,280px)]">
                  <p className="text-xs leading-5 text-muted-foreground">
                    {t("Directional grouping is only a diagnostic preview. It is not used by the optimized plan.")}
                  </p>
                  <Field label="Grouping Granularity">
                    <select className={fieldClassName} value={sectorCount} onChange={(event) => handleSectorCountChange(Number(event.target.value) as 4 | 8 | 12)}>
                      <option value={4}>{t("4 sectors")}</option>
                      <option value={8}>{t("8 sectors")}</option>
                      <option value={12}>{t("12 sectors")}</option>
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
                    {t("Preview groups")}
                  </Button>
                  <div className="lg:col-start-2">
                    <Field label="Grouped Route Direction">
                      <div className="grid grid-cols-2 gap-2">
                        <ModeButton active={routeDirection === "to_school"} onClick={() => handleRouteDirectionChange("to_school")}>
                          {t("To School")}
                        </ModeButton>
                        <ModeButton active={routeDirection === "from_school"} onClick={() => handleRouteDirectionChange("from_school")}>
                          {t("From School")}
                        </ModeButton>
                      </div>
                    </Field>
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!clusterResult || routePreviewMutation.isPending}
                    icon={routePreviewMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Route className="h-4 w-4" />}
                    onClick={() => routePreviewMutation.mutate()}
                  >
                    {t("Preview grouped routes")}
                  </Button>
                </div>
              </details>
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
              saveHistoryResult={saveHistoryMutation.data}
              saveHistoryError={saveHistoryMutation.error as Error | null}
              isSavingHistory={saveHistoryMutation.isPending}
              historyRecord={loadedHistoryRecord}
              activeView={activeResultView}
              onActiveViewChange={setActiveResultView}
            />
          ) : (
            <EmptyResultState title="No result selected" detail="Run Fleet preview or open a saved Fleet Planner history item." />
          )}
        </div>
      </div>
      <FleetPlannerHowToUse open={howToUseOpen} onClose={() => setHowToUseOpen(false)} />
      <FleetSettingGuide open={fleetSettingHelpOpen} onClose={() => setFleetSettingHelpOpen(false)} />
      <VehicleConfigModal
        open={vehicleConfigOpen}
        market={market}
        configs={activeVehicleConfigs}
        mode={vehicleProfileMode}
        edited={isCustomVehicleCatalog}
        onModeChange={handleVehicleProfileModeChange}
        onChange={handleVehicleConfigsChange}
        onReset={handleVehicleCatalogReset}
        onClose={() => setVehicleConfigOpen(false)}
      />
    </div>
  );
}

function DemandWorkbookSummaryCard({
  workbook,
  fileName,
  isLoading,
}: {
  workbook?: NonNullable<FleetPlannerPreviewResponse["demand_workbook"]> | null;
  fileName?: string;
  isLoading: boolean;
}) {
  const t = useT();
  if (isLoading) {
    return (
      <div className="flex min-h-24 items-center justify-center rounded-md border border-border bg-surface px-3 py-4 text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin text-primary" aria-hidden="true" />
        {t("Parsing workbook")}
      </div>
    );
  }

  if (!workbook) {
    return (
      <div className="rounded-md border border-dashed border-border bg-surface px-3 py-4 text-sm text-muted-foreground">
        {t("Upload a demand workbook to review its parsed city, school, address count, and total students.")}
      </div>
    );
  }

  const summary = workbook.summary || {};
  const school = workbook.school || {};
  const city = String(summary.city || school.city || "N/A");
  const schoolName = String(summary.school_name || school.school_name || "N/A");
  const addressCount = summary.unique_address_count ?? summary.row_count;
  const studentCount = summary.student_count;

  return (
    <div className="space-y-3 rounded-md border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{fileName || workbook.source_label || t("Demand workbook")}</div>
          <div className="mt-1 text-xs text-muted-foreground">{t("Parsed workbook summary")}</div>
        </div>
        <Badge tone="success">{t("Ready")}</Badge>
      </div>
      <div className="grid gap-2 text-sm sm:grid-cols-2">
        <WorkbookSummaryItem label="City" value={city} />
        <WorkbookSummaryItem label="School" value={schoolName} />
        <WorkbookSummaryItem label="Addresses" value={formatNumber(addressCount)} />
        <WorkbookSummaryItem label="Students" value={formatNumber(studentCount)} />
      </div>
      {(workbook.warnings || []).map((warning) => (
        <InlineError key={warning} message={warning} />
      ))}
    </div>
  );
}

function WorkbookSummaryItem({ label, value }: { label: string; value: ReactNode }) {
  const t = useT();
  return (
    <div className="min-w-0 rounded-md border border-border bg-muted/40 px-3 py-2">
      <div className="text-xs text-muted-foreground">{t(label)}</div>
      <div className="mt-1 truncate font-semibold text-foreground">{value}</div>
    </div>
  );
}

function VehicleProfileSummary({
  configs,
  mode,
  edited,
  isLoading,
  error,
  onManage,
  onReset,
}: {
  configs: FleetVehicleConfigDraft[];
  mode: VehicleProfileMode;
  edited: boolean;
  isLoading: boolean;
  error?: Error | null;
  onManage: () => void;
  onReset: () => void;
}) {
  const t = useT();
  const enabledConfigs = configs.filter((config) => config.enabled);
  const totalAvailable = enabledConfigs.reduce((sum, config) => sum + Math.max(0, Number(config.available_count) || 0), 0);
  const maxSeats = Math.max(0, ...enabledConfigs.map((config) => Number(config.listed_seats) || 0));
  return (
    <div className="space-y-2 rounded-md border border-border bg-surface p-3">
      <div className="flex flex-col gap-3 min-[1680px]:flex-row min-[1680px]:items-start min-[1680px]:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Bus className="h-4 w-4 text-primary" aria-hidden="true" />
            <h3 className="text-sm font-semibold">{t("Vehicle profile")}</h3>
            <Badge tone={edited ? "warning" : "neutral"}>{mode === "manual" ? t("manual") : edited ? t("custom default") : t("default")}</Badge>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {isLoading
              ? t("Loading market defaults.")
              : template(t("{types} vehicle types, {available} available vehicles, largest {seats} seats."), {
                  types: formatNumber(enabledConfigs.length),
                  available: formatNumber(totalAvailable),
                  seats: formatNumber(maxSeats),
                })}
          </p>
        </div>
        <div className="flex shrink-0 gap-1">
          {edited ? (
            <button type="button" className={buttonClassName("ghost")} aria-label={t("Reset vehicle profile")} onClick={onReset}>
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
            </button>
          ) : null}
          <button type="button" className={buttonClassName("secondary")} onClick={onManage}>
            {t("Manage")}
          </button>
        </div>
      </div>
      {error ? <InlineError message={error.message} /> : null}
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
  saveHistoryResult,
  saveHistoryError,
  isSavingHistory,
  historyRecord,
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
  saveHistoryResult?: FleetPlannerHistoryCreateResponse;
  saveHistoryError?: Error | null;
  isSavingHistory: boolean;
  historyRecord?: FleetPlannerHistoryRecord | null;
  activeView: FleetResultView;
  onActiveViewChange: (view: FleetResultView) => void;
}) {
  const t = useT();
  const assumptions = result.assumptions || {};
  const previewSummary = result.summary || {};
  const demandWorkbookSummary = result.demand_workbook?.summary || {};
  const globalPlanSummary = globalPlanResult?.summary || {};
  const submittedBy = historyRecord?.owner_email || saveHistoryResult?.job.owner_email || "";
  const savedAt = historyRecord?.created_at || saveHistoryResult?.job.created_at || "";
  const runId = historyRecord?.run_id || saveHistoryResult?.job.run_id || "";
  const mapJobName = historyRecord?.title || saveHistoryResult?.job.title || defaultFleetHistoryTitle();
  const tabs: Array<{ key: FleetResultView; label: string; badge?: string; available: boolean }> = [
    {
      key: "plan",
      label: "Plan",
      badge: globalPlanResult ? template(t("{count} routes"), { count: formatNumber(globalPlanSummary.route_count) }) : undefined,
      available: Boolean(globalPlanResult),
    },
    {
      key: "map",
      label: "Map",
      badge: mapOutputs.length ? template(t("{count} routes"), { count: formatNumber(globalPlanSummary.route_count || mapOutputs[0]?.mapData?.summary.route_count || 0) }) : undefined,
      available: mapOutputs.length > 0,
    },
    {
      key: "review",
      label: "Review",
      badge: result.demand_workbook ? template(t("{count} riders"), { count: formatNumber(previewSummary.total_riders) }) : undefined,
      available: true,
    },
  ];

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">{t("Results workspace")}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{t("Review the optimized plan, route map, and supporting input checks in one workspace.")}</p>
            {historyRecord || saveHistoryResult?.job ? (
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                {runId ? <span className="font-mono">{runId}</span> : null}
                <span>{template(t("Submitted by {owner}"), { owner: submittedBy || t("Unknown") })}</span>
                <span>{template(t("Saved {time}"), { time: formatDateTime(savedAt) })}</span>
              </div>
            ) : null}
          </div>
          <Badge tone={globalPlanResult ? "success" : "info"}>{globalPlanResult ? t("Plan ready") : t("Preview mode")}</Badge>
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

        {activeView === "plan" ? (
          globalPlanResult ? (
            <div className="space-y-4">
              <RoutePreviewResult result={globalPlanResult} title={t("Optimized Plan")} framed={false} />
              <FleetHistoryAutoSaveStatus
                saveResult={saveHistoryResult}
                saveError={saveHistoryError}
                isSaving={isSavingHistory}
              />
            </div>
          ) : (
            <EmptyResultState title="Optimized plan not built" detail="Validate addresses first, then use Build optimized plan." />
          )
        ) : null}

        {activeView === "map" ? (
          mapOutputs.length ? (
            <ToolMapsPanel mapOutputs={mapOutputs} workbookResult={globalPlanResult} jobName={mapJobName} />
          ) : (
            <EmptyResultState title="No maps available" detail="Run address validation or build an optimized plan to render maps here." />
          )
        ) : null}

        {activeView === "review" ? (
          <div className="space-y-4">
            <section className="space-y-4">
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
                  <h3 className="mb-2 text-sm font-semibold">{t("Estimated Mix")}</h3>
                  <ResultTable rows={mixRows} columns={["vehicle", "count"]} />
                </div>
              ) : null}
            </section>
            <details className="group rounded-md border border-border bg-muted/30" open={Boolean(geocodeResult || clusterResult)}>
              <summary className="cursor-pointer list-none px-3 py-3 text-sm font-semibold marker:hidden">{t("Input and address review")}</summary>
              <div className="space-y-4 border-t border-border px-3 py-3">
                {result.demand_workbook ? (
                  <div className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-4">
                      <Metric label="Rows" value={formatNumber(demandWorkbookSummary.row_count)} />
                      <Metric label="Students" value={formatNumber(demandWorkbookSummary.student_count)} />
                      <Metric label="Unique Addresses" value={formatNumber(demandWorkbookSummary.unique_address_count)} />
                    <Metric label="City" value={String(demandWorkbookSummary.city || t("N/A"))} />
                    </div>
                    {(result.demand_workbook.warnings || []).map((warning) => (
                      <InlineError key={warning} message={warning} />
                    ))}
                    <ResultTable rows={result.demand_workbook.riders || []} columns={["Country", "City", "School", "Student Address", "Students", "Notes"]} />
                  </div>
                ) : (
                  <EmptyResultState title="No workbook preview" detail="Upload a demand workbook and run Preview fleet to review parsed rows here." />
                )}
                {geocodeResult ? <DemandGeocodePreview result={geocodeResult} framed={false} /> : null}
              </div>
            </details>
            <details className="group rounded-md border border-border bg-muted/30">
              <summary className="cursor-pointer list-none px-3 py-3 text-sm font-semibold marker:hidden">{t("Diagnostics and vehicle catalog")}</summary>
              <div className="space-y-4 border-t border-border px-3 py-3">
                {clusterResult ? <DemandClusterPreview result={clusterResult} framed={false} /> : null}
                {routePreviewResult ? <RoutePreviewResult result={routePreviewResult} title={t("Grouped Route Preview")} framed={false} /> : null}
                {!clusterResult && !routePreviewResult ? (
                  <EmptyResultState title="No diagnostic preview yet" detail="Open Advanced diagnostics and run Preview groups if you need spatial grouping diagnostics." />
                ) : null}
                <div>
                  <h3 className="mb-2 text-sm font-semibold">{t("Vehicle Catalog")}</h3>
                  <ResultTable rows={result.catalog} columns={["vehicle", "category", "propulsion", "listed_seats", "monitor_seats", "student_capacity", "available_count", "notes"]} />
                </div>
              </div>
            </details>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function DemandGeocodePreview({ result, framed = true }: { result: FleetPlannerGeocodeResponse; framed?: boolean }) {
  const t = useT();
  const summary = result.summary || {};
  const content = (
    <>
      {framed ? (
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">{t("Address Validation")}</h2>
            </div>
            <Badge tone={summary.failed_student_rows ? "warning" : "success"}>
              {template(t("{count} resolved"), { count: formatNumber(summary.resolved_student_rows) })}
            </Badge>
          </div>
        </CardHeader>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Address Validation")}</h2>
          </div>
          <Badge tone={summary.failed_student_rows ? "warning" : "success"}>
            {template(t("{count} resolved"), { count: formatNumber(summary.resolved_student_rows) })}
          </Badge>
        </div>
      )}
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-5">
          <Metric label="School" value={String(summary.school_status || "unknown")} />
          <Metric label="Resolved Rows" value={formatNumber(summary.resolved_student_rows)} />
          <Metric label="Failed Rows" value={formatNumber(summary.failed_student_rows)} />
          <Metric label="Resolved Students" value={formatNumber(summary.resolved_students)} />
          <Metric label="Cache Hits" value={formatNumber(summary.cache_hits)} />
        </div>
        <ResultTable
          rows={result.rows || []}
          columns={["Role", "Row", "Address", "Students", "Status", "Cache Hit", "Provider", "Formatted Address", "Lat", "Lng", "Warning"]}
        />
      </div>
    </>
  );
  return framed ? <Card>{content}</Card> : <div className="space-y-4">{content}</div>;
}

function DemandClusterPreview({ result, framed = true }: { result: FleetPlannerClusterResponse; framed?: boolean }) {
  const t = useT();
  const summary = result.summary || {};
  const content = (
    <>
      {framed ? (
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">{t("Demand Grouping Diagnostics")}</h2>
            </div>
            <Badge tone="success">{template(t("{count} groups"), { count: formatNumber(summary.cluster_count) })}</Badge>
          </div>
        </CardHeader>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Demand Grouping Diagnostics")}</h2>
          </div>
          <Badge tone="success">{template(t("{count} groups"), { count: formatNumber(summary.cluster_count) })}</Badge>
        </div>
      )}
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-5">
          <Metric label="Groups" value={formatNumber(summary.cluster_count)} />
          <Metric label="Resolved Points" value={formatNumber(summary.resolved_points)} />
          <Metric label="Resolved Students" value={formatNumber(summary.resolved_students)} />
          <Metric label="Failed Points" value={formatNumber(summary.failed_points)} />
          <Metric label="Max Capacity" value={formatNumber(summary.max_vehicle_student_capacity)} />
        </div>
        <ResultTable
          rows={result.rows || []}
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
        {(result.stop_rows || []).length ? (
          <details className="rounded-md border border-border bg-muted/40">
            <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">{t("Group stop detail")}</summary>
            <div className="border-t border-border">
              <ResultTable
                rows={result.stop_rows || []}
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
  const t = useT();
  const summary = result.summary || {};
  const content = (
    <>
      {framed ? (
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Route className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold">{title}</h2>
            </div>
            <Badge tone={result.refinement_note ? "warning" : "success"}>{template(t("{count} routes"), { count: formatNumber(summary.route_count) })}</Badge>
          </div>
        </CardHeader>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Route className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{title}</h2>
          </div>
          <Badge tone={result.refinement_note ? "warning" : "success"}>{template(t("{count} routes"), { count: formatNumber(summary.route_count) })}</Badge>
        </div>
      )}
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-5">
          <Metric label="Routes" value={formatNumber(summary.route_count)} />
          <Metric label="Distance" value={`${formatNumber(summary.total_distance_km)} km`} />
          <Metric label="Time" value={`${formatNumber(summary.total_duration_min)} min`} />
          <Metric label="Direction" value={summary.service_direction === "from_school" ? t("From School") : t("To School")} />
          <Metric label="Target" value={summary.max_route_duration_minutes ? `${formatNumber(summary.max_route_duration_minutes)} ${t("min")}` : t("N/A")} />
        </div>
        {summary.candidate_vehicle_count || summary.solver ? (
          <div className="grid gap-3 md:grid-cols-2">
            <Metric label="Candidates" value={formatNumber(summary.candidate_vehicle_count)} />
            <Metric label="Solver" value={String(summary.solver || "global_ortools")} />
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
            {t("Download workbook")}
          </Button>
        ) : null}
        <ResultTable
          rows={result.rows || []}
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
        {(result.stop_rows || []).length ? (
          <details className="rounded-md border border-border bg-muted/40">
            <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">{t("Route stop detail")}</summary>
            <div className="border-t border-border">
              <ResultTable
                rows={result.stop_rows || []}
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

function FleetHistoryAutoSaveStatus({
  saveResult,
  saveError,
  isSaving,
}: {
  saveResult?: FleetPlannerHistoryCreateResponse;
  saveError?: Error | null;
  isSaving: boolean;
}) {
  const t = useT();
  if (!isSaving && !saveError && !saveResult?.job) {
    return null;
  }

  return (
    <div className="space-y-3 rounded-md border border-border bg-muted/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{t("Fleet Planner History")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("Optimized plans are saved automatically after a successful run.")}</p>
        </div>
        {isSaving ? <Badge tone="info">{t("Saving")}</Badge> : null}
        {saveResult?.job ? <Badge tone="success">{t("Saved")}</Badge> : null}
      </div>
      {saveError ? <InlineError message={saveError.message} /> : null}
    </div>
  );
}

function FleetPlannerHistoryPanel({
  className,
  jobs,
  activeRunId,
  deletingRunId,
  isLoading,
  error,
  collapsed,
  onCollapsedChange,
  onRefresh,
  onOpen,
  onDelete,
  onBulkDelete,
  canDeleteShared = false,
  bulkDeleting,
}: {
  className?: string;
  jobs: FleetPlannerHistorySummary[];
  activeRunId?: string;
  deletingRunId?: string;
  bulkDeleting?: boolean;
  isLoading: boolean;
  error?: Error | null;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  onRefresh: () => void;
  onOpen: (runId: string) => void;
  onDelete: (runId: string) => void;
  onBulkDelete: (runIds: string[]) => void;
  canDeleteShared?: boolean;
}) {
  const t = useT();
  const [selecting, setSelecting] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(() => new Set());
  const selectedCount = selectedRunIds.size;

  useEffect(() => {
    const runIds = new Set(jobs.map((job) => job.run_id));
    setSelectedRunIds((previous) => {
      const next = new Set([...previous].filter((runId) => runIds.has(runId)));
      return next.size === previous.size ? previous : next;
    });
  }, [jobs]);

  function canDeleteJob(job: FleetPlannerHistorySummary) {
    return !job.shared_with_all || canDeleteShared;
  }

  function toggleSelected(runId: string) {
    setSelectedRunIds((previous) => {
      const next = new Set(previous);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
  }

  if (collapsed) {
    return (
      <Card className={cn("overflow-hidden", className)}>
        <div className="flex min-h-[72px] items-stretch gap-2 p-2 lg:min-h-[320px] lg:flex-col">
          <button
            type="button"
            className="group flex min-w-0 flex-1 items-center justify-between gap-3 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-left transition hover:border-primary/60 hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-primary/30 lg:flex-col lg:justify-start lg:px-2 lg:py-3"
            aria-label={t("Open Fleet Planner history")}
            onClick={() => onCollapsedChange(false)}
          >
            <span className="flex min-w-0 items-center gap-2 lg:flex-col">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface shadow-sm ring-1 ring-border transition group-hover:ring-primary/40">
                <History className="h-4 w-4 text-primary" aria-hidden="true" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold text-foreground lg:[text-orientation:mixed] lg:[writing-mode:vertical-rl]">
                  {t("History")}
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground lg:hidden">{t("Open saved runs")}</span>
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-2 lg:mt-3 lg:flex-col">
              <Badge tone={jobs.length ? "info" : "neutral"}>{formatNumber(jobs.length)}</Badge>
              <ArrowRight className="h-4 w-4 text-primary transition group-hover:translate-x-0.5 lg:rotate-90 lg:group-hover:translate-x-0 lg:group-hover:translate-y-0.5" aria-hidden="true" />
            </span>
          </button>
          <div className="flex items-center lg:mt-auto">
            <button type="button" className={buttonClassName("ghost")} aria-label={t("Refresh Fleet Planner history")} title={t("Refresh history")} onClick={onRefresh}>
              <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} aria-hidden="true" />
            </button>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Fleet Planner History")}</h2>
          </div>
          <div className="flex items-center gap-1">
            <button type="button" className={buttonClassName("ghost")} aria-label={t("Refresh Fleet Planner history")} onClick={onRefresh}>
              <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} aria-hidden="true" />
            </button>
            <button
              type="button"
              className={buttonClassName("ghost")}
              aria-label={t("Collapse Fleet Planner history")}
              onClick={() => onCollapsedChange(true)}
            >
              <ArrowRight className="h-4 w-4 rotate-180" aria-hidden="true" />
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {error ? <InlineError message={error.message} /> : null}
        {!jobs.length && !isLoading ? (
          <div className="rounded-md border border-dashed border-border bg-muted/40 px-3 py-4 text-sm text-muted-foreground">
            {t("Saved Fleet Planner runs will appear here.")}
          </div>
        ) : null}
        {jobs.length ? (
          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              className={buttonClassName("ghost")}
              onClick={() => {
                setSelecting(!selecting);
                setSelectedRunIds(new Set());
              }}
            >
              {t(selecting ? "Cancel" : "Select")}
            </button>
            {selecting ? (
              <button
                type="button"
                className={buttonClassName("secondary")}
                disabled={!selectedCount || bulkDeleting}
                onClick={() => {
                  const runIds = [...selectedRunIds];
                  if (runIds.length && window.confirm(t("Delete selected Fleet Planner history runs? This cannot be undone."))) {
                    onBulkDelete(runIds);
                    setSelectedRunIds(new Set());
                    setSelecting(false);
                  }
                }}
              >
                {bulkDeleting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Trash2 className="h-4 w-4" aria-hidden="true" />}
                {t("Delete selected")} {selectedCount ? `(${formatNumber(selectedCount)})` : ""}
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="max-h-72 space-y-2 overflow-y-auto pr-1 lg:max-h-[calc(100vh-220px)]">
          {jobs.map((job) => {
            const summary = job.summary || {};
            const isActive = activeRunId === job.run_id;
            const isDeleting = deletingRunId === job.run_id;
            const canDeleteRun = canDeleteJob(job);
            return (
              <div
                key={job.run_id}
                className={cn(
                  "flex items-stretch gap-1 rounded-md border p-2 transition",
                  isActive ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface hover:bg-muted",
                )}
              >
                {selecting && canDeleteRun ? (
                  <input
                    type="checkbox"
                    className="mt-2 h-4 w-4 shrink-0 accent-primary"
                    checked={selectedRunIds.has(job.run_id)}
                    aria-label={`${t("Select")} ${job.title || t("Fleet Planner Run")}`}
                    onChange={() => toggleSelected(job.run_id)}
                  />
                ) : null}
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onOpen(job.run_id)}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{job.title || t("Fleet Planner Run")}</div>
                      <div className={cn("mt-1 text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                        {formatDateTime(job.created_at)}
                      </div>
                      <div className={cn("mt-1 truncate text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                        {t("Submitted by")} {job.owner_email || t("Unknown")}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <Badge tone={isActive ? "neutral" : "success"}>{formatNumber(summary.routes)} {t("routes")}</Badge>
                    </div>
                  </div>
                  <div className={cn("mt-2 grid grid-cols-2 gap-1 text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                    <span>{formatNumber(summary.students)} {t("students")}</span>
                    <span>{formatNumber(summary.total_distance_km)} km</span>
                  </div>
                </button>
                {canDeleteRun && !selecting ? (
                  <button
                    type="button"
                    className={cn(
                      "flex h-9 w-9 shrink-0 items-center justify-center rounded-md border transition",
                      isActive
                        ? "border-primary-foreground/30 text-primary-foreground/80 hover:bg-primary-foreground/10 hover:text-primary-foreground"
                        : "border-transparent text-muted-foreground hover:border-border hover:bg-surface hover:text-destructive",
                    )}
                    aria-label={`${t("Delete")} ${job.title || t("Fleet Planner Run")}`}
                    disabled={isDeleting}
                    onClick={() => {
                      if (window.confirm(t("Delete this Fleet Planner history run? This cannot be undone."))) {
                        onDelete(job.run_id);
                      }
                    }}
                  >
                    {isDeleting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Trash2 className="h-4 w-4" aria-hidden="true" />}
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function ToolMapsPanel({
  mapOutputs,
  workbookResult,
  jobName,
}: {
  mapOutputs: ToolMapOutput[];
  workbookResult?: FleetPlannerRoutePreviewResponse;
  jobName: string;
}) {
  const t = useT();
  const [selectedKey, setSelectedKey] = useState("");
  const [isMapFullscreenOpen, setIsMapFullscreenOpen] = useState(false);
  const selected = mapOutputs.find((item) => item.key === selectedKey) || mapOutputs[0];
  const canDownloadWorkbook = Boolean(workbookResult?.workbook_base64);

  if (!selected) {
    return <EmptyResultState title="No maps available" detail="This Fleet Planner run has no rendered maps." />;
  }

  const mapToolbar = (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Button
        type="button"
        variant="secondary"
        className="bg-surface shadow-sm"
        icon={<Maximize2 className="h-4 w-4" />}
        disabled={!selected.mapData}
        onClick={() => setIsMapFullscreenOpen(true)}
      >
        {t("Open")}
      </Button>
      <Button
        type="button"
        variant="secondary"
        className="bg-surface shadow-sm"
        icon={<Download className="h-4 w-4" />}
        disabled={!selected.mapData}
        onClick={() => selected.mapData && downloadInteractiveMapHtml(selected.mapData, jobName, selected.name, t)}
      >
        {t("Map")}
      </Button>
      <Button
        type="button"
        variant="secondary"
        className="bg-surface shadow-sm"
        icon={<FileSpreadsheet className="h-4 w-4" />}
        disabled={!canDownloadWorkbook}
        onClick={() =>
          workbookResult?.workbook_base64 &&
          downloadBase64Workbook(workbookResult.workbook_base64, workbookResult.workbook_file_name || "fleet_planner_generated_plan.xlsx")
        }
      >
        {t("Workbook")}
      </Button>
    </div>
  );

  return (
    <section className="space-y-4">
      <div className="rounded-md border border-border bg-surface px-3 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Map className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{selected.name}</h2>
            {mapOutputs.length > 1 ? <Badge tone="info">{formatNumber(mapOutputs.length)} {t("maps")}</Badge> : null}
          </div>
        </div>
        {mapOutputs.length > 1 ? (
          <div className="mt-3 flex flex-wrap gap-2">
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
      </div>
      <div className="relative overflow-hidden rounded-md border border-border bg-muted/30">
        {selected.mapData ? (
          <InteractiveRouteMap data={selected.mapData} focusKey={`${selected.key}:inline`} />
        ) : (
          <EmptyResultState
            title="Interactive map not available"
            detail="Build the optimized plan again to render this Fleet Planner result with the interactive map."
          />
        )}
        <div className="absolute right-3 top-3 z-10">{mapToolbar}</div>
      </div>
      {isMapFullscreenOpen && selected.mapData ? (
        <div className="fixed inset-0 z-50 p-3">
          <div className="absolute inset-0 bg-slate-950/30 backdrop-blur-sm" />
          <div className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-white/50 bg-white/75 shadow-2xl backdrop-blur-xl">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/50 bg-white/70 px-4 py-3 backdrop-blur-xl">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold text-foreground">{selected.name}</h2>
                <p className="text-xs text-muted-foreground">{t("Interactive Fleet Planner map")}</p>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  className="bg-surface shadow-sm"
                  icon={<Download className="h-4 w-4" />}
                  onClick={() => downloadInteractiveMapHtml(selected.mapData as JobMapData, jobName, selected.name, t)}
                >
                  {t("Map")}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  className="bg-surface shadow-sm"
                  icon={<FileSpreadsheet className="h-4 w-4" />}
                  disabled={!canDownloadWorkbook}
                  onClick={() =>
                    workbookResult?.workbook_base64 &&
                    downloadBase64Workbook(workbookResult.workbook_base64, workbookResult.workbook_file_name || "fleet_planner_generated_plan.xlsx")
                  }
                >
                  {t("Workbook")}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  className="border-red-200 bg-red-50 text-red-700 shadow-sm hover:bg-red-100 hover:text-red-800"
                  icon={<X className="h-4 w-4" />}
                  onClick={() => setIsMapFullscreenOpen(false)}
                >
                  {t("Close")}
                </Button>
              </div>
            </div>
            <div className="min-h-0 flex-1">
              <InteractiveRouteMap data={selected.mapData} fullscreen focusKey={`${selected.key}:fullscreen`} />
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function VehicleConfigModal({
  open,
  market,
  configs,
  mode,
  edited,
  onModeChange,
  onChange,
  onReset,
  onClose,
}: {
  open: boolean;
  market: FleetMarket;
  configs: FleetVehicleConfigDraft[];
  mode: VehicleProfileMode;
  edited: boolean;
  onModeChange: (mode: VehicleProfileMode) => void;
  onChange: (configs: FleetVehicleConfigDraft[]) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const t = useT();
  if (!open) {
    return null;
  }
  const visibleConfigs = configs;

  function updateConfig(id: string, updates: Partial<FleetVehicleConfigDraft>) {
    onChange(
      visibleConfigs.map((config) =>
        config.id === id
          ? {
              ...config,
              ...updates,
            }
          : config,
      ),
    );
  }

  function addConfig() {
    onChange([...visibleConfigs, newVehicleConfigDraft(market, visibleConfigs.length + 1)]);
  }

  function deleteConfig(id: string) {
    onChange(visibleConfigs.filter((config) => config.id !== id));
  }

  return (
    <div className="fixed inset-0 z-40">
      <button type="button" className="absolute inset-0 bg-slate-950/20" aria-label={t("Close vehicle configuration")} onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-3xl flex-col border-l border-border bg-surface shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">{t("Vehicle profile")}</h2>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t("Configure the vehicles available for this Fleet Planner run. Changes apply to preview, optimization, and saved history.")}
            </p>
          </div>
          <button type="button" className={buttonClassName("ghost")} aria-label={t("Close vehicle configuration")} onClick={onClose}>
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <div className="grid grid-cols-2 gap-2">
              <ModeButton active={mode === "default"} onClick={() => onModeChange("default")}>
                {t("Default")}
              </ModeButton>
              <ModeButton active={mode === "manual"} onClick={() => onModeChange("manual")}>
                {t("Manual input")}
              </ModeButton>
            </div>
            <Badge tone={edited ? "warning" : "neutral"}>{t(mode === "manual" ? "manual profile" : edited ? "custom defaults" : "market defaults")}</Badge>
            <span className="text-muted-foreground">{formatNumber(visibleConfigs.filter((config) => config.enabled).length)} {t("enabled types")}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={buttonClassName("secondary")} onClick={addConfig}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              {t("Add vehicle")}
            </button>
            <button type="button" className={buttonClassName("secondary")} onClick={onReset}>
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              {t("Reset profile")}
            </button>
          </div>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {visibleConfigs.length ? (
            visibleConfigs.map((config) => (
              <div key={config.id} className="rounded-md border border-border bg-muted/30 p-3">
                <div className="grid gap-3 lg:grid-cols-[minmax(180px,1.4fr)_90px_130px_120px_110px_40px]">
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground">{t("Vehicle Name")}</span>
                    <input
                      className={fieldClassName}
                      value={config.display_name}
                      onChange={(event) => updateConfig(config.id, { display_name: event.target.value })}
                    />
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground">{t("Seats")}</span>
                    <input
                      className={fieldClassName}
                      type="number"
                      min="1"
                      max="120"
                      step="1"
                      value={config.listed_seats}
                      onChange={(event) => updateConfig(config.id, { listed_seats: normalizeVehicleInt(event.target.value, 1, 120) })}
                    />
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground">{t("Category")}</span>
                    <select
                      className={fieldClassName}
                      value={config.category}
                      onChange={(event) => updateConfig(config.id, { category: normalizeVehicleCategory(event.target.value) })}
                    >
                      {VEHICLE_CATEGORIES.map((category) => (
                        <option key={category} value={category}>
                          {t(vehicleCategoryLabel(category))}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground">{t("Energy")}</span>
                    <select
                      className={fieldClassName}
                      value={config.propulsion}
                      onChange={(event) => updateConfig(config.id, { propulsion: normalizeVehiclePropulsion(event.target.value) })}
                    >
                      {VEHICLE_PROPULSIONS.map((propulsion) => (
                        <option key={propulsion} value={propulsion}>
                          {t(propulsion === "electric" ? "Electric" : "Diesel")}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground">{t("Available")}</span>
                    <input
                      className={fieldClassName}
                      type="number"
                      min="0"
                      max="100"
                      step="1"
                      value={config.available_count}
                      onChange={(event) => updateConfig(config.id, { available_count: normalizeVehicleInt(event.target.value, 0, 100) })}
                    />
                  </label>
                  <div className="flex items-end justify-end gap-1">
                    <button
                      type="button"
                      className={cn(
                        "flex h-9 w-9 items-center justify-center rounded-md border transition",
                        config.enabled ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface text-muted-foreground hover:bg-muted",
                      )}
                      aria-label={t(config.enabled ? "Disable vehicle" : "Enable vehicle")}
                      onClick={() => updateConfig(config.id, { enabled: !config.enabled })}
                    >
                      <Bus className="h-4 w-4" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-surface text-muted-foreground transition hover:text-destructive"
                      aria-label={t("Delete vehicle")}
                      onClick={() => deleteConfig(config.id)}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </div>
                </div>
                <label className="mt-3 block space-y-1.5">
                  <span className="text-xs font-medium text-muted-foreground">{t("Notes")}</span>
                  <input
                    className={fieldClassName}
                    value={config.notes || ""}
                    onChange={(event) => updateConfig(config.id, { notes: event.target.value })}
                  />
                </label>
              </div>
            ))
          ) : (
            <EmptyResultState title="No vehicles configured" detail="Add at least one enabled vehicle type before running the planner." />
          )}
        </div>
        <div className="flex justify-end border-t border-border px-4 py-3">
          <button type="button" className={buttonClassName("primary")} onClick={onClose}>
            {t("Done")}
          </button>
        </div>
      </aside>
    </div>
  );
}

function FleetPlannerHowToUse({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  if (!open) {
    return null;
  }
  return (
    <div className="fixed inset-0 z-40">
      <button type="button" className="absolute inset-0 bg-slate-950/20" aria-label={t("Close how to use")} onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col border-l border-border bg-surface shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">{t("How to use Fleet Planner")}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{t("Follow the setup flow from left to right; diagnostics are optional.")}</p>
          </div>
          <button type="button" className={buttonClassName("ghost")} aria-label={t("Close how to use")} onClick={onClose}>
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="space-y-5 overflow-y-auto px-4 py-4 text-sm leading-6 text-muted-foreground">
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{t("Operation flow")}</h3>
            <ol className="list-decimal space-y-2 pl-5">
              <li>{t("Demand source: upload a demand workbook, name the run, reserve monitor seats, choose service direction, and confirm the parsed city, school, address count, and students.")}</li>
              <li>{t("Run settings: confirm the auto-selected Fleet Setting and vehicle profile, then choose planning mode and route time target.")}</li>
              <li>{t("Preview fleet: checks vehicle choices from the uploaded workbook without geocoding addresses.")}</li>
              <li>{t("Validate & geocode: resolves workbook addresses into school and pickup points for routing and maps.")}</li>
              <li>{t("Build optimized plan: runs the route solver, renders the optimized map, and saves the run to Fleet Planner History.")}</li>
            </ol>
          </section>
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{t("Demand source")}</h3>
            <ul className="list-disc space-y-2 pl-5">
              <li>{t("Upload the demand workbook first so Fleet Planner can parse city, school, address count, and total students.")}</li>
              <li>{t("Job Name controls the title saved in Fleet Planner History.")}</li>
              <li>{t("Bus Monitor Seats reserves adult seats before student capacity is calculated.")}</li>
              <li>{t("Service Direction controls whether routes are built toward school pickup or away from school drop-off.")}</li>
            </ul>
          </section>
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{t("Run settings")}</h3>
            <ul className="list-disc space-y-2 pl-5">
              <li>{t("Fleet Setting is auto-selected from the workbook country, and controls vehicle catalog, capacity rules, and local routing assumptions.")}</li>
              <li>{t("Vehicle profile controls which vehicle types, seat counts, energy types, and available counts are used by this run.")}</li>
              <li>{t("Planning Mode changes the tradeoff between tighter vehicle fill and rider comfort.")}</li>
              <li>{t("Route Time Target caps each route's one-way completion time. Tighter targets may need more vehicles or become infeasible when individual demand points are too far from school.")}</li>
            </ul>
          </section>
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{t("Advanced diagnostics")}</h3>
            <p>{t("Preview groups is a diagnostic view for demand distribution around the school; it does not drive the optimized plan.")}</p>
            <p>{t("Grouping Granularity only changes that diagnostic grouping. The optimized solver still splits routes by capacity, travel time, distance, and service direction.")}</p>
          </section>
        </div>
      </aside>
    </div>
  );
}

function FleetSettingGuide({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  if (!open) {
    return null;
  }
  return (
    <div className="fixed inset-0 z-40">
      <button type="button" className="absolute inset-0 bg-slate-950/20" aria-label={t("Close Fleet Setting guide")} onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col border-l border-border bg-surface shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">{t("Fleet Setting guide")}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{t("Use defaults first; adjust only when the uploaded demand or available fleet requires it.")}</p>
          </div>
          <button type="button" className={buttonClassName("ghost")} aria-label={t("Close Fleet Setting guide")} onClick={onClose}>
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="space-y-5 overflow-y-auto px-4 py-4 text-sm leading-6 text-muted-foreground">
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{t("Default behavior")}</h3>
            <p>
              {t("After a workbook is uploaded, Fleet Setting is inferred from the workbook country. If nothing is changed, Fleet Planner uses that market's default vehicle catalog. Demand source settings still control the saved job name, monitor-seat reservation, and service direction for the run.")}
            </p>
            <p>
              {t("Preview fleet chooses the best feasible vehicle for each rider group from the active catalog. The optimized plan may add routes or vehicles when needed to satisfy capacity and Route Time Target.")}
            </p>
          </section>
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">{t("When to adjust")}</h3>
            <ul className="list-disc space-y-2 pl-5">
              <li>{t("Fleet Setting: change KR/CN only if the workbook country was inferred incorrectly.")}</li>
              <li>{t("Vehicle profile: use Manage when the operator needs custom seat counts, vehicle availability, diesel/electric mix, or disabled vehicle types.")}</li>
              <li>{t("Planning Mode: use Balanced for normal planning, Cost Saver for fuller vehicles, and Comfort Saver when lower loads are acceptable.")}</li>
              <li>{t("Route Time Target: tighten it for shorter routes; loosen it if the plan becomes infeasible or requires too many vehicles.")}</li>
            </ul>
          </section>
        </div>
      </aside>
    </div>
  );
}

function Field({ label, description, children }: { label: string; description?: string; children: ReactNode }) {
  const t = useT();
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{t(label)}</span>
      {description ? <span className="block text-xs leading-5 text-muted-foreground">{t(description)}</span> : null}
      {children}
    </label>
  );
}

function ModeButton({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-9 min-w-0 items-center justify-center whitespace-nowrap rounded-md border px-3 text-sm font-medium transition",
        active ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface text-foreground hover:bg-muted",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function RouteTimeTargetControl({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const [draftValue, setDraftValue] = useState(String(value));

  useEffect(() => {
    setDraftValue(String(value));
  }, [value]);

  function commitDraft() {
    const nextValue = Number(draftValue);
    const normalizedValue = normalizeRouteTimeTarget(nextValue);
    setDraftValue(String(normalizedValue));
    onChange(normalizedValue);
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-4 gap-1.5">
        {ROUTE_TIME_TARGET_PRESETS.map((minutes) => (
          <ModeButton key={minutes} active={value === minutes} onClick={() => onChange(minutes)}>
            {minutes}
          </ModeButton>
        ))}
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
        <input
          className={fieldClassName}
          type="number"
          min="5"
          max="240"
          step="5"
          value={draftValue}
          onChange={(event) => setDraftValue(event.target.value)}
          onBlur={commitDraft}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.currentTarget.blur();
            }
          }}
        />
        <span className="shrink-0 text-xs font-medium text-muted-foreground">min</span>
      </div>
    </div>
  );
}

function SetupFlowArrow() {
  return (
    <div className="hidden items-center justify-center xl:flex" aria-hidden="true">
      <div className="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-surface text-primary shadow-sm">
        <ArrowRight className="h-4 w-4" />
      </div>
    </div>
  );
}

function WorkflowAction({
  step,
  title,
  description,
  icon,
  pending,
  disabled,
  variant = "primary",
  onClick,
}: {
  step: string;
  title: string;
  description: string;
  icon: ReactNode;
  pending?: boolean;
  disabled?: boolean;
  variant?: "primary" | "secondary";
  onClick: () => void;
}) {
  const t = useT();
  return (
    <div className="rounded-md border border-border bg-surface p-2">
      <div className="mb-2 flex items-start gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
          {step}
        </span>
        <div className="min-w-0">
          <div className="text-sm font-semibold">{t(title)}</div>
          <div className="mt-0.5 text-xs leading-5 text-muted-foreground">{t(description)}</div>
        </div>
      </div>
      <Button
        type="button"
        variant={variant}
        className="w-full"
        disabled={disabled}
        icon={pending ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
        onClick={onClick}
      >
        {t(title)}
      </Button>
    </div>
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
  const t = useT();
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
      <span>{t(label)}</span>
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
  const t = useT();
  return (
    <div className="rounded-md border border-dashed border-border bg-muted/40 px-4 py-8 text-center">
      <h3 className="text-sm font-semibold text-foreground">{t(title)}</h3>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground">{t(detail)}</p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  const t = useT();
  return (
    <div className="rounded-md border border-border bg-muted/50 p-3">
      <div className="text-xs text-muted-foreground">{t(label)}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function ResultTable({ rows, columns }: { rows: Array<Record<string, unknown>>; columns: string[] }) {
  const t = useT();
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
                {t(column)}
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

function template(text: string, values: Record<string, string | number>) {
  return text.replace(/\{(\w+)\}/g, (match, key) => String(values[key] ?? match));
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
  globalPlanResult,
}: {
  geocodeResult?: FleetPlannerGeocodeResponse;
  clusterResult?: FleetPlannerClusterResponse;
  routePreviewResult?: FleetPlannerRoutePreviewResponse;
  globalPlanResult?: FleetPlannerRoutePreviewResponse;
}) {
  return [
    {
      key: "optimized-plan",
      name: "Optimized Plan",
      mapData: globalPlanResult?.map_data,
    },
  ].filter((item) => item.mapData) as ToolMapOutput[];
}

function inferMarketFromDemandWorkbook(workbook?: FleetPlannerPreviewResponse["demand_workbook"] | null): "KR" | "CN" | null {
  const summary = workbook?.summary || {};
  const school = workbook?.school || {};
  const country = String(summary.country || school.country || "").trim().toLowerCase();
  if (!country) {
    return null;
  }
  if (country.includes("china") || country.includes("中国")) {
    return "CN";
  }
  if (country.includes("korea") || country.includes("대한민국") || country.includes("한국")) {
    return "KR";
  }
  return null;
}

function applyHistoryScenario(
  record: FleetPlannerHistoryRecord,
  setters: {
    setMarket: (value: FleetMarket) => void;
    setMode: (value: FleetMode) => void;
    setMonitorSeats: (value: number) => void;
    setRouteTimeTargetMinutes: (value: number) => void;
    setVehicleProfileMode: (value: VehicleProfileMode) => void;
    setDefaultVehicleCatalogEdited: (value: boolean) => void;
    setDefaultVehicleConfigsDraft: (value: FleetVehicleConfigDraft[]) => void;
    setManualVehicleConfigs: (value: FleetVehicleConfigDraft[]) => void;
    setRouteDirection: (value: FleetServiceDirection) => void;
    setGlobalDirection: (value: FleetServiceDirection) => void;
  },
) {
  const scenario = record.scenario || {};
  const previewSummary = (record.preview_result?.summary || {}) as Record<string, unknown>;
  const globalPlanSummary = (record.global_plan_result?.summary || {}) as Record<string, unknown>;

  const market = normalizeFleetMarket(scenario.market ?? previewSummary.market);
  if (market) {
    setters.setMarket(market);
  }

  const mode = normalizeFleetMode(scenario.mode ?? previewSummary.mode);
  if (mode) {
    setters.setMode(mode);
  }

  const monitorSeats = numberFromUnknown(scenario.monitor_seats ?? previewSummary.monitor_seats);
  if (monitorSeats !== null) {
    setters.setMonitorSeats(Math.max(0, Math.round(monitorSeats)));
  }

  const routeTimeTarget = numberFromUnknown(
    scenario.max_route_duration_minutes ?? globalPlanSummary.max_route_duration_minutes ?? previewSummary.max_route_duration_minutes,
  );
  if (routeTimeTarget !== null) {
    setters.setRouteTimeTargetMinutes(normalizeRouteTimeTarget(routeTimeTarget));
  }

  const vehicleCatalogSource = String(scenario.vehicle_catalog_source || "").trim().toLowerCase();
  const vehicleCatalogSnapshot = Array.isArray(scenario.vehicle_catalog_snapshot)
    ? scenario.vehicle_catalog_snapshot
    : Array.isArray(scenario.vehicle_catalog)
      ? scenario.vehicle_catalog
      : [];
  if (vehicleCatalogSnapshot.length) {
    const normalizedSnapshot = normalizeVehicleConfigDrafts(vehicleCatalogSnapshot.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null), market || "KR");
    if (vehicleCatalogSource === "custom") {
      setters.setVehicleProfileMode("manual");
      setters.setManualVehicleConfigs(normalizedSnapshot);
      setters.setDefaultVehicleCatalogEdited(false);
      setters.setDefaultVehicleConfigsDraft([]);
    } else {
      setters.setVehicleProfileMode("default");
      setters.setDefaultVehicleCatalogEdited(false);
      setters.setDefaultVehicleConfigsDraft([]);
      setters.setManualVehicleConfigs([]);
    }
  } else {
    setters.setVehicleProfileMode("default");
    setters.setDefaultVehicleCatalogEdited(false);
    setters.setDefaultVehicleConfigsDraft([]);
    setters.setManualVehicleConfigs([]);
  }

  const direction = normalizeFleetServiceDirection(scenario.service_direction ?? globalPlanSummary.service_direction);
  if (direction) {
    setters.setRouteDirection(direction);
    setters.setGlobalDirection(direction);
  }
}

function normalizeRouteTimeTarget(value: number) {
  if (!Number.isFinite(value)) {
    return 60;
  }
  return Math.min(240, Math.max(5, Math.round(value)));
}

function normalizeVehicleConfigDrafts(catalog: FleetVehicleCatalogInput[], market: FleetMarket): FleetVehicleConfigDraft[] {
  return catalog.map((vehicle, index) => ({
    id: `${market.toLowerCase()}-${String(vehicle.vehicle_type || vehicle.display_name || vehicle.vehicle || index)}`,
    vehicle_type: String(vehicle.vehicle_type || `vehicle_${index + 1}`),
    display_name: String(vehicle.display_name || vehicle.vehicle || `Vehicle ${index + 1}`),
    listed_seats: normalizeVehicleInt(vehicle.listed_seats, 1, 120),
    category: normalizeVehicleCategory(vehicle.category),
    propulsion: normalizeVehiclePropulsion(vehicle.propulsion),
    available_count: normalizeVehicleInt(vehicle.available_count, 0, 100),
    enabled: vehicle.enabled !== false,
    notes: String(vehicle.notes || ""),
  }));
}

function fleetVehiclePayloadFromDrafts(configs: FleetVehicleConfigDraft[]): FleetPlannerVehicleConfig[] {
  return configs.map((config) => ({
    vehicle_type: config.vehicle_type,
    display_name: config.display_name.trim() || "Unnamed vehicle",
    listed_seats: normalizeVehicleInt(config.listed_seats, 1, 120),
    category: normalizeVehicleCategory(config.category),
    propulsion: normalizeVehiclePropulsion(config.propulsion),
    available_count: normalizeVehicleInt(config.available_count, 0, 100),
    enabled: Boolean(config.enabled),
    notes: String(config.notes || "").trim(),
  }));
}

function newVehicleConfigDraft(market: FleetMarket, sequence: number): FleetVehicleConfigDraft {
  return {
    id: `custom-${Date.now()}-${sequence}`,
    vehicle_type: `custom_${market.toLowerCase()}_${sequence}`,
    display_name: `Custom ${market} vehicle ${sequence}`,
    listed_seats: 45,
    category: "large_bus",
    propulsion: "diesel",
    available_count: 10,
    enabled: true,
    notes: "",
  };
}

function normalizeVehicleInt(value: unknown, min: number, max: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

function normalizeVehicleCategory(value: unknown): FleetVehicleCategory {
  const normalized = String(value || "").trim().toLowerCase();
  return VEHICLE_CATEGORIES.includes(normalized as FleetVehicleCategory) ? (normalized as FleetVehicleCategory) : "large_bus";
}

function normalizeVehiclePropulsion(value: unknown): FleetVehiclePropulsion {
  const normalized = String(value || "").trim().toLowerCase();
  return VEHICLE_PROPULSIONS.includes(normalized as FleetVehiclePropulsion) ? (normalized as FleetVehiclePropulsion) : "diesel";
}

function vehicleCategoryLabel(value: FleetVehicleCategory) {
  return {
    van: "Van",
    mini_bus: "Mini bus",
    mid_bus: "Mid bus",
    large_bus: "Large bus",
  }[value];
}

function normalizeFleetMarket(value: unknown): FleetMarket | null {
  const normalized = String(value || "").trim().toUpperCase();
  return normalized === "KR" || normalized === "CN" ? normalized : null;
}

function normalizeFleetMode(value: unknown): FleetMode | null {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "balanced" || normalized === "cost_saver" || normalized === "comfort_saver") {
    return normalized;
  }
  return null;
}

function normalizeFleetServiceDirection(value: unknown): FleetServiceDirection | null {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "to_school" || normalized === "from_school") {
    return normalized;
  }
  return null;
}

function numberFromUnknown(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
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
