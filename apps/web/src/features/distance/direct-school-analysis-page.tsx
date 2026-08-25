import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
type RecommendationFilter = "all" | "dedicated_candidate" | "route_adjustment" | "far_not_main_cause" | "data_review";

const DEFAULT_CONFIG: DirectSchoolAnalysisConfig = {
  service_direction: "To School",
  stop_service_minutes: 1,
  time_window_start: "06:30",
  time_window_end: "08:00",
  from_school_departure_time: "15:40",
  far_distance_km: 20,
  far_duration_minutes: 45,
  burden_minutes: 15,
  bypass_candidate_limit: 10,
  candidate_cluster_radius_km: 3,
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
  const [recommendationFilter, setRecommendationFilter] = useState<RecommendationFilter>("all");
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
  const result = rawResult && Array.isArray(rawResult.stops) ? rawResult : null;
  const filteredStops = useMemo(() => {
    const rows = result?.stops || [];
    const search = searchText.trim().toLowerCase();
    return rows.filter((row) => {
      if (recommendationFilter !== "all" && row.recommendation !== recommendationFilter) return false;
      if (!search) return true;
      return [row.address, row.city, row.primary_route_id, ...(row.route_ids || [])]
        .join(" ")
        .toLowerCase()
        .includes(search);
    });
  }, [recommendationFilter, result?.stops, searchText]);
  const selectedStop = result?.stops.find((row) => row.stop_key === selectedStopKey) || filteredStops[0] || null;
  const scheduledEnabled = featuresQuery.data?.scheduled_jobs_enabled === true;

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
                    {t("Measure each service address against the school with fresh provider traffic, then separate remoteness from route burden.")}
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
                  <CandidateBoard
                    rows={filteredStops}
                    allRows={result.stops}
                    selectedStopKey={selectedStop?.stop_key || ""}
                    filter={recommendationFilter}
                    search={searchText}
                    onFilter={setRecommendationFilter}
                    onSearch={setSearchText}
                    onSelect={setSelectedStopKey}
                  />
                  <div className="grid min-w-0 gap-4">
                    <DirectSchoolMap result={result} selectedStop={selectedStop} onSelect={setSelectedStopKey} />
                    <DistanceScatter rows={result.stops} selectedStopKey={selectedStop?.stop_key || ""} onSelect={setSelectedStopKey} />
                  </div>
                  <StopDetailTable rows={filteredStops} selectedStopKey={selectedStop?.stop_key || ""} onSelect={setSelectedStopKey} />
                  <MultiDayPanel record={selectedRecord!} />
                  <ClusterPanel clusters={result.candidate_clusters || []} />
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
                  <Field label="Service Direction">
                    <select
                      className={fieldClassName}
                      value={config.service_direction}
                      onChange={(event) => updateConfig({ service_direction: event.target.value as DirectSchoolAnalysisConfig["service_direction"] })}
                    >
                      <option value="To School">{t("To School")}</option>
                      <option value="From School">{t("From School")}</option>
                    </select>
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <NumberField label="Far distance km" value={config.far_distance_km} min={1} step={1} onChange={(value) => updateConfig({ far_distance_km: value })} />
                    <NumberField label="Far duration min" value={config.far_duration_minutes} min={1} step={5} onChange={(value) => updateConfig({ far_duration_minutes: value })} />
                    <NumberField label="Burden threshold min" value={config.burden_minutes} min={1} step={1} onChange={(value) => updateConfig({ burden_minutes: value })} />
                    <NumberField label="Bypass checks" value={config.bypass_candidate_limit} min={0} max={50} step={1} onChange={(value) => updateConfig({ bypass_candidate_limit: value })} />
                    <NumberField label="Cluster radius km" value={config.candidate_cluster_radius_km} min={0.1} step={0.5} onChange={(value) => updateConfig({ candidate_cluster_radius_km: value })} />
                    <NumberField label="Stop dwell min" value={config.stop_service_minutes} min={0} step={0.5} onChange={(value) => updateConfig({ stop_service_minutes: value })} />
                  </div>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {t("Dedicated service is suggested only when a stop is remote and its measured route burden is also material.")}
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
                    <input className={fieldClassName} value={customName} placeholder={t("Remote-stop review")} onChange={(event) => setCustomName(event.target.value)} />
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
  const summary = result.summary;
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
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <Metric label="Addresses" value={summary.address_count} />
          <Metric label="Dedicated candidates" value={summary.dedicated_candidate_count} accent />
          <Metric label="Route adjustments" value={summary.route_adjustment_count} />
          <Metric label="Longest direct time" value={`${formatNumber(summary.max_direct_duration_min)} min`} />
          <Metric label="Provider API calls" value={summary.provider_api_calls} />
        </div>
        <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs leading-5 text-muted-foreground">
          {t("Times are live map estimates captured at the recorded provider call time, not vehicle GPS observations. OSRM is shown only as a free-flow baseline.")}
        </div>
      </CardContent>
    </Card>
  );
}

