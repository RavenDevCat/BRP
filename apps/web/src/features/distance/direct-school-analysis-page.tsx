import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type WheelEvent as ReactWheelEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BusFront,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  FileSpreadsheet,
  Loader2,
  MapPinned,
  Play,
  RefreshCw,
  Route,
  RotateCcw,
  Search,
  Upload,
} from "lucide-react";
import MapView, { Layer, NavigationControl, Source, type MapRef } from "react-map-gl/maplibre";
import type { FeatureCollection, LineString, Point } from "geojson";
import "maplibre-gl/dist/maplibre-gl.css";
import { HistorySidebar } from "@/components/history-sidebar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  cancelDirectSchoolAnalysisJob,
  createDirectSchoolAnalysisJob,
  deleteDirectSchoolAnalysisJob,
  getDeploymentFeatures,
  getDirectSchoolAnalysisExportUrl,
  getDirectSchoolAnalysisJob,
  getWorkbookTemplateUrl,
  listDirectSchoolAnalysisJobs,
  previewDirectSchoolAnalysis,
  retryDirectSchoolAnalysisJob,
  type DirectSchoolAnalysisConfig,
  type DirectSchoolJobRecord,
  type DirectSchoolJobSummary,
  type DirectSchoolStopResult,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useT } from "@/lib/i18n/context";

type DistancePageMode = "reference" | "route_cost" | "direct_school";
type ClassificationFilter = "all" | "direct_over_limit" | "route_only_over_limit" | "additional_window_candidate" | "within_limit" | "data_review";

const DEFAULT_CONFIG: DirectSchoolAnalysisConfig = {
  service_direction: "To School",
  stop_service_minutes: 1,
  time_window_start: "06:30",
  time_window_end: "08:00",
  from_school_departure_time: "15:40",
  far_duration_minutes: 45,
};

const fieldClassName =
  "h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none transition focus:border-primary";

