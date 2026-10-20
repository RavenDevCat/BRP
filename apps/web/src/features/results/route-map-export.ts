import type { JobMapData } from "@/lib/api";
import { formatNumber } from "@/lib/format";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}
function template(text: string, values: Record<string, string | number>) {
  return text.replace(/\{(\w+)\}/g, (match, key: string) => String(values[key] ?? match));
}
function htmlEscape(value: string) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function downloadInteractiveMapHtml(data: JobMapData, jobName: string, mapName: string, t: (key: string, fallback?: string) => string = (key) => key) {
  const filename = `${sanitizeDownloadFilename(jobName)} - ${sanitizeDownloadFilename(mapName)}.html`;
  const blob = new Blob([buildStandaloneInteractiveMapHtml(data, jobName, mapName, t)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function sanitizeDownloadFilename(value: string) {
  const cleaned = value
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (cleaned || "BRP Map").slice(0, 120).trim();
}

function buildStandaloneInteractiveMapHtml(data: JobMapData, jobName: string, mapName: string, t: (key: string, fallback?: string) => string = (key) => key) {
  const payload = JSON.stringify(data).replace(/</g, "\\u003c");
  const title = `${jobName} - ${mapName}`;
  const summaryRecord = asRecord(data.summary);
  const amGate = asRecord(summaryRecord.am_arrival_gate);
  const amStatus = String(amGate.status || "");
  const amWindowSummary =
    amStatus === "failed"
      ? template(t("Time window failed ({count} routes)"), { count: formatNumber(amGate.failed_route_count) })
      : amStatus === "unavailable"
        ? template(t("Time window unverified ({count} routes)"), { count: formatNumber(amGate.unavailable_route_count) })
        : amStatus === "passed"
          ? t("Time window passed")
          : "";
  const underLimitCount = data.routes.filter(route => route.duration_s !== null && route.duration_s < (data.duration_limit_s || 0)).length;
  const limitSummary = data.duration_limit_s ? ` · ${t("Under limit")} (< ${data.duration_limit_s / 60} ${t("min")}): ${underLimitCount}` : "";
  const mapSummary = `${formatNumber(data.summary.route_count)} ${t("routes")} · ${formatNumber(data.summary.stop_count)} ${t("stops")} · ${formatNumber(data.summary.passenger_count)} ${t("riders")}${amWindowSummary ? ` · ${amWindowSummary}` : ""}${limitSummary}`;
  const standaloneTileUrls = ["https://tile.openstreetmap.de/{z}/{x}/{y}.png"];
  const routeColors = [
    "#0f766e", "#2563eb", "#c2410c", "#7c3aed", "#15803d", "#be123c", "#0891b2", "#a16207",
    "#4338ca", "#db2777", "#047857", "#b45309", "#0369a1", "#9333ea", "#4d7c0f", "#dc2626",
    "#0e7490", "#6d28d9", "#ca8a04", "#1d4ed8", "#9f1239", "#166534",
  ];
  const labelsPayload = JSON.stringify({
    fitAll: t("Fit all"),
    close: t("Close"),
    search: t("Search route, bus, vehicle"),
    all: t("All"),
    long: t("Long"),
    underLimit: t("Under limit"),
    highLoad: t("High load"),
    manyStops: t("Many stops"),
    showing: t("Showing {shown} of {total} routes"),
    bus: t("Bus"),
    riders: t("riders"),
    stops: t("stops"),
    stopSequence: t("Stop sequence"),
    schoolStart: t("School / Start"),
    stop: t("Stop"),
    unknownAddress: t("Unknown address"),
    timeWindow: t("TIME WINDOW"),
    unverified: t("UNVERIFIED"),
    capacity: t("CAPACITY"),
    highLoadBadge: t("HIGH LOAD"),
    longBadge: t("LONG"),
    frozenRoute: t("Frozen route"),
    mergedRoute: t("Merged route"),
    retainedRoute: t("Retained route"),
    newOptimizedRoute: t("New optimized route"),
    min: t("min"),
    unavailable: t("Not available"),
  }).replace(/</g, "\\u003c");
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${htmlEscape(title)}</title>
  <link href="https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.css" rel="stylesheet" />
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; background: #0f172a; }
    .app { height: 100vh; height: 100dvh; min-height: 0; padding: 18px; background: radial-gradient(circle at top left, rgba(15,118,110,.22), transparent 32%), linear-gradient(135deg, #0f172a, #1e293b 55%, #0f172a); }
    .viewer { display: grid; grid-template-rows: auto minmax(0, 1fr); height: 100%; min-height: 0; overflow: hidden; border: 1px solid rgba(255,255,255,.55); border-radius: 14px; background: rgba(255,255,255,.72); box-shadow: 0 28px 70px rgba(15,23,42,.38); backdrop-filter: blur(18px); }
    .toolbar { min-height: 58px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,.55); background: rgba(255,255,255,.72); backdrop-filter: blur(18px); }
    .title { min-width: 0; max-width: 100%; }
    .title h1 { margin: 0; font-size: 15px; line-height: 1.25; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .title p { margin: 3px 0 0; color: #64748b; font-size: 12px; }
    .toolbar-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    button { font: inherit; }
    .button { height: 36px; border-radius: 8px; border: 1px solid #cbd5e1; background: rgba(255,255,255,.78); padding: 0 12px; font-size: 14px; font-weight: 650; color: #334155; cursor: pointer; backdrop-filter: blur(12px); }
    .button:hover { background: white; }
    .button.close { border-color: #fca5a5; background: rgba(254,242,242,.92); color: #b91c1c; }
    .body { position: relative; min-height: 0; }
    #map { position: absolute; inset: 0; }
    .sidebar { position: absolute; z-index: 3; inset: 12px auto 12px 12px; width: 360px; display: flex; min-height: 0; flex-direction: column; overflow: hidden; border: 1px solid rgba(255,255,255,.48); border-radius: 14px; background: rgba(255,255,255,.30); box-shadow: 0 24px 55px rgba(15,23,42,.24); backdrop-filter: blur(26px); }
    .sidebar-head { padding: 14px; border-bottom: 1px solid rgba(255,255,255,.42); background: rgba(255,255,255,.18); backdrop-filter: blur(22px); }
    .sidebar-title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .sidebar h2 { margin: 0; font-size: 18px; }
    .summary { margin-top: 6px; color: #64748b; font-size: 13px; }
    .fit { height: 34px; border: 1px solid #0f766e; color: #0f766e; background: rgba(255,255,255,.72); }
    .search { width: 100%; height: 40px; margin-top: 14px; border: 1px solid rgba(255,255,255,.48); border-radius: 9px; padding: 0 12px; background: rgba(255,255,255,.42); color: #111827; outline: none; font-size: 14px; backdrop-filter: blur(12px); }
    .chips { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
    .chip { height: 30px; border-radius: 8px; border: 1px solid rgba(255,255,255,.45); background: rgba(255,255,255,.42); color: #64748b; padding: 0 10px; font-size: 12px; font-weight: 700; cursor: pointer; }
    .chip.active { border-color: #0f766e; background: #0f766e; color: white; }
    .showing { margin-top: 10px; color: #64748b; font-size: 12px; }
    .routes { min-height: 0; flex: 1; overflow: auto; padding: 9px; display: flex; flex-direction: column; gap: 8px; }
    .route-card { flex: 0 0 auto; overflow: hidden; border: 1px solid rgba(255,255,255,.38); border-radius: 12px; background: rgba(255,255,255,.28); box-shadow: 0 4px 16px rgba(15,23,42,.10); backdrop-filter: blur(20px); }
    .route-card.active { background: rgba(255,255,255,.58); }
    .route-card.frozen { border-color: #a5b4fc; background: rgba(238,242,255,.86); }
    .route-card.frozen.active { background: rgba(224,231,255,.94); }
    .route-card.frozen .route-title { color: #3730a3; }
    .route-main { width: 100%; min-height: 76px; display: flex; gap: 12px; align-items: flex-start; border: 0; border-left: 3px solid transparent; background: transparent; padding: 13px 12px; text-align: left; cursor: pointer; color: #111827; }
    .dot { width: 12px; height: 12px; margin-top: 5px; flex: none; border-radius: 999px; box-shadow: 0 0 0 2px rgba(255,255,255,.9); }
    .route-text { display: block; min-width: 0; flex: 1; }
    .route-title { display: flex; align-items: center; gap: 8px; font-weight: 800; font-size: 15px; line-height: 1.3; }
    .route-title span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badge { flex: 0 0 auto; border: 1px solid #bae6fd; background: #eff6ff; color: #0369a1; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 800; text-transform: uppercase; }
    .badge.capacity { border-color: #fecdd3; background: #fff1f2; color: #be123c; }
    .badge.high { border-color: #fde68a; background: #fffbeb; color: #b45309; }
    .route-meta { display: block; margin-top: 7px; color: #64748b; font-size: 13px; line-height: 1.45; }
    .chevron { width: 26px; height: 26px; margin-top: 2px; display: flex; align-items: center; justify-content: center; border-radius: 999px; background: rgba(255,255,255,.48); color: #475569; flex: none; }
    .stops { max-height: 300px; overflow: auto; border-top: 1px solid rgba(255,255,255,.32); background: rgba(255,255,255,.18); padding: 9px; }
    .stops-label { margin: 0 0 6px; color: #64748b; font-size: 11px; font-weight: 800; text-transform: uppercase; }
    .stop-row { width: 100%; display: grid; grid-template-columns: 28px minmax(0,1fr); gap: 8px; border: 0; border-radius: 8px; background: transparent; padding: 8px; text-align: left; cursor: pointer; }
    .stop-row:hover { background: rgba(255,255,255,.46); }
    .stop-row.active { background: #0f766e; color: white; }
    .stop-address { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 650; }
    .stop-meta { margin-top: 3px; color: #64748b; font-size: 12px; }
    .stop-row.active .stop-meta { color: rgba(255,255,255,.82); }
    .maplibregl-popup-content { border-radius: 10px; box-shadow: 0 14px 40px rgba(15,23,42,.25); }
    .popup { max-width: 260px; font-size: 12px; }
    .popup strong { display: block; margin-bottom: 4px; }
    @media (max-width: 760px) { .app { padding: 8px; } .toolbar { align-items: flex-start; flex-direction: column; } .sidebar { inset: 10px; width: auto; max-height: 45%; } }
  </style>
</head>
<body>
  <div class="app">
    <div class="viewer">
      <div class="toolbar">
        <div class="title">
          <h1>${htmlEscape(title)}</h1>
          <p>${htmlEscape(data.scenario_name)} · ${htmlEscape(mapSummary)}</p>
        </div>
        <div class="toolbar-actions">
          <button class="button" type="button" onclick="fitAll()">${htmlEscape(t("Fit all"))}</button>
          <button class="button close" type="button" onclick="window.close()">${htmlEscape(t("Close"))}</button>
        </div>
      </div>
      <div class="body">
        <div id="map"></div>
        <aside class="sidebar">
          <div class="sidebar-head">
            <div class="sidebar-title-row">
              <div><h2>${htmlEscape(data.scenario_name)}</h2><div class="summary">${htmlEscape(mapSummary)}</div></div>
              <button class="button fit" type="button" onclick="fitAll()">${htmlEscape(t("Fit all"))}</button>
            </div>
            <input id="search" class="search" placeholder="${htmlEscape(t("Search route, bus, vehicle"))}" />
            <div class="chips"><button class="chip active" data-filter="all">${htmlEscape(t("All"))}</button><button class="chip" data-filter="long">${htmlEscape(t("Long"))}</button><button class="chip" data-filter="high">${htmlEscape(t("High load"))}</button><button class="chip" data-filter="many">${htmlEscape(t("Many stops"))}</button></div>
            <div id="showing" class="showing"></div>
          </div>
          <div id="routes" class="routes"></div>
        </aside>
      </div>
    </div>
  </div>
  <script src="https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.js"></script>
  <script>window.BRP_MAP_DATA = ${payload};</script>
  <script>
    const data = window.BRP_MAP_DATA;
    const labels = ${labelsPayload};
    const colors = ${JSON.stringify(routeColors)};
    const bounds = normalizeBounds(data.bounds);
    let selectedRouteId = "";
    let selectedStopId = "";
    let hoverPopup = null;
    let filter = "all";
    if (data.duration_limit_s) {
      const button = document.createElement("button");
      button.className = "chip"; button.dataset.filter = "under";
      button.textContent = labels.underLimit + " (< " + data.duration_limit_s / 60 + " " + labels.min + ")";
      document.querySelector(".chips").appendChild(button);
    }
    let search = "";
    const routesById = new Map(data.routes.map(route => [route.id, route]));
    const stopsByRouteId = new Map();
    for (const stop of data.stops) { const list = stopsByRouteId.get(stop.route_id) || []; list.push(stop); stopsByRouteId.set(stop.route_id, list); }
    for (const list of stopsByRouteId.values()) list.sort((a,b) => a.order - b.order);
    const routeConnectors = Array.isArray(data.route_connectors) ? data.route_connectors : [];
    const longThreshold = data.duration_limit_s || percentile(data.routes.map(route => route.duration_s), 0.75);
    const map = new maplibregl.Map({ container: "map", style: { version: 8, sources: { osm: { type: "raster", tiles: ${JSON.stringify(standaloneTileUrls)}, tileSize: 256, attribution: "OpenStreetMap contributors" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] }, center: bounds ? [(bounds.min_lng + bounds.max_lng) / 2, (bounds.min_lat + bounds.max_lat) / 2] : [121.4737,31.2304], zoom: 11 });
    map.addControl(new maplibregl.NavigationControl(), "bottom-right");
    map.on("load", () => { addLayers(); fitAll(); renderRoutes(); });
    document.getElementById("search").addEventListener("input", event => { search = event.target.value.toLowerCase().trim(); renderRoutes(); });
    document.querySelectorAll(".chip").forEach(button => button.addEventListener("click", () => { filter = button.dataset.filter; document.querySelectorAll(".chip").forEach(item => item.classList.toggle("active", item === button)); renderRoutes(); }));
    renderRoutes();
    function addLayers() {
      map.addSource("route-connectors", { type: "geojson", data: connectorGeojson(false) });
      map.addLayer({ id: "route-connectors-line", type: "line", source: "route-connectors", paint: { "line-color": ["get","color"], "line-width": 2, "line-opacity": .58, "line-dasharray": [1.5,1.5] } });
      map.addSource("routes", { type: "geojson", data: routeGeojson(false) });
      map.addLayer({ id: "route-casing", type: "line", source: "routes", paint: { "line-color": data.duration_limit_s ? ["case", ["get","under_limit"], "#a7f3d0", "#ffffff"] : "#ffffff", "line-width": 7, "line-opacity": .72 } });
      map.addLayer({ id: "route-lines", type: "line", source: "routes", paint: { "line-color": ["get","color"], "line-width": 4, "line-opacity": .78 } });
      map.addSource("selected-route-connectors", { type: "geojson", data: selectedConnectorGeojson() });
      map.addLayer({ id: "selected-route-connectors-line", type: "line", source: "selected-route-connectors", paint: { "line-color": ["get","color"], "line-width": 3, "line-opacity": .86, "line-dasharray": [1.5,1.5] } });
      map.addSource("selected-route", { type: "geojson", data: selectedRouteGeojson() });
      map.addLayer({ id: "selected-route-casing", type: "line", source: "selected-route", paint: { "line-color": "#ffffff", "line-width": 13, "line-opacity": .98 } });
      map.addLayer({ id: "selected-route-line", type: "line", source: "selected-route", paint: { "line-color": ["get","color"], "line-width": 8, "line-opacity": .98 } });
      map.addSource("stops", { type: "geojson", data: stopGeojson() });
      map.addLayer({ id: "stops-circle", type: "circle", source: "stops", paint: { "circle-color": ["get","color"], "circle-radius": ["case",["get","is_depot"],6,4], "circle-opacity": .72, "circle-stroke-color": "#111827", "circle-stroke-width": 1.5 } });
      map.on("click", "route-lines", event => { const id = event.features && event.features[0] && event.features[0].properties.route_id; if (id) selectRoute(String(id)); });
      map.on("click", "selected-route-line", event => { const id = event.features && event.features[0] && event.features[0].properties.route_id; if (id) selectRoute(String(id)); });
      map.on("click", "stops-circle", event => { const id = event.features && event.features[0] && event.features[0].properties.stop_id; if (id) selectStop(String(id)); });
      map.on("mousemove", "route-lines", showRouteHover);
      map.on("mousemove", "selected-route-line", showRouteHover);
      map.on("mousemove", "stops-circle", showStopHover);
      map.on("mouseenter", "route-lines", () => map.getCanvas().style.cursor = "pointer");
      map.on("mouseenter", "selected-route-line", () => map.getCanvas().style.cursor = "pointer");
      map.on("mouseenter", "stops-circle", () => map.getCanvas().style.cursor = "pointer");
      map.on("mouseleave", "route-lines", clearHover);
      map.on("mouseleave", "selected-route-line", clearHover);
      map.on("mouseleave", "stops-circle", clearHover);
    }
    function showRouteHover(event) { const feature = event.features && event.features[0]; if (!feature) return; const route = routesById.get(String(feature.properties.route_id)); if (!route) return; showHover(event.lngLat, '<div class="popup"><strong>' + esc(route.id || (labels.bus + ' ' + (route.vehicle_id || route.route_index + 1))) + '</strong><div>' + fmt(route.load) + ' ' + labels.riders + ' · ' + fmt(route.stop_count) + ' ' + labels.stops + '</div><div>' + duration(route.duration_s) + ' · ' + distance(route.distance_m) + '</div>' + (route.bus_type_name ? '<div>' + esc(route.bus_type_name) + '</div>' : '') + '</div>'); }
    function showStopHover(event) { const feature = event.features && event.features[0]; if (!feature) return; const stop = data.stops.find(item => item.id === String(feature.properties.stop_id)); if (!stop) return; showHover(event.lngLat, '<div class="popup"><strong>' + esc(stop.is_depot ? labels.schoolStart : labels.stop + ' ' + stop.order) + '</strong><div>' + esc(stop.address || stop.requested_address || labels.unknownAddress) + '</div><div>' + esc(stop.route_id) + ' · ' + fmt(stop.passenger_count) + ' ' + labels.riders + '</div><div>' + stopTiming(stop) + ' · ' + distance(stop.cumulative_distance_m) + '</div></div>'); }
    function showHover(lngLat, html) { if (!hoverPopup) hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 }); hoverPopup.setLngLat(lngLat).setHTML(html).addTo(map); }
    function clearHover() { map.getCanvas().style.cursor = "grab"; if (hoverPopup) { hoverPopup.remove(); hoverPopup = null; } }
    function routeGeometry(route) { return route && Array.isArray(route.display_geometry) && route.display_geometry.length >= 2 ? route.display_geometry : (route && Array.isArray(route.geometry) ? route.geometry : []); }
    function routeLineGeometry(route) { return route.geometry_segments && route.geometry_segments.length ? { type: "MultiLineString", coordinates: route.geometry_segments } : { type: "LineString", coordinates: routeGeometry(route) }; }
    function connectorGeojson(dimmed) { return { type: "FeatureCollection", features: routeConnectors.filter(connector => connector.geometry && connector.geometry.length >= 2 && (!dimmed || connector.route_id !== selectedRouteId)).map(connector => ({ type: "Feature", properties: { connector_id: connector.id, route_id: connector.route_id, color: color(connector.route_index || 0) }, geometry: { type: "LineString", coordinates: connector.geometry } })) }; }
    function selectedConnectorGeojson() { return { type: "FeatureCollection", features: routeConnectors.filter(connector => connector.route_id === selectedRouteId && connector.geometry && connector.geometry.length >= 2).map(connector => ({ type: "Feature", properties: { connector_id: connector.id, route_id: connector.route_id, color: color(connector.route_index || 0) }, geometry: { type: "LineString", coordinates: connector.geometry } })) }; }
    function routeGeojson(dimmed) { return { type: "FeatureCollection", features: data.routes.filter(route => routeGeometry(route).length >= 2 && (!dimmed || route.id !== selectedRouteId)).map(route => ({ type: "Feature", properties: { route_id: route.id, color: color(route.route_index), under_limit: Number.isFinite(route.duration_s) && route.duration_s < (data.duration_limit_s || 0) }, geometry: routeLineGeometry(route) })) }; }
    function selectedRouteGeojson() { const route = routesById.get(selectedRouteId); return { type: "FeatureCollection", features: route && routeGeometry(route).length >= 2 ? [{ type: "Feature", properties: { route_id: route.id, color: color(route.route_index), under_limit: Number.isFinite(route.duration_s) && route.duration_s < (data.duration_limit_s || 0) }, geometry: routeLineGeometry(route) }] : [] }; }
    function stopGeojson() { const stops = selectedRouteId ? data.stops.filter(stop => stop.route_id === selectedRouteId) : data.stops; return { type: "FeatureCollection", features: stops.map(stop => ({ type: "Feature", properties: { stop_id: stop.id, route_id: stop.route_id, color: color(stop.route_index), is_depot: stop.is_depot }, geometry: { type: "Point", coordinates: [stop.lng, stop.lat] } })) }; }
    function updateMapSources() { if (!map.getSource("routes")) return; map.getSource("route-connectors").setData(connectorGeojson(Boolean(selectedRouteId))); map.getSource("routes").setData(routeGeojson(Boolean(selectedRouteId))); map.getSource("selected-route-connectors").setData(selectedConnectorGeojson()); map.getSource("selected-route").setData(selectedRouteGeojson()); map.getSource("stops").setData(stopGeojson()); }
    function renderRoutes() { const routes = data.routes.filter(route => { const hay = [route.id, route.source_route_id, route.bus_type_name, route.vehicle_id, route.route_index + 1].join(" ").toLowerCase(); if (search && !hay.includes(search)) return false; if (filter === "under") return Number.isFinite(route.duration_s) && route.duration_s < (data.duration_limit_s || 0); if (filter === "long") return Number.isFinite(route.duration_s) && route.duration_s >= longThreshold; if (filter === "high") return loadRatio(route) >= .85; if (filter === "many") return route.stop_count >= 8; return true; }); document.getElementById("showing").textContent = labels.showing.replace("{shown}", routes.length).replace("{total}", data.routes.length); document.getElementById("routes").innerHTML = routes.map(routeCard).join(""); document.querySelectorAll("[data-route]").forEach(btn => btn.addEventListener("click", () => selectRoute(btn.dataset.route))); document.querySelectorAll("[data-stop]").forEach(btn => btn.addEventListener("click", event => { event.stopPropagation(); selectStop(btn.dataset.stop); })); }
    function routeCard(route) { const active = route.id === selectedRouteId; const frozen = route.exception_role === 'frozen_current'; const stops = stopsByRouteId.get(route.id) || []; return '<div class="route-card ' + (active ? 'active ' : '') + (frozen ? 'frozen' : '') + '"><button class="route-main" data-route="' + escAttr(route.id) + '" style="border-left-color:' + color(route.route_index) + '"><span class="dot" style="background:' + color(route.route_index) + '"></span><span class="route-text"><span class="route-title"><span>' + esc(route.id || (labels.bus + ' ' + (route.vehicle_id || route.route_index + 1))) + '</span>' + lineageBadge(route) + statusBadge(route) + '</span><span class="route-meta">' + fmt(route.load) + ' ' + labels.riders + ' · ' + fmt(route.stop_count) + ' ' + labels.stops + ' · ' + duration(route.duration_s) + '<br />' + distance(route.distance_m) + (route.bus_type_name ? ' · ' + esc(route.bus_type_name) : '') + '</span></span><span class="chevron">' + (active ? '⌃' : '⌄') + '</span></button>' + (active ? '<div class="stops"><p class="stops-label">' + esc(labels.stopSequence) + '</p>' + stops.map(stop => '<button class="stop-row ' + (stop.id === selectedStopId ? 'active ' : '') + '" data-stop="' + escAttr(stop.id) + '"><span><strong>' + (stop.is_depot ? 'S' : stop.order) + '</strong></span><span><span class="stop-address">' + esc(stop.address || stop.requested_address || labels.unknownAddress) + '</span><span class="stop-meta">' + fmt(stop.passenger_count) + ' ' + labels.riders + ' · ' + stopTiming(stop) + '</span></span></button>').join('') + '</div>' : '') + '</div>'; }
    function selectRoute(routeId) { if (selectedRouteId === routeId) { selectedRouteId = ''; selectedStopId = ''; updateMapSources(); renderRoutes(); fitAll(); return; } selectedRouteId = routeId; selectedStopId = ''; updateMapSources(); renderRoutes(); fitRoute(routesById.get(routeId)); }
    function selectStop(stopId) { const stop = data.stops.find(item => item.id === stopId); if (!stop) return; selectedStopId = stopId; selectedRouteId = stop.route_id; updateMapSources(); renderRoutes(); map.flyTo({ center: [stop.lng, stop.lat], zoom: Math.max(map.getZoom(), 14), duration: 450 }); new maplibregl.Popup().setLngLat([stop.lng, stop.lat]).setHTML('<div class="popup"><strong>' + esc(stop.is_depot ? labels.schoolStart : labels.stop + ' ' + stop.order) + '</strong><div>' + esc(stop.address || stop.requested_address || labels.unknownAddress) + '</div><div>' + esc(stop.route_id) + ' · ' + fmt(stop.passenger_count) + ' ' + labels.riders + '</div><div>' + stopTiming(stop) + ' · ' + distance(stop.cumulative_distance_m) + '</div></div>').addTo(map); }
    function fitAll() { if (!bounds) return; map.fitBounds([[bounds.min_lng, bounds.min_lat], [bounds.max_lng, bounds.max_lat]], { padding: 72, duration: 500 }); }
    function normalizeBounds(value) {
      if (!value) return null;
      const normalized = {
        min_lng: Number(value.min_lng ?? value.west),
        min_lat: Number(value.min_lat ?? value.south),
        max_lng: Number(value.max_lng ?? value.east),
        max_lat: Number(value.max_lat ?? value.north),
      };
      return [normalized.min_lng, normalized.min_lat, normalized.max_lng, normalized.max_lat].every(Number.isFinite) ? normalized : null;
    }
    function fitRoute(route) { const geometry = routeGeometry(route); if (!route || geometry.length < 2) return; const connectorPoints = routeConnectors.filter(connector => connector.route_id === route.id).flatMap(connector => connector.geometry || []); const points = geometry.concat(connectorPoints); const lngs = points.map(p => p[0]).filter(Number.isFinite); const lats = points.map(p => p[1]).filter(Number.isFinite); map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 96, duration: 500 }); }
    function statusBadge(route) { const gate = route.final_route_traffic_gate || route.am_arrival_gate || {}; const uncertain = ['legacy', 'needs_review', 'unavailable'].includes(route.evidence_status); const status = uncertain ? labels.unverified : gate.status === 'failed' ? labels.timeWindow : gate.status === 'unavailable' ? labels.unverified : route.load / (route.bus_capacity || 0) >= 1 ? labels.capacity : route.load / (route.bus_capacity || 1) >= .85 ? labels.highLoadBadge : route.duration_s >= (data.duration_limit_s || 3600) ? labels.longBadge : ''; if (!status) return ''; return '<span title="' + escAttr(route.display_geometry_message || '') + '" class="badge ' + (status === labels.capacity || status === labels.timeWindow ? 'capacity' : status === labels.highLoadBadge || status === labels.unverified ? 'high' : '') + '">' + esc(status) + '</span>'; }
    function lineageBadge(route) { const type = route.route_change_type || (route.exception_role === 'frozen_current' ? 'frozen' : route.exception_role === 'optimized_remainder' ? 'merged' : route.exception_role === 'protected_unchanged' ? 'retained' : ''); const label = type === 'frozen' ? labels.frozenRoute : type === 'merged' ? labels.mergedRoute : type === 'retained' ? labels.retainedRoute : type === 'new' ? labels.newOptimizedRoute : ''; return label ? '<span class="badge">' + esc(label) + '</span>' : ''; }
    function stopTiming(stop) { return (stop.scheduled_time_label ? esc(stop.scheduled_time_label) + ' · ' : '') + duration(stop.cumulative_duration_s); }
    function color(index) { return colors[index % colors.length]; }
    function loadRatio(route) { return route.bus_capacity ? route.load / route.bus_capacity : 0; }
    function percentile(values, ratio) { const sorted = values.filter(Number.isFinite).sort((a,b)=>a-b); return sorted.length ? sorted[Math.max(0, Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio)))] : 0; }
    function fmt(value) { return new Intl.NumberFormat().format(Number(value || 0)); }
    function duration(seconds) { if (seconds == null || !Number.isFinite(Number(seconds))) return labels.unavailable; const m = Math.round(Number(seconds) / 60); return m >= 60 ? Math.floor(m / 60) + 'h ' + (m % 60) + 'm' : m + ' ' + labels.min; }
    function distance(meters) { if (meters == null || !Number.isFinite(Number(meters))) return labels.unavailable; const km = Number(meters) / 1000; return (Math.round(km * 10) / 10) + ' km'; }
    function esc(value) { return String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    function escAttr(value) { return esc(value).split(String.fromCharCode(96)).join("&#96;"); }
  </script>
</body>
</html>`;
}

