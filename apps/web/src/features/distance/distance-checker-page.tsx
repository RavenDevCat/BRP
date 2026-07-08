import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Calculator, FileSpreadsheet, Fuel, History, Loader2, MapPinned, RefreshCw, Ruler, Trash2, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  deleteDistanceCheckerHistory,
  getDistanceCheckerHistory,
  listDistanceCheckerHistory,
  previewDistanceWorkbook,
  runCurrentPlanRouteCost,
  runReferenceDistanceCheck,
  saveDistanceCheckerHistory,
  type DistanceCheckerHistoryCreateResponse,
  type DistanceCheckerHistoryRecord,
  type DistanceCheckerHistorySummary,
  type DistanceCheckerToolMode,
  type DistanceWorkbookPreview,
  type ReferenceDistanceResponse,
  type RouteCostResponse,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useT } from "@/lib/i18n/context";

const fieldClassName =
  "h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none transition focus:border-primary";
const routeCostProfiles = {
  china: {
    label: "China",
    defaultCity: "Shanghai",
    defaultCountry: "China",
    currencyCode: "CNY",
    currencyLabel: "RMB",
    dieselPrice: 8.402,
    dieselStep: 0.1,
  },
  korea: {
    label: "South Korea",
    defaultCity: "Seoul",
    defaultCountry: "South Korea",
    currencyCode: "KRW",
    currencyLabel: "KRW",
    dieselPrice: 2006.19,
    dieselStep: 10,
  },
};

