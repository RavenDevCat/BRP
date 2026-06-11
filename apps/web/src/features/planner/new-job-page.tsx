import type { Dispatch, ReactNode, SetStateAction } from "react";
import { useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { CheckCircle2, Download, FileSpreadsheet, Loader2, Send, SlidersHorizontal, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { DEFAULT_PLANNER_CONFIG, SERVICE_DIRECTION_OPTIONS, TRAFFIC_PROFILE_OPTIONS } from "@/features/planner/config";
import {
  getWorkbookTemplateUrl,
  previewWorkbook,
  submitWorkbookJob,
  type PlannerConfigPayload,
  type WorkbookPreview,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";

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
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [jobCustomName, setJobCustomName] = useState("");
  const [config, setConfig] = useState<PlannerConfigPayload>(DEFAULT_PLANNER_CONFIG);
  const [preview, setPreview] = useState<WorkbookPreview | null>(null);
  const configOverridesRef = useRef<Partial<PlannerConfigPayload>>({});

  function buildConfigWithOverrides(
    baseConfig: PlannerConfigPayload,
    subwayAggregationBlocked = false,
    overrides = configOverridesRef.current,
  ) {
    const safeOverrides = { ...overrides };
    if (subwayAggregationBlocked) {
      delete safeOverrides.include_subway_aggregation_scenario;
    }
    return { ...baseConfig, ...safeOverrides };
  }

  function resetConfigToDefaultsWithOverrides() {
    setConfig(buildConfigWithOverrides(DEFAULT_PLANNER_CONFIG));
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
        : buildConfigWithOverrides(DEFAULT_PLANNER_CONFIG, false, nextOverrides),
    );
  }

  function configFromPreview(payload: WorkbookPreview) {
    return buildConfigWithOverrides(
      payload.suggested_config,
      Boolean(payload.subway_aggregation_block_reason),
    );
  }

  const previewMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64) {
        throw new Error("Select a workbook first.");
      }
      return previewWorkbook({ file_name: file.name, file_base64: fileBase64, config });
    },
    onSuccess: (payload) => {
      setPreview(payload);
      setConfig(configFromPreview(payload));
    },
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64) {
        throw new Error("Select a workbook first.");
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
      });
    },
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await navigate({ to: "/jobs/$jobId", params: { jobId: payload.job.job_id } });
    },
  });

  const jobNamePreview = useMemo(() => {
    const fallback = file?.name ? file.name.replace(/\.[^.]+$/, "") : "Untitled job";
    const baseName = preview?.job_default_name || fallback;
    const suffix = jobCustomName.trim().replace(/\s+/g, " ");
    return suffix ? `${baseName} - ${suffix}` : baseName;
  }, [file?.name, jobCustomName, preview?.job_default_name]);

  async function handleFileChange(nextFile: File | null) {
    setFile(nextFile);
    setPreview(null);
    resetConfigToDefaultsWithOverrides();
    setFileError("");
    setFileBase64("");
    if (!nextFile) {
      return;
    }
    const suffix = nextFile.name.split(".").pop()?.toLowerCase();
    if (!suffix || !["xlsx", "xlsm"].includes(suffix)) {
      setFileError("Use an .xlsx or .xlsm workbook.");
      return;
    }
    try {
      setFileBase64(await fileToBase64(nextFile));
    } catch (error) {
      setFileError(error instanceof Error ? error.message : "Workbook could not be read.");
    }
  }

  const busy = previewMutation.isPending || submitMutation.isPending;
  const canSubmit = Boolean(fileBase64 && !busy);

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-primary">Current plan audit</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">New audit</h1>
        </div>
        <Link to="/jobs" className={buttonClassName("secondary")}>
          Audit history
        </Link>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">Workbook</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
                <div className="text-sm text-muted-foreground">Upload a completed current-plan workbook.</div>
                <a className={buttonClassName("secondary")} href={getWorkbookTemplateUrl()}>
                  <Download className="h-4 w-4" aria-hidden="true" />
                  Template
                </a>
              </div>

              <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/60 px-4 py-6 text-center transition hover:border-primary/60 hover:bg-muted">
                <Upload className="mb-3 h-6 w-6 text-primary" aria-hidden="true" />
                <span className="text-sm font-medium">{file?.name || "Select workbook"}</span>
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
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">Run settings</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <Field label="Service Direction">
                  <select
                    className={fieldClassName}
                    value={config.service_direction}
                    onChange={(event) => {
                      setPreview(null);
                      updateUserConfig({ service_direction: event.target.value });
                    }}
                  >
                    {SERVICE_DIRECTION_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
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
                        {option}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Target Duration">
                  <input
                    className={fieldClassName}
                    type="number"
                    min={10}
                    max={300}
                    step={5}
                    value={config.max_route_duration_minutes}
                    onChange={(event) => updateUserConfig({ max_route_duration_minutes: Number(event.target.value) })}
                  />
                </Field>
              </div>

              <Field label="Custom Job Name">
                <input
                  className={fieldClassName}
                  value={jobCustomName}
                  onChange={(event) => setJobCustomName(event.target.value)}
                  placeholder="May audit before parent review"
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
                  <span>Improve comfort</span>
                </ToggleOption>
                <ToggleOption tooltip="Adds a comparison scenario that groups eligible stops near subway stations before optimizing.">
                  <input
                    type="checkbox"
                    checked={config.include_subway_aggregation_scenario}
                    disabled={Boolean(preview?.subway_aggregation_block_reason)}
                    onChange={(event) => updateUserConfig({ include_subway_aggregation_scenario: event.target.checked })}
                  />
                  <span>Subway baseline</span>
                </ToggleOption>
                <ToggleOption tooltip="Adds a comparison scenario that clusters nearby stops before optimizing.">
                  <input
                    type="checkbox"
                    checked={config.include_nearby_aggregation_scenario}
                    onChange={(event) => updateUserConfig({ include_nearby_aggregation_scenario: event.target.checked })}
                  />
                  <span>Nearby baseline</span>
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
                  variant="secondary"
                  disabled={!fileBase64 || busy}
                  icon={previewMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                  onClick={() => previewMutation.mutate()}
                >
                  Validate workbook
                </Button>
                <Button
                  type="button"
                  disabled={!canSubmit}
                  title={preview ? undefined : "Workbook will be validated before the audit starts."}
                  icon={submitMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  onClick={() => submitMutation.mutate()}
                >
                  Run audit
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold">Job</h2>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div>
                <div className="text-xs uppercase text-muted-foreground">Name</div>
                <div className="mt-1 break-words font-medium">{jobNamePreview}</div>
              </div>
              {preview ? <PreviewSummary preview={preview} /> : <EmptyPreview />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold">Fleet slots</h2>
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
  return (
    <label className={`${toggleClassName} group relative cursor-pointer`}>
      {children}
      <span className="pointer-events-none absolute left-3 top-[calc(100%+6px)] z-20 w-72 translate-y-1 rounded-md border border-border bg-surface px-3 py-2 text-xs font-normal leading-relaxed text-muted-foreground opacity-0 shadow-lg transition group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:translate-y-0 group-focus-within:opacity-100">
        {tooltip}
      </span>
    </label>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium uppercase text-muted-foreground">{label}</span>
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
  return (
    <details className="group rounded-md border border-border bg-muted/40">
      <summary className="cursor-pointer list-none px-3 py-3 marker:hidden">
        <span className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <span className="inline-flex min-w-0 items-start gap-2">
            <span className="mt-0.5 text-muted-foreground transition group-open:rotate-90">&gt;</span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-foreground">{title}</span>
              <span className="mt-1 block text-xs font-normal text-muted-foreground">{description}</span>
            </span>
          </span>
          <span className="flex w-fit shrink-0 items-center gap-2">
            <Badge tone="info">Optional</Badge>
            {customized ? <Badge tone="warning">Custom</Badge> : null}
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
                Reset
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
  const summary = preview.summary;
  return (
    <div className="grid grid-cols-2 gap-3">
      <SummaryMetric label="Routes" value={summary.route_count} />
      <SummaryMetric label="Service rows" value={summary.assignment_count} />
      <SummaryMetric label="Planning stops" value={summary.planning_stop_count} />
      <SummaryMetric label="Fleet types" value={summary.fleet_count} />
      <div className="col-span-2">
        <div className="mb-2 text-xs uppercase text-muted-foreground">Bus types</div>
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
  return (
    <div className="rounded-md border border-border bg-muted px-3 py-4 text-sm text-muted-foreground">
      No workbook validated
    </div>
  );
}

function SummaryMetric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-md border border-border bg-muted px-3 py-2">
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{formatNumber(value)}</div>
    </div>
  );
}

function FleetSlot({ label, seats, count }: { label: string; seats: number; count: number }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm">
      <div className="min-w-0">
        <div className="truncate font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{formatNumber(seats)} seats</div>
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
