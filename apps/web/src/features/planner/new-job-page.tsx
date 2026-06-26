import type { Dispatch, ReactNode, SetStateAction } from "react";
import { useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Download, FileSpreadsheet, Loader2, Send, SlidersHorizontal, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  DEFAULT_PLANNER_CONFIG,
  SERVICE_DIRECTION_OPTIONS,
  TRAFFIC_PROFILE_OPTIONS,
} from "@/features/planner/config";
import {
  getWorkbookTemplateUrl,
  previewWorkbook,
  submitWorkbookJob,
  type PlannerConfigPayload,
  type WorkbookPreview,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { useT } from "@/lib/i18n/context";

type PlannerConfigKey = keyof PlannerConfigPayload;

const ROUTE_TIMING_KEYS: PlannerConfigKey[] = [
  "stop_service_minutes",
];

const AGGREGATION_SETTING_KEYS: PlannerConfigKey[] = [
  "subway_search_radius_m",
  "max_subway_walk_distance_m",
  "nearby_cluster_radius_m",
];

const COMFORT_LOAD_FACTOR = 0.85;
const FULL_CAPACITY_LOAD_FACTOR = 1.0;

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
  const [config, setConfig] = useState<PlannerConfigPayload>(defaultConfig);
  const [preview, setPreview] = useState<WorkbookPreview | null>(null);
  const configOverridesRef = useRef<Partial<PlannerConfigPayload>>({});

  function buildConfigWithOverrides(
    baseConfig: PlannerConfigPayload,
    subwayAggregationBlocked = false,
    overrides = configOverridesRef.current,
  ) {
    const safeOverrides = { ...overrides };
    delete safeOverrides.max_route_duration_minutes;
    if (subwayAggregationBlocked) {
      delete safeOverrides.include_subway_aggregation_scenario;
    }
    return { ...baseConfig, ...safeOverrides, traffic_coefficient_mode: DEFAULT_PLANNER_CONFIG.traffic_coefficient_mode };
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
        ? buildConfigWithOverrides(preview.suggested_config, Boolean(preview.subway_aggregation_block_reason), nextOverrides)
        : buildConfigWithOverrides(defaultConfig, false, nextOverrides),
    );
  }

  function configFromPreview(payload: WorkbookPreview) {
    return buildConfigWithOverrides(
      payload.suggested_config,
      Boolean(payload.subway_aggregation_block_reason),
    );
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
      return submitWorkbookJob({
        file_name: file.name,
        file_base64: fileBase64,
        config: submitConfig,
        job_custom_name: jobCustomName,
        scheduled_job: scheduledJob,
      });
    },
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await navigate({ to: "/jobs/$jobId", params: { jobId: payload.job.job_id } });
    },
  });

  const jobNamePreview = useMemo(() => {
    const fallback = file?.name ? file.name.replace(/\.[^.]+$/, "") : t("Untitled job");
    const baseName = preview?.job_default_name || fallback;
    const suffix = jobCustomName.trim().replace(/\s+/g, " ");
    return suffix ? `${baseName} - ${suffix}` : baseName;
  }, [file?.name, jobCustomName, preview?.job_default_name, t]);

  async function handleFileChange(nextFile: File | null) {
    previewMutation.reset();
    setFile(nextFile);
    setPreview(null);
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

  const autoRouteBudget = preview?.auto_route_budget;
  const routeBudgetPending = previewMutation.isPending && !preview;
  const routeBudgetReady = Boolean(
    preview && autoRouteBudget?.status === "ready" && Number.isFinite(Number(autoRouteBudget.minutes)),
  );
  const amapRouteDetail =
    autoRouteBudget?.amap_route_status === "ready" &&
    Number.isFinite(Number(autoRouteBudget.amap_route_duration_minutes))
      ? `${t("AMap drive time")}: ${formatNumber(autoRouteBudget.amap_route_duration_minutes)} min${
          Number.isFinite(Number(autoRouteBudget.amap_route_distance_km))
            ? ` · ${formatNumber(autoRouteBudget.amap_route_distance_km)} km`
            : ""
        }`
      : autoRouteBudget?.amap_route_status === "unavailable"
        ? `${t("AMap drive time")}: ${t("Unavailable")}`
        : "";
  const routeBudgetDetail = routeBudgetPending
    ? t("Calculating current-plan longest route. Please wait before running the audit.")
    : autoRouteBudget?.status === "ready"
      ? `${t("Auto-filled from longest current-plan route")}: ${
          autoRouteBudget.longest_route_id || t("Unknown")
        } · ${formatNumber(autoRouteBudget.minutes)} min${
          autoRouteBudget.longest_route_duration_minutes
            ? ` (${formatNumber(autoRouteBudget.longest_route_duration_minutes)} min OSRM)`
            : ""
        }${amapRouteDetail ? ` · ${amapRouteDetail}` : ""}`
      : preview
        ? t("Route budget calculation unavailable; fix the workbook or OSRM route data before running audit.")
        : t("Upload a workbook to calculate the route budget from the current plan.");
  const busy = previewMutation.isPending || submitMutation.isPending;
  const canSubmit = Boolean(fileBase64 && preview && routeBudgetReady && !busy);
  const controlsLocked = previewMutation.isPending || submitMutation.isPending;

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
                  onChange={(event) => void handleFileChange(event.target.files?.[0] || null)}
                />
              </label>
              {fileError ? <InlineError message={fileError} /> : null}
              {previewMutation.error ? <InlineError message={(previewMutation.error as Error).message} /> : null}
              {previewMutation.isPending ? (
                <div className="flex items-center gap-2 rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  {t("Validating workbook and calculating OSRM route budget...")}
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">{t("Run settings")}</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
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
                  title={t("When enabled, To School audits release at 06:00 and From School audits release at 15:40 local time.")}
                  onClick={() => setScheduledJob((value) => !value)}
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

              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <Field label="Service Direction">
                  <select
                    className={fieldClassName}
                    value={config.service_direction}
                    onChange={(event) => {
                      updateUserConfig({ service_direction: event.target.value });
                    }}
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
                      onChange={(event) => updateUserConfig({ to_school_arrival_time: event.target.value })}
                    />
                  </Field>
                ) : (
                  <Field label="School Departure">
                    <input
                      className={fieldClassName}
                      type="time"
                      value={config.from_school_departure_time}
                      onChange={(event) => updateUserConfig({ from_school_departure_time: event.target.value })}
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
                    disabled={!preview}
                    title={t("Auto-set from the uploaded current plan after geocoding. This is not a live traffic estimate.")}
                  />
                  <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {routeBudgetDetail}
                  </div>
                </Field>
              </div>

              <Field label="Custom Job Name">
                <input
                  className={fieldClassName}
                  value={jobCustomName}
                  onChange={(event) => setJobCustomName(event.target.value)}
                  placeholder={t("May audit before parent review")}
                />
              </Field>

              <div className="grid gap-3 md:grid-cols-2">
                <ToggleOption tooltip="Limits planned load to 85% of vehicle capacity so routes are less crowded; may require more buses.">
                  <input
                    type="checkbox"
                    checked={Number(config.comfort_load_factor ?? FULL_CAPACITY_LOAD_FACTOR) < FULL_CAPACITY_LOAD_FACTOR}
                    onChange={(event) =>
                      updateUserConfig({
                        comfort_load_factor: event.target.checked ? COMFORT_LOAD_FACTOR : FULL_CAPACITY_LOAD_FACTOR,
                      })
                    }
                  />
                  <span>{t("Improve comfort")}</span>
                </ToggleOption>
                <ToggleOption tooltip="Adds a comparison scenario that groups eligible stops near subway stations before optimizing.">
                  <input
                    type="checkbox"
                    checked={config.include_subway_aggregation_scenario}
                    disabled={Boolean(preview?.subway_aggregation_block_reason)}
                    onChange={(event) => updateUserConfig({ include_subway_aggregation_scenario: event.target.checked })}
                  />
                  <span>{t("Subway baseline")}</span>
                </ToggleOption>
                <ToggleOption tooltip="Adds a comparison scenario that clusters nearby stops before optimizing.">
                  <input
                    type="checkbox"
                    checked={config.include_nearby_aggregation_scenario}
                    onChange={(event) => updateUserConfig({ include_nearby_aggregation_scenario: event.target.checked })}
                  />
                  <span>{t("Nearby baseline")}</span>
                </ToggleOption>
              </div>

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

              <SettingsSection
                title="Advanced aggregation settings"
                description="Optional search and clustering radii; only used when Subway or Nearby baselines are enabled."
                customized={hasUserConfigOverrides(AGGREGATION_SETTING_KEYS)}
                onReset={() => clearUserConfigOverrides(AGGREGATION_SETTING_KEYS)}
              >
                <div className="grid gap-3 md:grid-cols-3">
                  <NumberField
                    label="Subway Search Radius (m)"
                    value={config.subway_search_radius_m}
                    min={100}
                    max={5000}
                    step={100}
                    onChange={(value) => updateUserConfig({ subway_search_radius_m: value })}
                  />
                  <NumberField
                    label="Max Subway Walk Distance (m)"
                    value={config.max_subway_walk_distance_m}
                    min={50}
                    max={3000}
                    step={50}
                    onChange={(value) => updateUserConfig({ max_subway_walk_distance_m: value })}
                  />
                  <NumberField
                    label="Nearby Cluster Radius (m)"
                    value={config.nearby_cluster_radius_m}
                    min={50}
                    max={3000}
                    step={50}
                    onChange={(value) => updateUserConfig({ nearby_cluster_radius_m: value })}
                  />
                </div>
              </SettingsSection>

              {preview?.subway_aggregation_block_reason ? (
                <InlineError message={preview.subway_aggregation_block_reason} />
              ) : null}
              {submitMutation.error ? <InlineError message={(submitMutation.error as Error).message} /> : null}

              <div className="flex flex-col gap-3 sm:flex-row">
                <Button
                  type="button"
                  disabled={!canSubmit}
                  title={canSubmit ? undefined : t("Run audit will unlock after workbook validation and route-budget calculation finish.")}
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
  "h-10 w-full rounded-md border border-border bg-surface px-3 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20";

const toggleClassName =
  "flex h-11 items-center gap-3 rounded-md border border-border bg-surface px-3 text-sm font-medium text-foreground";

function ToggleOption({ tooltip, children }: { tooltip: string; children: ReactNode }) {
  const t = useT();
  return (
    <label className={`${toggleClassName} group relative cursor-pointer`}>
      {children}
      <span className="pointer-events-none absolute left-3 top-[calc(100%+6px)] z-20 w-72 translate-y-1 rounded-md border border-border bg-surface px-3 py-2 text-xs font-normal leading-relaxed text-muted-foreground opacity-0 shadow-lg transition group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:translate-y-0 group-focus-within:opacity-100">
        {t(tooltip)}
      </span>
    </label>
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
