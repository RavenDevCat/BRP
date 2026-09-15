import { useState } from "react";
import { createRoot } from "react-dom/client";
import { GoogleValidationControls } from "../apps/web/src/features/planner/google-validation-controls";
import { DEFAULT_PLANNER_CONFIG } from "../apps/web/src/features/planner/config";
import { LanguageProvider } from "../apps/web/src/lib/i18n/context";

function Fixture() {
  const [config, setConfig] = useState(DEFAULT_PLANNER_CONFIG);
  const params = new URLSearchParams(location.search);
  return <LanguageProvider switchEnabled availableLanguages={["en", "zh", "ko"]}>
    <main style={{ maxWidth: 820, margin: "24px auto", padding: 16, background: "white" }}>
      <GoogleValidationControls config={config} available={!params.has("disabled")}
        scheduled={params.has("scheduled")} onChange={patch => setConfig(previous => ({ ...previous, ...patch }))} />
      <output id="mode" style={{ display: "block", marginTop: 24 }}>{config.final_time_validation_mode || "legacy"}</output>
    </main>
  </LanguageProvider>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
