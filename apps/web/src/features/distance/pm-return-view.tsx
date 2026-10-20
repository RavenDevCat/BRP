import { useMemo, useState } from "react";
import { Download, FileSpreadsheet, Map, Maximize2, X } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { InteractiveRouteMap } from "@/features/results/interactive-route-map";
import { ScenarioChoice } from "@/features/results/scenario-choice";
import { downloadInteractiveMapHtml } from "@/features/results/route-map-export";
import { returnMapData, underReturnLimit } from "@/features/distance/return-map-data";
import { formatDateTime, formatDistanceKmFromMeters, formatDurationMinFromSeconds } from "@/lib/format";
import { useT } from "@/lib/i18n/context";
import { cn } from "@/lib/cn";
import type { ReturnResult, ReturnScenarioGroup } from "@/lib/return-types";

export const returnDuration = (seconds: number | undefined) => seconds == null ? "-" : `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const scenarios = [
  ["current_plan", "Current Plan"], ["time_constrained", "Strict Plan"], ["exception_preserving", "Protected Plan"],
] as const;

export function PmReturnView({ result, exportUrl }: { result: ReturnResult | ReturnScenarioGroup; exportUrl: string }) {
  const t = useT();
  const [scenario, setScenario] = useState("current_plan");
  const [fullscreen, setFullscreen] = useState(false);
  const initialLimit = result.parameters?.return_duration_limit_minutes ??
    ("threshold_s" in result ? (result.threshold_s ?? 3600) / 60 : 60);
  const [limit, setLimit] = useState(initialLimit);
  const [draft, setDraft] = useState(String(initialLimit));
  const valid = draft.trim() !== "" && Number.isFinite(Number(draft)) && Number(draft) >= 1 && Number(draft) <= 240;
  const grouped = "scenarios" in result;
  const selected = grouped ? result.scenarios[scenario] : result;
  const choices = grouped ? scenarios : [scenarios[0]];
  const label = t(scenarios.find(([key]) => key === scenario)![1]);
  const data = useMemo(() => selected ? returnMapData(selected, label, limit) : null, [selected, label, limit]);
  const downloadUrl = `${exportUrl}${exportUrl.includes("?") ? "&" : "?"}return_duration_limit_minutes=${limit}`;
  const underLabel = `${t("Under limit")} (< ${limit} ${t("min")})`;
  const downloadMap = () => {
    if (data) downloadInteractiveMapHtml(data, t("Afternoon return"), `${label} - ${underLabel}`, t);
  };
  const warning = "rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900";
  return <section className="min-w-0 space-y-4" aria-label={t("Afternoon return")}>
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2"><Map className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">{t("Route maps")} · {t("Afternoon return")}</h2></div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{t("Return duration limit (min)")}</span>
            <input type="number" min="1" max="240" step="any" value={draft} aria-invalid={!valid}
              className="h-9 w-20 rounded-md border border-border bg-background px-2 text-sm text-foreground"
              onChange={event => {
                const value = event.target.value;
                setDraft(value);
                const numeric = Number(value);
                if (value.trim() && Number.isFinite(numeric) && numeric >= 1 && numeric <= 240) setLimit(numeric);
              }} />
          </label>
        </div>
        {!valid ? <p role="alert" className="text-xs text-danger">{t("Return duration limit must be between 1 and 240 minutes.")}</p> : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3" aria-label={t("Return scenarios")}>
          {choices.map(([key, title]) => {
            const item = grouped ? result.scenarios[key] : result;
            const mapped = item ? returnMapData(item, t(title), limit) : null;
            return <ScenarioChoice key={key} selected={key === scenario} onSelect={() => setScenario(key)}>
              <div className="flex items-center justify-between gap-3">
                <div className="truncate text-sm font-semibold">{t(title)}</div>
                <Badge tone={key === scenario ? "info" : "neutral"}>{item?.summary.route_count ?? 0} {t("routes")}</Badge>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <div>{t("Stops")}: {mapped?.summary.stop_count ?? "-"}</div>
                <div>{t("Riders")}: {item?.summary.rider_count ?? "-"}</div>
                <div>{t("Total")}: {formatDistanceKmFromMeters(mapped?.summary.distance_m ?? null)}</div>
                <div>{t("Longest")}: {formatDurationMinFromSeconds(mapped?.summary.duration_s ?? null)}</div>
                <div className="col-span-2 font-medium text-emerald-700">{underLabel}: {item?.routes.filter(route => underReturnLimit(route, limit)).length ?? 0}</div>
                <div className="col-span-2">{t("Routes measured")}: {item?.summary.completed_route_count ?? 0} / {item?.summary.route_count ?? 0}</div>
              </div>
            </ScenarioChoice>;
          })}
        </div>
        {grouped && result.delayed ? <p role="status" className={warning}>{t("Return measured after morning completion; actual measurement time applies.")}</p> : null}
        {selected?.source_status && selected.source_status !== "passed" ? <p role="status" className={warning}>
          {t("Morning scenario status")}: {t(selected.source_status)}. {t("Return timing does not approve the morning plan.")}
        </p> : null}
        {selected?.error ? <p role="alert" className={warning}>{t(selected.error)}</p> : null}
        {selected && selected.summary.completed_route_count < selected.summary.route_count ?
          <p role="status" className={warning}>{t("Incomplete measurement; counts cover measured routes only.")}</p> : null}
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>{t("Measured at")}: {formatDateTime(selected?.started_at)} · {t("Current traffic snapshot")}</span>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={buttonClassName("secondary")} disabled={!data?.routes.length}
              title={t("Open map")} aria-label={t("Open map")} onClick={() => setFullscreen(true)}>
              <Maximize2 className="h-4 w-4" />{t("Open")}
            </button>
            <button type="button" className={buttonClassName("secondary")} disabled={!data?.routes.length || !valid}
              title={t("Download map")} aria-label={t("Download map")} onClick={downloadMap}>
              <Download className="h-4 w-4" />{t("Map")}
            </button>
            <a className={cn(buttonClassName("secondary"), !valid && "pointer-events-none opacity-50")}
              aria-disabled={!valid} tabIndex={valid ? 0 : -1} href={valid ? downloadUrl : undefined}
              title={t("Download workbook")} aria-label={t("Download workbook")}>
              <FileSpreadsheet className="h-4 w-4" />{t("Workbook")}
            </a>
          </div>
        </div>
        {data?.routes.length ? <InteractiveRouteMap key={scenario} data={data} /> :
          <p role="status" className="py-6 text-sm text-muted-foreground">{t("Waiting for scenario measurement")}</p>}
        <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
          <span>{t("Driving + stop dwell")}</span>
          {grouped ? <><a className="text-primary underline" href={`/jobs/${result.source_job_id}`}>{t("Morning to school")}</a>
            <span>{t("Source version")}: {result.source_version.slice(0, 12)}</span></> : null}
          <span>{t("Provider calls")}: {result.progress.provider_api_calls}</span>
        </div>
      </CardContent>
    </Card>
    {fullscreen && data ? <div role="dialog" aria-modal="true" aria-label={t("Route maps")}
      className="fixed inset-0 z-50 bg-slate-950/42 p-2 backdrop-blur-sm sm:p-4 lg:p-6"
      onKeyDown={event => { if (event.key === "Escape") setFullscreen(false); }}>
      <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-md border border-border bg-surface">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{label} · {t("Afternoon return")}</h2>
          <button autoFocus type="button" className={buttonClassName("secondary")} aria-label={t("Close")}
            onClick={() => setFullscreen(false)}><X className="h-4 w-4" /></button>
        </div>
        <div className="min-h-0 flex-1"><InteractiveRouteMap key={scenario} data={data} fullscreen /></div>
      </div>
    </div> : null}
  </section>;
}
