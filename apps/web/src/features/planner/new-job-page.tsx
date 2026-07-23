import type { Dispatch, ReactNode, SetStateAction } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { AlertTriangle, CalendarDays, CheckCircle2, ChevronLeft, ChevronRight, Download, FileSpreadsheet, Loader2, RefreshCw, Send, SlidersHorizontal, Trash2, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  DEFAULT_PLANNER_CONFIG,
  SERVICE_DIRECTION_OPTIONS,
  TRAFFIC_PROFILE_OPTIONS,
} from "@/features/planner/config";
import { InteractiveRouteMap } from "@/features/results/interactive-route-map";
import {
  clearGeocodeCache,
  getDeploymentFeatures,
  getWorkbookTemplateUrl,
  previewWorkbook,
  submitWorkbookJob,
  type AddressReviewItem,
  type PlannerConfigPayload,
  type WorkbookPreview,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { useT } from "@/lib/i18n/context";

type PlannerConfigKey = keyof PlannerConfigPayload;

const ROUTE_TIMING_KEYS: PlannerConfigKey[] = [
  "stop_service_minutes",
];

const COMFORT_LOAD_FACTOR = 0.85;
const FULL_CAPACITY_LOAD_FACTOR = 1.0;
const ROUTE_BUDGET_AUTO_RETRY_LIMIT = 4;
const ROUTE_BUDGET_AUTO_RETRY_BASE_DELAY_MS = 2500;
const ROUTE_BUDGET_RETRYABLE_REASONS = new Set([
  "ConnectionError",
  "ConnectTimeout",
  "HTTPError",
  "JSONDecodeError",
  "OSError",
  "ReadTimeout",
  "RequestException",
  "RuntimeError",
  "Timeout",
  "TimeoutError",
]);

function isRetryableRouteBudgetReason(reason?: string) {
  return !reason || ROUTE_BUDGET_RETRYABLE_REASONS.has(reason);
}

export function NewJobPage() {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const defaultConfig = DEFAULT_PLANNER_CONFIG;
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [jobCustomName, setJobCustomName] = useState("");
  const [scheduledJob, setScheduledJob] = useState(false);
  const [scheduledDates, setScheduledDates] = useState<string[]>([]);
  const [schedulePickerOpen, setSchedulePickerOpen] = useState(false);
  const [scheduleDraftDates, setScheduleDraftDates] = useState<string[]>([]);
  const [scheduleMonth, setScheduleMonth] = useState(() => monthKey(new Date()));
  const [config, setConfig] = useState<PlannerConfigPayload>(defaultConfig);
  const [preview, setPreview] = useState<WorkbookPreview | null>(null);
  const [addressReviewAcknowledged, setAddressReviewAcknowledged] = useState(false);
  const [routeBudgetRetryAttempts, setRouteBudgetRetryAttempts] = useState(0);
  const configOverridesRef = useRef<Partial<PlannerConfigPayload>>({});
  const featuresQuery = useQuery({
    queryKey: ["deployment-features"],
    queryFn: getDeploymentFeatures,
    staleTime: 60_000,
  });
  const scheduledJobsEnabled = featuresQuery.data?.scheduled_jobs_enabled === true;

  function buildConfigWithOverrides(
    baseConfig: PlannerConfigPayload,
    overrides = configOverridesRef.current,
  ) {
    const safeOverrides = { ...overrides };
    delete safeOverrides.max_route_duration_minutes;
    const merged = {
      ...baseConfig,
      ...safeOverrides,
    };
    if (!Object.prototype.hasOwnProperty.call(safeOverrides, "route_stop_limit") && merged.route_stop_limit == null) {
      merged.route_stop_limit = DEFAULT_PLANNER_CONFIG.route_stop_limit;
    }
    return merged;
  }

  function updateUserConfig(patch: Partial<PlannerConfigPayload>) {
    configOverridesRef.current = { ...configOverridesRef.current, ...patch };
    updateConfig(setConfig, patch);
  }

  function hasUserConfigOverrides(keys: PlannerConfigKey[]) {
    return keys.some((key) => Object.prototype.hasOwnProperty.call(configOverridesRef.current, key));
  }

  function clearUserConfigOverrides(keys: PlannerConfigKey[]) {
    const nextOverrides = { ...configOverridesRef.current };
    for (const key of keys) {
      delete nextOverrides[key];
    }
    configOverridesRef.current = nextOverrides;
    setConfig(
      preview
        ? buildConfigWithOverrides(preview.suggested_config, nextOverrides)
        : buildConfigWithOverrides(defaultConfig, nextOverrides),
    );
  }

  function applyServiceDirection(direction: string) {
    const patch: Partial<PlannerConfigPayload> =
      direction === "To School"
        ? { service_direction: direction, time_window_start: "06:30", time_window_end: "08:00" }
        : { service_direction: direction, time_window_start: "15:40", time_window_end: "17:40" };
    updateUserConfig(patch);
  }

  function removeScheduledDate(date: string) {
    setScheduledDates((dates) => dates.filter((item) => item !== date));
  }

  function openSchedulePicker() {
    setScheduleDraftDates(scheduledDates);
    setScheduleMonth(scheduledDates[0]?.slice(0, 7) || monthKey(new Date()));
    setSchedulePickerOpen(true);
  }

  function confirmScheduleDates() {
    const nextDates = Array.from(new Set(scheduleDraftDates)).sort();
    setScheduledDates(nextDates);
    setScheduledJob(nextDates.length > 0);
    setSchedulePickerOpen(false);
  }

  function toggleScheduledJob() {
    if (scheduledJob) {
      setScheduledJob(false);
      return;
    }
    openSchedulePicker();
  }

  function configFromPreview(payload: WorkbookPreview) {
    return buildConfigWithOverrides(payload.suggested_config);
  }

  const previewMutation = useMutation({
    mutationFn: async (source?: { file: File; fileBase64: string; config: PlannerConfigPayload }) => {
      const sourceFile = source?.file || file;
      const sourceBase64 = source?.fileBase64 || fileBase64;
      const sourceConfig = source?.config || config;
      if (!sourceFile || !sourceBase64) {
        throw new Error(t("Select a workbook first."));
      }
      return previewWorkbook({ file_name: sourceFile.name, file_base64: sourceBase64, config: sourceConfig });
    },
    onSuccess: (payload) => {
      setPreview(payload);
      setConfig(configFromPreview(payload));
      setAddressReviewAcknowledged(false);
      if (payload.auto_route_budget?.status === "ready") {
        setRouteBudgetRetryAttempts(0);
      }
    },
  });

  const clearAddressCacheMutation = useMutation({
    mutationFn: clearGeocodeCache,
    onSuccess: () => {
      setAddressReviewAcknowledged(false);
      if (!file || !fileBase64) {
        return;
      }
      previewMutation.reset();
      setFileError("");
      setPreview(null);
      setRouteBudgetRetryAttempts(0);
      previewMutation.mutate({ file, fileBase64, config });
    },
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64) {
        throw new Error(t("Select a workbook first."));
      }
      let submitConfig = config;
      if (!preview) {
        const payload = await previewWorkbook({ file_name: file.name, file_base64: fileBase64, config });
        submitConfig = configFromPreview(payload);
        setPreview(payload);
        setConfig(submitConfig);
      }
      const dates = scheduledJobsEnabled && scheduledJob ? scheduledDates : [undefined];
      let lastPayload = null;
      for (const scheduledDate of dates) {
        lastPayload = await submitWorkbookJob({
          file_name: file.name,
          file_base64: fileBase64,
          config: submitConfig,
          job_custom_name: jobCustomName,
          scheduled_job: scheduledJobsEnabled && scheduledJob,
          scheduled_date: scheduledDate,
          address_review_acknowledged: addressReviewAcknowledged,
        });
      }
      if (!lastPayload) {
        throw new Error(t("Select at least one schedule date."));
      }
      return lastPayload;
    },
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await navigate({ to: "/jobs/$jobId", params: { jobId: payload.job.job_id } });
    },
  });

  const jobNamePreview = useMemo(() => {
    const customName = jobCustomName.trim().replace(/\s+/g, " ");
    if (customName) return customName;
    const fallback = file?.name ? file.name.replace(/\.[^.]+$/, "") : t("Untitled job");
    return preview?.job_default_name || fallback;
  }, [file?.name, jobCustomName, preview?.job_default_name, t]);

  async function handleFileChange(nextFile: File | null) {
    previewMutation.reset();
    setFile(nextFile);
    setPreview(null);
    setAddressReviewAcknowledged(false);
    setRouteBudgetRetryAttempts(0);
    configOverridesRef.current = {};
    setConfig(defaultConfig);
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
      const nextBase64 = await fileToBase64(nextFile);
      setFileBase64(nextBase64);
      previewMutation.mutate({ file: nextFile, fileBase64: nextBase64, config: defaultConfig });
    } catch (error) {
      setFileError(error instanceof Error ? error.message : t("Workbook could not be read."));
    }
  }

  function retryRouteBudgetPreview() {
    if (!file || !fileBase64) {
      return;
    }
    previewMutation.reset();
    setFileError("");
    setPreview(null);
    setAddressReviewAcknowledged(false);
    setRouteBudgetRetryAttempts(0);
    previewMutation.mutate({ file, fileBase64, config });
  }

  const autoRouteBudget = preview?.auto_route_budget;
  const addressReview = preview?.address_review;
  const addressReviewBlocking = Boolean((addressReview?.blocking_count ?? 0) > 0);
  const addressReviewNeedsAcknowledgement = Boolean(addressReview?.requires_acknowledgement);
  const addressReviewReady = Boolean(
    !addressReview || (!addressReviewBlocking && (!addressReviewNeedsAcknowledgement || addressReviewAcknowledged)),
  );
  const routeBudgetPending = previewMutation.isPending && !preview;
  const routeBudgetReady = Boolean(
    preview && autoRouteBudget?.status === "ready" && Number.isFinite(Number(autoRouteBudget.minutes)),
  );
  const busy = previewMutation.isPending || submitMutation.isPending || clearAddressCacheMutation.isPending;
  const timeWindowReady = Boolean(config.time_window_start && config.time_window_end && config.time_window_start < config.time_window_end);
  const comfortEnabled =
    Number(config.comfort_load_factor ?? FULL_CAPACITY_LOAD_FACTOR) <
    FULL_CAPACITY_LOAD_FACTOR;
  const scheduledReady = Boolean(!scheduledJob || !scheduledJobsEnabled || scheduledDates.length > 0);
  const routeBudgetUnavailableRetryable = Boolean(
    preview &&
    autoRouteBudget?.status !== "ready" &&
    isRetryableRouteBudgetReason(autoRouteBudget?.reason),
  );
  const routeBudgetShouldRetry = Boolean(
    file &&
    fileBase64 &&
    !busy &&
    !routeBudgetReady &&
    (previewMutation.error || routeBudgetUnavailableRetryable),
  );
  const routeBudgetAutoRetrying = routeBudgetShouldRetry && routeBudgetRetryAttempts < ROUTE_BUDGET_AUTO_RETRY_LIMIT;
  const routeBudgetRetryExhausted = routeBudgetShouldRetry && !routeBudgetAutoRetrying;
  const canSubmit = Boolean(fileBase64 && preview && routeBudgetReady && addressReviewReady && timeWindowReady && scheduledReady && !busy);
  const canRetryRouteBudget = Boolean(
    file &&
    fileBase64 &&
    !busy &&
    routeBudgetRetryExhausted,
  );
  const controlsLocked = previewMutation.isPending || submitMutation.isPending;
  const submitDisabledTitle = !timeWindowReady
    ? t("Time window start must be before end.")
    : !scheduledReady
      ? t("Select at least one schedule date.")
      : !addressReviewReady
        ? t("Review address warnings before running audit.")
        : t("Run audit will unlock after workbook validation and route-budget calculation finish.");

  useEffect(() => {
    if (!routeBudgetAutoRetrying || !file || !fileBase64) {
      return;
    }
    const delayMs = Math.min(
      10_000,
      ROUTE_BUDGET_AUTO_RETRY_BASE_DELAY_MS * (routeBudgetRetryAttempts + 1),
    );
    const timer = window.setTimeout(() => {
      setRouteBudgetRetryAttempts((attempts) => attempts + 1);
      previewMutation.reset();
      setFileError("");
      previewMutation.mutate({ file, fileBase64, config });
    }, delayMs);
    return () => window.clearTimeout(timer);
  }, [routeBudgetAutoRetrying, routeBudgetRetryAttempts, file, fileBase64, config]);

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-primary">{t("Current plan audit")}</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">{t("New audit")}</h1>
        </div>
        <Link to="/jobs" className={buttonClassName("secondary")}>
          {t("Audit history")}
        </Link>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">{t("Workbook")}</h2>
              </div>
            </CardHeader>
            <CardContent className={`space-y-4 ${controlsLocked ? "pointer-events-none opacity-60" : ""}`}>
              <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
                <div className="text-sm text-muted-foreground">{t("Upload a completed current-plan workbook.")}</div>
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
                    const nextFile = event.target.files?.[0] || null;
                    event.currentTarget.value = "";
                    void handleFileChange(nextFile);
                  }}
                />
              </label>
              {fileError ? <InlineError message={fileError} /> : null}
              {previewMutation.error && routeBudgetRetryExhausted ? (
                <InlineError message={(previewMutation.error as Error).message} />
              ) : null}
              {previewMutation.isPending ? (
                <div className="flex items-center gap-2 rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  {t("Validating workbook and calculating OSRM route budget...")}
                </div>
              ) : null}
            </CardContent>
          </Card>

          {preview?.address_review ? (
            <CurrentPlanReviewPanel
              review={preview.address_review}
              mapData={preview.current_plan_map}
              mapError={preview.current_plan_map_error}
              acknowledged={addressReviewAcknowledged}
              clearPending={clearAddressCacheMutation.isPending}
              onAcknowledge={setAddressReviewAcknowledged}
              onClearCache={(item) =>
                clearAddressCacheMutation.mutate({
                  country: item.country,
                  city: item.city,
                  address: item.address,
                })
              }
            />
          ) : null}
          {clearAddressCacheMutation.error ? <InlineError message={(clearAddressCacheMutation.error as Error).message} /> : null}

          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">{t("Run settings")}</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {scheduledJobsEnabled ? (
                <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 p-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <div className="text-sm font-medium">{t("Scheduled Job")}</div>
                    <div className="text-xs text-muted-foreground">
                      {t("Queue this audit for the fixed traffic window instead of running it immediately.")}
                    </div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={scheduledJob}
                    className="grid h-9 w-full max-w-[240px] shrink-0 grid-cols-2 rounded-full border border-border bg-surface p-1 text-xs font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                    title={t("When enabled, audits release on selected dates at the time-window start.")}
                    onClick={toggleScheduledJob}
                  >
                    <span
                      className={[
                        "flex items-center justify-center rounded-full px-2 transition",
                        scheduledJob ? "text-muted-foreground" : "bg-primary text-primary-foreground shadow-sm",
                      ].join(" ")}
                    >
                      {t("Not enabled")}
                    </span>
                    <span
                      className={[
                        "flex items-center justify-center rounded-full px-2 transition",
                        scheduledJob ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground",
                      ].join(" ")}
                    >
                      {t("Schedule enabled")}
                    </span>
                  </button>
                </div>
              ) : null}

              <fieldset disabled className="rounded-lg border border-border bg-muted/40 p-3">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <Field label="Service Direction">
                  <select
                    className={fieldClassName}
                    value={config.service_direction}
                    onChange={(event) => applyServiceDirection(event.target.value)}
                  >
                    {SERVICE_DIRECTION_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {t(option)}
                      </option>
                    ))}
                  </select>
                </Field>
                {config.service_direction === "To School" ? (
                  <Field label="School Arrival">
                    <input
                      className={fieldClassName}
                      type="time"
                      value={config.to_school_arrival_time}
                      onChange={(event) =>
                        updateUserConfig({
                          to_school_arrival_time: event.target.value,
                          time_window_end: event.target.value,
                        })
                      }
                    />
                  </Field>
                ) : (
                  <Field label="School Departure">
                    <input
                      className={fieldClassName}
                      type="time"
                      value={config.from_school_departure_time}
                      onChange={(event) => {
                        const start = event.target.value;
                        updateUserConfig({
                          from_school_departure_time: start,
                          time_window_start: start,
                          time_window_end: addMinutesToTime(start, 120),
                        });
                      }}
                    />
                  </Field>
                )}
                <Field label="Traffic Assumptions">
                  <select
                    className={fieldClassName}
                    value={config.traffic_profile_name}
                    onChange={(event) => updateUserConfig({ traffic_profile_name: event.target.value })}
                  >
                    {TRAFFIC_PROFILE_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {t(option)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="OSRM Route Budget">
                  <input
                    className={fieldClassName}
                    type="number"
                    min={10}
                    max={300}
                    step={5}
                    value={routeBudgetPending ? "" : config.max_route_duration_minutes}
                    placeholder={routeBudgetPending ? t("Calculating") : undefined}
                    readOnly
                    title={t("Auto-set from the uploaded current plan after geocoding. This is not a live traffic estimate.")}
                  />
                </Field>
                </div>
                <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                  {t("Service direction, school time, traffic assumptions, and route budget update automatically from the uploaded workbook.")}
                </p>
              </fieldset>
              {canRetryRouteBudget ? (
                <Button
                  type="button"
                  variant="secondary"
                  className="h-8 text-xs"
                  icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />}
                  onClick={retryRouteBudgetPreview}
                >
                  {t("Recalculate route budget")}
                </Button>
              ) : null}

              {scheduledJobsEnabled && scheduledJob ? (
                <div className="rounded-lg border border-border bg-surface p-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <div className="text-sm font-medium">{t("Schedule dates")}</div>
                    <Button
                      type="button"
                      variant="secondary"
                      className="h-8 text-xs"
                      icon={<CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />}
                      onClick={openSchedulePicker}
                    >
                      {t("Edit dates")}
                    </Button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {scheduledDates.length ? (
                      scheduledDates.map((date) => (
                        <button
                          key={date}
                          type="button"
                          className="rounded-full border border-border bg-muted px-3 py-1 text-xs"
                          title={t("Remove schedule date")}
                          onClick={() => removeScheduledDate(date)}
                        >
                          {date} x
                        </button>
                      ))
                    ) : (
                      <span className="text-xs text-muted-foreground">{t("Select at least one schedule date.")}</span>
                    )}
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">
                    {t("Each selected date creates one scheduled audit.")}
                  </div>
                </div>
              ) : null}

              {schedulePickerOpen ? (
                <ScheduleDatePickerDialog
                  month={scheduleMonth}
                  selectedDates={scheduleDraftDates}
                  onMonthChange={setScheduleMonth}
                  onToggleDate={(date) =>
                    setScheduleDraftDates((dates) =>
                      dates.includes(date) ? dates.filter((item) => item !== date) : [...dates, date],
                    )
                  }
                  onCancel={() => setSchedulePickerOpen(false)}
                  onConfirm={confirmScheduleDates}
                />
              ) : null}

              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                <Field label="Window Start">
                  <input
                    className={fieldClassName}
                    type="time"
                    value={config.time_window_start}
                    onChange={(event) => updateUserConfig({ time_window_start: event.target.value })}
                  />
                </Field>
                <Field label="Window End">
                  <input
                    className={fieldClassName}
                    type="time"
                    value={config.time_window_end}
                    onChange={(event) => updateUserConfig({ time_window_end: event.target.value })}
                  />
                </Field>
                <Field label="Stops Limit">
                  <input
                    className={fieldClassName}
                    type="number"
                    min={1}
                    step={1}
                    value={config.route_stop_limit ?? ""}
                    placeholder={t("No limit")}
                    onChange={(event) =>
                      updateUserConfig({
                        route_stop_limit: event.target.value ? Number(event.target.value) : null,
                      })
                    }
                  />
                  <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {t("Leave blank for no per-route stop cap.")}
                  </div>
                </Field>
                <Field label="Minimum Saving">
                  <input
                    className={fieldClassName}
                    type="number"
                    min={0}
                    step={1}
                    value={config.minimum_vehicle_reduction}
                    onChange={(event) =>
                      updateUserConfig({
                        minimum_vehicle_reduction: Math.max(0, Number(event.target.value || 0)),
                      })
                    }
                  />
                  <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {t("Required vehicle reduction versus current plan.")}
                  </div>
                </Field>
                <Field label="Time Impact Limit">
                  <input
                    className={fieldClassName}
                    type="number"
                    min={0}
                    max={240}
                    step={1}
                    value={config.time_impact_limit_minutes}
                    onChange={(event) =>
                      updateUserConfig({
                        time_impact_limit_minutes: Math.max(0, Math.min(240, Number(event.target.value || 0))),
                      })
                    }
                  />
                  <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {t("Used by the X-minute time-impact scenarios.")}
                  </div>
                </Field>
                <Field label="Improve comfort">
                  <div className="flex h-10 items-center">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={comfortEnabled}
                      aria-label={t("Improve comfort")}
                      className={[
                        "relative inline-flex h-6 w-11 shrink-0 rounded-full border transition-colors",
                        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
                        comfortEnabled ? "border-primary bg-primary" : "border-border bg-muted",
                      ].join(" ")}
                      title={t(
                        "Limits planned load to 85% of vehicle capacity so routes are less crowded and timing is more balanced; may require more buses.",
                      )}
                      onClick={() =>
                        updateUserConfig({
                          comfort_load_factor: comfortEnabled
                            ? FULL_CAPACITY_LOAD_FACTOR
                            : COMFORT_LOAD_FACTOR,
                        })
                      }
                    >
                      <span
                        aria-hidden="true"
                        className={[
                          "pointer-events-none absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform",
                          comfortEnabled ? "translate-x-5" : "translate-x-0.5",
                        ].join(" ")}
                      />
                    </button>
                  </div>
                </Field>
              </div>

              {!timeWindowReady ? <InlineError message={t("Time window start must be before end.")} /> : null}

              <Field label="Custom Job Name">
                <input
                  className={fieldClassName}
                  value={jobCustomName}
                  onChange={(event) => setJobCustomName(event.target.value)}
                  placeholder={t("May audit before parent review")}
                />
              </Field>

              <SettingsSection
                title="Route timing"
                description="Optional stop dwell time used by route calculations."
                customized={hasUserConfigOverrides(ROUTE_TIMING_KEYS)}
                onReset={() => clearUserConfigOverrides(ROUTE_TIMING_KEYS)}
              >
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  <NumberField
                    label="Stop Dwell Minutes"
                    value={config.stop_service_minutes}
                    min={0}
                    max={20}
                    step={1}
                    onChange={(value) => updateUserConfig({ stop_service_minutes: value })}
                  />
                </div>
              </SettingsSection>

              {submitMutation.error ? <InlineError message={(submitMutation.error as Error).message} /> : null}

              <div className="flex flex-col gap-3 sm:flex-row">
                <Button
                  type="button"
                  disabled={!canSubmit}
                  title={canSubmit ? undefined : submitDisabledTitle}
                  icon={submitMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  onClick={() => submitMutation.mutate()}
                >
                  {t("Run audit")}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold">{t("Job")}</h2>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div>
                <div className="text-xs uppercase text-muted-foreground">{t("Name")}</div>
                <div className="mt-1 break-words font-medium">{jobNamePreview}</div>
              </div>
              {preview ? <PreviewSummary preview={preview} /> : <EmptyPreview />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold">{t("Fleet slots")}</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <FleetSlot label={config.large_bus_name} seats={config.large_bus_capacity} count={config.large_bus_max_count} />
              <FleetSlot label={config.mid_bus_name} seats={config.mid_bus_capacity} count={config.mid_bus_max_count} />
              <FleetSlot label={config.small_bus_name} seats={config.small_bus_capacity} count={config.small_bus_max_count} />
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}

const fieldClassName =
  "h-10 w-full rounded-md border border-border bg-surface px-3 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground";

function normalizeReviewAddress(value?: string) {
  return String(value || "").trim().toLowerCase();
}

function CurrentPlanReviewPanel({
  review,
  mapData,
  mapError,
  acknowledged,
  clearPending,
  onAcknowledge,
  onClearCache,
}: {
  review: NonNullable<WorkbookPreview["address_review"]>;
  mapData?: WorkbookPreview["current_plan_map"];
  mapError?: string | null;
  acknowledged: boolean;
  clearPending: boolean;
  onAcknowledge: (value: boolean) => void;
  onClearCache: (item: AddressReviewItem) => void;
}) {
  const t = useT();
  const [showAll, setShowAll] = useState(false);
  const reviewByAddress = useMemo(() => {
    const lookup = new Map<string, AddressReviewItem>();
    for (const item of review.items) {
      for (const value of [item.address, item.formatted_address]) {
        const key = normalizeReviewAddress(value);
        if (key && !lookup.has(key)) {
          lookup.set(key, item);
        }
      }
    }
    return lookup;
  }, [review.items]);
  const flagged = review.items.filter((item) => item.status !== "ok");
  const visibleItems = showAll ? review.items : flagged.slice(0, 8);
  const blocked = review.blocking_count > 0;
  const needsReview = review.review_count > 0;
  const toneClass = blocked
    ? "border-red-200 bg-red-50 text-red-900"
    : needsReview
      ? "border-amber-200 bg-amber-50 text-amber-900"
      : "border-emerald-200 bg-emerald-50 text-emerald-900";
  const Icon = blocked || needsReview ? AlertTriangle : CheckCircle2;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-primary" aria-hidden="true" />
          <h2 className="text-sm font-semibold">{t("Current plan review")}</h2>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className={`rounded-md border px-3 py-2 text-sm ${toneClass}`}>
          {blocked
            ? t("Some workbook addresses could not be resolved. Clear bad cache entries or correct the workbook before running audit.")
            : needsReview
              ? t("Some resolved addresses need human review before running audit.")
              : t("No address issues were detected.")}
          <div className="mt-1 text-xs opacity-80">
            {formatNumber(review.total_count)} {t("addresses")} · {formatNumber(review.review_count)} {t("review")} · {formatNumber(review.blocking_count)} {t("blocked")}
          </div>
        </div>

        {mapData ? (
          <div className="overflow-hidden rounded-md border border-border">
            <InteractiveRouteMap
              data={mapData}
              renderStopActions={(stop) => {
                const item =
                  reviewByAddress.get(normalizeReviewAddress(stop.requested_address)) ||
                  reviewByAddress.get(normalizeReviewAddress(stop.address));
                if (!item || stop.is_depot) {
                  return null;
                }
                return (
                  <Button
                    type="button"
                    variant="secondary"
                    className="h-8 text-xs"
                    disabled={clearPending}
                    icon={clearPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    onClick={(event) => {
                      event.stopPropagation();
                      onClearCache(item);
                    }}
                  >
                    {t("Clear cache")}
                  </Button>
                );
              }}
            />
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
            {t("Current plan map preview is unavailable. Review the address list below.")}
            {mapError ? <div className="mt-1 text-xs">{mapError}</div> : null}
          </div>
        )}

        {visibleItems.length ? (
          <div className="max-h-96 space-y-2 overflow-auto pr-1">
            {visibleItems.map((item) => (
              <div key={item.id} className="rounded-md border border-border bg-surface p-3 text-sm">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={item.status === "blocking" ? "danger" : item.status === "needs_review" ? "warning" : "success"}>
                        {t(item.status)}
                      </Badge>
                      {item.source_excel_rows ? (
                        <span className="text-xs text-muted-foreground">{t("Rows")} {item.source_excel_rows}</span>
                      ) : null}
                      {item.provider ? <span className="text-xs text-muted-foreground">{item.provider}</span> : null}
                    </div>
                    <div className="mt-2 font-medium text-foreground">{item.address}</div>
                    {item.formatted_address ? (
                      <div className="mt-1 text-xs text-muted-foreground">{t("Resolved")}: {item.formatted_address}</div>
                    ) : null}
                    {item.reason ? <div className="mt-1 text-xs text-muted-foreground">{item.reason}</div> : null}
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    className="h-8 shrink-0 text-xs"
                    disabled={clearPending}
                    icon={clearPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    onClick={() => onClearCache(item)}
                  >
                    {t("Clear cache")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <Button type="button" variant="secondary" className="h-8 text-xs" onClick={() => setShowAll((value) => !value)}>
            {showAll ? t("Show flagged only") : t("Show all resolved addresses")}
          </Button>
          {review.requires_acknowledgement ? (
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={acknowledged}
                disabled={blocked}
                onChange={(event) => onAcknowledge(event.target.checked)}
              />
              <span>{t("I reviewed these address warnings")}</span>
            </label>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function ScheduleDatePickerDialog({
  month,
  selectedDates,
  onMonthChange,
  onToggleDate,
  onCancel,
  onConfirm,
}: {
  month: string;
  selectedDates: string[];
  onMonthChange: (month: string) => void;
  onToggleDate: (date: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  const selectedSet = new Set(selectedDates);
  const days = calendarDays(month);
  const visibleMonth = parseMonthKey(month);
  const monthLabel = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(visibleMonth);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-4 py-6">
      <div className="w-full max-w-xl rounded-lg border border-border bg-surface p-4 shadow-xl">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-foreground">{t("Select schedule dates")}</h3>
            <div className="mt-1 text-xs text-muted-foreground">
              {formatNumber(selectedDates.length)} {t("selected")}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="secondary" className="h-8 px-3" onClick={() => onMonthChange(addMonthsToMonthKey(month, -1))}>
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </Button>
            <div className="min-w-36 text-center text-sm font-medium">{monthLabel}</div>
            <Button type="button" variant="secondary" className="h-8 px-3" onClick={() => onMonthChange(addMonthsToMonthKey(month, 1))}>
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-7 gap-1 text-center text-xs font-medium uppercase text-muted-foreground">
          {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day) => (
            <div key={day}>{t(day)}</div>
          ))}
        </div>
        <div className="mt-2 grid grid-cols-7 gap-1">
          {days.map((item) => {
            const selected = selectedSet.has(item.key);
            return (
              <button
                key={item.key}
                type="button"
                className={[
                  "h-10 rounded-md border text-sm font-medium transition",
                  item.inMonth ? "border-border bg-surface text-foreground" : "border-transparent bg-muted/40 text-muted-foreground",
                  selected ? "border-primary bg-primary text-primary-foreground" : "hover:border-primary/60",
                ].join(" ")}
                onClick={() => onToggleDate(item.key)}
              >
                {item.day}
              </button>
            );
          })}
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {selectedDates.length ? (
            [...selectedDates].sort().map((date) => (
              <button
                key={date}
                type="button"
                className="rounded-full border border-border bg-muted px-3 py-1 text-xs"
                onClick={() => onToggleDate(date)}
              >
                {date} x
              </button>
            ))
          ) : (
            <span className="text-xs text-muted-foreground">{t("Select at least one schedule date.")}</span>
          )}
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onCancel}>
            {t("Cancel")}
          </Button>
          <Button type="button" disabled={!selectedDates.length} onClick={onConfirm}>
            {t("Confirm")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  const t = useT();
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium uppercase text-muted-foreground">{t(label)}</span>
      {children}
    </label>
  );
}

function SettingsSection({
  title,
  description,
  customized = false,
  onReset,
  children,
}: {
  title: string;
  description: string;
  customized?: boolean;
  onReset?: () => void;
  children: ReactNode;
}) {
  const t = useT();
  return (
    <details className="group rounded-md border border-border bg-muted/40">
      <summary className="cursor-pointer list-none px-3 py-3 marker:hidden">
        <span className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <span className="inline-flex min-w-0 items-start gap-2">
            <span className="mt-0.5 text-muted-foreground transition group-open:rotate-90">&gt;</span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-foreground">{t(title)}</span>
              <span className="mt-1 block text-xs font-normal text-muted-foreground">{t(description)}</span>
            </span>
          </span>
          <span className="flex w-fit shrink-0 items-center gap-2">
            <Badge tone="info">{t("Optional")}</Badge>
            {customized ? <Badge tone="warning">{t("Custom")}</Badge> : null}
            {customized && onReset ? (
              <button
                type="button"
                className="h-6 rounded-md border border-border bg-surface px-2 text-xs font-medium text-muted-foreground transition hover:text-foreground"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onReset();
                }}
              >
                {t("Reset")}
              </button>
            ) : null}
          </span>
        </span>
      </summary>
      <div className="space-y-3 border-t border-border px-3 py-3">{children}</div>
    </details>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <Field label={label}>
      <input
        className={fieldClassName}
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </Field>
  );
}

function PreviewSummary({ preview }: { preview: WorkbookPreview }) {
  const t = useT();
  const summary = preview.summary;
  return (
    <div className="grid grid-cols-2 gap-3">
      <SummaryMetric label="Routes" value={summary.route_count} />
      <SummaryMetric label="Service rows" value={summary.assignment_count} />
      <SummaryMetric label="Planning stops" value={summary.planning_stop_count} />
      <SummaryMetric label="Fleet types" value={summary.fleet_count} />
      <div className="col-span-2">
        <div className="mb-2 text-xs uppercase text-muted-foreground">{t("Bus types")}</div>
        <div className="flex flex-wrap gap-2">
          {toStringArray(summary.bus_types).map((item) => (
            <Badge key={item} tone="info">
              {item}
            </Badge>
          ))}
        </div>
      </div>
    </div>
  );
}

function EmptyPreview() {
  const t = useT();
  return (
    <div className="rounded-md border border-border bg-muted px-3 py-4 text-sm text-muted-foreground">
      {t("No workbook validated")}
    </div>
  );
}

function SummaryMetric({ label, value }: { label: string; value: unknown }) {
  const t = useT();
  return (
    <div className="rounded-md border border-border bg-muted px-3 py-2">
      <div className="text-xs uppercase text-muted-foreground">{t(label)}</div>
      <div className="mt-1 text-lg font-semibold">{formatNumber(value)}</div>
    </div>
  );
}

function FleetSlot({ label, seats, count }: { label: string; seats: number; count: number }) {
  const t = useT();
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm">
      <div className="min-w-0">
        <div className="truncate font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{formatNumber(seats)} {t("seats")}</div>
      </div>
      <Badge tone={count > 0 ? "success" : "neutral"}>{formatNumber(count)}</Badge>
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{message}</div>;
}

function monthKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function parseMonthKey(value: string) {
  const [yearText, monthText] = value.split("-");
  return new Date(Number(yearText) || new Date().getFullYear(), Math.max(0, (Number(monthText) || 1) - 1), 1);
}

function addMonthsToMonthKey(value: string, delta: number) {
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
    return {
      key: dateKey(date),
      day: date.getDate(),
      inMonth: date.getMonth() === monthStart.getMonth(),
    };
  });
}

function addMinutesToTime(value: string, minutesToAdd: number) {
  const [hoursText, minutesText] = value.split(":");
  const total = ((Number(hoursText) || 0) * 60 + (Number(minutesText) || 0) + minutesToAdd) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function updateConfig(
  setConfig: Dispatch<SetStateAction<PlannerConfigPayload>>,
  patch: Partial<PlannerConfigPayload>,
) {
  setConfig((current) => ({ ...current, ...patch }));
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item)).filter(Boolean);
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}
