import { useQuery } from "@tanstack/react-query";
import { getDeploymentFeatures, type FinalTimingConfig } from "@/lib/api";
import { GoogleValidationControls } from "./google-validation-controls";

export const DEFAULT_FINAL_TIMING: FinalTimingConfig = {
  final_time_validation_mode: "legacy", validation_service_date: "",
  time_window_start: "06:30", time_window_end: "08:00", service_direction: "To School",
};

export function FinalTimingOptions({ value, onChange, eligible = true }: {
  value: FinalTimingConfig; onChange: (value: FinalTimingConfig) => void; eligible?: boolean;
}) {
  const features = useQuery({ queryKey: ["deployment-features"], queryFn: getDeploymentFeatures });
  return <div className="min-w-0 space-y-2">
    <GoogleValidationControls config={value} scheduled={false}
      available={eligible && features.data?.google_final_validation?.available === true}
      onChange={(patch) => onChange({ ...value, ...patch })} />
  </div>;
}
