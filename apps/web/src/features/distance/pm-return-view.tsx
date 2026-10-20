import { useEffect, useMemo, useRef, useState } from "react";
import MapView, { Layer, NavigationControl, Source, type MapRef } from "react-map-gl/maplibre";
import type { FeatureCollection, MultiLineString, Point } from "geojson";
import { Download } from "lucide-react";
import { BRP_MAP_STYLE } from "@/features/results/interactive-route-map";
import { buttonClassName } from "@/components/ui/button-styles";
import { formatDateTime } from "@/lib/format";
import { useT } from "@/lib/i18n/context";
import { cn } from "@/lib/cn";

import type { ReturnResult, ReturnScenarioGroup } from "@/lib/return-types";

export const returnDuration = (seconds: number | undefined) => seconds == null ? "-" : `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

const returnScenarios = [
  ["current_plan", "Current Plan"], ["time_constrained", "Strict Plan"], ["exception_preserving", "Protected Plan"],
] as const;

export function PmReturnView({ result, exportUrl }: { result: ReturnResult | ReturnScenarioGroup; exportUrl: string }) {
  const t = useT();
  const [scenario, setScenario] = useState("current_plan");
  if (!("scenarios" in result)) return <ReturnScenarioView result={result} exportUrl={exportUrl} />;
  const selected = result.scenarios[scenario];
  const label = returnScenarios.find(([key]) => key === scenario)![1];
  return <section className="min-w-0 space-y-4" aria-label={t("Afternoon return")}>
    <div role="tablist" aria-label={t("Return scenarios")} className="flex flex-wrap gap-1 border-b border-border">
      {returnScenarios.map(([key, title]) => <button key={key} type="button" role="tab"
        aria-selected={scenario === key} onClick={() => setScenario(key)}
        className={cn("min-h-10 border-b-2 px-3 text-sm", scenario === key ? "border-primary font-semibold text-primary" : "border-transparent text-muted-foreground")}>
        {t(title)}
      </button>)}
    </div>
    <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
      <a href={`/jobs/${result.source_job_id}`} className="text-primary underline">{t("Morning to school")}</a>
      <span>{t("Source version")}: {result.source_version.slice(0, 12)}</span>
      <span>{t("Provider calls")}: {result.progress.provider_api_calls}</span>
    </div>
    {result.delayed ? <p role="status" className="border-l-4 border-amber-500 px-3 py-2 text-sm text-amber-900">{t("Return measured after morning completion; actual measurement time applies.")}</p> : null}
    {selected?.source_status && selected.source_status !== "passed" ? <p role="status" className="border-l-4 border-amber-500 px-3 py-2 text-sm text-amber-900">
      {t("Morning scenario status")}: {t(selected.source_status)}. {t("Return timing does not approve the morning plan.")}
    </p> : null}
    {selected ? <ReturnScenarioView key={scenario} result={selected} exportUrl={exportUrl} scenarioLabel={t(label)} />
      : <p role="status" className="py-6 text-sm text-muted-foreground">{t("Waiting for scenario measurement")}</p>}
  </section>;
}

function ReturnScenarioView({ result, exportUrl, scenarioLabel }: { result: ReturnResult; exportUrl: string; scenarioLabel?: string }) {
  const t = useT();
  const [onlyShort, setOnlyShort] = useState(false);
  const [selected, setSelected] = useState("");
  const map = useRef<MapRef>(null);
  const shown = useMemo(() => result.routes.filter(route => !onlyShort || route.under_one_hour), [result.routes, onlyShort]);
  const active = shown.find(route => route.route_id === selected) || shown[0];
  const lines = useMemo<FeatureCollection<MultiLineString>>(() => ({ type: "FeatureCollection", features: shown.flatMap(route => {
    const segments = route.geometry_segments || (route.geometry?.length ? [route.geometry] : []);
    const valid = segments.filter(segment => segment.length >= 2);
    return valid.length ? [{ type: "Feature" as const, geometry: { type: "MultiLineString" as const, coordinates: valid },
      properties: { id: route.route_id, short: route.under_one_hour, active: route.route_id === active?.route_id } }] : [];
  }) }), [shown, active?.route_id]);
  const points = useMemo<FeatureCollection<Point>>(() => ({ type: "FeatureCollection", features: (active?.stops || []).flatMap(stop =>
    Number.isFinite(stop.lat) && Number.isFinite(stop.lng) ? [{ type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [stop.lng!, stop.lat!] },
      properties: { label: stop.is_depot ? "S" : String(stop.stop_sequence) } }] : []) }), [active]);
  const allCoordinates = useMemo(() => shown.flatMap(route => route.stops.flatMap(stop =>
    Number.isFinite(stop.lat) && Number.isFinite(stop.lng) ? [[stop.lng!, stop.lat!]] : [])), [shown]);
  const bounds: [[number, number], [number, number]] | undefined = allCoordinates.length ? [
    [Math.min(...allCoordinates.map(p => p[0])), Math.min(...allCoordinates.map(p => p[1]))],
    [Math.max(...allCoordinates.map(p => p[0])), Math.max(...allCoordinates.map(p => p[1]))],
  ] : undefined;
  useEffect(() => {
    if (bounds) map.current?.fitBounds(bounds, { padding: 55, maxZoom: 14, duration: 0 });
  }, [allCoordinates]);
  const exportMap = () => {
    const source = map.current?.getCanvas();
    if (!source) return;
    const canvas = document.createElement("canvas");
    canvas.width = source.width; canvas.height = source.height + 110;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.fillStyle = "#ffffff"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#111827"; context.font = "18px sans-serif";
    context.fillText(`${t("Afternoon return")}${scenarioLabel ? ` - ${scenarioLabel}` : ""} · ${t("Under 1 hour")}: ${result.summary.under_one_hour_count}`, 16, 26);
    context.font = "13px sans-serif";
    context.fillText(`${t("Measured at")}: ${formatDateTime(result.started_at)} · ${t("Current traffic snapshot")}`, 16, 48);
    context.fillStyle = "#059669";
    context.fillText(`${t("Under 1 hour")} · ${t("Driving + stop dwell")}`, 16, 68);
    context.drawImage(source, 0, 80);
    context.fillStyle = "#334155";
    context.fillText("© OpenStreetMap contributors | MapLibre", 16, canvas.height - 10);
    canvas.toBlob(blob => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `afternoon-return-${result.scenario_key || "map"}.png`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  };
  return <section className="min-w-0 space-y-4" aria-label={t("Afternoon return")}>
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
      <div><h2 className="text-base font-semibold">{t("Afternoon return")}{scenarioLabel ? ` - ${scenarioLabel}` : ""}</h2>
        <p className="mt-1 text-xs text-muted-foreground">{t("Measured at")}: {formatDateTime(result.started_at)} · {t("Current traffic snapshot")}</p></div>
      <a className={buttonClassName("secondary")} href={exportUrl}><Download className="h-4 w-4" />{t("Export statistics")}</a>
    </header>
    {result.error ? <p role="alert" className="border-l-4 border-amber-500 px-3 py-2 text-sm text-amber-900">{t(result.error)}</p> : null}
    {result.summary.completed_route_count < result.summary.route_count ? <p role="status" className="border-l-4 border-amber-500 bg-amber-50 px-3 py-2 text-sm text-amber-900">{t("Incomplete measurement; counts cover measured routes only.")}</p> : null}
    <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
      <span>{t("Routes measured")}: <strong>{result.summary.completed_route_count} / {result.summary.route_count}</strong></span>
      <span className="text-emerald-800">{t("Under 1 hour")}: <strong>{result.summary.under_one_hour_count}</strong></span>
      <span>{t("riders")}: <strong>{result.summary.rider_count}</strong></span>
      <span>{t("Students over trip limit")} ({result.parameters?.far_duration_minutes ?? 45} {t("min")}): <strong>{result.routes.reduce((count, route) => count + (route.over_limit_riders || 0), 0)}</strong></span>
      <span>{t("Provider calls")}: {result.progress.provider_api_calls}</span>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div role="tablist" aria-label={t("Return route filter")} className="flex gap-1 border-b border-border">
        {[false, true].map(short => <button key={String(short)} type="button" role="tab" aria-selected={short === onlyShort}
          className={cn("min-h-9 border-b-2 px-3 text-sm", short === onlyShort ? "border-primary font-semibold text-primary" : "border-transparent text-muted-foreground")}
          onClick={() => setOnlyShort(short)}>{t(short ? "Under 1 hour" : "All routes")}</button>)}
      </div>
      <button className={buttonClassName("secondary")} onClick={exportMap} disabled={!lines.features.length}>
        <Download className="h-4 w-4" />{t("Export map")}</button>
    </div>
    <div className="grid min-w-0 gap-3 xl:grid-cols-[260px_minmax(0,1fr)]">
      <div className="max-h-[520px] overflow-y-auto border border-border" aria-label={t("Return routes")}>
        {!shown.length ? <p className="p-4 text-sm text-muted-foreground">{t("No routes under 1 hour")}</p> : shown.map(route =>
          <button key={route.route_id} type="button" onClick={() => setSelected(route.route_id)} aria-pressed={active?.route_id === route.route_id}
            className={cn("block w-full border-b border-border border-l-4 p-3 text-left last:border-b-0", active?.route_id === route.route_id ? "bg-primary/5" : "bg-surface",
              route.under_one_hour ? "border-l-emerald-600" : "border-l-gray-400")}>
            <div className="flex flex-wrap items-center justify-between gap-2"><strong>{route.route_id}</strong>
              {route.under_one_hour ? <span className="text-xs text-emerald-800">{t("Under 1 hour")}</span> : null}</div>
            <div className="mt-1 text-sm tabular-nums">{returnDuration(route.total_duration_s)} {t("min:sec")}</div>
            <div className="mt-1 text-xs text-muted-foreground">{route.stop_count} {t("stops")} · {route.riders} {t("riders")}</div>
            {route.status !== "resolved" ? <div className="mt-1 text-xs text-amber-800">{t("Travel time unavailable")}</div> : null}
          </button>)}
      </div>
      <div className="relative h-[420px] min-w-0 overflow-hidden border border-border sm:h-[520px]">
        <MapView ref={map} mapStyle={BRP_MAP_STYLE} canvasContextAttributes={{ preserveDrawingBuffer: true }}
          initialViewState={bounds ? { bounds, fitBoundsOptions: { padding: 55, maxZoom: 14 } } : { longitude: 121.47, latitude: 31.23, zoom: 10 }}
          interactiveLayerIds={["return-lines"]} onClick={event => {
            const id = event.features?.[0]?.properties?.id;
            if (typeof id === "string") setSelected(id);
          }}>
          <NavigationControl position="bottom-right" />
          <Source id="return-routes" type="geojson" data={lines}>
            <Layer id="return-casing" type="line" paint={{ "line-color": "#ffffff", "line-width": ["case", ["get", "active"], 10, 7] }} />
            <Layer id="return-lines" type="line" paint={{ "line-color": ["case", ["get", "short"], "#059669", "#64748b"],
              "line-width": ["case", ["get", "active"], 6, ["get", "short"], 5, 3],
              "line-opacity": ["case", ["get", "active"], 1, ["get", "short"], .95, .5] }} />
          </Source>
          <Source id="return-stops" type="geojson" data={points}>
            <Layer id="return-stop-circles" type="circle" paint={{ "circle-radius": 12, "circle-color": "#ffffff", "circle-stroke-width": 2, "circle-stroke-color": "#0f172a" }} />
            <Layer id="return-stop-labels" type="symbol" layout={{ "text-field": ["get", "label"], "text-size": 12, "text-allow-overlap": true }} paint={{ "text-color": "#0f172a" }} />
          </Source>
        </MapView>
        <div className="pointer-events-none absolute left-3 top-3 border border-border bg-white/95 px-3 py-2 text-xs">
          <span className="mr-2 inline-block h-2 w-2 bg-emerald-600" />{t("Under 1 hour")} · {t("Driving + stop dwell")}
        </div>
      </div>
    </div>
    {active ? <div className="min-w-0 overflow-x-auto border-t border-border pt-3">
      <div className="mb-3 flex flex-wrap gap-x-5 gap-y-2 text-sm"><strong>{active.route_id}</strong>
        <span>{t("Driving")}: {returnDuration(active.drive_duration_s)}</span><span>{t("Stop dwell")}: {returnDuration(active.dwell_duration_s)}</span>
        <span>{t("Measured at")}: {formatDateTime(active.measured_at)}</span></div>
      <table className="w-full min-w-[580px] text-left text-sm"><thead className="border-b border-border text-xs text-muted-foreground"><tr>
        {["Stop", "Address", "riders", "Ride time", "Estimated arrival"].map(label => <th key={label} className="p-2 font-medium">{t(label)}</th>)}
      </tr></thead><tbody>{active.stops.map(stop => <tr key={stop.stop_sequence} className="border-b border-border last:border-0">
        <td className="p-2">{stop.is_depot ? "S" : stop.stop_sequence}</td><td className="max-w-[320px] break-words p-2">{stop.address}</td>
        <td className="p-2">{stop.passenger_count}</td><td className={cn("p-2 tabular-nums", stop.over_limit && "font-semibold text-red-700")}>{returnDuration(stop.ride_duration_s)}</td>
        <td className="p-2">{formatDateTime(stop.arrival_at)}</td>
      </tr>)}</tbody></table>
    </div> : null}
  </section>;
}
