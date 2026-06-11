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
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { JobMapData, JobMapRoute, JobMapStop } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
    formatDistanceKmFromMeters,
    formatDurationMinFromSeconds,
    formatNumber,
} from "@/lib/format";
import { useT } from "@/lib/i18n/context";

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
            tiles: ["/api/map-tiles/{z}/{x}/{y}.png"],
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

type HoverInfo =
    | {
          type: "route";
          routeId: string;
          label: string;
          longitude: number;
          latitude: number;
      }
    | {
          type: "stop";
          stopId: string;
          routeId: string;
          label: string;
          address: string;
          passengerCount: number;
          cumulativeDurationSeconds: number;
          longitude: number;
          latitude: number;
      }
    | null;

type RouteFilter = "all" | "long" | "high_load" | "many_stops";

function useTranslatedFilterOptions(t: (key: string) => string) {
    return useMemo(
        () => [
            { key: "all" as RouteFilter, label: t("All") },
            { key: "long" as RouteFilter, label: t("Long") },
            { key: "high_load" as RouteFilter, label: t("High load") },
            { key: "many_stops" as RouteFilter, label: t("Many stops") },
        ],
        [t],
    );
}

const ROUTE_LABEL_COLLATOR = new Intl.Collator(undefined, {
    numeric: true,
    sensitivity: "base",
});
const STOP_LAYER_IDS = [
    "stops-hit-area",
    "selected-stops-circle",
    "selected-stops-label",
    "stops-circle",
    "stops-label",
];
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

