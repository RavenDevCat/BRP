import { useEffect, useMemo, useRef, useState } from "react";
import MapView, {
  Layer,
  NavigationControl,
  Popup,
  Source,
  type MapLayerMouseEvent,
  type MapRef,
} from "react-map-gl/maplibre";
import type { StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Button } from "@/components/ui/button";
import type { JobMapData, JobMapRoute, JobMapStop } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDistanceKmFromMeters, formatDurationMinFromSeconds, formatNumber } from "@/lib/format";

const ROUTE_COLORS = [
  "#0f766e",
  "#2563eb",
  "#c2410c",
  "#7c3aed",
  "#15803d",
  "#be123c",
  "#0891b2",
  "#a16207",
  "#4338ca",
  "#db2777",
  "#047857",
  "#b45309",
  "#0369a1",
  "#9333ea",
  "#4d7c0f",
  "#dc2626",
  "#0e7490",
  "#6d28d9",
  "#ca8a04",
  "#1d4ed8",
  "#9f1239",
  "#166534",
];

const MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.de/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "OpenStreetMap contributors",
    },
  },
  layers: [
    {
      id: "osm",
      type: "raster",
      source: "osm",
    },
  ],
};

type FeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    id?: string;
    properties: Record<string, string | number | boolean | null>;
    geometry: {
      type: "LineString" | "Point";
      coordinates: number[] | number[][];
    };
  }>;
};

type HoverInfo = {
  type: "route";
  routeId: string;
  label: string;
  longitude: number;
  latitude: number;
} | {
  type: "stop";
  stopId: string;
  routeId: string;
  label: string;
  address: string;
  passengerCount: number;
  cumulativeDurationSeconds: number;
  longitude: number;
  latitude: number;
} | null;

type RouteFilter = "all" | "long" | "high_load" | "many_stops";

const routeFilterOptions: Array<{ key: RouteFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "long", label: "Long" },
  { key: "high_load", label: "High load" },
  { key: "many_stops", label: "Many stops" },
];

const STOP_LAYER_IDS = ["stops-hit-area", "selected-stops-circle", "selected-stops-label", "stops-circle", "stops-label"];
const ROUTE_LAYER_IDS = ["selected-route-line", "route-lines"];
const INTERACTIVE_LAYER_IDS = [
  "stops-hit-area",
  "selected-stops-circle",
  "selected-stops-label",
  "stops-circle",
  "stops-label",
  "selected-route-line",
  "route-lines",
];

