import type { PlannerConfigPayload } from "@/lib/api";
import { getGoogleValidationQuota } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { useT } from "@/lib/i18n/context";

type Props = {
  config: Pick<PlannerConfigPayload, "final_time_validation_mode" | "validation_service_date" | "timing_policy" | "service_direction" | "time_window_start" | "time_window_end">;
  available: boolean;
  scheduled: boolean;
  minimumCalls?: number;
  onChange: (patch: Partial<Omit<Props["config"], "service_direction">>) => void;
};

export function GoogleValidationControls({ config, available, scheduled, minimumCalls, onChange }: Props) {
  const t = useT();
  const enabled = config.final_time_validation_mode === "google";
  const quotaQuery = useQuery({ queryKey: ["google-validation-quota"], queryFn: getGoogleValidationQuota,
    enabled: enabled && available, refetchInterval: 30000 });
  const quota = quotaQuery.data?.quota;
  const fixed = config.service_direction === "From School" || config.timing_policy === "fixed_departure";
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-3 border-b border-border py-3">
      <label htmlFor="google-final-validation" className="text-sm font-medium">
        {t("Google time validation")}
      </label>
      <button id="google-final-validation" type="button" role="switch"
        aria-checked={enabled} aria-label={t("Google time validation")}
        disabled={!available && !enabled}
        title={!available ? t("Google validation is not available on this deployment.") : undefined}
        onClick={() => onChange({ final_time_validation_mode: enabled ? "legacy" : "google",
          ...(!enabled ? { timing_policy: fixed ? "fixed_departure" : "arrival_anchored" } : {}) })}
        className={`relative h-6 w-11 shrink-0 rounded-full border transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50 ${enabled ? "border-primary bg-primary" : "border-border bg-muted"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${enabled ? "left-0.5 translate-x-5" : "left-0.5"}`} />
      </button>
      {enabled ? <div className="grid w-full min-w-0 gap-3 sm:grid-cols-2">
        <label className="flex flex-wrap items-center gap-2 text-sm">
          {t("Service date")}
          <input aria-label={t("Service date")} type="date" required
            className="h-9 min-w-0 max-w-full rounded-md border border-border bg-background px-2"
            value={config.validation_service_date || ""}
            onChange={(event) => onChange({ validation_service_date: event.target.value })} />
        </label>
        <span className="self-center text-xs text-muted-foreground">{t("Time zone")}: Asia/Shanghai (UTC+08:00)</span>
        <div role="tablist" aria-label={t("Prediction mode")} className="flex min-w-0 rounded-md border border-border p-1 sm:col-span-2">
          {(["arrival_anchored", "fixed_departure"] as const).map(policy => <button key={policy}
            type="button" role="tab" aria-selected={(policy === "fixed_departure") === fixed}
            disabled={policy === "arrival_anchored" && config.service_direction === "From School"}
            className={`min-h-9 min-w-0 flex-1 whitespace-normal rounded px-2 text-sm disabled:opacity-40 ${(policy === "fixed_departure") === fixed ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            onClick={() => onChange({ timing_policy: policy })}>
            {t(policy === "arrival_anchored" ? "Reverse from arrival" : "Fixed departure")}
          </button>)}
        </div>
        {(["time_window_start", "time_window_end"] as const).map(key => <label key={key} className="min-w-0 text-xs">
          {t(key === "time_window_end" ? "Latest arrival" : fixed ? "Fixed departure" : "Earliest departure")}
          <input type="time" required aria-label={t(key === "time_window_end" ? "Latest arrival" : fixed ? "Fixed departure" : "Earliest departure")}
            className="mt-1 h-9 w-full min-w-0 rounded-md border border-border bg-background px-2"
            value={config[key]} onChange={event => onChange({ [key]: event.target.value })} />
        </label>)}
        <dl className="grid min-w-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs sm:col-span-2">
          <dt>{t("Execution")}</dt><dd>{t(scheduled ? "Scheduled" : "Run now")}</dd>
          <dt>{t("Prediction")}</dt><dd className="break-words">{config.validation_service_date || t("Service date")} · {config.time_window_start} → {config.time_window_end}</dd>
          <dt>{t("Prediction mode")}</dt><dd>{t(fixed ? "Fixed departure" : "Reverse from arrival")}</dd>
          <dt>{t("Monthly requests remaining")}</dt><dd>{quota ? `${quota.remaining.toLocaleString()} / ${quota.limit.toLocaleString()} (${quota.month})` : "-"}</dd>
          {minimumCalls != null ? <><dt>{t("Minimum initial requests")}</dt><dd className={quota && minimumCalls > quota.remaining ? "text-destructive" : ""}>{minimumCalls.toLocaleString()}</dd>
            <dt>{t("Additional predictions")}</dt><dd>{t("Charged to the same monthly allowance")}</dd></> : null}
        </dl>
      </div> : null}
    </div>
  );
}