export function InteractiveRouteMap({
    data,
    fullscreen = false,
}: {
    data: JobMapData;
    fullscreen?: boolean;
}) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const mapRef = useRef<MapRef | null>(null);
    const t = useT();
    const filterOptions = useTranslatedFilterOptions(t);
    const [selectedRouteId, setSelectedRouteId] = useState<string>("");
    const [selectedStop, setSelectedStop] = useState<JobMapStop | null>(null);
    const [hoverInfo, setHoverInfo] = useState<HoverInfo>(null);
    const [routeSearch, setRouteSearch] = useState("");
    const [routeFilter, setRouteFilter] = useState<RouteFilter>("all");
    const [showRouteContext, setShowRouteContext] = useState(true);

    const routesById = useMemo(
        () => new Map(data.routes.map((route) => [route.id, route])),
        [data.routes],
    );
    const stopsById = useMemo(
        () => new Map(data.stops.map((stop) => [stop.id, stop])),
        [data.stops],
    );
    const selectedRoute = selectedRouteId
        ? routesById.get(selectedRouteId) || null
        : null;
    const longRouteThreshold = useMemo(
        () =>
            percentile(
                data.routes.map((route) => route.duration_s),
                0.75,
            ),
        [data.routes],
    );
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
    const sortedRoutes = useMemo(
        () =>
            [...data.routes].sort((a, b) => {
                const labelOrder = ROUTE_LABEL_COLLATOR.compare(
                    routeSortLabel(a),
                    routeSortLabel(b),
                );
                const workbookOrder =
                    routeWorkbookOrder(a, stopsByRouteId) -
                    routeWorkbookOrder(b, stopsByRouteId);
                return (
                    labelOrder || workbookOrder || a.route_index - b.route_index
                );
            }),
        [data.routes, stopsByRouteId],
    );
    const routeFilterCounts = useMemo(
        () => ({
            all: sortedRoutes.length,
            long: sortedRoutes.filter(
                (route) => route.duration_s >= longRouteThreshold,
            ).length,
            high_load: sortedRoutes.filter(
                (route) => routeLoadRatio(route) >= 0.85,
            ).length,
            many_stops: sortedRoutes.filter((route) => route.stop_count >= 8)
                .length,
        }),
        [longRouteThreshold, sortedRoutes],
    );
    const visibleRoutes = useMemo(() => {
        const normalizedSearch = routeSearch.trim().toLowerCase();
        return sortedRoutes.filter((route) => {
            if (normalizedSearch) {
                const haystack = [
                    route.id,
                    route.bus_type_name,
                    String(route.vehicle_id ?? ""),
                    String(route.route_index + 1),
                ]
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
    }, [longRouteThreshold, routeFilter, routeSearch, sortedRoutes]);
    const topImpactedStops = useMemo(
        () =>
            data.stops
                .filter(
                    (stop) =>
                        !stop.is_depot &&
                        Boolean(stop.time_impact?.comparison_available) &&
                        stopAdverseDeltaMinutes(stop) > 10,
                )
                .sort(
                    (a, b) =>
                        stopAdverseDeltaMinutes(b) -
                        stopAdverseDeltaMinutes(a),
                )
                .slice(0, 5),
        [data.stops],
    );

    const routeFeatures = useMemo<FeatureCollection>(
        () => ({
            type: "FeatureCollection",
            features: sortedRoutes
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
        [sortedRoutes],
    );

    const selectedRouteFeatures = useMemo<FeatureCollection>(
        () => ({
            type: "FeatureCollection",
            features: routeFeatures.features.filter(
                (feature) => feature.properties.route_id === selectedRouteId,
            ),
        }),
        [routeFeatures, selectedRouteId],
    );
    const contextRouteFeatures = useMemo<FeatureCollection>(
        () => ({
            type: "FeatureCollection",
            features:
                selectedRouteId && !showRouteContext
                    ? []
                    : routeFeatures.features,
        }),
        [routeFeatures, selectedRouteId, showRouteContext],
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
            features: stopFeatures.features.filter(
                (feature) => feature.properties.route_id === selectedRouteId,
            ),
        }),
        [selectedRouteId, stopFeatures],
    );
    const contextStopFeatures = useMemo<FeatureCollection>(
        () => ({
            type: "FeatureCollection",
            features: selectedRouteId
                ? stopFeatures.features.filter(
                      (feature) =>
                          feature.properties.route_id !== selectedRouteId,
                  )
                : stopFeatures.features,
        }),
        [selectedRouteId, stopFeatures],
    );
    const interactiveStopFeatures = selectedRouteId
        ? selectedStopFeatures
        : stopFeatures;

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
        if (selectedRouteId === route.id) {
            clearFocus();
            return;
        }
        setSelectedRouteId(route.id);
        setSelectedStop(null);
        if (!fullscreen) {
            containerRef.current?.scrollIntoView({
                block: "start",
                behavior: "smooth",
            });
        }
        window.setTimeout(() => mapRef.current?.resize(), 120);
        fitRoute(mapRef.current, route);
    };

    const focusStop = (stop: JobMapStop) => {
        setSelectedRouteId(stop.route_id);
        setSelectedStop(stop);
        mapRef.current?.flyTo({
            center: [stop.lng, stop.lat],
            zoom: Math.max(mapRef.current.getZoom(), 14),
            duration: 450,
        });
    };

    const clearFocus = () => {
        setSelectedRouteId("");
        setSelectedStop(null);
        setShowRouteContext(true);
        window.setTimeout(() => mapRef.current?.resize(), 120);
        fitAll(mapRef.current, data);
    };

    const handleMapClick = (event: MapLayerMouseEvent) => {
        const feature =
            event.features?.find((item) =>
                STOP_LAYER_IDS.includes(item.layer.id),
            ) ||
            event.features?.find((item) =>
                ROUTE_LAYER_IDS.includes(item.layer.id),
            );
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
        const stopFeature = event.features?.find((item) =>
            STOP_LAYER_IDS.includes(item.layer.id),
        );
        const stopId = String(stopFeature?.properties?.stop_id || "");
        if (stopId) {
            const stop = stopsById.get(stopId);
            if (stop) {
                setHoverInfo({
                    type: "stop",
                    stopId,
                    routeId: stop.route_id,
                    label: stop.is_depot
                        ? "School / Start"
                        : `Stop ${stop.order}`,
                    address:
                        stop.address ||
                        stop.requested_address ||
                        "Unknown address",
                    passengerCount: stop.passenger_count,
                    cumulativeDurationSeconds: stop.cumulative_duration_s,
                    longitude: event.lngLat.lng,
                    latitude: event.lngLat.lat,
                });
                return;
            }
        }

        const feature = event.features?.find((item) =>
            ROUTE_LAYER_IDS.includes(item.layer.id),
        );
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

    const hoveredRoute =
        hoverInfo?.type === "route"
            ? routesById.get(hoverInfo.routeId) || null
            : null;

    return (
        <div
            ref={containerRef}
            className={cn(
                fullscreen
                    ? "relative overflow-hidden border-0 bg-transparent"
                    : "grid overflow-hidden border border-border bg-surface lg:grid-cols-[320px_minmax(0,1fr)]",
                fullscreen
                    ? "h-full min-h-0 rounded-none"
                    : "min-h-[560px] rounded-md",
            )}
            style={{
                height: fullscreen
                    ? "100%"
                    : "clamp(560px, calc(100vh - 220px), 760px)",
            }}
        >
            <aside
                className={cn(
                    "flex min-h-0 flex-col",
                    fullscreen
                        ? "absolute inset-y-3 left-3 right-3 z-10 overflow-hidden rounded-lg border border-white/45 bg-white/30 shadow-2xl ring-1 ring-slate-950/10 backdrop-blur-2xl sm:right-auto sm:w-[360px]"
                        : "border-b border-border bg-surface lg:border-b-0 lg:border-r",
                )}
            >
                <div
                    className={cn(
                        "border-b p-3",
                        fullscreen
                            ? "border-white/35 bg-white/18 backdrop-blur-2xl"
                            : "border-border",
                    )}
                >
                    <div className="flex items-center justify-between gap-3">
                        <div>
                            <h3 className="text-sm font-semibold">
                                {data.scenario_name}
                            </h3>
                            <div className="mt-1 text-xs text-muted-foreground">
                                {formatNumber(data.summary.route_count)} routes
                                · {formatNumber(data.summary.stop_count)} stops
                                · {formatNumber(data.summary.passenger_count)}{" "}
                                riders
                            </div>
                        </div>
                        <Button
                            className={cn(
                                "h-8 px-3 text-xs",
                                fullscreen
                                    ? "bg-white/70 backdrop-blur hover:bg-white"
                                    : "",
                            )}
                            variant="secondary"
                            onClick={clearFocus}
                        >
                            Fit all
                        </Button>
                    </div>
                    {data.summary.time_impact?.available ? (
                        <div
                            className={cn(
                                "mt-3 rounded-md border px-3 py-2 text-xs",
                                fullscreen
                                    ? "border-white/40 bg-white/28 backdrop-blur"
                                    : "border-border bg-muted/35",
                            )}
                        >
                            <div className="flex items-center justify-between gap-3">
                                <span className="font-medium text-foreground">
                                    Time impact
                                </span>
                                <span className="text-muted-foreground">
                                    {formatNumber(
                                        data.summary.time_impact
                                            .compared_stop_count || 0,
                                    )}{" "}
                                    stops
                                </span>
                            </div>
                            <div className="mt-1 text-muted-foreground">
                                P90{" "}
                                {formatDeltaMinutes(
                                    data.summary.time_impact
                                        .p90_adverse_delta_minutes,
                                )}{" "}
                                · Max{" "}
                                {formatDeltaMinutes(
                                    data.summary.time_impact
                                        .max_adverse_delta_minutes,
                                )}{" "}
                                ·{" "}
                                {formatNumber(
                                    data.summary.time_impact
                                        .high_risk_stop_count || 0,
                                )}{" "}
                                high risk
                            </div>
                        </div>
                    ) : null}
                    <div className="mt-3 space-y-2">
                        <input
                            className={cn(
                                "h-9 w-full rounded-md border px-3 text-sm outline-none transition placeholder:text-muted-foreground focus:border-primary",
                                fullscreen
                                    ? "border-white/45 bg-white/38 shadow-sm backdrop-blur placeholder:text-slate-500"
                                    : "border-border bg-surface",
                            )}
                            value={routeSearch}
                            onChange={(event) =>
                                setRouteSearch(event.target.value)
                            }
                            placeholder={t("Search route, bus, vehicle")}
                        />
                        <div className="flex flex-wrap gap-1.5">
                            {filterOptions.map((option) => (
                                <button
                                    key={option.key}
                                    type="button"
                                    className={cn(
                                        "h-7 rounded-md border px-2 text-xs font-medium transition",
                                        routeFilter === option.key
                                            ? "border-primary bg-primary text-primary-foreground"
                                            : fullscreen
                                              ? "border-white/45 bg-white/45 text-muted-foreground backdrop-blur hover:bg-white/70 hover:text-foreground"
                                              : "border-border bg-surface text-muted-foreground hover:bg-muted hover:text-foreground",
                                    )}
                                    onClick={() => setRouteFilter(option.key)}
                                >
                                    {option.label}
                                    {option.key === "all" ? null : (
                                        <span className="ml-1 opacity-75">
                                            {formatNumber(
                                                routeFilterCounts[option.key],
                                            )}
                                        </span>
                                    )}
                                </button>
                            ))}
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                            Showing {formatNumber(visibleRoutes.length)} of{" "}
                            {formatNumber(data.routes.length)} routes
                        </div>
                        {selectedRoute ? (
                            <div
                                className={cn(
                                    "flex items-center justify-between gap-3 rounded-md border px-3 py-2",
                                    fullscreen
                                        ? "border-white/40 bg-white/30 backdrop-blur"
                                        : "border-border bg-muted/40",
                                )}
                            >
                                <div>
                                    <div className="text-xs font-medium text-foreground">
                                        Route context
                                    </div>
                                    <div className="text-[11px] text-muted-foreground">
                                        {showRouteContext
                                            ? "Other routes visible"
                                            : "Only selected route"}
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    className={cn(
                                        "relative h-6 w-10 rounded-full border transition",
                                        showRouteContext
                                            ? "border-primary bg-primary"
                                            : "border-border bg-muted",
                                    )}
                                    aria-pressed={showRouteContext}
                                    onClick={() =>
                                        setShowRouteContext((value) => !value)
                                    }
                                >
                                    <span
                                        className={cn(
                                            "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition",
                                            showRouteContext
                                                ? "left-[17px]"
                                                : "left-0.5",
                                        )}
                                    />
                                </button>
                            </div>
                        ) : null}
                    </div>
                </div>
                <div
                    className={cn(
                        "min-h-0 flex-1 overflow-auto",
                        fullscreen ? "space-y-2 bg-transparent p-2" : "",
                    )}
                >
                    {visibleRoutes.length ? null : (
                        <div className="p-4 text-sm text-muted-foreground">
                            No routes match the current filter.
                        </div>
                    )}
                    {visibleRoutes.map((route) => {
                        const active = selectedRouteId === route.id;
                        const routeStops = stopsByRouteId.get(route.id) || [];
                        return (
                            <div
                                key={route.id}
                                className={cn(
                                    fullscreen
                                        ? "overflow-hidden rounded-lg border border-white/38 bg-white/28 shadow-sm backdrop-blur-xl"
                                        : "border-b border-border",
                                )}
                            >
                                <button
                                    type="button"
                                    className={cn(
                                        "flex w-full items-start gap-3 border-l-2 px-3 py-3 text-left transition",
                                        fullscreen ? "rounded-lg" : "",
                                        active
                                            ? fullscreen
                                                ? "bg-white/56 backdrop-blur"
                                                : "bg-primary/10"
                                            : fullscreen
                                              ? "hover:bg-white/42"
                                              : "hover:bg-muted",
                                        routeListAccentClass(route),
                                    )}
                                    onClick={() => focusRoute(route)}
                                >
                                    <span
                                        className="mt-1 h-3 w-3 shrink-0 rounded-full ring-2 ring-white"
                                        style={{
                                            backgroundColor: routeColor(
                                                route.route_index,
                                            ),
                                        }}
                                        aria-hidden="true"
                                    />
                                    <span className="min-w-0 flex-1">
                                        <span className="flex min-w-0 items-center gap-2">
                                            <span className="truncate text-sm font-semibold text-foreground">
                                                {routeLabel(route)}
                                            </span>
                                            <RouteStatusBadge route={route} />
                                        </span>
                                        <span className="mt-1 block text-xs text-muted-foreground">
                                            {formatNumber(route.load)} riders ·{" "}
                                            {formatNumber(route.stop_count)}{" "}
                                            stops ·{" "}
                                            {formatDurationMinFromSeconds(
                                                route.duration_s,
                                            )}
                                        </span>
                                        <span className="mt-1 block text-xs text-muted-foreground">
                                            {formatDistanceKmFromMeters(
                                                route.distance_m,
                                            )}
                                            {route.bus_type_name
                                                ? ` · ${route.bus_type_name}`
                                                : ""}
                                        </span>
                                    </span>
                                    <span
                                        className={cn(
                                            "mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition",
                                            fullscreen
                                                ? "bg-white/45 text-slate-700"
                                                : "text-muted-foreground",
                                        )}
                                    >
                                        {active ? (
                                            <ChevronUp
                                                className="h-4 w-4"
                                                aria-hidden="true"
                                            />
                                        ) : (
                                            <ChevronDown
                                                className="h-4 w-4"
                                                aria-hidden="true"
                                            />
                                        )}
                                    </span>
                                </button>
                                {active ? (
                                    <div
                                        className={cn(
                                            "max-h-[320px] overflow-auto px-3 pb-3",
                                            fullscreen
                                                ? "border-t border-white/30 bg-white/18 backdrop-blur-xl"
                                                : "bg-muted/45",
                                        )}
                                    >
                                        <div className="mb-2 pt-2 text-[11px] font-semibold uppercase text-muted-foreground">
                                            Stop sequence
                                        </div>
                                        <div className="space-y-1">
                                            {routeStops.map((stop) => (
                                                <button
                                                    key={stop.id}
                                                    type="button"
                                                    className={cn(
                                                        "grid w-full grid-cols-[28px_minmax(0,1fr)] gap-2 rounded-md px-2 py-2 text-left text-xs transition",
                                                        selectedStop?.id ===
                                                            stop.id
                                                            ? "bg-primary text-primary-foreground"
                                                            : fullscreen
                                                              ? "hover:bg-white/45"
                                                              : "hover:bg-surface",
                                                    )}
                                                    onClick={() =>
                                                        focusStop(stop)
                                                    }
                                                >
                                                    <span className="font-semibold">
                                                        {stop.is_depot
                                                            ? "S"
                                                            : stop.order}
                                                    </span>
                                                    <span className="min-w-0">
                                                        <span className="block truncate font-medium">
                                                            {stop.address ||
                                                                stop.requested_address ||
                                                                "Unknown address"}
                                                        </span>
                                                        <span
                                                            className={cn(
                                                                "mt-0.5 block",
                                                                selectedStop?.id ===
                                                                    stop.id
                                                                    ? "text-primary-foreground/80"
                                                                    : "text-muted-foreground",
                                                            )}
                                                        >
                                                            {formatNumber(
                                                                stop.passenger_count,
                                                            )}{" "}
                                                            riders ·{" "}
                                                            {formatDurationMinFromSeconds(
                                                                stop.cumulative_duration_s,
                                                            )}
                                                        </span>
                                                        {stopScheduleImpactLabel(
                                                            stop,
                                                        ) ? (
                                                            <span
                                                                className={cn(
                                                                    "mt-1 flex flex-wrap items-center gap-1",
                                                                    selectedStop?.id ===
                                                                        stop.id
                                                                        ? "text-primary-foreground/85"
                                                                        : "text-muted-foreground",
                                                                )}
                                                            >
                                                                <span>
                                                                    {stopScheduleImpactLabel(
                                                                        stop,
                                                                    )}
                                                                </span>
                                                                <StopTimeImpactBadge
                                                                    stop={stop}
                                                                />
                                                            </span>
                                                        ) : null}
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

            <div className={cn("relative min-h-0", fullscreen ? "h-full" : "")}>
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
                    <NavigationControl position="bottom-right" />
                    <Source
                        id="private-links"
                        type="geojson"
                        data={privateLinkFeatures}
                    >
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
                    <Source
                        id="routes"
                        type="geojson"
                        data={contextRouteFeatures}
                    >
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
                    <Source
                        id="selected-route"
                        type="geojson"
                        data={selectedRouteFeatures}
                    >
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
                        <Layer
                            id="selected-route-arrows"
                            type="symbol"
                            minzoom={11}
                            layout={{
                                "symbol-placement": "line",
                                "symbol-spacing": 90,
                                "text-field": ">",
                                "text-size": [
                                    "interpolate",
                                    ["linear"],
                                    ["zoom"],
                                    11,
                                    13,
                                    14,
                                    17,
                                    16,
                                    20,
                                ],
                                "text-font": [
                                    "Open Sans Bold",
                                    "Arial Unicode MS Bold",
                                ],
                                "text-rotation-alignment": "map",
                                "text-keep-upright": false,
                                "text-allow-overlap": false,
                                "text-ignore-placement": false,
                            }}
                            paint={{
                                "text-color": "#ffffff",
                                "text-halo-color": ["get", "color"],
                                "text-halo-width": 2.5,
                                "text-opacity": selectedRouteId ? 0.92 : 0,
                            }}
                        />
                    </Source>
                    <Source
                        id="stops"
                        type="geojson"
                        data={contextStopFeatures}
                    >
                        <Layer
                            id="stops-halo"
                            type="circle"
                            beforeId="selected-route-casing"
                            paint={{
                                "circle-color": "#94a3b8",
                                "circle-radius": selectedRouteId
                                    ? [
                                          "interpolate",
                                          ["linear"],
                                          ["zoom"],
                                          10,
                                          4,
                                          14,
                                          5.5,
                                          16,
                                          7,
                                      ]
                                    : [
                                          "case",
                                          ["get", "is_depot"],
                                          8,
                                          [
                                              "interpolate",
                                              ["linear"],
                                              ["zoom"],
                                              10,
                                              4,
                                              14,
                                              5.5,
                                              16,
                                              7,
                                          ],
                                      ],
                                "circle-opacity": selectedRouteId
                                    ? showRouteContext
                                        ? 0.18
                                        : 0
                                    : 0.24,
                            }}
                        />
                        <Layer
                            id="stops-circle"
                            type="circle"
                            beforeId="selected-route-casing"
                            paint={{
                                "circle-color": "#64748b",
                                "circle-radius": selectedRouteId
                                    ? [
                                          "interpolate",
                                          ["linear"],
                                          ["zoom"],
                                          10,
                                          2.5,
                                          14,
                                          3.5,
                                          16,
                                          4.5,
                                      ]
                                    : [
                                          "case",
                                          ["get", "is_depot"],
                                          5,
                                          [
                                              "interpolate",
                                              ["linear"],
                                              ["zoom"],
                                              10,
                                              3,
                                              14,
                                              4,
                                              16,
                                              5,
                                          ],
                                      ],
                                "circle-opacity": selectedRouteId
                                    ? showRouteContext
                                        ? 0.34
                                        : 0
                                    : 0.46,
                                "circle-stroke-width": 0,
                            }}
                        />
                        <Layer
                            id="stops-label"
                            type="symbol"
                            minzoom={12.25}
                            beforeId="selected-route-casing"
                            layout={{
                                "text-field": ["get", "label"],
                                "text-size": [
                                    "interpolate",
                                    ["linear"],
                                    ["zoom"],
                                    12,
                                    9,
                                    14,
                                    11,
                                    16,
                                    13,
                                ],
                                "text-font": [
                                    "Open Sans Bold",
                                    "Arial Unicode MS Bold",
                                ],
                                "text-allow-overlap": false,
                                "text-ignore-placement": false,
                                "text-optional": true,
                            }}
                            paint={{
                                "text-color": "#ffffff",
                                "text-halo-color": "#111827",
                                "text-halo-width": 1.1,
                                "text-opacity": 0,
                            }}
                        />
                    </Source>
                    <Source
                        id="selected-stops"
                        type="geojson"
                        data={selectedStopFeatures}
                    >
                        <Layer
                            id="selected-stops-halo"
                            type="circle"
                            paint={{
                                "circle-color": "#ffffff",
                                "circle-radius": [
                                    "case",
                                    ["get", "is_depot"],
                                    20,
                                    [
                                        "interpolate",
                                        ["linear"],
                                        ["zoom"],
                                        10,
                                        13,
                                        14,
                                        16,
                                        16,
                                        18,
                                    ],
                                ],
                                "circle-opacity": selectedRouteId ? 0.98 : 0,
                            }}
                        />
                        <Layer
                            id="selected-stops-circle"
                            type="circle"
                            paint={{
                                "circle-color": [
                                    "case",
                                    ["get", "is_depot"],
                                    "#111827",
                                    ["get", "color"],
                                ],
                                "circle-radius": [
                                    "case",
                                    ["get", "is_depot"],
                                    15,
                                    [
                                        "interpolate",
                                        ["linear"],
                                        ["zoom"],
                                        10,
                                        9,
                                        14,
                                        12,
                                        16,
                                        14,
                                    ],
                                ],
                                "circle-opacity": selectedRouteId ? 0.98 : 0,
                                "circle-stroke-color": "#111827",
                                "circle-stroke-width": 3,
                            }}
                        />
                        <Layer
                            id="selected-stops-label"
                            type="symbol"
                            layout={{
                                "text-field": ["get", "label"],
                                "text-size": [
                                    "interpolate",
                                    ["linear"],
                                    ["zoom"],
                                    10,
                                    12,
                                    14,
                                    15,
                                    16,
                                    17,
                                ],
                                "text-font": [
                                    "Open Sans Bold",
                                    "Arial Unicode MS Bold",
                                ],
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
                    <Source
                        id="stop-hit-areas"
                        type="geojson"
                        data={interactiveStopFeatures}
                    >
                        <Layer
                            id="stops-hit-area"
                            type="circle"
                            paint={{
                                "circle-color": "#ffffff",
                                "circle-radius": [
                                    "case",
                                    ["get", "is_depot"],
                                    16,
                                    [
                                        "interpolate",
                                        ["linear"],
                                        ["zoom"],
                                        10,
                                        10,
                                        14,
                                        13,
                                        16,
                                        16,
                                    ],
                                ],
                                "circle-opacity": 0.001,
                            }}
                        />
                    </Source>
                    {hoverInfo && !selectedStop ? (
                        <Popup
                            longitude={hoverInfo.longitude}
                            latitude={hoverInfo.latitude}
                            closeButton={false}
                            closeOnClick={false}
                        >
                            {hoverInfo.type === "route" ? (
                                <div className="max-w-[260px] space-y-1.5 text-xs">
                                    <div className="flex items-center gap-2">
                                        <span className="font-semibold">
                                            {hoveredRoute
                                                ? routeLabel(hoveredRoute)
                                                : hoverInfo.label}
                                        </span>
                                        {hoveredRoute ? (
                                            <RouteStatusBadge
                                                route={hoveredRoute}
                                            />
                                        ) : null}
                                    </div>
                                    {hoveredRoute ? (
                                        <>
                                            <div className="text-muted-foreground">
                                                {routeLoadLabel(hoveredRoute)}{" "}
                                                riders ·{" "}
                                                {formatNumber(
                                                    hoveredRoute.stop_count,
                                                )}{" "}
                                                stops
                                            </div>
                                            <div className="text-muted-foreground">
                                                {formatDurationMinFromSeconds(
                                                    hoveredRoute.duration_s,
                                                )}{" "}
                                                ·{" "}
                                                {formatDistanceKmFromMeters(
                                                    hoveredRoute.distance_m,
                                                )}
                                            </div>
                                            <div className="text-muted-foreground">
                                                {routeVehicleLabel(
                                                    hoveredRoute,
                                                )}
                                            </div>
                                        </>
                                    ) : null}
                                </div>
                            ) : (
                                <div className="max-w-[240px] space-y-1 text-xs">
                                    <div className="font-semibold">
                                        {hoverInfo.label}
                                    </div>
                                    <div className="line-clamp-2">
                                        {hoverInfo.address}
                                    </div>
                                    <div className="text-muted-foreground">
                                        {hoverInfo.routeId} ·{" "}
                                        {formatNumber(hoverInfo.passengerCount)}{" "}
                                        riders
                                    </div>
                                    <div className="text-muted-foreground">
                                        {formatDurationMinFromSeconds(
                                            hoverInfo.cumulativeDurationSeconds,
                                        )}
                                    </div>
                                </div>
                            )}
                        </Popup>
                    ) : null}
                    {selectedStop ? (
                        <Popup
                            longitude={selectedStop.lng}
                            latitude={selectedStop.lat}
                            closeButton
                            closeOnClick={false}
                            onClose={() => setSelectedStop(null)}
                        >
                            <div className="max-w-[260px] space-y-1 text-xs">
                                <div className="font-semibold">
                                    {selectedStop.is_depot
                                        ? "School / Start"
                                        : `Stop ${selectedStop.order}`}
                                </div>
                                <div>
                                    {selectedStop.address ||
                                        selectedStop.requested_address ||
                                        "Unknown address"}
                                </div>
                                <div className="text-muted-foreground">
                                    {selectedStop.route_id} ·{" "}
                                    {formatNumber(selectedStop.passenger_count)}{" "}
                                    riders
                                </div>
                                <div className="text-muted-foreground">
                                    {formatDurationMinFromSeconds(
                                        selectedStop.cumulative_duration_s,
                                    )}{" "}
                                    ·{" "}
                                    {formatDistanceKmFromMeters(
                                        selectedStop.cumulative_distance_m,
                                    )}
                                </div>
                                {stopScheduleImpactLabel(selectedStop) ? (
                                    <div className="text-muted-foreground">
                                        {stopScheduleImpactLabel(selectedStop)}
                                    </div>
                                ) : null}
                                <StopTimeImpactBadge stop={selectedStop} />
                            </div>
                        </Popup>
                    ) : null}
                </MapView>
                {topImpactedStops.length ? (
                    <div
                        className={cn(
                            "absolute right-3 top-14 z-10 max-h-[42%] w-[min(340px,calc(100%-24px))] overflow-auto rounded-md border p-3 text-xs shadow-xl",
                            fullscreen
                                ? "border-white/45 bg-white/35 backdrop-blur-2xl"
                                : "border-border bg-surface/95 backdrop-blur",
                        )}
                    >
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <div className="font-semibold text-foreground">
                                    Review first
                                </div>
                                <div className="mt-0.5 text-[11px] text-muted-foreground">
                                    Top {formatNumber(topImpactedStops.length)} time impacts
                                </div>
                            </div>
                            <span className="text-[11px] text-muted-foreground">
                                Time impact
                            </span>
                        </div>
                        <div className="mt-2 space-y-1.5">
                            {topImpactedStops.map((stop) => {
                                const route = routesById.get(stop.route_id);
                                return (
                                    <button
                                        key={stop.id}
                                        type="button"
                                        className={cn(
                                            "grid w-full grid-cols-[minmax(0,1fr)_auto] gap-2 rounded-md px-2 py-1.5 text-left transition",
                                            fullscreen
                                                ? "bg-white/22 hover:bg-white/48"
                                                : "bg-muted/70 hover:bg-muted",
                                        )}
                                        onClick={() => focusStop(stop)}
                                    >
                                        <span className="min-w-0">
                                            <span className="block truncate font-medium text-foreground">
                                                {stop.address ||
                                                    stop.requested_address ||
                                                    "Unknown address"}
                                            </span>
                                            <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                                                {route
                                                    ? routeLabel(route)
                                                    : stop.route_id}{" "}
                                                · {stopScheduleImpactLabel(stop)}
                                            </span>
                                        </span>
                                        <span className="flex items-center">
                                            <StopTimeImpactBadge stop={stop} />
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                ) : null}
                {selectedRoute ? (
                    <div className="absolute bottom-3 left-3 right-3 rounded-md border border-border bg-surface/95 p-3 shadow-lg backdrop-blur md:left-auto md:w-[420px]">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                    <div className="truncate text-sm font-semibold">
                                        {routeLabel(selectedRoute)}
                                    </div>
                                    <RouteStatusBadge route={selectedRoute} />
                                </div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                    {routeVehicleLabel(selectedRoute)}
                                </div>
                                <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                                    <RouteMetric
                                        label="Load"
                                        value={routeLoadLabel(selectedRoute)}
                                    />
                                    <RouteMetric
                                        label="Stops"
                                        value={formatNumber(
                                            selectedRoute.stop_count,
                                        )}
                                    />
                                    <RouteMetric
                                        label="Duration"
                                        value={formatDurationMinFromSeconds(
                                            selectedRoute.duration_s,
                                        )}
                                    />
                                    <RouteMetric
                                        label="Distance"
                                        value={formatDistanceKmFromMeters(
                                            selectedRoute.distance_m,
                                        )}
                                    />
                                </div>
                                <div className="mt-2 text-xs text-muted-foreground">
                                    {selectedRoute.traffic_time_source
                                        ? `${selectedRoute.traffic_time_source} timing`
                                        : "Planned route timing"}
                                </div>
                            </div>
                            <Button
                                className={cn(
                                    "h-8 px-3 text-xs",
                                    fullscreen
                                        ? "bg-white/70 backdrop-blur hover:bg-white"
                                        : "",
                                )}
                                variant="secondary"
                                onClick={clearFocus}
                            >
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

function formatDeltaMinutes(value: unknown) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
        return "0 min";
    }
    return `${formatNumber(Math.round(Math.abs(numericValue)))} min`;
}

function stopAdverseDeltaMinutes(stop: JobMapStop) {
    const numericValue = Number(stop.time_impact?.adverse_delta_minutes || 0);
    return Number.isFinite(numericValue) ? numericValue : 0;
}

function stopScheduleImpactLabel(stop: JobMapStop) {
    const impact = stop.time_impact;
    if (impact?.comparison_available) {
        const currentTime = impact.current_time_label || "";
        const newTime = impact.new_time_label || stop.scheduled_time_label || "";
        const delta = Number(impact.delta_minutes || 0);
        const absoluteDelta = formatDeltaMinutes(delta);
        const direction =
            impact.adverse_direction === "earlier_pickup"
                ? delta < 0
                    ? "earlier pickup"
                    : delta > 0
                      ? "later pickup"
                      : "no change"
                : delta > 0
                  ? "later dropoff"
                  : delta < 0
                    ? "earlier dropoff"
                    : "no change";
        if (direction === "no change") {
            return `${currentTime || newTime} · no time change`;
        }
        return `${currentTime} -> ${newTime} · ${absoluteDelta} ${direction}`;
    }
    if (stop.scheduled_time_label) {
        return `Estimated time ${stop.scheduled_time_label}`;
    }
    return "";
}

function stopTimeImpactBadgeClass(level: string | undefined) {
    if (level === "critical") {
        return "border-rose-300 bg-rose-50 text-rose-700";
    }
    if (level === "severe") {
        return "border-orange-300 bg-orange-50 text-orange-700";
    }
    if (level === "elevated") {
        return "border-amber-300 bg-amber-50 text-amber-700";
    }
    if (level === "notice") {
        return "border-sky-300 bg-sky-50 text-sky-700";
    }
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

function StopTimeImpactBadge({ stop }: { stop: JobMapStop }) {
    const impact = stop.time_impact;
    if (!impact?.comparison_available) {
        return null;
    }
    const adverseDelta = Number(impact.adverse_delta_minutes || 0);
    if (adverseDelta <= 10) {
        return null;
    }
    const label =
        impact.level === "critical"
            ? "Critical"
            : impact.level === "severe"
              ? "Severe"
              : impact.level === "elevated"
                ? "Elevated"
                : "Notice";
    return (
        <span
            className={cn(
                "inline-flex w-fit items-center rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase",
                stopTimeImpactBadgeClass(impact.level),
            )}
        >
            {label}
        </span>
    );
}

function routeLabel(route: JobMapRoute) {
    return route.id || `Bus ${route.vehicle_id || route.route_index + 1}`;
}

function routeSortLabel(route: JobMapRoute) {
    return route.id || String(route.vehicle_id || route.route_index + 1);
}

function routeLoadRatio(route: JobMapRoute) {
    const capacity = routeCapacity(route);
    return capacity > 0 ? route.load / capacity : 0;
}

function routeWorkbookOrder(
    route: JobMapRoute,
    stopsByRouteId: Map<string, JobMapStop[]>,
) {
    const stops = stopsByRouteId.get(route.id) || [];
    const firstWorkbookStop = stops
        .filter((stop) => !stop.is_depot && Number.isFinite(stop.node_index))
        .reduce<
            number | null
        >((best, stop) => (best === null || stop.node_index < best ? stop.node_index : best), null);
    return firstWorkbookStop ?? Number.MAX_SAFE_INTEGER;
}

function routeCapacity(route: JobMapRoute) {
    return route.bus_capacity || 0;
}

function routeLoadLabel(route: JobMapRoute) {
    const capacity = routeCapacity(route);
    return capacity > 0
        ? `${formatNumber(route.load)} / ${formatNumber(capacity)}`
        : formatNumber(route.load);
}

function routeVehicleLabel(route: JobMapRoute) {
    const vehicle = route.vehicle_id
        ? `Vehicle ${route.vehicle_id}`
        : `Route ${route.route_index + 1}`;
    return route.bus_type_name
        ? `${vehicle} · ${route.bus_type_name}`
        : vehicle;
}

function routeStatusLabel(route: JobMapRoute) {
    const loadRatio = routeLoadRatio(route);
    if (loadRatio >= 1) {
        return "Capacity";
    }
    if (loadRatio >= 0.85) {
        return "High load";
    }
    if (route.duration_s >= 3600) {
        return "Long";
    }
    return "";
}

function routeListAccentClass(route: JobMapRoute) {
    const status = routeStatusLabel(route);
    if (status === "Capacity") {
        return "border-l-rose-300";
    }
    if (status === "High load") {
        return "border-l-amber-300";
    }
    if (status === "Long") {
        return "border-l-sky-300";
    }
    return "border-l-transparent";
}

function RouteStatusBadge({ route }: { route: JobMapRoute }) {
    const label = routeStatusLabel(route);
    if (!label) {
        return null;
    }
    return (
        <span
            className={cn(
                "shrink-0 rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase",
                label === "Capacity"
                    ? "border-rose-200 bg-rose-50 text-rose-700"
                    : label === "High load"
                      ? "border-amber-200 bg-amber-50 text-amber-700"
                      : "border-sky-200 bg-sky-50 text-sky-700",
            )}
        >
            {label}
        </span>
    );
}

function RouteMetric({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-md border border-border bg-muted/40 px-2 py-1.5">
            <div className="text-[10px] font-semibold uppercase text-muted-foreground">
                {label}
            </div>
            <div className="mt-0.5 truncate font-semibold text-foreground">
                {value}
            </div>
        </div>
    );
}

function percentile(values: number[], ratio: number) {
    const sorted = values
        .filter((value) => Number.isFinite(value))
        .sort((a, b) => a - b);
    if (!sorted.length) {
        return 0;
    }
    const index = Math.min(
        sorted.length - 1,
        Math.max(0, Math.floor((sorted.length - 1) * ratio)),
    );
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
    const lngs = route.geometry
        .map((item) => item[0])
        .filter((item) => Number.isFinite(item));
    const lats = route.geometry
        .map((item) => item[1])
        .filter((item) => Number.isFinite(item));
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