function CandidateBoard({
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
  filter: RecommendationFilter;
  search: string;
  onFilter: (filter: RecommendationFilter) => void;
  onSearch: (value: string) => void;
  onSelect: (stopKey: string) => void;
}) {
  const t = useT();
  const filters: RecommendationFilter[] = ["all", "dedicated_candidate", "route_adjustment", "far_not_main_cause", "data_review"];
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-2">
            <BusFront className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">{t("Priority candidates")}</h2>
            <Badge tone="info">{formatNumber(rows.length)} {t("shown")}</Badge>
          </div>
          <div className="flex flex-wrap gap-2">
            {filters.map((item) => (
              <button key={item} type="button" className={cn("h-8 rounded-md border px-3 text-xs font-medium", filter === item ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface")} onClick={() => onFilter(item)}>
                {t(recommendationLabel(item))} {item === "all" ? allRows.length : allRows.filter((row) => row.recommendation === item).length}
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
          {rows.slice(0, 8).map((row) => (
            <button key={row.stop_key} type="button" className={cn("min-h-36 rounded-md border p-3 text-left transition", selectedStopKey === row.stop_key ? "border-primary bg-primary/5" : "border-border bg-surface hover:bg-muted/40")} onClick={() => onSelect(row.stop_key)}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{row.address}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{row.primary_route_id || row.route_ids?.join(", ")} · {formatNumber(row.riders)} {t("riders")}</div>
                </div>
                <RecommendationBadge value={row.recommendation} />
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <MiniMetric label="Direct" value={metricPair(row.direct_duration_min, "min", row.direct_distance_km, "km")} />
                <MiniMetric label="Rider detour" value={minutes(row.rider_detour_min)} />
                <MiniMetric label="Route burden" value={minutes(row.marginal_route_burden_min)} />
              </div>
              <div className="mt-3 line-clamp-2 text-xs leading-5 text-muted-foreground">{(row.reasons || []).map((reason) => t(reason)).join(" ")}</div>
            </button>
          ))}
        </div>
        {!rows.length ? <div className="rounded-md border border-border bg-muted/40 px-3 py-6 text-center text-sm text-muted-foreground">{t("No addresses match the current filters.")}</div> : null}
      </CardContent>
    </Card>
  );
}

function DirectSchoolMap({ result, selectedStop, onSelect }: { result: NonNullable<DirectSchoolJobRecord["result"]>; selectedStop: DirectSchoolStopResult | null; onSelect: (key: string) => void }) {
  const t = useT();
  const mapRef = useRef<MapRef | null>(null);
  const school = result.school as { lat?: number; lng?: number; address?: string };
  const visibleStops = result.stops.filter((row) => Number.isFinite(row.lat) && Number.isFinite(row.lng));
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
        properties: { stop_key: row.stop_key, category: row.recommendation, color: recommendationColor(row.recommendation) },
        geometry: { type: "Point" as const, coordinates: [Number(row.lng), Number(row.lat)] },
      })),
    ],
  }), [school.lat, school.lng, visibleStops]);
  const lineData = useMemo<FeatureCollection<LineString>>(() => ({
    type: "FeatureCollection",
    features: selectedStop?.direct_geometry?.length ? [{
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: selectedStop.direct_geometry },
    }] : [],
  }), [selectedStop]);

  useEffect(() => {
    const points = pointData.features.map((feature) => feature.geometry.coordinates).filter((coords) => coords.length >= 2);
    if (!mapRef.current || !points.length) return;
    const lngs = points.map((coords) => Number(coords[0]));
    const lats = points.map((coords) => Number(coords[1]));
    mapRef.current.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 60, duration: 0, maxZoom: 13 });
  }, [pointData]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2"><MapPinned className="h-4 w-4 text-primary" /><h2 className="text-sm font-semibold">{t("Direct route map")}</h2></div>
          <span className="max-w-[60%] truncate text-xs text-muted-foreground">{selectedStop?.address || t("Select an address")}</span>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="h-[440px] min-h-[440px] overflow-hidden rounded-b-md">
          <MapView
            ref={mapRef}
            initialViewState={{ longitude: Number(school.lng || 121.47), latitude: Number(school.lat || 31.23), zoom: 9 }}
            mapStyle={MAP_STYLE}
            interactiveLayerIds={["direct-school-points"]}
            onClick={(event) => {
              const key = String(event.features?.[0]?.properties?.stop_key || "");
              if (key && key !== "school") onSelect(key);
            }}
          >
            <NavigationControl position="top-right" />
            <Source id="direct-school-line-source" type="geojson" data={lineData}>
              <Layer id="direct-school-line" type="line" paint={{ "line-color": "#0f766e", "line-width": 5, "line-opacity": 0.9 }} />
            </Source>
            <Source id="direct-school-points-source" type="geojson" data={pointData}>
              <Layer id="direct-school-points" type="circle" paint={{ "circle-color": ["get", "color"], "circle-radius": ["case", ["==", ["get", "category"], "school"], 9, 6], "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 }} />
            </Source>
          </MapView>
        </div>
      </CardContent>
    </Card>
  );
}

