import type { PlannerConfigPayload } from "@/lib/api";
import { useT } from "@/lib/i18n/context";

type Props = {
  config: PlannerConfigPayload;
  available: boolean;
  scheduled: boolean;
  onChange: (patch: Partial<PlannerConfigPayload>) => void;
};

export function GoogleValidationControls({ config, available, scheduled, onChange }: Props) {
  const t = useT();
  const enabled = config.final_time_validation_mode === "google";
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border py-3">
      <label htmlFor="google-final-validation" className="text-sm font-medium">
        {t("Google time validation")}
      </label>
      <button id="google-final-validation" type="button" role="switch"
        aria-checked={enabled} aria-label={t("Google time validation")}
        disabled={!available && !enabled}
        title={!available ? t("Google validation is not available on this deployment.") : undefined}
        onClick={() => onChange({ final_time_validation_mode: enabled ? "legacy" : "google" })}
        className={`relative h-6 w-11 shrink-0 rounded-full border transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50 ${enabled ? "border-primary bg-primary" : "border-border bg-muted"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${enabled ? "left-0.5 translate-x-5" : "left-0.5"}`} />
      </button>
      {enabled && !scheduled ? (
        <label className="flex flex-wrap items-center gap-2 text-sm">
          {t("Service date")}
          <input aria-label={t("Service date")} type="date" required
            className="h-9 min-w-0 max-w-full rounded-md border border-border bg-background px-2"
            value={config.validation_service_date || ""}
            onChange={(event) => onChange({ validation_service_date: event.target.value })} />
        </label>
      ) : null}
    </div>
  );
}