export function DistanceCheckerPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const [activeTool, setActiveTool] = useState<DistanceCheckerToolMode>("reference");
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [preview, setPreview] = useState<DistanceWorkbookPreview | null>(null);
  const [selectedSheet, setSelectedSheet] = useState("");
  const [routeColumn, setRouteColumn] = useState("");
  const [addressColumn, setAddressColumn] = useState("");
  const [sequenceColumn, setSequenceColumn] = useState("");
  const [busTypeColumn, setBusTypeColumn] = useState("");
  const [cityColumn, setCityColumn] = useState("");
  const [countryColumn, setCountryColumn] = useState("");
  const [originCountry, setOriginCountry] = useState("South Korea");
  const [originCity, setOriginCity] = useState("Seoul");
  const [originAddress, setOriginAddress] = useState("");
  const [distanceMode, setDistanceMode] = useState<"road" | "straight_line">("road");
  const [result, setResult] = useState<ReferenceDistanceResponse | null>(null);
  const [routeCostProfileKey, setRouteCostProfileKey] = useState<keyof typeof routeCostProfiles>("korea");
  const [routeDefaultCity, setRouteDefaultCity] = useState(routeCostProfiles.korea.defaultCity);
  const [routeDefaultCountry, setRouteDefaultCountry] = useState(routeCostProfiles.korea.defaultCountry);
  const [dieselPrice, setDieselPrice] = useState(routeCostProfiles.korea.dieselPrice);
  const [fuelEfficiency, setFuelEfficiency] = useState(3);
  const [routeCostResult, setRouteCostResult] = useState<RouteCostResponse | null>(null);
  const [loadedHistoryRecord, setLoadedHistoryRecord] = useState<DistanceCheckerHistoryRecord | null>(null);
  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const [deletingRunId, setDeletingRunId] = useState("");
  const historyPanelRef = useRef<HTMLDivElement | null>(null);

  const historyQuery = useQuery({
    queryKey: ["distance-checker-history", activeTool],
    queryFn: () => listDistanceCheckerHistory(activeTool),
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

  const previewMutation = useMutation({
    mutationFn: async (sheetName?: string) => {
      if (!file || !fileBase64) {
        throw new Error(t("Select a workbook first."));
      }
      return previewDistanceWorkbook({
        file_name: file.name,
        file_base64: fileBase64,
        selected_sheet: sheetName || selectedSheet || undefined,
      });
    },
    onSuccess: (payload) => {
      setPreview(payload);
      setSelectedSheet(payload.selected_sheet);
      setRouteColumn(payload.suggested_columns.route || "");
      setAddressColumn(payload.suggested_columns.address || payload.columns[0] || "");
      setSequenceColumn(payload.suggested_columns.sequence || "");
      setBusTypeColumn(payload.suggested_columns.bus_type || "");
      setCityColumn(payload.suggested_columns.city || "");
      setCountryColumn(payload.suggested_columns.country || "");
      applyPreviewDefaults(payload);
      setResult(null);
      setRouteCostResult(null);
      setLoadedHistoryRecord(null);
    },
  });

  const saveHistoryMutation = useMutation({
    mutationFn: saveDistanceCheckerHistory,
    onSuccess: async (_payload, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["distance-checker-history", variables.tool_mode] });
    },
  });

  const openHistoryMutation = useMutation({
    mutationFn: ({ toolMode, runId }: { toolMode: DistanceCheckerToolMode; runId: string }) => getDistanceCheckerHistory(toolMode, runId),
    onSuccess: (record) => applyHistoryRecord(record),
  });

  const deleteHistoryMutation = useMutation({
    mutationFn: ({ toolMode, runId }: { toolMode: DistanceCheckerToolMode; runId: string }) => deleteDistanceCheckerHistory(toolMode, runId),
    onMutate: ({ runId }) => setDeletingRunId(runId),
    onSuccess: async (payload, variables) => {
      if (loadedHistoryRecord?.run_id === payload.run_id) {
        setLoadedHistoryRecord(null);
        setResult(null);
        setRouteCostResult(null);
      }
      await queryClient.invalidateQueries({ queryKey: ["distance-checker-history", variables.toolMode] });
    },
    onSettled: () => setDeletingRunId(""),
  });
  const bulkDeleteHistoryMutation = useMutation({
    mutationFn: async ({ toolMode, runIds }: { toolMode: DistanceCheckerToolMode; runIds: string[] }) => {
      for (const runId of runIds) {
        await deleteDistanceCheckerHistory(toolMode, runId);
      }
      return { toolMode, runIds };
    },
    onSuccess: async ({ runIds, toolMode }) => {
      if (runIds.includes(loadedHistoryRecord?.run_id || "")) {
        setLoadedHistoryRecord(null);
        setResult(null);
        setRouteCostResult(null);
      }
      await queryClient.invalidateQueries({ queryKey: ["distance-checker-history", toolMode] });
    },
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64 || !preview) {
        throw new Error(t("Preview a workbook first."));
      }
      return runReferenceDistanceCheck({
        file_name: file.name,
        file_base64: fileBase64,
        selected_sheet: selectedSheet,
        address_column: addressColumn,
        city_column: cityColumn || undefined,
        country_column: countryColumn || undefined,
        distance_mode: distanceMode,
        origin: {
          country: originCountry,
          city: originCity,
          address: originAddress,
        },
      });
    },
    onSuccess: (payload) => {
      setResult(payload);
      setRouteCostResult(null);
      setLoadedHistoryRecord(null);
      saveHistory("reference", payload);
    },
  });

  const routeCostMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64 || !preview) {
        throw new Error(t("Preview a workbook first."));
      }
      const profile = routeCostProfiles[routeCostProfileKey];
      return runCurrentPlanRouteCost({
        file_name: file.name,
        file_base64: fileBase64,
        selected_sheet: selectedSheet,
        route_column: routeColumn,
        address_column: addressColumn,
        sequence_column: sequenceColumn || undefined,
        bus_type_column: busTypeColumn || undefined,
        city_column: cityColumn || undefined,
        country_column: countryColumn || undefined,
        default_city: routeDefaultCity,
        default_country: routeDefaultCountry,
        currency_code: profile.currencyCode,
        currency_label: profile.currencyLabel,
        diesel_price_per_liter: dieselPrice,
        fuel_efficiency_km_per_liter: fuelEfficiency,
      });
    },
    onSuccess: (payload) => {
      setRouteCostResult(payload);
      setResult(null);
      setLoadedHistoryRecord(null);
      saveHistory("route_cost", payload);
    },
  });

  useEffect(() => {
    if (preview && selectedSheet && selectedSheet !== preview.selected_sheet) {
      previewMutation.mutate(selectedSheet);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSheet]);

  useEffect(() => {
    if (file && fileBase64 && !loadedHistoryRecord) {
      previewMutation.mutate(undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileBase64]);

  function clearDistanceResult() {
    setResult(null);
    setLoadedHistoryRecord(null);
    saveHistoryMutation.reset();
  }

  function clearRouteCostResult() {
    setRouteCostResult(null);
    setLoadedHistoryRecord(null);
    saveHistoryMutation.reset();
  }

  function applyRouteCostProfileKey(nextKey: keyof typeof routeCostProfiles) {
    const profile = routeCostProfiles[nextKey];
    setRouteCostProfileKey(nextKey);
    setRouteDefaultCity(profile.defaultCity);
    setRouteDefaultCountry(profile.defaultCountry);
    setDieselPrice(profile.dieselPrice);
    clearRouteCostResult();
  }

  function applyPreviewDefaults(payload: DistanceWorkbookPreview) {
    const country = normalizeWorkbookCountry(firstPreviewValue(payload, payload.suggested_columns.country));
    const city = firstPreviewValue(payload, payload.suggested_columns.city);
    const profileKey = inferProfileKey(country, city);
    if (country) {
      setOriginCountry(country);
      setRouteDefaultCountry(country);
    }
    if (city) {
      setOriginCity(city);
      setRouteDefaultCity(city);
    }
    if (profileKey) {
      setRouteCostProfileKey(profileKey);
      setDieselPrice(routeCostProfiles[profileKey].dieselPrice);
    }
  }

  async function handleFileChange(nextFile: File | null) {
    setFile(nextFile);
    setPreview(null);
    setResult(null);
    setRouteCostResult(null);
    setLoadedHistoryRecord(null);
    saveHistoryMutation.reset();
    previewMutation.reset();
    setFileError("");
    setFileBase64("");
    if (!nextFile) {
      return;
    }
    const suffix = nextFile.name.split(".").pop()?.toLowerCase();
    if (!suffix || !["xlsx", "xlsm"].includes(suffix)) {
      setFileError(t("Use an .xlsx or .xlsm workbook."));
      return;
    }
    try {
      setFileBase64(await fileToBase64(nextFile));
    } catch {
      setFileError(t("Workbook could not be read."));
    }
  }

  function handleToolChange(nextTool: DistanceCheckerToolMode) {
    if (nextTool === activeTool) {
      return;
    }
    setActiveTool(nextTool);
    setLoadedHistoryRecord(null);
    if (nextTool === "reference") {
      setRouteCostResult(null);
    } else {
      setResult(null);
    }
    saveHistoryMutation.reset();
  }

  function buildScenario(toolMode: DistanceCheckerToolMode) {
    return {
      tool_mode: toolMode,
      file_name: file?.name || preview?.source_label || "",
      selected_sheet: selectedSheet,
      address_column: addressColumn,
      city_column: cityColumn,
      country_column: countryColumn,
      route_column: routeColumn,
      sequence_column: sequenceColumn,
      bus_type_column: busTypeColumn,
      origin: {
        country: originCountry,
        city: originCity,
        address: originAddress,
      },
      distance_mode: distanceMode,
      route_cost_profile_key: routeCostProfileKey,
      default_city: routeDefaultCity,
      default_country: routeDefaultCountry,
      currency_code: routeCostProfiles[routeCostProfileKey].currencyCode,
      currency_label: routeCostProfiles[routeCostProfileKey].currencyLabel,
      diesel_price_per_liter: dieselPrice,
      fuel_efficiency_km_per_liter: fuelEfficiency,
    };
  }

  function saveHistory(toolMode: "reference", payload: ReferenceDistanceResponse): void;
  function saveHistory(toolMode: "route_cost", payload: RouteCostResponse): void;
  function saveHistory(toolMode: DistanceCheckerToolMode, payload: ReferenceDistanceResponse | RouteCostResponse) {
    saveHistoryMutation.mutate({
      title: payload.job?.label || defaultDistanceHistoryTitle(toolMode),
      tool_mode: toolMode,
      scenario: buildScenario(toolMode),
      preview,
      reference_result: toolMode === "reference" ? (payload as ReferenceDistanceResponse) : null,
      route_cost_result: toolMode === "route_cost" ? (payload as RouteCostResponse) : null,
    });
  }

  function applyHistoryRecord(record: DistanceCheckerHistoryRecord) {
    const scenario = record.scenario || {};
    const summary = record.summary || {};
    const toolMode = normalizeDistanceToolMode(scenario.tool_mode || summary.tool_mode || (record.route_cost_result ? "route_cost" : "reference"));
    const recordPreview = record.preview || null;
    saveHistoryMutation.reset();
    setLoadedHistoryRecord(record);
    setActiveTool(toolMode);
    setFile(null);
    setFileBase64("");
    setFileError("");
    setPreview(recordPreview);
    setSelectedSheet(String(scenario.selected_sheet || recordPreview?.selected_sheet || summary.selected_sheet || ""));
    setRouteColumn(String(scenario.route_column || recordPreview?.suggested_columns?.route || ""));
    setAddressColumn(String(scenario.address_column || recordPreview?.suggested_columns?.address || recordPreview?.columns?.[0] || ""));
    setSequenceColumn(String(scenario.sequence_column || recordPreview?.suggested_columns?.sequence || ""));
    setBusTypeColumn(String(scenario.bus_type_column || recordPreview?.suggested_columns?.bus_type || ""));
    setCityColumn(String(scenario.city_column || recordPreview?.suggested_columns?.city || ""));
    setCountryColumn(String(scenario.country_column || recordPreview?.suggested_columns?.country || ""));

    const origin = asRecord(scenario.origin);
    setOriginCountry(String(origin.country || "South Korea"));
    setOriginCity(String(origin.city || "Seoul"));
    setOriginAddress(String(origin.address || summary.origin_address || ""));
    setDistanceMode(normalizeDistanceMode(scenario.distance_mode || summary.distance_mode));

    const routeProfileKey = normalizeRouteCostProfileKey(scenario.route_cost_profile_key, record.route_cost_result?.summary.currency_code);
    const routeProfile = routeCostProfiles[routeProfileKey];
    setRouteCostProfileKey(routeProfileKey);
    setRouteDefaultCity(String(scenario.default_city || routeProfile.defaultCity));
    setRouteDefaultCountry(String(scenario.default_country || routeProfile.defaultCountry));
    setDieselPrice(numberOrDefault(scenario.diesel_price_per_liter, routeProfile.dieselPrice));
    setFuelEfficiency(numberOrDefault(scenario.fuel_efficiency_km_per_liter, 3));

    const referenceResult = normalizeReferenceDistanceResult(record.reference_result);
    const costResult = normalizeRouteCostResult(record.route_cost_result);
    setResult(toolMode === "reference" ? referenceResult : null);
    setRouteCostResult(toolMode === "route_cost" ? costResult : null);
  }

  const routeCostProfile = routeCostProfiles[routeCostProfileKey];
  const resultRows = result?.results || [];
  const resultColumns = useMemo(() => {
    const keys = new Set<string>();
    for (const row of resultRows.slice(0, 20)) {
      Object.keys(row).forEach((key) => keys.add(key));
    }
    const preferred = [
      "source_excel_row",
      "input_address",
      "status",
      "distance_km",
      "duration_min",
      "formatted_address",
      "warning",
    ];
    return [...preferred.filter((key) => keys.has(key)), ...[...keys].filter((key) => !preferred.includes(key)).slice(0, 6)];
  }, [resultRows]);

  const routeRows = routeCostResult?.route_results || [];
  const routeColumns = useMemo(
    () =>
      tableColumns(routeRows, [
        "route_id",
        "bus_type",
        "diesel_cost_status",
        "stops_in_file",
        "resolved_stops",
        "failed_stops",
        "drive_legs",
        "route_distance_km",
        "route_duration_min",
        "estimated_diesel_liters",
        "estimated_one_way_fuel_cost",
      ]),
    [routeRows],
  );
  const legRows = routeCostResult?.leg_results || [];
  const legColumns = useMemo(
    () => tableColumns(legRows, ["route_id", "leg", "from_stop_sequence", "from_address", "to_stop_sequence", "to_address", "distance_km", "duration_min"]),
    [legRows],
  );

  const busy = previewMutation.isPending || runMutation.isPending || routeCostMutation.isPending;
  const activeLoadedRunId =
    loadedHistoryRecord && normalizeDistanceToolMode(loadedHistoryRecord.summary?.tool_mode || loadedHistoryRecord.scenario?.tool_mode) === activeTool
      ? loadedHistoryRecord.run_id
      : undefined;
  const activeSaveResult =
    saveHistoryMutation.data && normalizeDistanceToolMode(saveHistoryMutation.data.job.summary?.tool_mode) === activeTool
      ? saveHistoryMutation.data
      : undefined;
  const activeSavePending = saveHistoryMutation.isPending && saveHistoryMutation.variables?.tool_mode === activeTool;
  const activeSaveError = saveHistoryMutation.variables?.tool_mode === activeTool ? (saveHistoryMutation.error as Error | null) : null;
  const activeSavedRunId = activeSaveResult?.job.run_id;
  const activeHistoryTitle = activeTool === "route_cost" ? "Route Cost History" : "Reference Distance History";
  const activeHistoryEmpty =
    activeTool === "route_cost"
      ? "Saved Route Cost calculations will appear here."
      : "Saved Reference Distance checks will appear here.";

  return (
    <div className="pb-16 lg:pb-0">
      <div
        className={cn(
          "grid gap-4 lg:items-start",
          historyCollapsed ? "lg:grid-cols-[88px_minmax(0,1fr)]" : "lg:grid-cols-[320px_minmax(0,1fr)]",
        )}
      >
        <div ref={historyPanelRef} className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <DistanceCheckerHistoryPanel
            className="min-w-0"
            title={activeHistoryTitle}
            emptyMessage={activeHistoryEmpty}
            toolMode={activeTool}
            jobs={historyQuery.data || []}
            activeRunId={activeLoadedRunId || activeSavedRunId}
            deletingRunId={deletingRunId}
            bulkDeleting={bulkDeleteHistoryMutation.isPending}
            isLoading={historyQuery.isLoading || openHistoryMutation.isPending || deleteHistoryMutation.isPending || bulkDeleteHistoryMutation.isPending}
            error={(historyQuery.error as Error | null) || (openHistoryMutation.error as Error | null) || (deleteHistoryMutation.error as Error | null) || (bulkDeleteHistoryMutation.error as Error | null)}
            collapsed={historyCollapsed}
            onCollapsedChange={setHistoryCollapsed}
            onRefresh={() => void historyQuery.refetch()}
            onOpen={(runId) => {
              setHistoryCollapsed(true);
              openHistoryMutation.mutate({ toolMode: activeTool, runId });
            }}
            onDelete={(runId) => deleteHistoryMutation.mutate({ toolMode: activeTool, runId })}
            onBulkDelete={(runIds) => bulkDeleteHistoryMutation.mutate({ toolMode: activeTool, runIds })}
          />
        </div>

        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-start">
                <div>
                  <p className="text-sm font-medium text-primary">{t("Side tools")}</p>
                  <h1 className="mt-1 text-2xl font-semibold tracking-normal text-foreground">{t("Distance & Cost")}</h1>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                    {t("Measure reference-stop distance or estimate current-plan route distance and one-way diesel cost.")}
                  </p>
                </div>
                <div className="inline-grid shrink-0 grid-cols-2 rounded-md border border-border bg-muted p-1">
                  <ToolTab active={activeTool === "reference"} onClick={() => handleToolChange("reference")}>
                    {t("Reference Distance")}
                  </ToolTab>
                  <ToolTab active={activeTool === "route_cost"} onClick={() => handleToolChange("route_cost")}>
                    {t("Route Cost")}
                  </ToolTab>
                </div>
              </div>
            </CardHeader>
          </Card>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">{t("Workbook")}</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/60 px-4 py-6 text-center transition hover:border-primary/60 hover:bg-muted">
                <Upload className="mb-3 h-6 w-6 text-primary" aria-hidden="true" />
                <span className="text-sm font-medium">{file?.name || t("Select address workbook")}</span>
                <span className="mt-1 text-xs text-muted-foreground">.xlsx or .xlsm</span>
                <input
                  className="sr-only"
                  type="file"
                  accept=".xlsx,.xlsm"
                  onChange={(event) => void handleFileChange(event.target.files?.[0] || null)}
                />
              </label>
              {fileError ? <InlineError message={fileError} /> : null}
              {previewMutation.error ? <InlineError message={(previewMutation.error as Error).message} /> : null}
              {previewMutation.isPending ? (
                <div className="flex items-center gap-2 rounded-md border border-border bg-muted/60 px-3 py-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
                  {t("Reading workbook...")}
                </div>
              ) : preview ? (
                <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                  {t("Workbook parameters detected automatically.")}
                </div>
              ) : null}
            </CardContent>
          </Card>

          {preview ? (
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">{t("Workbook preview")}</h2>
                  <Badge tone="info">{template(t("{count} rows"), { count: formatNumber(preview.row_count) })}</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">{t("Detected sheet")}:</span> {selectedSheet}
                  <span className="mx-2 text-border">|</span>
                  {activeTool === "route_cost"
                    ? t("Route, address, order, city, and country columns are detected from the workbook.")
                    : t("Address, city, and country columns are detected from the workbook.")}
                </div>
                {!addressColumn || (activeTool === "route_cost" && !routeColumn) ? (
                  <InlineError message={t("Required workbook columns were not detected. Check the workbook headers.")} />
                ) : null}
                <DataPreview rows={preview.sample_rows} />
              </CardContent>
            </Card>
          ) : null}

          {activeTool === "reference" && result ? (
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">{t("Results")}</h2>
                  <Badge tone="success">{template(t("{count} resolved"), { count: formatNumber(result.summary.resolved_count) })}</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-4">
                  <Metric label="Rows" value={result.summary.row_count} />
                  <Metric label="Resolved" value={result.summary.resolved_count} />
                  <Metric label="Failed" value={result.summary.failed_count} />
                  <Metric label="Blank" value={result.summary.blank_count} />
                </div>
                <DistanceHistoryAutoSaveStatus
                  historyTitle={activeHistoryTitle}
                  isSaving={activeSavePending}
                  saveError={activeSaveError}
                  saveResult={activeSaveResult}
                />
                <ResultTable rows={resultRows} columns={resultColumns} />
              </CardContent>
            </Card>
          ) : null}

          {activeTool === "route_cost" && routeCostResult ? (
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">{t("Route Cost Results")}</h2>
                  <Badge tone="success">{formatNumber(routeCostResult.summary.route_count)} {t("routes")}</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-4">
                  <Metric label="One-Way Distance km" value={routeCostResult.summary.total_one_way_distance_km} />
                  <Metric
                    label={`Diesel Cost ${routeCostResult.summary.currency_label}`}
                    value={formatCurrency(routeCostResult.summary.estimated_one_way_fuel_cost, routeCostResult.summary.currency_code)}
                  />
                  <Metric label="Routes With Failed Stops" value={routeCostResult.summary.routes_with_unresolved_stops} />
                  <Metric label="Electric Routes Skipped" value={routeCostResult.summary.electric_routes_skipped} />
                </div>
                <DistanceHistoryAutoSaveStatus
                  historyTitle={activeHistoryTitle}
                  isSaving={activeSavePending}
                  saveError={activeSaveError}
                  saveResult={activeSaveResult}
                />
                <ResultTable rows={routeRows} columns={routeColumns} />
                {legRows.length ? (
                  <details className="rounded-md border border-border bg-muted/40">
                    <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">{t("Leg-by-leg details")}</summary>
                    <div className="border-t border-border">
                      <ResultTable rows={legRows} columns={legColumns} />
                    </div>
                  </details>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </div>

        <aside className="space-y-4">
          {activeTool === "reference" ? (
            <>
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Distance Target")}</h2>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Field label="Country">
                    <input className={fieldClassName} value={originCountry} onChange={(event) => {
                      setOriginCountry(event.target.value);
                      clearDistanceResult();
                    }} />
                  </Field>
                  <Field label="City">
                    <input className={fieldClassName} value={originCity} onChange={(event) => {
                      setOriginCity(event.target.value);
                      clearDistanceResult();
                    }} />
                  </Field>
                  <Field label="Target Address">
                    <input
                      className={fieldClassName}
                      value={originAddress}
                      placeholder={t("School gate or reference stop address")}
                      onChange={(event) => {
                      setOriginAddress(event.target.value);
                      clearDistanceResult();
                    }} />
                  </Field>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {t("Workbook addresses are measured to this target address.")}
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Ruler className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Mode")}</h2>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-2 gap-2">
                    <ModeButton active={distanceMode === "road"} onClick={() => {
                      setDistanceMode("road");
                      clearDistanceResult();
                    }}>
                      {t("Road")}
                    </ModeButton>
                    <ModeButton active={distanceMode === "straight_line"} onClick={() => {
                      setDistanceMode("straight_line");
                      clearDistanceResult();
                    }}>
                      {t("Straight")}
                    </ModeButton>
                  </div>
                  {runMutation.error ? <InlineError message={(runMutation.error as Error).message} /> : null}
                  <Button
                    type="button"
                    disabled={!preview || !originAddress.trim() || busy}
                    icon={runMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Ruler className="h-4 w-4" />}
                    onClick={() => runMutation.mutate()}
                  >
                    {t("Run check")}
                  </Button>
                </CardContent>
              </Card>
            </>
          ) : (
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Fuel className="h-4 w-4 text-primary" aria-hidden="true" />
                  <h2 className="text-sm font-semibold">{t("Route Cost Settings")}</h2>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <Field label="Market">
                  <select className={fieldClassName} value={routeCostProfileKey} onChange={(event) => {
                    applyRouteCostProfileKey(event.target.value as keyof typeof routeCostProfiles);
                  }}>
                    {Object.entries(routeCostProfiles).map(([key, profile]) => (
                      <option key={key} value={key}>
                        {profile.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Default Country">
                  <input className={fieldClassName} value={routeDefaultCountry} onChange={(event) => {
                    setRouteDefaultCountry(event.target.value);
                    clearRouteCostResult();
                  }} />
                </Field>
                <Field label="Default City">
                  <input className={fieldClassName} value={routeDefaultCity} onChange={(event) => {
                    setRouteDefaultCity(event.target.value);
                    clearRouteCostResult();
                  }} />
                </Field>
                <Field label={`Diesel Price (${routeCostProfile.currencyCode}/L)`}>
                  <input
                    className={fieldClassName}
                    type="number"
                    min="0"
                    step={routeCostProfile.dieselStep}
                    value={dieselPrice}
                    onChange={(event) => {
                      setDieselPrice(Number(event.target.value));
                      clearRouteCostResult();
                    }}
                  />
                </Field>
                <Field label="Fuel Efficiency (km/L)">
                  <input
                    className={fieldClassName}
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={fuelEfficiency}
                    onChange={(event) => {
                      setFuelEfficiency(Number(event.target.value));
                      clearRouteCostResult();
                    }}
                  />
                </Field>
                <p className="text-xs leading-5 text-muted-foreground">
                  Electric, e-bus, EV, and new-energy bus types keep distance results but skip diesel-cost estimation.
                </p>
                {routeCostMutation.error ? <InlineError message={(routeCostMutation.error as Error).message} /> : null}
                <Button
                  type="button"
                  disabled={!preview || !routeColumn || !addressColumn || busy}
                  icon={routeCostMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Calculator className="h-4 w-4" />}
                  onClick={() => routeCostMutation.mutate()}
                >
                  Calculate cost
                </Button>
              </CardContent>
            </Card>
          )}
          </aside>
          </div>
        </div>
      </div>
    </div>
  );
}

function DistanceCheckerHistoryPanel({
  className,
  title,
  emptyMessage,
  toolMode,
  jobs,
  activeRunId,
  deletingRunId,
  bulkDeleting,
  isLoading,
  error,
  collapsed,
  onCollapsedChange,
  onRefresh,
  onOpen,
  onDelete,
  onBulkDelete,
}: {
  className?: string;
  title: string;
  emptyMessage: string;
  toolMode: DistanceCheckerToolMode;
  jobs: DistanceCheckerHistorySummary[];
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
            aria-label={`${t("Open")} ${t(title)}`}
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
            <button type="button" className={buttonClassName("ghost")} aria-label={`${t("Refresh")} ${t(title)}`} title={t("Refresh history")} onClick={onRefresh}>
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
            <h2 className="text-sm font-semibold">{t(title)}</h2>
          </div>
          <div className="flex items-center gap-1">
            <button type="button" className={buttonClassName("ghost")} aria-label={`${t("Refresh")} ${t(title)}`} onClick={onRefresh}>
              <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} aria-hidden="true" />
            </button>
            <button
              type="button"
              className={buttonClassName("ghost")}
              aria-label={`${t("Collapse")} ${t(title)}`}
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
            {emptyMessage}
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
                  if (runIds.length && window.confirm(t("Delete selected Distance & Cost history runs? This cannot be undone."))) {
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
            const isReference = toolMode === "reference";
            const isActive = activeRunId === job.run_id;
            const isDeleting = deletingRunId === job.run_id;
            const badgeLabel = isReference ? distanceModeLabel(summary.distance_mode) : String(summary.currency_label || summary.currency_code || "Cost");
            return (
              <div
                key={job.run_id}
                className={cn(
                  "flex items-stretch gap-1 rounded-md border p-2 transition",
                  isActive ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface hover:bg-muted",
                )}
              >
                {selecting ? (
                  <input
                    type="checkbox"
                    className="mt-2 h-4 w-4 shrink-0 accent-primary"
                    checked={selectedRunIds.has(job.run_id)}
                    aria-label={`${t("Select")} ${job.title || t("Distance & Cost Run")}`}
                    onChange={() => toggleSelected(job.run_id)}
                  />
                ) : null}
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onOpen(job.run_id)}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{job.title || t("Distance & Cost Run")}</div>
                      <div className={cn("mt-1 text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                        {formatDateTime(job.created_at)}
                      </div>
                      <div className={cn("mt-1 truncate text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                        {t("Submitted by")} {job.owner_email || t("Unknown")}
                      </div>
                    </div>
                    <Badge tone={isActive ? "neutral" : isReference ? "info" : "success"}>{badgeLabel}</Badge>
                  </div>
                  <div className={cn("mt-2 grid grid-cols-2 gap-1 text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                    {isReference ? (
                      <>
                        <span>{formatNumber(summary.resolved_count)} {t("resolved")}</span>
                        <span>{formatNumber(summary.failed_count)} {t("failed")}</span>
                      </>
                    ) : (
                      <>
                        <span>{formatNumber(summary.route_count)} {t("routes")}</span>
                        <span>{formatNumber(summary.total_one_way_distance_km)} km</span>
                      </>
                    )}
                  </div>
                </button>
                {!selecting ? (
                  <button
                  type="button"
                  className={cn(
                    "flex h-9 w-9 shrink-0 items-center justify-center rounded-md border transition",
                    isActive
                      ? "border-primary-foreground/30 text-primary-foreground/80 hover:bg-primary-foreground/10 hover:text-primary-foreground"
                      : "border-transparent text-muted-foreground hover:border-border hover:bg-surface hover:text-destructive",
                  )}
                  aria-label={`${t("Delete")} ${job.title || t("Distance & Cost Run")}`}
                  disabled={isDeleting}
                  onClick={() => {
                    if (window.confirm(t("Delete this Distance & Cost history run? This cannot be undone."))) {
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

function DistanceHistoryAutoSaveStatus({
  historyTitle,
  isSaving,
  saveError,
  saveResult,
}: {
  historyTitle: string;
  isSaving: boolean;
  saveError?: Error | null;
  saveResult?: DistanceCheckerHistoryCreateResponse;
}) {
  const t = useT();
  if (isSaving) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
        {t("Saving to")} {historyTitle}...
      </div>
    );
  }
  if (saveError) {
    return <InlineError message={`${t("History autosave failed")}: ${saveError.message}`} />;
  }
  if (saveResult?.job?.run_id) {
    return (
      <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        {t("Saved to")} {historyTitle}.
      </div>
    );
  }
  return null;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  const t = useT();
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{t(label)}</span>
      {children}
    </label>
  );
}

function ToolTab({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      className={cn(
        "h-9 rounded px-3 text-sm font-medium transition",
        active ? "bg-surface text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
      )}
      onClick={onClick}
    >
      {children}
    </button>
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

function Metric({ label, value }: { label: string; value: ReactNode }) {
  const t = useT();
  return (
    <div className="rounded-md border border-border bg-muted/50 p-3">
      <div className="text-xs text-muted-foreground">{t(label)}</div>
      <div className="mt-1 text-lg font-semibold">{typeof value === "number" ? formatNumber(value) : value}</div>
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
          {rows.slice(0, 100).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="max-w-72 truncate px-3 py-2">
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

function DataPreview({ rows }: { rows: Array<Record<string, unknown>> }) {
  const columns = Object.keys(rows[0] || {}).slice(0, 8);
  if (!rows.length || !columns.length) {
    return null;
  }
  return (
    <div className="overflow-auto rounded-md border border-border">
      <table className="min-w-full divide-y divide-border text-left text-xs">
        <thead className="bg-muted text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="max-w-60 truncate px-3 py-2">
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

function tableColumns(rows: Array<Record<string, unknown>>, preferred: string[]) {
  const keys = new Set<string>();
  for (const row of rows.slice(0, 20)) {
    Object.keys(row).forEach((key) => keys.add(key));
  }
  return [...preferred.filter((key) => keys.has(key)), ...[...keys].filter((key) => !preferred.includes(key)).slice(0, 8)];
}

function formatCurrency(value: number, currencyCode: string) {
  if (currencyCode === "KRW") {
    return formatNumber(Math.round(value));
  }
  return formatNumber(Number(value.toFixed(2)));
}

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "number") {
    return formatNumber(value);
  }
  return String(value);
}

function template(text: string, values: Record<string, string | number>) {
  return text.replace(/\{(\w+)\}/g, (match, key) => String(values[key] ?? match));
}

function firstPreviewValue(preview: DistanceWorkbookPreview, column?: string) {
  if (!column) {
    return "";
  }
  for (const row of preview.sample_rows) {
    const value = row[column];
    if (value !== null && value !== undefined && String(value).trim()) {
      return String(value).trim();
    }
  }
  return "";
}

function normalizeWorkbookCountry(value: string) {
  const normalized = value.trim().toLowerCase();
  if (["cn", "china", "中国", "中國"].includes(normalized)) {
    return "China";
  }
  if (["kr", "korea", "south korea", "republic of korea", "韩国", "韓國", "대한민국", "한국"].includes(normalized)) {
    return "South Korea";
  }
  return value.trim();
}

function inferProfileKey(country: string, city: string): keyof typeof routeCostProfiles | null {
  const text = `${country} ${city}`.toLowerCase();
  if (text.includes("china") || text.includes("中国") || text.includes("上海") || text.includes("shanghai")) {
    return "china";
  }
  if (text.includes("korea") || text.includes("韩国") || text.includes("韓國") || text.includes("서울") || text.includes("seoul")) {
    return "korea";
  }
  return null;
}

function defaultDistanceHistoryTitle(toolMode: "reference" | "route_cost") {
  const label = toolMode === "route_cost" ? "Route Cost Run" : "Reference Distance Run";
  const timestamp = new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
  return `${label} - ${timestamp}`;
}

function normalizeDistanceToolMode(value: unknown): "reference" | "route_cost" {
  return String(value || "").toLowerCase() === "route_cost" ? "route_cost" : "reference";
}

function normalizeReferenceDistanceResult(value: unknown): ReferenceDistanceResponse | null {
  const record = asRecord(value);
  const summary = asRecord(record.summary);
  if (!("resolved_count" in summary) || !Array.isArray(record.results)) {
    return null;
  }
  return value as ReferenceDistanceResponse;
}

function normalizeRouteCostResult(value: unknown): RouteCostResponse | null {
  const record = asRecord(value);
  const summary = asRecord(record.summary);
  if (!("route_count" in summary) || !Array.isArray(record.route_results) || !Array.isArray(record.leg_results)) {
    return null;
  }
  return value as RouteCostResponse;
}

function normalizeDistanceMode(value: unknown): "road" | "straight_line" {
  return String(value || "").toLowerCase() === "straight_line" ? "straight_line" : "road";
}

function distanceModeLabel(value: unknown) {
  return normalizeDistanceMode(value) === "straight_line" ? "Straight" : "Road";
}

function normalizeRouteCostProfileKey(value: unknown, currencyCode?: unknown): keyof typeof routeCostProfiles {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "china" || normalized === "korea") {
    return normalized;
  }
  return String(currencyCode || "").toUpperCase() === "CNY" ? "china" : "korea";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function numberOrDefault(value: unknown, fallback: number) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue : fallback;
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