const MAP_STYLE = {
  version: 8 as const,
  sources: {
    osm: {
      type: "raster" as const,
      tiles: ["/api/map-tiles/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster" as const, source: "osm" }],
};

export function DirectSchoolAnalysisPage({
  onToolChange,
}: {
  onToolChange: (mode: DistancePageMode) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [config, setConfig] = useState<DirectSchoolAnalysisConfig>(DEFAULT_CONFIG);
  const [customName, setCustomName] = useState("");
  const [scheduled, setScheduled] = useState(false);
  const [scheduledTime, setScheduledTime] = useState("06:30");
  const [scheduledDates, setScheduledDates] = useState<string[]>([]);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [calendarMonth, setCalendarMonth] = useState(monthKey(new Date()));
  const [calendarDraft, setCalendarDraft] = useState<string[]>([]);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [deletingJobId, setDeletingJobId] = useState("");
  const [selectedStopKey, setSelectedStopKey] = useState("");
  const [selectionRevision, setSelectionRevision] = useState(0);
  const [classificationFilter, setClassificationFilter] = useState<ClassificationFilter>("all");
  const [searchText, setSearchText] = useState("");

  const featuresQuery = useQuery({
    queryKey: ["deployment-features"],
    queryFn: getDeploymentFeatures,
  });
  const historyQuery = useQuery({
    queryKey: ["direct-school-history"],
    queryFn: listDirectSchoolAnalysisJobs,
    refetchInterval: (query) =>
      (query.state.data || []).some((item) => isActiveStatus(item.status)) ? 3000 : false,
  });
  const detailQuery = useQuery({
    queryKey: ["direct-school-job", selectedJobId],
    queryFn: () => getDirectSchoolAnalysisJob(selectedJobId),
    enabled: Boolean(selectedJobId),
    refetchInterval: (query) => (isActiveStatus(query.state.data?.status) ? 2500 : false),
  });

  const previewMutation = useMutation({
    mutationFn: () => {
      if (!file || !fileBase64) throw new Error(t("Select a workbook first."));
      return previewDirectSchoolAnalysis({
        file_name: file.name,
        file_base64: fileBase64,
        config: { service_direction: config.service_direction },
        analysis_config: config,
      });
    },
    onSuccess: (preview) => {
      setConfig((current) => ({
        ...current,
        ...preview.analysis_config,
        service_direction: preview.service_direction,
      }));
      if (preview.service_direction === "From School" && scheduledTime === "06:30") {
        setScheduledTime(preview.analysis_config.from_school_departure_time || "15:40");
      }
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64 || !previewMutation.data) {
        throw new Error(t("Validate an Audit workbook first."));
      }
      if (scheduled && !scheduledDates.length) {
        throw new Error(t("Select at least one schedule date."));
      }
      const dates = scheduled ? [...scheduledDates].sort() : [""];
      const created = [];
      for (const date of dates) {
        const datedName = scheduled && dates.length > 1 && customName.trim()
          ? `${customName.trim()} - ${date}`
          : customName.trim();
        created.push(
          await createDirectSchoolAnalysisJob({
            file_name: file.name,
            file_base64: fileBase64,
            config: { service_direction: config.service_direction },
            analysis_config: config,
            job_custom_name: datedName || undefined,
            scheduled_job: scheduled,
            scheduled_date: date || undefined,
            scheduled_time: scheduled ? scheduledTime : undefined,
          }),
        );
      }
      return created;
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["direct-school-history"] });
      const firstId = created[0]?.job.job_id || "";
      setSelectedJobId(firstId);
      setSelectedStopKey("");
      setSelectionRevision(0);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (jobIds: string[]) => {
      for (const jobId of jobIds) await deleteDirectSchoolAnalysisJob(jobId);
      return jobIds;
    },
    onMutate: (jobIds) => setDeletingJobId(jobIds.length === 1 ? jobIds[0] : "bulk"),
    onSuccess: async (jobIds) => {
      if (jobIds.includes(selectedJobId)) setSelectedJobId("");
      await queryClient.invalidateQueries({ queryKey: ["direct-school-history"] });
    },
    onSettled: () => setDeletingJobId(""),
  });
  const cancelMutation = useMutation({
    mutationFn: cancelDirectSchoolAnalysisJob,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["direct-school-history"] });
      await queryClient.invalidateQueries({ queryKey: ["direct-school-job", selectedJobId] });
    },
  });
  const retryMutation = useMutation({
    mutationFn: retryDirectSchoolAnalysisJob,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["direct-school-history"] });
      await queryClient.invalidateQueries({ queryKey: ["direct-school-job", selectedJobId] });
    },
  });

  useEffect(() => {
    if (file && fileBase64) previewMutation.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileBase64]);

  useEffect(() => {
    const result = detailQuery.data?.result;
    if (!result?.stops?.length) return;
    if (!selectedStopKey || !result.stops.some((row) => row.stop_key === selectedStopKey)) {
      setSelectedStopKey(result.stops[0].stop_key);
    }
  }, [detailQuery.data?.result, selectedStopKey]);

  async function handleFileChange(nextFile: File | null) {
    setFile(nextFile);
    setFileBase64("");
    setFileError("");
    previewMutation.reset();
    createMutation.reset();
    if (!nextFile) return;
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

  function updateConfig(patch: Partial<DirectSchoolAnalysisConfig>) {
    setConfig((current) => ({ ...current, ...patch }));
    createMutation.reset();
  }

  const selectedRecord = detailQuery.data || null;
  const rawResult = selectedRecord?.result;
  const result = rawResult && Array.isArray(rawResult.stops) && !isActiveStatus(selectedRecord?.status) ? rawResult : null;
  const filteredStops = useMemo(() => {
    const rows = result?.stops || [];
    const search = searchText.trim().toLowerCase();
    return rows.filter((row) => {
      if (classificationFilter !== "all" && operationalCategory(row) !== classificationFilter) return false;
      if (!search) return true;
      return [row.address, row.city, row.primary_route_id, ...(row.route_ids || [])]
        .join(" ")
        .toLowerCase()
        .includes(search);
    });
  }, [classificationFilter, result?.stops, searchText]);
  const selectedStop = result?.stops.find((row) => row.stop_key === selectedStopKey) || filteredStops[0] || null;
  const scheduledEnabled = featuresQuery.data?.scheduled_jobs_enabled === true;
  const selectStop = (stopKey: string) => {
    setSelectedStopKey(stopKey);
    setSelectionRevision((value) => value + 1);
  };

  return (
    <div className="pb-16 lg:pb-0">
      <div className={cn("grid gap-4 lg:items-start", historyCollapsed ? "lg:grid-cols-[88px_minmax(0,1fr)]" : "lg:grid-cols-[320px_minmax(0,1fr)]")}>
        <HistorySidebar
          items={historyQuery.data || []}
          itemId={(job) => job.job_id}
          itemName={(job) => historyName(job)}
          activeId={selectedJobId || undefined}
          title={t("Analysis History")}
          emptyMessage={t("Direct-to-School measurements will appear here.")}
          collapsed={historyCollapsed}
          onCollapsedChange={setHistoryCollapsed}
          isLoading={historyQuery.isLoading}
          isFetching={historyQuery.isFetching}
          error={(historyQuery.error as Error | null) || (detailQuery.error as Error | null) || (deleteMutation.error as Error | null)}
          deletingId={deletingJobId}
          bulkDeleting={deleteMutation.isPending && deletingJobId === "bulk"}
          onRefresh={() => void historyQuery.refetch()}
          onOpen={(jobId) => {
            setSelectedJobId(jobId);
            setSelectedStopKey("");
            setSelectionRevision(0);
          }}
          onDelete={(jobId) => deleteMutation.mutate([jobId])}
          onBulkDelete={(jobIds) => deleteMutation.mutate(jobIds)}
          groupScope="distance_direct_school"
          renderItem={(job, active) => <DirectSchoolHistoryItem job={job} active={active} />}
          className="min-w-0 lg:sticky lg:top-20 lg:self-start"
        />

        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col justify-between gap-3 2xl:flex-row 2xl:items-start">
                <div>
                  <p className="text-sm font-medium text-primary">{t("Planning tools")}</p>
                  <h1 className="mt-1 text-2xl font-semibold tracking-normal text-foreground">{t("Distance & Cost")}</h1>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                    {t("Measure direct and current-route travel times, then identify which students must be separated for every route to meet the time window.")}
                  </p>
                </div>
                <div className="grid w-full grid-cols-3 rounded-md border border-border bg-muted p-1 2xl:w-auto">
                  <ToolTab active={false} onClick={() => onToolChange("reference")}>{t("Reference Distance")}</ToolTab>
                  <ToolTab active={false} onClick={() => onToolChange("route_cost")}>{t("Route Cost")}</ToolTab>
                  <ToolTab active onClick={() => undefined}>{t("Direct-to-School")}</ToolTab>
                </div>
              </div>
            </CardHeader>
          </Card>

          <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="min-w-0 space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Audit workbook")}</h2>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-col items-start gap-2">
                    <p className="text-sm text-muted-foreground">{t("Use the same completed workbook accepted by Route Audit.")}</p>
                    <a className={buttonClassName("secondary")} href={getWorkbookTemplateUrl()}>
                      <Download className="h-4 w-4" aria-hidden="true" />
                      {t("Template")}
                    </a>
                  </div>
                  <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/60 px-4 py-6 text-center transition hover:border-primary/60 hover:bg-muted">
                    <Upload className="mb-3 h-6 w-6 text-primary" aria-hidden="true" />
                    <span className="text-sm font-medium">{file?.name || t("Select workbook")}</span>
                    <span className="mt-1 text-xs text-muted-foreground">current_plan_assignments + current_plan_fleet</span>
                    <input
                      className="sr-only"
                      type="file"
                      accept=".xlsx,.xlsm"
                      onChange={(event) => {
                        const nextFile = event.currentTarget.files?.[0] || null;
                        event.currentTarget.value = "";
                        void handleFileChange(nextFile);
                      }}
                    />
                  </label>
                  {fileError ? <InlineError message={fileError} /> : null}
                  {previewMutation.error ? <InlineError message={(previewMutation.error as Error).message} /> : null}
                  {previewMutation.isPending ? <LoadingLine text={t("Validating Audit workbook and geocoding addresses...")} /> : null}
                  {previewMutation.data ? (
                    <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-800">
                      <div className="font-medium">{t("Audit workbook validated")}</div>
                      <div className="mt-1 text-xs">
                        {previewMutation.data.school.address} · {formatNumber(previewMutation.data.summary.unique_address_count)} {t("addresses")} · {formatNumber(previewMutation.data.summary.route_count)} {t("routes")}
                      </div>
                    </div>
                  ) : null}
                </CardContent>
              </Card>

              {result ? (
                <>
                  <ResultSummary record={selectedRecord!} />
                  <AddressClassificationBoard
                    rows={filteredStops}
                    allRows={result.stops}
                    selectedStopKey={selectedStop?.stop_key || ""}
                    filter={classificationFilter}
                    search={searchText}
                    onFilter={setClassificationFilter}
                    onSearch={setSearchText}
                    onSelect={selectStop}
                  />
                  <div className="grid min-w-0 gap-4">
                    <DirectSchoolMap result={result} selectedStop={selectedStop} selectionRevision={selectionRevision} onSelect={selectStop} />
                    <DistanceScatter rows={result.stops} selectedStopKey={selectedStop?.stop_key || ""} onSelect={selectStop} />
                  </div>
                  <StopDetailTable rows={filteredStops} selectedStopKey={selectedStop?.stop_key || ""} onSelect={selectStop} />
                  <MultiDayPanel record={selectedRecord!} />
                  <RouteRecoveryPanel rows={result.route_window_analysis} />
                </>
              ) : selectedRecord ? (
                <PendingResult record={selectedRecord} />
              ) : null}
            </div>

            <aside className="min-w-0 space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Route className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Analysis settings")}</h2>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Field label="Analysis direction">
                    <select
                      className={fieldClassName}
                      value={config.service_direction}
                      onChange={(event) => {
                        const direction = event.target.value as DirectSchoolAnalysisConfig["service_direction"];
                        updateConfig(direction === "To School"
                          ? { service_direction: direction, time_window_start: "06:30", time_window_end: "08:00" }
                          : { service_direction: direction, from_school_departure_time: "15:40", time_window_start: "15:40", time_window_end: "17:40" });
                      }}
                    >
                      <option value="To School">{t("To School")}</option>
                      <option value="From School">{t("From School")}</option>
                    </select>
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <NumberField label="Student trip time limit (min)" value={config.far_duration_minutes} min={1} step={5} onChange={(value) => updateConfig({ far_duration_minutes: value })} />
                    <NumberField label="Per-stop dwell time (min)" value={config.stop_service_minutes} min={0} step={0.5} onChange={(value) => updateConfig({ stop_service_minutes: value })} />
                    <Field label="Route operating window start">
                      <input type="time" className={fieldClassName} value={config.time_window_start} onChange={(event) => updateConfig({
                        time_window_start: event.target.value,
                        ...(config.service_direction === "From School" ? { from_school_departure_time: event.target.value } : {}),
                      })} />
                    </Field>
                    <Field label="Route operating window end">
                      <input type="time" className={fieldClassName} value={config.time_window_end} onChange={(event) => updateConfig({ time_window_end: event.target.value })} />
                    </Field>
                  </div>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {t("The student trip time limit applies to both direct and current-route travel. Route recovery uses the operating window below, while distance is reported only.")}
                  </p>
                </CardContent>
              </Card>

              {scheduledEnabled ? (
                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <CalendarDays className="h-4 w-4 text-primary" aria-hidden="true" />
                      <h2 className="text-sm font-semibold">{t("Execution")}</h2>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={scheduled}
                      className="grid h-9 w-full grid-cols-2 rounded-full border border-border bg-surface p-1 text-xs font-medium"
                      onClick={() => setScheduled((value) => !value)}
                    >
                      <span className={cn("flex items-center justify-center rounded-full", !scheduled && "bg-primary text-primary-foreground")}>{t("Run now")}</span>
                      <span className={cn("flex items-center justify-center rounded-full", scheduled && "bg-primary text-primary-foreground")}>{t("Scheduled")}</span>
                    </button>
                    {scheduled ? (
                      <>
                        <Field label="Measurement time">
                          <input className={fieldClassName} type="time" value={scheduledTime} onChange={(event) => setScheduledTime(event.target.value)} />
                        </Field>
                        <Button
                          type="button"
                          variant="secondary"
                          className="w-full"
                          icon={<CalendarDays className="h-4 w-4" />}
                          onClick={() => {
                            setCalendarDraft(scheduledDates);
                            setCalendarOpen(true);
                          }}
                        >
                          {scheduledDates.length ? `${scheduledDates.length} ${t("dates selected")}` : t("Select schedule dates")}
                        </Button>
                        <p className="text-xs leading-5 text-muted-foreground">{t("Each selected date creates one independent traffic snapshot.")}</p>
                      </>
                    ) : null}
                  </CardContent>
                </Card>
              ) : null}

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Play className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">{t("Create analysis")}</h2>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Field label="Custom Job Name">
                    <input className={fieldClassName} value={customName} placeholder={t("Student travel-time review")} onChange={(event) => setCustomName(event.target.value)} />
                  </Field>
                  {previewMutation.data ? (
                    <div className="rounded-md border border-border bg-muted/50 p-3">
                      <div className="text-xs text-muted-foreground">{t("Estimated provider requests")}</div>
                      <div className="mt-1 text-xl font-semibold">{formatNumber(previewMutation.data.summary.estimated_logical_provider_calls)}</div>
                      <div className="mt-1 text-xs text-muted-foreground">{t("Actual API calls may be higher when long routes require provider waypoint chunks.")}</div>
                    </div>
                  ) : null}
                  {createMutation.error ? <InlineError message={(createMutation.error as Error).message} /> : null}
                  <Button
                    type="button"
                    className="w-full"
                    disabled={!previewMutation.data || createMutation.isPending || (scheduled && !scheduledDates.length)}
                    icon={createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : scheduled ? <CalendarDays className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                    onClick={() => createMutation.mutate()}
                  >
                    {scheduled ? t("Create scheduled snapshots") : t("Run analysis")}
                  </Button>
                </CardContent>
              </Card>

              {selectedRecord ? (
                <Card>
                  <CardHeader><h2 className="text-sm font-semibold">{t("Job actions")}</h2></CardHeader>
                  <CardContent className="space-y-2">
                    {isActiveStatus(selectedRecord.status) ? (
                      <Button type="button" variant="secondary" className="w-full" disabled={cancelMutation.isPending} onClick={() => cancelMutation.mutate(selectedRecord.job_id)}>{t("Cancel job")}</Button>
                    ) : null}
                    {selectedRecord.status === "failed" || selectedRecord.result?.status === "partial" ? (
                      <Button type="button" variant="secondary" className="w-full" disabled={retryMutation.isPending} icon={<RefreshCw className={cn("h-4 w-4", retryMutation.isPending && "animate-spin")} />} onClick={() => retryMutation.mutate(selectedRecord.job_id)}>{t("Retry missing measurements")}</Button>
                    ) : null}
                    {result ? (
                      <a className={cn(buttonClassName("secondary"), "w-full")} href={getDirectSchoolAnalysisExportUrl(selectedRecord.job_id)}>
                        <Download className="h-4 w-4" aria-hidden="true" />
                        {t("Download Excel")}
                      </a>
                    ) : null}
                    {(cancelMutation.error || retryMutation.error) ? <InlineError message={String((cancelMutation.error || retryMutation.error) as Error)} /> : null}
                  </CardContent>
                </Card>
              ) : null}
            </aside>
          </div>
        </div>
      </div>

      {calendarOpen ? (
        <ScheduleCalendar
          month={calendarMonth}
          selectedDates={calendarDraft}
          onMonthChange={setCalendarMonth}
          onToggle={(date) => setCalendarDraft((current) => current.includes(date) ? current.filter((item) => item !== date) : [...current, date])}
          onCancel={() => setCalendarOpen(false)}
          onConfirm={() => {
            setScheduledDates([...calendarDraft].sort());
            setCalendarOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}

function ResultSummary({ record }: { record: DirectSchoolJobRecord }) {
  const t = useT();
  const result = record.result;
  if (!result) return null;
  const conclusion = result.operational_conclusion;
  const totalRiders = result.stops.reduce((total, row) => total + Math.max(0, Number(row.riders || 0)), 0);
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Operational conclusion")}</h2>
          </div>
          <Badge tone={result.status === "complete" ? "success" : "warning"}>{t(result.status === "complete" ? "Complete" : "Partial")}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {conclusion ? (
          <>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              <span>{t("Student trip limit")}: <strong className="text-foreground">{formatNumber(conclusion.duration_limit_min)} min</strong></span>
              <span>{t("Route time window")}: <strong className="text-foreground">{formatNumber(conclusion.route_window_min)} min</strong></span>
              <span>{t("Times use the live map provider captured for this run.")}</span>
            </div>
            <div className="divide-y divide-border rounded-md border border-border">
              <ConclusionStep
                step="1"
                title={t("Direct trip already exceeds the student trip limit")}
                description={t("These students exceed the configured limit even when travelling directly to school.")}
                riders={conclusion.direct_over_limit.rider_count}
                totalRiders={totalRiders}
                addresses={conclusion.direct_over_limit.address_count}
                tone="danger"
              />
              <ConclusionStep
                step="2"
                title={t("Direct trip fits, but the current route ride exceeds the limit")}
                description={t("Their excess time is caused by the shared route rather than the direct trip itself.")}
                riders={conclusion.route_only_over_limit.rider_count}
                totalRiders={totalRiders}
                addresses={conclusion.route_only_over_limit.address_count}
                tone="warning"
              />
              <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                <div className="flex min-w-0 gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">3</span>
                  <div>
                    <div className="text-sm font-semibold">{t("Route-window recovery after removing the first two groups")}</div>
                    <div className="mt-1 text-xs leading-5 text-muted-foreground">
                      {formatNumber(conclusion.post_primary.over_window_count)} {t("routes still exceed the window after the first removal")}
                      {conclusion.additional_removal.rider_count > 0 ? ` · ${formatNumber(conclusion.additional_removal.rider_count)} ${t("additional students at")} ${formatNumber(conclusion.additional_removal.address_count)} ${t("addresses are suggested for removal")}` : ""}
                    </div>
                  </div>
                </div>
                <Badge tone={conclusion.final.all_measured_routes_within_window ? "success" : conclusion.final.data_review_count > 0 ? "warning" : "danger"}>
                  {conclusion.final.all_measured_routes_within_window
                    ? t("All measured routes fit the window")
                    : conclusion.final.data_review_count > 0
                      ? `${formatNumber(conclusion.final.data_review_count)} ${t("routes need data review")}`
                      : `${formatNumber(conclusion.final.over_window_count)} ${t("routes still exceed the window")}`}
                </Badge>
              </div>
            </div>
          </>
        ) : Number(result.analysis_version || 0) >= 2 ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">
            <div className="font-medium">{t("This analysis stopped before route-window recovery was completed.")}</div>
            <div className="mt-1 text-xs leading-5">{t("Retry the missing measurements to complete the student classification and route-removal conclusion.")}</div>
          </div>
        ) : (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">
            <div className="font-medium">{t("This legacy result does not contain route-window recovery evidence.")}</div>
            <div className="mt-1 text-xs leading-5">{t("Run the analysis again to generate the new student classification and route-removal conclusion.")}</div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ConclusionStep({ step, title, description, riders, totalRiders, addresses, tone }: { step: string; title: string; description: string; riders: number; totalRiders: number; addresses: number; tone: "danger" | "warning" }) {
  const t = useT();
  return (
    <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="flex min-w-0 gap-3">
        <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold", tone === "danger" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-900")}>{step}</span>
        <div><div className="text-sm font-semibold">{title}</div><div className="mt-1 text-xs leading-5 text-muted-foreground">{description}</div></div>
      </div>
      <div className="flex items-baseline gap-4 lg:justify-end">
        <div><span className="text-2xl font-semibold">{formatNumber(riders)}</span><span className="ml-1 text-xs text-muted-foreground">{t("out of")} {formatNumber(totalRiders)} {t("students")}</span></div>
        <div><span className="text-lg font-semibold">{formatNumber(addresses)}</span><span className="ml-1 text-xs text-muted-foreground">{t("addresses")}</span></div>
      </div>
    </div>
  );
}

function RouteRecoveryPanel({ rows }: { rows: NonNullable<DirectSchoolJobRecord["result"]>["route_window_analysis"] }) {
  const t = useT();
  const reviewed = (rows || [])
    .filter((row) => row.primary_removed_riders || row.additional_removed_riders || row.status !== "within_window")
    .sort((left, right) => routeRecoveryPriority(left) - routeRecoveryPriority(right) || String(left.route_id).localeCompare(String(right.route_id), undefined, { numeric: true }));
  if (!reviewed.length) return null;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">{t("Route recovery details")}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{t("Additional-removal candidates are tested from longest to shortest direct trip.")}</p>
          </div>
          <Badge tone="neutral">{formatNumber(reviewed.length)} {t("routes")}</Badge>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-auto">
          <table className="min-w-[850px] w-full divide-y divide-border text-left text-sm">
            <thead className="bg-muted text-xs text-muted-foreground"><tr>{["Route", "Original", "First removal", "After first removal", "Additional removal", "Final", "Status"].map((label) => <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">{t(label)}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">
              {reviewed.map((row) => (
                <tr key={row.route_id} className={cn(row.status === "within_window" && "text-muted-foreground")}>
                  <td className="px-3 py-2 font-medium text-foreground">{row.route_id}</td>
                  <td className="px-3 py-2">{minutes(row.original_duration_min)}</td>
                  <td className="px-3 py-2">{formatNumber(row.primary_removed_riders || 0)} {t("students")}</td>
                  <td className="px-3 py-2">{minutes(row.post_primary_duration_min)}</td>
                  <td className="px-3 py-2">{formatNumber(row.additional_removed_riders || 0)} {t("students")}</td>
                  <td className="px-3 py-2">{minutes(row.final_duration_min)}</td>
                  <td className="px-3 py-2"><Badge tone={row.status === "within_window" ? "success" : row.status === "data_review" ? "warning" : "danger"}>{t(row.status === "within_window" ? "Within window" : row.status === "data_review" ? "Data review" : "Still over window")}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function routeRecoveryPriority(row: NonNullable<NonNullable<DirectSchoolJobRecord["result"]>["route_window_analysis"]>[number]) {
  if (row.status === "still_over_window") return 0;
  if (row.status === "data_review") return 1;
  if (Number(row.additional_removed_riders || 0) > 0) return 2;
  if (Number(row.primary_removed_riders || 0) > 0) return 3;
  return 4;
}

function AddressClassificationBoard({
  rows,
  allRows,
  selectedStopKey,
  filter,
  search,
  onFilter,
  onSearch,
  onSelect,
}: {
  rows: DirectSchoolStopResult[];
  allRows: DirectSchoolStopResult[];
  selectedStopKey: string;
  filter: ClassificationFilter;
  search: string;
  onFilter: (filter: ClassificationFilter) => void;
  onSearch: (value: string) => void;
  onSelect: (stopKey: string) => void;
}) {
  const t = useT();
  const filters: ClassificationFilter[] = ["all", "direct_over_limit", "route_only_over_limit", "additional_window_candidate", "within_limit", "data_review"];
  const pageSize = 8;
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const firstVisibleIndex = currentPage * pageSize;
  const visibleRows = rows.slice(firstVisibleIndex, firstVisibleIndex + pageSize);

  useEffect(() => {
    setPage(0);
  }, [allRows, filter, search]);

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-2">
            <BusFront className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Address classification")}</h2>
            <Badge tone="info">{formatNumber(rows.length)} {t("addresses")}</Badge>
          </div>
          <div className="flex flex-wrap gap-2">
            {filters.map((item) => (
              <button key={item} type="button" className={cn("h-8 rounded-md border px-3 text-xs font-medium transition", classificationFilterClass(item, filter === item))} onClick={() => onFilter(item)}>
                {t(classificationLabel(item))} {item === "all" ? allRows.length : allRows.filter((row) => operationalCategory(row) === item).length}
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="relative block max-w-md">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <input className={cn(fieldClassName, "pl-9")} value={search} placeholder={t("Search address or route")} onChange={(event) => onSearch(event.target.value)} />
        </label>
        <div className="grid gap-3 lg:grid-cols-2">
          {visibleRows.map((row) => (
            <button key={row.stop_key} type="button" className={cn("min-h-36 rounded-md border p-3 text-left transition", classificationCardClass(operationalCategory(row)), selectedStopKey === row.stop_key && "ring-2 ring-primary/30")} onClick={() => onSelect(row.stop_key)}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{row.address}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{row.primary_route_id || row.route_ids?.join(", ")} · {formatNumber(row.riders)} {t("riders")}</div>
                </div>
                <ClassificationBadge value={operationalCategory(row)} />
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <MiniMetric label="Direct" value={metricPair(row.direct_duration_min, "min", row.direct_distance_km, "km")} tone={operationalCategory(row) === "direct_over_limit" ? "danger" : "neutral"} />
                <MiniMetric label="Current route ride" value={minutes(row.estimated_current_ride_min)} tone={operationalCategory(row) === "route_only_over_limit" ? "warning" : "neutral"} />
                <MiniMetric label="Over limit" value={minutes(largestOverLimit(row))} tone={classificationMetricTone(operationalCategory(row))} />
              </div>
              <div className="mt-3 line-clamp-2 text-xs leading-5 text-muted-foreground">{(row.reasons || []).map((reason) => t(reason)).join(" ")}</div>
            </button>
          ))}
        </div>
        {rows.length > pageSize ? (
          <div className="flex h-10 items-center justify-between border-t border-border pt-3">
            <span className="text-xs text-muted-foreground">
              {formatNumber(firstVisibleIndex + 1)}-{formatNumber(Math.min(firstVisibleIndex + pageSize, rows.length))} / {formatNumber(rows.length)}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
                aria-label={t("Previous page")}
                title={t("Previous page")}
                disabled={currentPage === 0}
                onClick={() => setPage((value) => Math.max(0, value - 1))}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
                aria-label={t("Next page")}
                title={t("Next page")}
                disabled={currentPage >= pageCount - 1}
                onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}
              >
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>
        ) : null}
        {!rows.length ? <div className="rounded-md border border-border bg-muted/40 px-3 py-6 text-center text-sm text-muted-foreground">{t("No addresses match the current filters.")}</div> : null}
      </CardContent>
    </Card>
  );
}

function DirectSchoolMap({ result, selectedStop, selectionRevision, onSelect }: { result: NonNullable<DirectSchoolJobRecord["result"]>; selectedStop: DirectSchoolStopResult | null; selectionRevision: number; onSelect: (key: string) => void }) {
  const t = useT();
  const mapRef = useRef<MapRef | null>(null);
  const school = result.school as { lat?: number; lng?: number; address?: string };
  const visibleStops = useMemo(() => result.stops.filter((row) => Number.isFinite(row.lat) && Number.isFinite(row.lng)), [result.stops]);
  const conclusion = result.operational_conclusion;
  const directOverRiders = Number(conclusion?.direct_over_limit.rider_count ?? result.stops.filter((row) => operationalCategory(row) === "direct_over_limit").reduce((total, row) => total + Number(row.riders || 0), 0));
  const routeOnlyOverRiders = Number(conclusion?.route_only_over_limit.rider_count ?? result.stops.filter((row) => operationalCategory(row) === "route_only_over_limit").reduce((total, row) => total + Number(row.riders || 0), 0));
  const selectedCategory = selectedStop ? operationalCategory(selectedStop) : "within_limit";
  const pointData = useMemo<FeatureCollection<Point>>(() => ({
    type: "FeatureCollection",
    features: [
      ...(Number.isFinite(school.lat) && Number.isFinite(school.lng) ? [{
        type: "Feature" as const,
        id: "school",
        properties: { stop_key: "school", category: "school", color: "#111827" },
        geometry: { type: "Point" as const, coordinates: [Number(school.lng), Number(school.lat)] },
      }] : []),
      ...visibleStops.map((row) => ({
        type: "Feature" as const,
        id: row.stop_key,
        properties: {
          stop_key: row.stop_key,
          category: operationalCategory(row),
          color: classificationColor(operationalCategory(row)),
          selected: row.stop_key === selectedStop?.stop_key,
        },
        geometry: { type: "Point" as const, coordinates: [Number(row.lng), Number(row.lat)] },
      })),
    ],
  }), [school.lat, school.lng, selectedStop?.stop_key, visibleStops]);
  const lineData = useMemo<FeatureCollection<LineString>>(() => ({
    type: "FeatureCollection",
    features: selectedStop?.direct_geometry?.length ? [{
      type: "Feature",
      properties: { color: classificationColor(selectedCategory) },
      geometry: { type: "LineString", coordinates: selectedStop.direct_geometry },
    }] : [],
  }), [selectedCategory, selectedStop]);

  const fitAll = () => {
    const points = [
      ...(Number.isFinite(school.lat) && Number.isFinite(school.lng) ? [[Number(school.lng), Number(school.lat)]] : []),
      ...visibleStops.map((row) => [Number(row.lng), Number(row.lat)]),
    ];
    if (!mapRef.current || !points.length) return;
    const lngs = points.map((coords) => Number(coords[0]));
    const lats = points.map((coords) => Number(coords[1]));
    mapRef.current.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 60, duration: 0, maxZoom: 13 });
  };

  useEffect(() => {
    if (!mapRef.current || !selectedStop || selectionRevision < 1) return;
    const routePoints = selectedStop?.direct_geometry?.length
      ? selectedStop.direct_geometry
      : Number.isFinite(selectedStop?.lng) && Number.isFinite(selectedStop?.lat) && Number.isFinite(school.lng) && Number.isFinite(school.lat)
        ? [[Number(selectedStop?.lng), Number(selectedStop?.lat)], [Number(school.lng), Number(school.lat)]]
        : [];
    if (!routePoints.length) return;
    const lngs = routePoints.map((coords) => Number(coords[0]));
    const lats = routePoints.map((coords) => Number(coords[1]));
    mapRef.current.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 80, duration: 350, maxZoom: 14 });
  }, [school.lat, school.lng, selectedStop, selectionRevision]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2"><MapPinned className="h-4 w-4 text-primary" /><h2 className="text-sm font-semibold">{t("Direct route map")}</h2></div>
          <div className="flex min-w-0 items-center justify-end gap-2">
            <span className="hidden max-w-72 truncate text-xs text-muted-foreground md:block">{selectedStop?.address || t("Select an address")}</span>
            <Button variant="secondary" className="h-9 w-9 p-0" title={t("Fit all")} aria-label={t("Fit all")} onClick={fitAll}>
              <MapPinned className="h-4 w-4" />
            </Button>
            <Button variant="secondary" className="h-9" icon={<Download className="h-4 w-4" />} onClick={() => downloadDirectSchoolMapHtml(result, selectedStop, t)}>
              {t("Export map")}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="relative h-[500px] min-h-[500px] overflow-hidden rounded-b-md">
          <MapView
            ref={mapRef}
            initialViewState={{ longitude: Number(school.lng || 121.47), latitude: Number(school.lat || 31.23), zoom: 9 }}
            mapStyle={MAP_STYLE}
            interactiveLayerIds={["direct-school-points"]}
            onLoad={fitAll}
            onClick={(event) => {
              const key = String(event.features?.[0]?.properties?.stop_key || "");
              if (key && key !== "school") onSelect(key);
            }}
          >
            <NavigationControl position="top-right" />
            <Source id="direct-school-line-source" type="geojson" data={lineData}>
              <Layer id="direct-school-line-casing" type="line" paint={{ "line-color": "#ffffff", "line-width": 9, "line-opacity": 0.9 }} />
              <Layer id="direct-school-line" type="line" paint={{ "line-color": ["get", "color"], "line-width": 5, "line-opacity": 0.95 }} />
            </Source>
            <Source id="direct-school-points-source" type="geojson" data={pointData}>
              <Layer id="direct-school-points" type="circle" paint={{
                "circle-color": ["get", "color"],
                "circle-radius": ["case", ["==", ["get", "category"], "school"], 9, ["==", ["get", "selected"], true], 10, 6],
                "circle-stroke-color": ["case", ["==", ["get", "selected"], true], "#111827", "#ffffff"],
                "circle-stroke-width": ["case", ["==", ["get", "selected"], true], 4, 2],
              }} />
            </Source>
          </MapView>
          <div className="pointer-events-none absolute left-3 top-3 z-10 w-[min(330px,calc(100%-80px))] rounded-md border border-border bg-surface/95 px-3 py-2 shadow-sm backdrop-blur-sm">
            <div className="text-xs font-semibold text-foreground">{t("Student time-limit summary")}</div>
            <div className="mt-2 grid gap-1.5 text-xs">
              <div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-muted-foreground"><span className="h-2.5 w-2.5 rounded-full bg-red-600" />{t("Direct-over-limit students")}</span><strong className="text-red-700">{formatNumber(directOverRiders)}</strong></div>
              <div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-muted-foreground"><span className="h-2.5 w-2.5 rounded-full bg-amber-600" />{t("Shared-route over-limit students")}</span><strong className="text-amber-800">{formatNumber(routeOnlyOverRiders)}</strong></div>
            </div>
          </div>
          {selectedStop ? (
            <div className="pointer-events-none absolute bottom-3 left-3 z-10 w-[min(420px,calc(100%-24px))] rounded-md border border-border bg-surface/95 px-3 py-3 shadow-sm backdrop-blur-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><div className="truncate text-sm font-semibold">{selectedStop.address}</div><div className="mt-1 text-xs text-muted-foreground">{selectedStop.primary_route_id || selectedStop.route_ids?.join(", ")} · {formatNumber(selectedStop.riders)} {t("students")}</div></div>
                <ClassificationBadge value={selectedCategory} />
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <MiniMetric label="Direct distance km" value={distance(selectedStop.direct_distance_km)} />
                <MiniMetric label="Direct duration min" value={minutes(selectedStop.direct_duration_min)} tone={selectedCategory === "direct_over_limit" ? "danger" : "neutral"} />
                <MiniMetric label="Current route ride" value={minutes(selectedStop.estimated_current_ride_min)} tone={selectedCategory === "route_only_over_limit" ? "warning" : "neutral"} />
              </div>
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function downloadDirectSchoolMapHtml(
  result: NonNullable<DirectSchoolJobRecord["result"]>,
  selectedStop: DirectSchoolStopResult | null,
  t: (key: string) => string,
) {
  const school = result.school as { lat?: number; lng?: number; address?: string };
  const conclusion = result.operational_conclusion;
  const stops = result.stops
    .filter((row) => Number.isFinite(row.lat) && Number.isFinite(row.lng))
    .map((row) => ({
      stopKey: row.stop_key,
      address: row.address,
      route: row.primary_route_id || row.route_ids?.join(", ") || "-",
      riders: Number(row.riders || 0),
      lat: Number(row.lat),
      lng: Number(row.lng),
      category: operationalCategory(row),
      color: classificationColor(operationalCategory(row)),
      directDistanceKm: row.direct_distance_km,
      directDurationMin: row.direct_duration_min,
      currentRideMin: row.estimated_current_ride_min,
      geometry: row.direct_geometry || [],
    }));
  const directOverRiders = Number(conclusion?.direct_over_limit.rider_count ?? stops.filter((row) => row.category === "direct_over_limit").reduce((total, row) => total + row.riders, 0));
  const routeOnlyOverRiders = Number(conclusion?.route_only_over_limit.rider_count ?? stops.filter((row) => row.category === "route_only_over_limit").reduce((total, row) => total + row.riders, 0));
  const payload = JSON.stringify({
    school: { address: school.address || "", lat: Number(school.lat), lng: Number(school.lng) },
    stops,
    selectedStopKey: selectedStop?.stop_key || stops[0]?.stopKey || "",
    summary: { directOverRiders, routeOnlyOverRiders },
  }).replace(/</g, "\\u003c");
  const labels = JSON.stringify({
    title: t("Direct route map"),
    directOver: t("Direct-over-limit students"),
    routeOnlyOver: t("Shared-route over-limit students"),
    selectedAddress: t("Selected address details"),
    route: t("Route"),
    students: t("students"),
    directDistance: t("Direct distance km"),
    directDuration: t("Direct duration min"),
    currentRide: t("Current route ride"),
    fitAll: t("Fit all"),
  }).replace(/</g, "\\u003c");
  const title = `${t("Direct route map")} - ${selectedStop?.primary_route_id || t("All")}`;
  const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${htmlEscape(title)}</title>
  <link href="https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.css" rel="stylesheet" />
  <style>
    * { box-sizing: border-box; }
    html, body, #map { width: 100%; height: 100%; margin: 0; }
    body { font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; }
    .panel { position: absolute; z-index: 3; width: min(360px, calc(100% - 92px)); border: 1px solid #cbd5e1; border-radius: 8px; background: rgba(255,255,255,.94); box-shadow: 0 10px 28px rgba(15,23,42,.18); backdrop-filter: blur(8px); }
    .summary { top: 14px; left: 14px; padding: 12px; }
    .detail { left: 14px; bottom: 14px; padding: 12px; }
    .title-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    h1, h2 { margin: 0; font-size: 14px; }
    .metric { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 8px; color: #64748b; font-size: 12px; }
    .metric strong { color: #111827; font-size: 14px; }
    .metric.danger strong { color: #b91c1c; }
    .metric.warning strong { color: #b45309; }
    .address { margin-top: 8px; font-size: 14px; font-weight: 700; line-height: 1.35; }
    .meta { margin-top: 4px; color: #64748b; font-size: 12px; }
    .detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 7px; margin-top: 10px; }
    .detail-item { min-width: 0; border: 1px solid #e2e8f0; border-radius: 6px; background: #f8fafc; padding: 8px; }
    .detail-label { color: #64748b; font-size: 10px; line-height: 1.25; }
    .detail-value { margin-top: 4px; font-size: 12px; font-weight: 700; }
    button { height: 32px; border: 1px solid #94a3b8; border-radius: 6px; background: white; padding: 0 10px; color: #334155; font: inherit; font-size: 12px; font-weight: 650; cursor: pointer; }
    @media (max-width: 600px) { .detail-grid { grid-template-columns: 1fr; } .panel { width: min(300px, calc(100% - 28px)); } }
  </style>
</head>
<body>
  <div id="map"></div>
  <section class="panel summary">
    <div class="title-row"><h1 id="summaryTitle"></h1><button id="fitAll" type="button"></button></div>
    <div class="metric danger"><span id="directLabel"></span><strong id="directValue"></strong></div>
    <div class="metric warning"><span id="routeLabel"></span><strong id="routeValue"></strong></div>
  </section>
  <section class="panel detail">
    <h2 id="detailTitle"></h2>
    <div class="address" id="address"></div>
    <div class="meta" id="meta"></div>
    <div class="detail-grid">
      <div class="detail-item"><div class="detail-label" id="distanceLabel"></div><div class="detail-value" id="distanceValue"></div></div>
      <div class="detail-item"><div class="detail-label" id="durationLabel"></div><div class="detail-value" id="durationValue"></div></div>
      <div class="detail-item"><div class="detail-label" id="rideLabel"></div><div class="detail-value" id="rideValue"></div></div>
    </div>
  </section>
  <script src="https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.js"></script>
  <script>
    const data = ${payload};
    const labels = ${labels};
    let selectedStopKey = data.selectedStopKey;
    const map = new maplibregl.Map({
      container: "map",
      style: { version: 8, sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.de/{z}/{x}/{y}.png"], tileSize: 256, attribution: "OpenStreetMap contributors" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] },
      center: Number.isFinite(data.school.lng) && Number.isFinite(data.school.lat) ? [data.school.lng, data.school.lat] : [121.47, 31.23],
      zoom: 9,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    document.getElementById("summaryTitle").textContent = labels.title;
    document.getElementById("fitAll").textContent = labels.fitAll;
    document.getElementById("directLabel").textContent = labels.directOver;
    document.getElementById("routeLabel").textContent = labels.routeOnlyOver;
    document.getElementById("directValue").textContent = String(data.summary.directOverRiders);
    document.getElementById("routeValue").textContent = String(data.summary.routeOnlyOverRiders);
    document.getElementById("detailTitle").textContent = labels.selectedAddress;
    document.getElementById("distanceLabel").textContent = labels.directDistance;
    document.getElementById("durationLabel").textContent = labels.directDuration;
    document.getElementById("rideLabel").textContent = labels.currentRide;
    document.getElementById("fitAll").addEventListener("click", fitAll);

    function pointGeojson() {
      const features = data.stops.map(stop => ({ type: "Feature", properties: { stopKey: stop.stopKey, color: stop.color, selected: stop.stopKey === selectedStopKey }, geometry: { type: "Point", coordinates: [stop.lng, stop.lat] } }));
      if (Number.isFinite(data.school.lng) && Number.isFinite(data.school.lat)) features.push({ type: "Feature", properties: { stopKey: "school", color: "#111827", selected: false, school: true }, geometry: { type: "Point", coordinates: [data.school.lng, data.school.lat] } });
      return { type: "FeatureCollection", features };
    }
    function lineGeojson() {
      const stop = data.stops.find(item => item.stopKey === selectedStopKey);
      return { type: "FeatureCollection", features: stop && Array.isArray(stop.geometry) && stop.geometry.length ? [{ type: "Feature", properties: { color: stop.color }, geometry: { type: "LineString", coordinates: stop.geometry } }] : [] };
    }
    function fitAll() {
      const points = data.stops.map(stop => [stop.lng, stop.lat]);
      if (Number.isFinite(data.school.lng) && Number.isFinite(data.school.lat)) points.push([data.school.lng, data.school.lat]);
      if (!points.length) return;
      const bounds = points.reduce((value, point) => value.extend(point), new maplibregl.LngLatBounds(points[0], points[0]));
      map.fitBounds(bounds, { padding: 70, duration: 0, maxZoom: 13 });
    }
    function focusSelected(stop) {
      const points = Array.isArray(stop.geometry) && stop.geometry.length ? stop.geometry : [[stop.lng, stop.lat], [data.school.lng, data.school.lat]];
      const valid = points.filter(point => Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1]));
      if (!valid.length) return;
      const bounds = valid.reduce((value, point) => value.extend(point), new maplibregl.LngLatBounds(valid[0], valid[0]));
      map.fitBounds(bounds, { padding: 90, duration: 300, maxZoom: 14 });
    }
    function selectStop(key, focus) {
      const stop = data.stops.find(item => item.stopKey === key);
      if (!stop) return;
      selectedStopKey = key;
      map.getSource("points").setData(pointGeojson());
      map.getSource("selected-line").setData(lineGeojson());
      document.getElementById("address").textContent = stop.address;
      document.getElementById("meta").textContent = labels.route + " " + stop.route + " · " + stop.riders + " " + labels.students;
      document.getElementById("distanceValue").textContent = Number.isFinite(stop.directDistanceKm) ? stop.directDistanceKm + " km" : "-";
      document.getElementById("durationValue").textContent = Number.isFinite(stop.directDurationMin) ? stop.directDurationMin + " min" : "-";
      document.getElementById("rideValue").textContent = Number.isFinite(stop.currentRideMin) ? stop.currentRideMin + " min" : "-";
      if (focus) focusSelected(stop);
    }
    map.on("load", () => {
      map.addSource("selected-line", { type: "geojson", data: lineGeojson() });
      map.addLayer({ id: "selected-line-casing", type: "line", source: "selected-line", paint: { "line-color": "#ffffff", "line-width": 9, "line-opacity": .9 } });
      map.addLayer({ id: "selected-line", type: "line", source: "selected-line", paint: { "line-color": ["get", "color"], "line-width": 5, "line-opacity": .95 } });
      map.addSource("points", { type: "geojson", data: pointGeojson() });
      map.addLayer({ id: "points", type: "circle", source: "points", paint: { "circle-color": ["get", "color"], "circle-radius": ["case", ["==", ["get", "school"], true], 9, ["==", ["get", "selected"], true], 10, 6], "circle-stroke-color": ["case", ["==", ["get", "selected"], true], "#111827", "#ffffff"], "circle-stroke-width": ["case", ["==", ["get", "selected"], true], 4, 2] } });
      map.on("click", "points", event => { const key = event.features && event.features[0] && event.features[0].properties.stopKey; if (key && key !== "school") selectStop(String(key), true); });
      map.on("mouseenter", "points", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "points", () => { map.getCanvas().style.cursor = ""; });
      fitAll();
      if (selectedStopKey) selectStop(selectedStopKey, true);
    });
  </script>
</body>
</html>`;
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${sanitizeDownloadFilename(title)}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function DistanceScatter({ rows, selectedStopKey, onSelect }: { rows: DirectSchoolStopResult[]; selectedStopKey: string; onSelect: (key: string) => void }) {
  const t = useT();
  const baseView = { x: 0, y: 0, width: 640, height: 360 };
  const [view, setView] = useState(baseView);
  const dragRef = useRef<{ clientX: number; clientY: number; viewX: number; viewY: number; moved: boolean } | null>(null);
  const suppressClickRef = useRef(false);
  const plotted = rows.filter((row) => Number.isFinite(row.direct_distance_km) && Number.isFinite(row.direct_duration_min));
  const maxX = Math.max(1, ...plotted.map((row) => Number(row.direct_distance_km)));
  const maxY = Math.max(1, ...plotted.map((row) => Number(row.direct_duration_min)));
  const clampView = (next: typeof baseView) => ({
    ...next,
    x: Math.min(baseView.width - next.width, Math.max(0, next.x)),
    y: Math.min(baseView.height - next.height, Math.max(0, next.y)),
  });
  const zoomAt = (factor: number, anchorX: number, anchorY: number) => {
    setView((current) => {
      const width = Math.min(baseView.width, Math.max(baseView.width / 8, current.width * factor));
      const height = width * (baseView.height / baseView.width);
      const ratioX = (anchorX - current.x) / current.width;
      const ratioY = (anchorY - current.y) / current.height;
      return clampView({ x: anchorX - ratioX * width, y: anchorY - ratioY * height, width, height });
    });
  };
  const handleWheel = (event: ReactWheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    const bounds = event.currentTarget.getBoundingClientRect();
    const anchorX = view.x + ((event.clientX - bounds.left) / bounds.width) * view.width;
    const anchorY = view.y + ((event.clientY - bounds.top) / bounds.height) * view.height;
    zoomAt(event.deltaY < 0 ? 0.82 : 1.22, anchorX, anchorY);
  };
  const handlePointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if ((event.target as SVGElement).tagName.toLowerCase() === "circle") return;
    event.currentTarget.setPointerCapture(event.pointerId);
    suppressClickRef.current = false;
    dragRef.current = { clientX: event.clientX, clientY: event.clientY, viewX: view.x, viewY: view.y, moved: false };
  };
  const handlePointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const dx = ((event.clientX - drag.clientX) / bounds.width) * view.width;
    const dy = ((event.clientY - drag.clientY) / bounds.height) * view.height;
    if (Math.abs(dx) > 1 || Math.abs(dy) > 1) drag.moved = true;
    setView((current) => clampView({ ...current, x: drag.viewX - dx, y: drag.viewY - dy }));
  };
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2"><Clock3 className="h-4 w-4 text-primary" /><h2 className="text-sm font-semibold">{t("Distance and time distribution")}</h2></div>
          <Button variant="secondary" className="h-9 w-9 p-0" title={t("Reset chart view")} aria-label={t("Reset chart view")} onClick={() => setView(baseView)} disabled={view.width === baseView.width}>
            <RotateCcw className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="aspect-video min-h-[300px] w-full overflow-hidden rounded-md border border-border bg-surface">
          <svg
            viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
            className="h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
            role="img"
            aria-label={t("Distance and time distribution")}
            onWheel={handleWheel}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={(event) => { suppressClickRef.current = Boolean(dragRef.current?.moved); event.currentTarget.releasePointerCapture(event.pointerId); dragRef.current = null; }}
            onPointerCancel={() => { dragRef.current = null; }}
          >
            <rect width="640" height="360" fill="white" />
            <line x1="64" y1="310" x2="616" y2="310" stroke="#94a3b8" strokeWidth="1" />
            <line x1="64" y1="20" x2="64" y2="310" stroke="#94a3b8" strokeWidth="1" />
            {[0.25, 0.5, 0.75, 1].map((ratio) => <line key={ratio} x1="64" y1={310 - ratio * 290} x2="616" y2={310 - ratio * 290} stroke="#e2e8f0" strokeWidth="1" />)}
            {plotted.map((row) => {
              const x = 64 + (Number(row.direct_distance_km) / maxX) * 552;
              const y = 310 - (Number(row.direct_duration_min) / maxY) * 290;
              const radius = Math.min(14, 5 + Math.sqrt(Math.max(0, row.riders || 0)));
              return (
                <circle
                  key={row.stop_key}
                  cx={x}
                  cy={y}
                  r={selectedStopKey === row.stop_key ? radius + 3 : radius}
                  fill={classificationColor(operationalCategory(row))}
                  fillOpacity="0.82"
                  stroke={selectedStopKey === row.stop_key ? "#111827" : "#ffffff"}
                  strokeWidth={selectedStopKey === row.stop_key ? 3 : 1.5}
                  className="cursor-pointer"
                  role="button"
                  tabIndex={0}
                  aria-label={`${row.address}: ${row.direct_distance_km} km / ${row.direct_duration_min} min`}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => { event.stopPropagation(); onSelect(row.stop_key); suppressClickRef.current = false; }}
                  onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelect(row.stop_key); }}
                >
                  <title>{`${row.address}: ${row.direct_distance_km} km / ${row.direct_duration_min} min`}</title>
                </circle>
              );
            })}
            <text x="340" y="347" textAnchor="middle" fontSize="12" fill="#64748b">{t("Direct distance km")}</text>
            <text x="18" y="165" textAnchor="middle" fontSize="12" fill="#64748b" transform="rotate(-90 18 165)">{t("Direct duration min")}</text>
            <text x="64" y="329" fontSize="11" fill="#64748b">0</text>
            <text x="616" y="329" textAnchor="end" fontSize="11" fill="#64748b">{formatNumber(maxX)}</text>
            <text x="56" y="24" textAnchor="end" fontSize="11" fill="#64748b">{formatNumber(maxY)}</text>
          </svg>
        </div>
        <div className="mt-2 text-xs text-muted-foreground">{t("Bubble size represents riders at the address.")} {t("Scroll to zoom and drag to pan.")}</div>
      </CardContent>
    </Card>
  );
}

function StopDetailTable({ rows, selectedStopKey, onSelect }: { rows: DirectSchoolStopResult[]; selectedStopKey: string; onSelect: (key: string) => void }) {
  const t = useT();
  return (
    <Card>
      <CardHeader><h2 className="text-sm font-semibold">{t("Address evidence")}</h2></CardHeader>
      <CardContent className="p-0">
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[1100px] w-full divide-y divide-border text-left text-sm">
            <thead className="sticky top-0 bg-muted text-xs text-muted-foreground">
              <tr>{["Classification", "Address", "Students", "Route", "Direct", "Current route ride", "Over limit", "OSRM free-flow reference", "Additional removal routes", "Captured"].map((label) => <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">{t(label)}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => (
                <tr key={row.stop_key} className={cn("cursor-pointer hover:bg-muted/40", selectedStopKey === row.stop_key && "bg-primary/5")} onClick={() => onSelect(row.stop_key)}>
                  <td className="px-3 py-2"><ClassificationBadge value={operationalCategory(row)} /></td>
                  <td className="max-w-80 px-3 py-2"><div className="truncate font-medium">{row.address}</div><div className="mt-1 text-xs text-muted-foreground">{row.city}</div></td>
                  <td className="px-3 py-2">{formatNumber(row.riders)}</td>
                  <td className="px-3 py-2">{row.primary_route_id || row.route_ids?.join(", ")}</td>
                  <td className="whitespace-nowrap px-3 py-2">{metricPair(row.direct_duration_min, "min", row.direct_distance_km, "km")}</td>
                  <td className="whitespace-nowrap px-3 py-2">{minutes(row.estimated_current_ride_min)}</td>
                  <td className="whitespace-nowrap px-3 py-2">{minutes(largestOverLimit(row))}</td>
                  <td className="whitespace-nowrap px-3 py-2">{metricPair(row.osrm_duration_min, "min", row.osrm_distance_km, "km")}</td>
                  <td className="px-3 py-2">{row.additional_window_routes?.join(", ") || "-"}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{formatDateTime(row.provider_called_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function MultiDayPanel({ record }: { record: DirectSchoolJobRecord }) {
  const t = useT();
  const multiDay = record.multi_day;
  if (!multiDay || multiDay.run_count < 2) return null;
  const persistent = multiDay.stops.filter((row) => Boolean(row.persistent_direct_over_limit ?? row.persistent_candidate));
  return (
    <Card>
      <CardHeader><div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">{t("Multi-day evidence")}</h2><Badge tone="info">{multiDay.run_count} {t("snapshots")}</Badge></div></CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <Metric label="Compatible snapshots" value={multiDay.run_count} />
          <Metric label="Addresses sampled" value={multiDay.stop_count} />
          <Metric label="Persistent direct over-limit" value={persistent.length} accent />
        </div>
        <div className="overflow-auto rounded-md border border-border">
          <table className="min-w-full divide-y divide-border text-left text-sm">
            <thead className="bg-muted text-xs text-muted-foreground"><tr>{["Address", "Samples", "Median", "P90", "Max", "Variability", "Direct over-limit rate"].map((label) => <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">{t(label)}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">{multiDay.stops.slice(0, 20).map((row) => <tr key={String(row.stop_key)}><td className="max-w-80 truncate px-3 py-2 font-medium">{String(row.address || "")}</td><td className="px-3 py-2">{formatNumber(row.sample_count)}</td><td className="px-3 py-2">{minutes(Number(row.duration_median_min))}</td><td className="px-3 py-2">{minutes(Number(row.duration_p90_min))}</td><td className="px-3 py-2">{minutes(Number(row.duration_max_min))}</td><td className="px-3 py-2">{minutes(Number(row.duration_variability_min))}</td><td className="px-3 py-2">{formatNumber(Number(row.direct_over_limit_rate ?? row.dedicated_candidate_rate) * 100)}%</td></tr>)}</tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function PendingResult({ record }: { record: DirectSchoolJobRecord }) {
  const t = useT();
  const progress = record.result?.progress;
  return (
    <Card>
      <CardContent className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
        {record.status === "failed" ? <AlertTriangle className="h-7 w-7 text-destructive" /> : <Loader2 className="h-7 w-7 animate-spin text-primary" />}
        <div className="text-base font-semibold">{t(statusLabel(record.status))}</div>
        {progress ? <div className="text-sm text-muted-foreground">{formatNumber(progress.completed)} / {formatNumber(progress.total)} · {formatNumber(progress.provider_api_calls)} {t("provider calls")}</div> : null}
        {record.error ? <InlineError message={record.error} /> : null}
      </CardContent>
    </Card>
  );
}

function DirectSchoolHistoryItem({ job, active }: { job: DirectSchoolJobSummary; active: boolean }) {
  const t = useT();
  const summary = job.result_summary || {};
  const overLimitCount = Number(summary.direct_over_limit_address_count || 0) + Number(summary.route_only_over_limit_address_count || 0);
  return (
    <div className="min-w-0 px-1 py-1">
      <Badge tone={statusTone(job.status)}>{t(statusLabel(job.status))}</Badge>
      <div className={cn("mt-2 text-xs", active ? "text-primary-foreground/80" : "text-muted-foreground")}>{formatDateTime(job.created_at)}</div>
      {job.scheduled_start_at ? <div className={cn("mt-1 text-xs", active ? "text-primary-foreground/80" : "text-muted-foreground")}>{t("Scheduled for")} {formatDateTime(job.scheduled_start_at)}</div> : null}
      <div className={cn("mt-2 grid grid-cols-2 gap-1 text-xs", active ? "text-primary-foreground/80" : "text-muted-foreground")}>
        <span>{formatNumber(summary.address_count)} {t("addresses")}</span>
        <span>{isActiveStatus(job.status) ? t("Measurements pending") : `${formatNumber(overLimitCount || summary.dedicated_candidate_count || 0)} ${t("over limit")}`}</span>
      </div>
    </div>
  );
}

function ScheduleCalendar({ month, selectedDates, onMonthChange, onToggle, onCancel, onConfirm }: { month: string; selectedDates: string[]; onMonthChange: (month: string) => void; onToggle: (date: string) => void; onCancel: () => void; onConfirm: () => void }) {
  const t = useT();
  const selected = new Set(selectedDates);
  const visibleMonth = parseMonthKey(month);
  const label = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(visibleMonth);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-4 py-6">
      <div className="w-full max-w-xl rounded-lg border border-border bg-surface p-4 shadow-xl">
        <div className="flex items-center justify-between gap-3"><div><h3 className="font-semibold">{t("Select schedule dates")}</h3><div className="mt-1 text-xs text-muted-foreground">{selectedDates.length} {t("selected")}</div></div><div className="flex items-center gap-2"><Button type="button" variant="secondary" className="h-8 w-9 px-0" title={t("Previous month")} onClick={() => onMonthChange(addMonths(month, -1))}><ChevronLeft className="h-4 w-4" /></Button><div className="min-w-36 text-center text-sm font-medium">{label}</div><Button type="button" variant="secondary" className="h-8 w-9 px-0" title={t("Next month")} onClick={() => onMonthChange(addMonths(month, 1))}><ChevronRight className="h-4 w-4" /></Button></div></div>
        <div className="mt-4 grid grid-cols-7 gap-1 text-center text-xs font-medium text-muted-foreground">{["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day) => <div key={day}>{t(day)}</div>)}</div>
        <div className="mt-2 grid grid-cols-7 gap-1">{calendarDays(month).map((item) => <button key={item.key} type="button" className={cn("h-10 rounded-md border text-sm font-medium", item.inMonth ? "border-border bg-surface" : "border-transparent bg-muted/40 text-muted-foreground", selected.has(item.key) && "border-primary bg-primary text-primary-foreground")} onClick={() => onToggle(item.key)}>{item.day}</button>)}</div>
        <div className="mt-4 flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onCancel}>{t("Cancel")}</Button><Button type="button" disabled={!selectedDates.length} onClick={onConfirm}>{t("Confirm")}</Button></div>
      </div>
    </div>
  );
}

function ToolTab({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return <button type="button" className={cn("min-h-11 rounded px-2 text-xs font-medium leading-4 transition sm:h-9 sm:min-h-0 sm:whitespace-nowrap sm:px-3 sm:text-sm", active ? "bg-surface text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")} onClick={onClick}>{children}</button>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  const t = useT();
  return <label className="block space-y-1.5"><span className="text-xs font-medium text-muted-foreground">{t(label)}</span>{children}</label>;
}

function NumberField({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max?: number; step: number; onChange: (value: number) => void }) {
  return <Field label={label}><input className={fieldClassName} type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} /></Field>;
}

function Metric({ label, value, accent = false }: { label: string; value: ReactNode; accent?: boolean }) {
  const t = useT();
  return <div className={cn("rounded-md border p-3", accent ? "border-amber-200 bg-amber-50" : "border-border bg-muted/50")}><div className="text-xs text-muted-foreground">{t(label)}</div><div className="mt-1 text-lg font-semibold">{typeof value === "number" ? formatNumber(value) : value}</div></div>;
}

function MiniMetric({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "warning" | "danger" }) {
  const t = useT();
  return <div className={cn("rounded-md border p-2", tone === "danger" ? "border-red-300 bg-red-50" : tone === "warning" ? "border-amber-300 bg-amber-50" : "border-border bg-muted/40")}><div className="text-muted-foreground">{t(label)}</div><div className={cn("mt-1 font-semibold", tone === "danger" ? "text-red-800" : tone === "warning" ? "text-amber-900" : "text-foreground")}>{value}</div></div>;
}

function ClassificationBadge({ value }: { value: string }) {
  const t = useT();
  const tone = value === "direct_over_limit" ? "danger" : value === "route_only_over_limit" || value === "additional_window_candidate" ? "warning" : value === "data_review" ? "neutral" : "success";
  return <Badge tone={tone}>{t(classificationLabel(value))}</Badge>;
}

function LoadingLine({ text }: { text: string }) {
  return <div className="flex items-center gap-2 rounded-md border border-border bg-muted/60 px-3 py-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin text-primary" />{text}</div>;
}

function InlineError({ message }: { message: string }) {
  return <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{message}</div>;
}

function classificationLabel(value: string) {
  const labels: Record<string, string> = {
    all: "All",
    direct_over_limit: "Direct trip over limit",
    route_only_over_limit: "Current route only over limit",
    additional_window_candidate: "Additional removal candidate",
    data_review: "Data review",
    within_limit: "Within limit",
    pending: "Pending",
  };
  return labels[value] || value;
}

function classificationColor(value: string) {
  if (value === "direct_over_limit") return "#dc2626";
  if (value === "route_only_over_limit") return "#d97706";
  if (value === "additional_window_candidate") return "#7c3aed";
  if (value === "data_review") return "#64748b";
  return "#16a34a";
}

function classificationMetricTone(value: string): "neutral" | "warning" | "danger" {
  if (value === "direct_over_limit") return "danger";
  if (value === "route_only_over_limit" || value === "additional_window_candidate") return "warning";
  return "neutral";
}

function classificationCardClass(value: string) {
  if (value === "direct_over_limit") return "border-red-300 bg-red-50/70 hover:bg-red-50";
  if (value === "route_only_over_limit") return "border-amber-300 bg-amber-50/70 hover:bg-amber-50";
  if (value === "additional_window_candidate") return "border-violet-300 bg-violet-50/60 hover:bg-violet-50";
  if (value === "data_review") return "border-slate-300 bg-slate-50 hover:bg-slate-100/70";
  return "border-border bg-surface hover:bg-muted/40";
}

function classificationFilterClass(value: ClassificationFilter, active: boolean) {
  if (value === "direct_over_limit") return active ? "border-red-600 bg-red-600 text-white" : "border-red-300 bg-red-50 text-red-800 hover:bg-red-100";
  if (value === "route_only_over_limit") return active ? "border-amber-600 bg-amber-600 text-white" : "border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100";
  if (value === "additional_window_candidate") return active ? "border-violet-600 bg-violet-600 text-white" : "border-violet-300 bg-violet-50 text-violet-800 hover:bg-violet-100";
  if (value === "within_limit") return active ? "border-emerald-600 bg-emerald-600 text-white" : "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100";
  if (value === "data_review") return active ? "border-slate-600 bg-slate-600 text-white" : "border-slate-300 bg-slate-50 text-slate-700 hover:bg-slate-100";
  return active ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface hover:bg-muted";
}

function operationalCategory(row: DirectSchoolStopResult): Exclude<ClassificationFilter, "all"> {
  if (row.additional_window_candidate) return "additional_window_candidate";
  const value = String(row.operational_category || "");
  if (["direct_over_limit", "route_only_over_limit", "additional_window_candidate", "within_limit", "data_review"].includes(value)) {
    return value as Exclude<ClassificationFilter, "all">;
  }
  if (row.recommendation === "dedicated_candidate" || row.recommendation === "far_not_main_cause") return "direct_over_limit";
  if (row.recommendation === "route_adjustment") return "route_only_over_limit";
  if (row.recommendation === "within_range") return "within_limit";
  return "data_review";
}

function largestOverLimit(row: DirectSchoolStopResult) {
  return Math.max(0, ...(row.route_contexts || []).map((item) => Number(item.over_limit_min || 0)));
}

function statusLabel(status: string) {
  const labels: Record<string, string> = { queued: "Queued", running: "Running", scheduled: "Scheduled", succeeded: "Succeeded", failed: "Failed", canceled: "Canceled" };
  return labels[status] || status;
}

function statusTone(status: string): "neutral" | "info" | "success" | "warning" | "danger" {
  if (status === "succeeded") return "success";
  if (status === "failed") return "danger";
  if (status === "scheduled") return "info";
  if (status === "running") return "warning";
  return "neutral";
}

function isActiveStatus(status?: string | null) {
  return status === "queued" || status === "running" || status === "scheduled";
}

function historyName(job: DirectSchoolJobSummary) {
  return job.title || String(job.metadata?.job_name || "Direct-to-School Analysis");
}

function minutes(value: number | undefined) {
  return Number.isFinite(value) ? `${formatNumber(value)} min` : "-";
}

function distance(value: number | undefined) {
  return Number.isFinite(value) ? `${formatNumber(value)} km` : "-";
}

function metricPair(first: number | undefined, firstUnit: string, second: number | undefined, secondUnit: string) {
  if (!Number.isFinite(first) && !Number.isFinite(second)) return "-";
  return `${Number.isFinite(first) ? formatNumber(first) : "-"} ${firstUnit} / ${Number.isFinite(second) ? formatNumber(second) : "-"} ${secondUnit}`;
}

function sanitizeDownloadFilename(value: string) {
  const cleaned = value.replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim();
  return (cleaned || "BRP Map").slice(0, 120).trim();
}

function htmlEscape(value: string) {
  const entities: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return value.replace(/[&<>"']/g, (character) => entities[character] || character);
}

function monthKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function parseMonthKey(value: string) {
  const [year, month] = value.split("-").map(Number);
  return new Date(year || new Date().getFullYear(), Math.max(0, (month || 1) - 1), 1);
}

function addMonths(value: string, delta: number) {
  const date = parseMonthKey(value);
  date.setMonth(date.getMonth() + delta);
  return monthKey(date);
}

function dateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function calendarDays(value: string) {
  const monthStart = parseMonthKey(value);
  const firstVisible = new Date(monthStart);
  firstVisible.setDate(firstVisible.getDate() - firstVisible.getDay());
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(firstVisible);
    date.setDate(firstVisible.getDate() + index);
    return { key: dateKey(date), day: date.getDate(), inMonth: date.getMonth() === monthStart.getMonth() };
  });
}

async function fileToBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}
