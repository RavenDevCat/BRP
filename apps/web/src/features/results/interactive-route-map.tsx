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
  routeId: string;
  label: string;
  longitude: number;
  latitude: number;
} | null;

export function InteractiveRouteMap({ data }: { data: JobMapData }) {
  const mapRef = useRef<MapRef | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string>("");
  const [selectedStop, setSelectedStop] = useState<JobMapStop | null>(null);
  const [hoverInfo, setHoverInfo] = useState<HoverInfo>(null);

  const routesById = useMemo(() => new Map(data.routes.map((route) => [route.id, route])), [data.routes]);
  const stopsById = useMemo(() => new Map(data.stops.map((stop) => [stop.id, stop])), [data.stops]);
  const selectedRoute = selectedRouteId ? routesById.get(selectedRouteId) || null : null;

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
    fitRoute(mapRef.current, route);
  };

  const clearFocus = () => {
    setSelectedRouteId("");
    setSelectedStop(null);
    fitAll(mapRef.current, data);
  };

  const handleMapClick = (event: MapLayerMouseEvent) => {
    const feature = event.features?.[0];
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
    const feature = event.features?.find((item) => item.layer.id === "route-lines");
    const routeId = String(feature?.properties?.route_id || "");
    if (!routeId) {
      setHoverInfo(null);
      return;
    }
    setHoverInfo({
      routeId,
      label: String(feature?.properties?.label || routeId),
      longitude: event.lngLat.lng,
      latitude: event.lngLat.lat,
    });
  };

  return (
    <div className="grid min-h-[720px] overflow-hidden rounded-md border border-border bg-surface lg:grid-cols-[320px_minmax(0,1fr)]">
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
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {data.routes.map((route) => {
            const active = selectedRouteId === route.id;
            return (
              <button
                key={route.id}
                type="button"
                className={cn(
                  "flex w-full items-start gap-3 border-b border-border px-3 py-3 text-left transition",
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
            );
          })}
        </div>
      </aside>

      <div className="relative min-h-[620px]">
        <MapView
          ref={mapRef}
          initialViewState={initialViewState}
          mapStyle={MAP_STYLE}
          interactiveLayerIds={["stops-circle", "stops-label", "route-lines"]}
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
                "line-width": selectedRouteId ? ["case", ["==", ["get", "route_id"], selectedRouteId], 11, 5] : 7,
                "line-opacity": selectedRouteId ? ["case", ["==", ["get", "route_id"], selectedRouteId], 0.95, 0.16] : 0.75,
              }}
            />
            <Layer
              id="route-lines"
              type="line"
              paint={{
                "line-color": ["get", "color"],
                "line-width": selectedRouteId ? ["case", ["==", ["get", "route_id"], selectedRouteId], 7, 3] : 4,
                "line-opacity": selectedRouteId ? ["case", ["==", ["get", "route_id"], selectedRouteId], 0.95, 0.22] : 0.76,
              }}
            />
          </Source>
          <Source id="stops" type="geojson" data={stopFeatures}>
            <Layer
              id="stops-circle"
              type="circle"
              paint={{
                "circle-color": ["case", ["get", "is_depot"], "#111827", ["get", "color"]],
                "circle-radius": selectedRouteId
                  ? ["case", ["==", ["get", "route_id"], selectedRouteId], ["case", ["get", "is_depot"], 9, 7], 4]
                  : ["case", ["get", "is_depot"], 9, 6],
                "circle-opacity": selectedRouteId ? ["case", ["==", ["get", "route_id"], selectedRouteId], 0.96, 0.28] : 0.9,
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 2,
              }}
            />
            <Layer
              id="stops-label"
              type="symbol"
              layout={{
                "text-field": ["get", "label"],
                "text-size": 11,
                "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
                "text-allow-overlap": true,
                "text-ignore-placement": true,
              }}
              paint={{
                "text-color": "#ffffff",
                "text-opacity": selectedRouteId ? ["case", ["==", ["get", "route_id"], selectedRouteId], 1, 0.2] : 0.95,
              }}
            />
          </Source>
          {hoverInfo ? (
            <Popup longitude={hoverInfo.longitude} latitude={hoverInfo.latitude} closeButton={false} closeOnClick={false}>
              <div className="text-xs font-semibold">{hoverInfo.label}</div>
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