export function InteractiveRouteMap({ data }: { data: JobMapData }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapRef | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string>("");
  const [selectedStop, setSelectedStop] = useState<JobMapStop | null>(null);
  const [hoverInfo, setHoverInfo] = useState<HoverInfo>(null);
  const [routeSearch, setRouteSearch] = useState("");
  const [routeFilter, setRouteFilter] = useState<RouteFilter>("all");

  const routesById = useMemo(() => new Map(data.routes.map((route) => [route.id, route])), [data.routes]);
  const stopsById = useMemo(() => new Map(data.stops.map((stop) => [stop.id, stop])), [data.stops]);
  const selectedRoute = selectedRouteId ? routesById.get(selectedRouteId) || null : null;
  const longRouteThreshold = useMemo(() => percentile(data.routes.map((route) => route.duration_s), 0.75), [data.routes]);
  const visibleRoutes = useMemo(() => {
    const normalizedSearch = routeSearch.trim().toLowerCase();
    return data.routes.filter((route) => {
      if (normalizedSearch) {
        const haystack = [route.id, route.bus_type_name, String(route.vehicle_id ?? ""), String(route.route_index + 1)]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(normalizedSearch)) {
          return false;
        }
      }
      if (routeFilter === "long") {
        return route.duration_s >= longRouteThreshold;
      }
      if (routeFilter === "high_load") {
        return routeLoadRatio(route) >= 0.85;
      }
      if (routeFilter === "many_stops") {
        return route.stop_count >= 8;
      }
      return true;
    });
  }, [data.routes, longRouteThreshold, routeFilter, routeSearch]);
  const stopsByRouteId = useMemo(() => {
    const grouped = new Map<string, JobMapStop[]>();
    for (const stop of data.stops) {
      const stops = grouped.get(stop.route_id) || [];
      stops.push(stop);
      grouped.set(stop.route_id, stops);
    }
    for (const stops of grouped.values()) {
      stops.sort((a, b) => a.order - b.order);
    }
    return grouped;
  }, [data.stops]);

  const routeFeatures = useMemo<FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: data.routes
        .filter((route) => route.geometry.length >= 2)
        .map((route) => ({
          type: "Feature",
          id: route.id,
          properties: {
            route_id: route.id,
            route_index: route.route_index,
            label: routeLabel(route),
            color: routeColor(route.route_index),
            load: route.load,
            stop_count: route.stop_count,
            duration_s: route.duration_s,
            distance_m: route.distance_m,
          },
          geometry: {
            type: "LineString",
            coordinates: route.geometry,
          },
        })),
    }),
    [data.routes],
  );

  const selectedRouteFeatures = useMemo<FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: routeFeatures.features.filter((feature) => feature.properties.route_id === selectedRouteId),
    }),
    [routeFeatures, selectedRouteId],
  );

  const stopFeatures = useMemo<FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: data.stops.map((stop) => ({
        type: "Feature",
        id: stop.id,
        properties: {
          stop_id: stop.id,
          route_id: stop.route_id,
          route_index: stop.route_index,
          label: stop.is_depot ? "S" : String(stop.order),
          address: stop.address,
          color: routeColor(stop.route_index),
          is_depot: stop.is_depot,
          passenger_count: stop.passenger_count,
        },
        geometry: {
          type: "Point",
          coordinates: [stop.lng, stop.lat],
        },
      })),
    }),
    [data.stops],
  );

  const selectedStopFeatures = useMemo<FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: stopFeatures.features.filter((feature) => feature.properties.route_id === selectedRouteId),
    }),
    [selectedRouteId, stopFeatures],
  );

  const privateLinkFeatures = useMemo<FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: data.private_links
        .filter((link) => link.geometry.length >= 2)
        .map((link) => ({
          type: "Feature",
          id: link.id,
          properties: {
            link_id: link.id,
            access_type: link.access_type,
            address: link.address,
            pickup_address: link.pickup_address,
            pickup_route_id: link.pickup_route_id,
          },
          geometry: {
            type: "LineString",
            coordinates: link.geometry,
          },
        })),
    }),
    [data.private_links],
  );

  const initialViewState = useMemo(() => {
    const bounds = data.bounds;
    if (!bounds) {
      return { longitude: 121.4737, latitude: 31.2304, zoom: 11 };
    }
    return {
      longitude: (bounds.min_lng + bounds.max_lng) / 2,
      latitude: (bounds.min_lat + bounds.max_lat) / 2,
      zoom: 11,
    };
  }, [data.bounds]);

  useEffect(() => {
    window.setTimeout(() => fitAll(mapRef.current, data), 100);
  }, [data]);

  const focusRoute = (route: JobMapRoute) => {
    setSelectedRouteId(route.id);
    setSelectedStop(null);
    containerRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    window.setTimeout(() => mapRef.current?.resize(), 120);
    fitRoute(mapRef.current, route);
  };

  const focusStop = (stop: JobMapStop) => {
    setSelectedRouteId(stop.route_id);
    setSelectedStop(stop);
    mapRef.current?.flyTo({ center: [stop.lng, stop.lat], zoom: Math.max(mapRef.current.getZoom(), 14), duration: 450 });
  };

  const clearFocus = () => {
    setSelectedRouteId("");
    setSelectedStop(null);
    window.setTimeout(() => mapRef.current?.resize(), 120);
    fitAll(mapRef.current, data);
  };

  const handleMapClick = (event: MapLayerMouseEvent) => {
    const feature =
      event.features?.find((item) => STOP_LAYER_IDS.includes(item.layer.id)) ||
      event.features?.find((item) => ROUTE_LAYER_IDS.includes(item.layer.id));
    const stopId = String(feature?.properties?.stop_id || "");
    const routeId = String(feature?.properties?.route_id || "");
    if (stopId) {
      setSelectedStop(stopsById.get(stopId) || null);
      if (routeId) {
        setSelectedRouteId(routeId);
      }
      return;
    }
    if (routeId) {
      const route = routesById.get(routeId);
      if (route) {
        focusRoute(route);
      }
    }
  };

  const handleMouseMove = (event: MapLayerMouseEvent) => {
    const stopFeature = event.features?.find((item) => STOP_LAYER_IDS.includes(item.layer.id));
    const stopId = String(stopFeature?.properties?.stop_id || "");
    if (stopId) {
      const stop = stopsById.get(stopId);
      if (stop) {
        setHoverInfo({
          type: "stop",
          stopId,
          routeId: stop.route_id,
          label: stop.is_depot ? "School / Start" : `Stop ${stop.order}`,
          address: stop.address || stop.requested_address || "Unknown address",
          passengerCount: stop.passenger_count,
          cumulativeDurationSeconds: stop.cumulative_duration_s,
          longitude: event.lngLat.lng,
          latitude: event.lngLat.lat,
        });
        return;
      }
    }

    const feature = event.features?.find((item) => ROUTE_LAYER_IDS.includes(item.layer.id));
    const routeId = String(feature?.properties?.route_id || "");
    if (!routeId) {
      setHoverInfo(null);
      return;
    }
    setHoverInfo({
      type: "route",
      routeId,
      label: String(feature?.properties?.label || routeId),
      longitude: event.lngLat.lng,
      latitude: event.lngLat.lat,
    });
  };

  return (
    <div
      ref={containerRef}
      className="grid min-h-[560px] overflow-hidden rounded-md border border-border bg-surface lg:grid-cols-[320px_minmax(0,1fr)]"
      style={{ height: "clamp(560px, calc(100vh - 220px), 760px)" }}
    >
      <aside className="flex min-h-0 flex-col border-b border-border bg-surface lg:border-b-0 lg:border-r">
        <div className="border-b border-border p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold">{data.scenario_name}</h3>
              <div className="mt-1 text-xs text-muted-foreground">
                {formatNumber(data.summary.route_count)} routes · {formatNumber(data.summary.stop_count)} stops ·{" "}
                {formatNumber(data.summary.passenger_count)} riders
              </div>
            </div>
            <Button className="h-8 px-3 text-xs" variant="secondary" onClick={clearFocus}>
              Fit all
            </Button>
          </div>
          <div className="mt-3 space-y-2">
            <input
              className="h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none transition placeholder:text-muted-foreground focus:border-primary"
              value={routeSearch}
              onChange={(event) => setRouteSearch(event.target.value)}
              placeholder="Search route, bus, vehicle"
            />
            <div className="flex flex-wrap gap-1.5">
              {routeFilterOptions.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  className={cn(
                    "h-7 rounded-md border px-2 text-xs font-medium transition",
                    routeFilter === option.key
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-surface text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                  onClick={() => setRouteFilter(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <div className="text-[11px] text-muted-foreground">
              Showing {formatNumber(visibleRoutes.length)} of {formatNumber(data.routes.length)} routes
            </div>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {visibleRoutes.length ? null : (
            <div className="p-4 text-sm text-muted-foreground">No routes match the current filter.</div>
          )}
          {visibleRoutes.map((route) => {
            const active = selectedRouteId === route.id;
            const routeStops = stopsByRouteId.get(route.id) || [];
            return (
              <div key={route.id} className="border-b border-border">
                <button
                  type="button"
                  className={cn(
                    "flex w-full items-start gap-3 px-3 py-3 text-left transition",
                    active ? "bg-primary/10" : "hover:bg-muted",
                  )}
                  onClick={() => focusRoute(route)}
                >
                  <span
                    className="mt-1 h-3 w-3 shrink-0 rounded-full ring-2 ring-white"
                    style={{ backgroundColor: routeColor(route.route_index) }}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-foreground">{routeLabel(route)}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {formatNumber(route.load)} riders · {formatNumber(route.stop_count)} stops ·{" "}
                      {formatDurationMinFromSeconds(route.duration_s)}
                    </span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {formatDistanceKmFromMeters(route.distance_m)}
                      {route.bus_type_name ? ` · ${route.bus_type_name}` : ""}
                    </span>
                  </span>
                </button>
                {active ? (
                  <div className="max-h-[320px] overflow-auto bg-muted/45 px-3 pb-3">
                    <div className="mb-2 pt-2 text-[11px] font-semibold uppercase text-muted-foreground">Stop sequence</div>
                    <div className="space-y-1">
                      {routeStops.map((stop) => (
                        <button
                          key={stop.id}
                          type="button"
                          className={cn(
                            "grid w-full grid-cols-[28px_minmax(0,1fr)] gap-2 rounded-md px-2 py-2 text-left text-xs transition",
                            selectedStop?.id === stop.id ? "bg-primary text-primary-foreground" : "hover:bg-surface",
                          )}
                          onClick={() => focusStop(stop)}
                        >
                          <span className="font-semibold">{stop.is_depot ? "S" : stop.order}</span>
                          <span className="min-w-0">
                            <span className="block truncate font-medium">{stop.address || stop.requested_address || "Unknown address"}</span>
                            <span className={cn("mt-0.5 block", selectedStop?.id === stop.id ? "text-primary-foreground/80" : "text-muted-foreground")}>
                              {formatNumber(stop.passenger_count)} riders · {formatDurationMinFromSeconds(stop.cumulative_duration_s)}
                            </span>
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </aside>

      <div className="relative min-h-0">
        <MapView
          ref={mapRef}
          initialViewState={initialViewState}
          mapStyle={MAP_STYLE}
          interactiveLayerIds={INTERACTIVE_LAYER_IDS}
          onClick={handleMapClick}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoverInfo(null)}
          cursor={hoverInfo ? "pointer" : "grab"}
        >
          <NavigationControl position="top-right" />
          <Source id="private-links" type="geojson" data={privateLinkFeatures}>
            <Layer
              id="private-links-line"
              type="line"
              paint={{
                "line-color": "#6b7280",
                "line-width": 2,
                "line-opacity": 0.55,
                "line-dasharray": [2, 2],
              }}
            />
          </Source>
          <Source id="routes" type="geojson" data={routeFeatures}>
            <Layer
              id="route-casing"
              type="line"
              paint={{
                "line-color": "#ffffff",
                "line-width": selectedRouteId ? 5 : 7,
                "line-opacity": selectedRouteId ? 0.18 : 0.75,
              }}
            />
            <Layer
              id="route-lines"
              type="line"
              paint={{
                "line-color": ["get", "color"],
                "line-width": selectedRouteId ? 3 : 4,
                "line-opacity": selectedRouteId ? 0.18 : 0.76,
              }}
            />
          </Source>
          <Source id="selected-route" type="geojson" data={selectedRouteFeatures}>
            <Layer
              id="selected-route-casing"
              type="line"
              paint={{
                "line-color": "#ffffff",
                "line-width": 13,
                "line-opacity": selectedRouteId ? 0.98 : 0,
              }}
            />
            <Layer
              id="selected-route-line"
              type="line"
              paint={{
                "line-color": ["get", "color"],
                "line-width": 8,
                "line-opacity": selectedRouteId ? 0.98 : 0,
              }}
            />
          </Source>
          <Source id="stops" type="geojson" data={stopFeatures}>
            <Layer
              id="stops-halo"
              type="circle"
              paint={{
                "circle-color": "#ffffff",
                "circle-radius": selectedRouteId
                  ? ["interpolate", ["linear"], ["zoom"], 10, 5, 14, 7, 16, 9]
                  : ["case", ["get", "is_depot"], 11, ["interpolate", ["linear"], ["zoom"], 10, 6, 14, 8, 16, 10]],
                "circle-opacity": selectedRouteId ? 0.18 : 0.92,
              }}
            />
            <Layer
              id="stops-circle"
              type="circle"
              paint={{
                "circle-color": ["case", ["get", "is_depot"], "#111827", ["get", "color"]],
                "circle-radius": selectedRouteId
                  ? ["interpolate", ["linear"], ["zoom"], 10, 3, 14, 4.5, 16, 6]
                  : ["case", ["get", "is_depot"], 9, ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 6, 16, 8]],
                "circle-opacity": selectedRouteId ? 0.24 : 0.9,
                "circle-stroke-color": selectedRouteId ? "#111827" : "#ffffff",
                "circle-stroke-width": selectedRouteId ? 1.5 : 2,
              }}
            />
            <Layer
              id="stops-label"
              type="symbol"
              minzoom={12.25}
              layout={{
                "text-field": ["get", "label"],
                "text-size": ["interpolate", ["linear"], ["zoom"], 12, 9, 14, 11, 16, 13],
                "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
                "text-allow-overlap": false,
                "text-ignore-placement": false,
                "text-optional": true,
              }}
              paint={{
                "text-color": "#ffffff",
                "text-halo-color": "#111827",
                "text-halo-width": 1.1,
                "text-opacity": selectedRouteId ? 0.12 : ["interpolate", ["linear"], ["zoom"], 12.25, 0, 13, 0.82, 15, 0.96],
              }}
            />
          </Source>
          <Source id="selected-stops" type="geojson" data={selectedStopFeatures}>
            <Layer
              id="selected-stops-halo"
              type="circle"
              paint={{
                "circle-color": "#ffffff",
                "circle-radius": ["case", ["get", "is_depot"], 16, ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 13, 16, 15]],
                "circle-opacity": selectedRouteId ? 0.98 : 0,
              }}
            />
            <Layer
              id="selected-stops-circle"
              type="circle"
              paint={{
                "circle-color": ["case", ["get", "is_depot"], "#111827", ["get", "color"]],
                "circle-radius": ["case", ["get", "is_depot"], 12, ["interpolate", ["linear"], ["zoom"], 10, 7, 14, 10, 16, 12]],
                "circle-opacity": selectedRouteId ? 0.98 : 0,
                "circle-stroke-color": "#111827",
                "circle-stroke-width": 2.5,
              }}
            />
            <Layer
              id="selected-stops-label"
              type="symbol"
              layout={{
                "text-field": ["get", "label"],
                "text-size": ["interpolate", ["linear"], ["zoom"], 10, 11, 14, 13, 16, 15],
                "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
                "text-allow-overlap": true,
                "text-ignore-placement": true,
              }}
              paint={{
                "text-color": "#ffffff",
                "text-halo-color": "#111827",
                "text-halo-width": 1.4,
                "text-opacity": selectedRouteId ? 1 : 0,
              }}
            />
          </Source>
          <Source id="stop-hit-areas" type="geojson" data={stopFeatures}>
            <Layer
              id="stops-hit-area"
              type="circle"
              paint={{
                "circle-color": "#ffffff",
                "circle-radius": ["case", ["get", "is_depot"], 16, ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 13, 16, 16]],
                "circle-opacity": 0.001,
              }}
            />
          </Source>
          {hoverInfo && !selectedStop ? (
            <Popup longitude={hoverInfo.longitude} latitude={hoverInfo.latitude} closeButton={false} closeOnClick={false}>
              {hoverInfo.type === "route" ? (
                <div className="text-xs font-semibold">{hoverInfo.label}</div>
              ) : (
                <div className="max-w-[240px] space-y-1 text-xs">
                  <div className="font-semibold">{hoverInfo.label}</div>
                  <div className="line-clamp-2">{hoverInfo.address}</div>
                  <div className="text-muted-foreground">
                    {hoverInfo.routeId} · {formatNumber(hoverInfo.passengerCount)} riders
                  </div>
                  <div className="text-muted-foreground">{formatDurationMinFromSeconds(hoverInfo.cumulativeDurationSeconds)}</div>
                </div>
              )}
            </Popup>
          ) : null}
          {selectedStop ? (
            <Popup longitude={selectedStop.lng} latitude={selectedStop.lat} closeButton closeOnClick={false} onClose={() => setSelectedStop(null)}>
              <div className="max-w-[260px] space-y-1 text-xs">
                <div className="font-semibold">{selectedStop.is_depot ? "School / Start" : `Stop ${selectedStop.order}`}</div>
                <div>{selectedStop.address || selectedStop.requested_address || "Unknown address"}</div>
                <div className="text-muted-foreground">
                  {selectedStop.route_id} · {formatNumber(selectedStop.passenger_count)} riders
                </div>
                <div className="text-muted-foreground">
                  {formatDurationMinFromSeconds(selectedStop.cumulative_duration_s)} ·{" "}
                  {formatDistanceKmFromMeters(selectedStop.cumulative_distance_m)}
                </div>
              </div>
            </Popup>
          ) : null}
        </MapView>
        {selectedRoute ? (
          <div className="absolute bottom-3 left-3 right-3 rounded-md border border-border bg-surface/95 p-3 shadow-lg backdrop-blur md:left-auto md:w-[360px]">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold">{routeLabel(selectedRoute)}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {formatNumber(selectedRoute.load)} riders · {formatNumber(selectedRoute.stop_count)} stops ·{" "}
                  {formatDurationMinFromSeconds(selectedRoute.duration_s)} · {formatDistanceKmFromMeters(selectedRoute.distance_m)}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  Select a stop in the left sequence to inspect its address and timing.
                </div>
              </div>
              <Button className="h-8 px-3 text-xs" variant="secondary" onClick={clearFocus}>
                Clear
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function routeColor(index: number) {
  return ROUTE_COLORS[index % ROUTE_COLORS.length];
}

function routeLabel(route: JobMapRoute) {
  return route.id || `Bus ${route.vehicle_id || route.route_index + 1}`;
}

function routeLoadRatio(route: JobMapRoute) {
  const capacity = route.comfort_capacity || route.bus_capacity || 0;
  return capacity > 0 ? route.load / capacity : 0;
}

function percentile(values: number[], ratio: number) {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!sorted.length) {
    return 0;
  }
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * ratio)));
  return sorted[index];
}

function fitAll(map: MapRef | null, data: JobMapData) {
  if (!map || !data.bounds) {
    return;
  }
  map.fitBounds(
    [
      [data.bounds.min_lng, data.bounds.min_lat],
      [data.bounds.max_lng, data.bounds.max_lat],
    ],
    { padding: 64, duration: 500 },
  );
}

function fitRoute(map: MapRef | null, route: JobMapRoute) {
  if (!map || route.geometry.length < 2) {
    return;
  }
  const lngs = route.geometry.map((item) => item[0]).filter((item) => Number.isFinite(item));
  const lats = route.geometry.map((item) => item[1]).filter((item) => Number.isFinite(item));
  if (!lngs.length || !lats.length) {
    return;
  }
  map.fitBounds(
    [
      [Math.min(...lngs), Math.min(...lats)],
      [Math.max(...lngs), Math.max(...lats)],
    ],
    { padding: 90, duration: 500 },
  );
}