function DistanceScatter({ rows, selectedStopKey, onSelect }: { rows: DirectSchoolStopResult[]; selectedStopKey: string; onSelect: (key: string) => void }) {
  const t = useT();
  const plotted = rows.filter((row) => Number.isFinite(row.direct_distance_km) && Number.isFinite(row.direct_duration_min));
  const maxX = Math.max(1, ...plotted.map((row) => Number(row.direct_distance_km)));
  const maxY = Math.max(1, ...plotted.map((row) => Number(row.direct_duration_min)));
  return (
    <Card>
      <CardHeader><div className="flex items-center gap-2"><Clock3 className="h-4 w-4 text-primary" /><h2 className="text-sm font-semibold">{t("Distance and time distribution")}</h2></div></CardHeader>
      <CardContent>
        <div className="aspect-square min-h-[360px] w-full">
          <svg viewBox="0 0 420 420" className="h-full w-full" role="img" aria-label={t("Distance and time distribution")}>
            <line x1="48" y1="372" x2="400" y2="372" stroke="#94a3b8" strokeWidth="1" />
            <line x1="48" y1="20" x2="48" y2="372" stroke="#94a3b8" strokeWidth="1" />
            {[0.25, 0.5, 0.75, 1].map((ratio) => <line key={ratio} x1="48" y1={372 - ratio * 352} x2="400" y2={372 - ratio * 352} stroke="#e2e8f0" strokeWidth="1" />)}
            {plotted.map((row) => {
              const x = 48 + (Number(row.direct_distance_km) / maxX) * 352;
              const y = 372 - (Number(row.direct_duration_min) / maxY) * 352;
              const radius = Math.min(14, 5 + Math.sqrt(Math.max(0, row.riders || 0)));
              return (
                <circle key={row.stop_key} cx={x} cy={y} r={selectedStopKey === row.stop_key ? radius + 3 : radius} fill={recommendationColor(row.recommendation)} fillOpacity="0.82" stroke={selectedStopKey === row.stop_key ? "#111827" : "#ffffff"} strokeWidth={selectedStopKey === row.stop_key ? 3 : 1.5} className="cursor-pointer" onClick={() => onSelect(row.stop_key)}>
                  <title>{`${row.address}: ${row.direct_distance_km} km / ${row.direct_duration_min} min`}</title>
                </circle>
              );
            })}
            <text x="224" y="410" textAnchor="middle" fontSize="12" fill="#64748b">{t("Direct distance km")}</text>
            <text x="14" y="196" textAnchor="middle" fontSize="12" fill="#64748b" transform="rotate(-90 14 196)">{t("Direct duration min")}</text>
            <text x="48" y="391" fontSize="11" fill="#64748b">0</text>
            <text x="400" y="391" textAnchor="end" fontSize="11" fill="#64748b">{formatNumber(maxX)}</text>
            <text x="40" y="24" textAnchor="end" fontSize="11" fill="#64748b">{formatNumber(maxY)}</text>
          </svg>
        </div>
        <div className="mt-2 text-xs text-muted-foreground">{t("Bubble size represents riders at the address.")}</div>
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
              <tr>{["Recommendation", "Address", "Riders", "Route", "Direct", "OSRM baseline", "Current ride", "Rider detour", "Route burden", "Captured"].map((label) => <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">{t(label)}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => (
                <tr key={row.stop_key} className={cn("cursor-pointer hover:bg-muted/40", selectedStopKey === row.stop_key && "bg-primary/5")} onClick={() => onSelect(row.stop_key)}>
                  <td className="px-3 py-2"><RecommendationBadge value={row.recommendation} /></td>
                  <td className="max-w-80 px-3 py-2"><div className="truncate font-medium">{row.address}</div><div className="mt-1 text-xs text-muted-foreground">{row.city}</div></td>
                  <td className="px-3 py-2">{formatNumber(row.riders)}</td>
                  <td className="px-3 py-2">{row.primary_route_id || row.route_ids?.join(", ")}</td>
                  <td className="whitespace-nowrap px-3 py-2">{metricPair(row.direct_duration_min, "min", row.direct_distance_km, "km")}</td>
                  <td className="whitespace-nowrap px-3 py-2">{metricPair(row.osrm_duration_min, "min", row.osrm_distance_km, "km")}</td>
                  <td className="whitespace-nowrap px-3 py-2">{minutes(row.estimated_current_ride_min)}</td>
                  <td className="whitespace-nowrap px-3 py-2">{minutes(row.rider_detour_min)}</td>
                  <td className="whitespace-nowrap px-3 py-2">{minutes(row.marginal_route_burden_min)}</td>
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
  const persistent = multiDay.stops.filter((row) => Boolean(row.persistent_candidate));
  return (
    <Card>
      <CardHeader><div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">{t("Multi-day evidence")}</h2><Badge tone="info">{multiDay.run_count} {t("snapshots")}</Badge></div></CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <Metric label="Compatible snapshots" value={multiDay.run_count} />
          <Metric label="Addresses sampled" value={multiDay.stop_count} />
          <Metric label="Persistent candidates" value={persistent.length} accent />
        </div>
        <div className="overflow-auto rounded-md border border-border">
          <table className="min-w-full divide-y divide-border text-left text-sm">
            <thead className="bg-muted text-xs text-muted-foreground"><tr>{["Address", "Samples", "Median", "P90", "Max", "Variability", "Candidate rate"].map((label) => <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">{t(label)}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">{multiDay.stops.slice(0, 20).map((row) => <tr key={String(row.stop_key)}><td className="max-w-80 truncate px-3 py-2 font-medium">{String(row.address || "")}</td><td className="px-3 py-2">{formatNumber(row.sample_count)}</td><td className="px-3 py-2">{minutes(Number(row.duration_median_min))}</td><td className="px-3 py-2">{minutes(Number(row.duration_p90_min))}</td><td className="px-3 py-2">{minutes(Number(row.duration_max_min))}</td><td className="px-3 py-2">{minutes(Number(row.duration_variability_min))}</td><td className="px-3 py-2">{formatNumber(Number(row.dedicated_candidate_rate) * 100)}%</td></tr>)}</tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function ClusterPanel({ clusters }: { clusters: Array<Record<string, unknown>> }) {
  const t = useT();
  if (!clusters.length) return null;
  return (
    <Card>
      <CardHeader><h2 className="text-sm font-semibold">{t("Dedicated service clusters")}</h2></CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {clusters.map((cluster) => <div key={String(cluster.cluster_id)} className="rounded-md border border-border bg-muted/40 p-3"><div className="flex items-center justify-between gap-3"><div className="font-semibold">{String(cluster.cluster_id)}</div><Badge tone="warning">{formatNumber(cluster.stop_count)} {t("stops")}</Badge></div><div className="mt-2 text-sm">{formatNumber(cluster.riders)} {t("riders")}</div><div className="mt-2 text-xs leading-5 text-muted-foreground">{Array.isArray(cluster.addresses) ? cluster.addresses.join(" · ") : ""}</div></div>)}
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
  return (
    <div className="min-w-0 px-1 py-1">
      <Badge tone={statusTone(job.status)}>{t(statusLabel(job.status))}</Badge>
      <div className={cn("mt-2 text-xs", active ? "text-primary-foreground/80" : "text-muted-foreground")}>{formatDateTime(job.created_at)}</div>
      {job.scheduled_start_at ? <div className={cn("mt-1 text-xs", active ? "text-primary-foreground/80" : "text-muted-foreground")}>{t("Scheduled for")} {formatDateTime(job.scheduled_start_at)}</div> : null}
      <div className={cn("mt-2 grid grid-cols-2 gap-1 text-xs", active ? "text-primary-foreground/80" : "text-muted-foreground")}><span>{formatNumber(summary.address_count)} {t("addresses")}</span><span>{formatNumber(summary.dedicated_candidate_count)} {t("candidates")}</span></div>
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

function MiniMetric({ label, value }: { label: string; value: string }) {
  const t = useT();
  return <div className="rounded-md border border-border bg-muted/40 p-2"><div className="text-muted-foreground">{t(label)}</div><div className="mt-1 font-semibold text-foreground">{value}</div></div>;
}

function RecommendationBadge({ value }: { value: string }) {
  const t = useT();
  const tone = value === "dedicated_candidate" ? "warning" : value === "route_adjustment" ? "info" : value === "data_review" ? "danger" : "neutral";
  return <Badge tone={tone}>{t(recommendationLabel(value))}</Badge>;
}

function LoadingLine({ text }: { text: string }) {
  return <div className="flex items-center gap-2 rounded-md border border-border bg-muted/60 px-3 py-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin text-primary" />{text}</div>;
}

function InlineError({ message }: { message: string }) {
  return <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{message}</div>;
}

function recommendationLabel(value: string) {
  const labels: Record<string, string> = {
    all: "All",
    dedicated_candidate: "Dedicated candidate",
    route_adjustment: "Route adjustment",
    far_not_main_cause: "Far, not main cause",
    data_review: "Data review",
    within_range: "Within range",
    pending: "Pending",
  };
  return labels[value] || value;
}

function recommendationColor(value: string) {
  if (value === "dedicated_candidate") return "#c2410c";
  if (value === "route_adjustment") return "#2563eb";
  if (value === "far_not_main_cause") return "#a16207";
  if (value === "data_review") return "#dc2626";
  return "#0f766e";
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

function metricPair(first: number | undefined, firstUnit: string, second: number | undefined, secondUnit: string) {
  if (!Number.isFinite(first) && !Number.isFinite(second)) return "-";
  return `${Number.isFinite(first) ? formatNumber(first) : "-"} ${firstUnit} / ${Number.isFinite(second) ? formatNumber(second) : "-"} ${secondUnit}`;
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
