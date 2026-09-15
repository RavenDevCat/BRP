import { useQuery } from "@tanstack/react-query";
import { getDeploymentFeatures, type FinalTimingConfig } from "@/lib/api";
import { useT } from "@/lib/i18n/context";
import { GoogleValidationControls } from "./google-validation-controls";

export const DEFAULT_FINAL_TIMING: FinalTimingConfig = {
  final_time_validation_mode: "legacy", validation_service_date: "",
  time_window_start: "06:30", time_window_end: "08:00", service_direction: "To School",
};

export function FinalTimingOptions({ value, onChange, eligible = true }: {
  value: FinalTimingConfig; onChange: (value: FinalTimingConfig) => void; eligible?: boolean;
}) {
  const t = useT();
  const features = useQuery({ queryKey: ["deployment-features"], queryFn: getDeploymentFeatures });
  const enabled = value.final_time_validation_mode === "google";
  return <div className="min-w-0 space-y-2">
    <GoogleValidationControls config={value} scheduled={false}
      available={eligible && features.data?.google_final_validation?.available === true}
      onChange={(patch) => onChange({ ...value, ...patch })} />
    {enabled && <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
      {(["time_window_start", "time_window_end"] as const).map((key) => <label key={key} className="min-w-0 text-xs">
        {t(key === "time_window_start" ? "Earliest departure" : "Latest arrival")}
        <input type="time" required aria-label={t(key === "time_window_start" ? "Earliest departure" : "Latest arrival")}
          className="mt-1 h-9 w-full min-w-0 rounded-md border border-border bg-background px-2"
          value={value[key]} onChange={(event) => onChange({ ...value, [key]: event.target.value })} />
      </label>)}
    </div>}
  </div>;
}
