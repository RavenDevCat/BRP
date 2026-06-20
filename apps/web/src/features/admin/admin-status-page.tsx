import type { ReactNode } from "react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
    AlertTriangle,
    CheckCircle2,
    Clipboard,
    Clock3,
    Database,
    Gauge,
    RefreshCw,
    Server,
    ShieldCheck,
    XCircle,
} from "lucide-react";
import {
    getCurrentUser,
    getTrafficRolloutStatus,
    type TrafficMarketStatus,
    type TrafficRolloutStatusResponse,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatNumber } from "@/lib/format";
import { useT } from "@/lib/i18n/context";

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

export function AdminStatusPage() {
    const t = useT();
    const [copied, setCopied] = useState(false);
    const userQuery = useQuery({
        queryKey: ["me"],
        queryFn: getCurrentUser,
        staleTime: 60_000,
    });
    const isAdmin = userQuery.data?.is_admin === true;
    const statusQuery = useQuery({
        queryKey: ["traffic-rollout-status", "admin-page"],
        queryFn: getTrafficRolloutStatus,
        enabled: isAdmin,
        staleTime: 30_000,
        refetchInterval: 60_000,
    });
    const status = statusQuery.data;

    if (userQuery.isLoading) {
        return (
            <div className="pb-16 lg:pb-0">
                <LoadingPanel label={t("Loading admin status")} />
            </div>
        );
    }

    if (!isAdmin) {
        return (
            <EmptyState
                title={t("Admin access required")}
                detail={t("Operations status is available to admins only.")}
            />
        );
    }

    async function copyDiagnostics() {
        if (!status) return;
        try {
            await navigator.clipboard.writeText(JSON.stringify(status, null, 2));
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1800);
        } catch {
            setCopied(false);
        }
    }

    return (
        <div className="space-y-6 pb-16 lg:pb-0">
            <section className="flex flex-col justify-between gap-4 xl:flex-row xl:items-end">
                <div>
                    <p className="text-sm font-medium text-primary">
                        {t("Admin")}
                    </p>
                    <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">
                        {t("Operations Status")}
                    </h1>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Badge tone={statusTone(status?.status)}>
                            {t(status?.status || "checking")}
                        </Badge>
                        <Badge
                            tone={
                                status?.endpoint?.provider_api_called
                                    ? "danger"
                                    : "success"
                            }
                        >
                            {t("Provider calls")}:{" "}
                            {status?.endpoint?.provider_api_called
                                ? t("yes")
                                : t("no")}
                        </Badge>
                        <Badge
                            tone={
                                status?.endpoint?.osrm_started
                                    ? "danger"
                                    : "success"
                            }
                        >
                            {t("OSRM starts")}:{" "}
                            {status?.endpoint?.osrm_started
                                ? t("yes")
                                : t("no")}
                        </Badge>
                        <Badge tone="neutral">{t("Read-only")}</Badge>
                    </div>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button
                        type="button"
                        variant="secondary"
                        icon={
                            <RefreshCw
                                className={
                                    statusQuery.isFetching
                                        ? "h-4 w-4 animate-spin"
                                        : "h-4 w-4"
                                }
                                aria-hidden="true"
                            />
                        }
                        onClick={() => void statusQuery.refetch()}
                    >
                        {t("Refresh")}
                    </Button>
                    <Button
                        type="button"
                        variant="secondary"
                        icon={<Clipboard className="h-4 w-4" aria-hidden="true" />}
                        disabled={!status}
                        onClick={() => void copyDiagnostics()}
                    >
                        {copied ? t("Copied") : t("Copy diagnostics")}
                    </Button>
                </div>
            </section>

            {statusQuery.error ? (
                <EmptyState
                    title={t("Traffic rollout status could not load")}
                    detail={(statusQuery.error as Error).message}
                />
            ) : statusQuery.isLoading ? (
                <LoadingPanel label={t("Loading traffic diagnostics")} />
            ) : (
                <>
                    <StatusSummary status={status} />
                    <MarketOverview status={status} />
                    <OpsDetails status={status} />
                </>
            )}
        </div>
    );
}

function StatusSummary({ status }: { status?: TrafficRolloutStatusResponse }) {
    const t = useT();
    const marketOverview = status?.market_overview;
    return (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <OpsMetric
                label={t("Traffic gate")}
                value={t(status?.status || "unknown")}
                detail={status?.next_step || t("No active warning")}
                icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
                tone={statusTone(status?.status)}
            />
            <OpsMetric
                label={t("Markets")}
                value={`${formatNumber(marketOverview?.markets?.length ?? 0)}`}
                detail={`${formatNumber(marketOverview?.blocked_count ?? 0)} ${t("blocked")} / ${formatNumber(marketOverview?.warning_count ?? 0)} ${t("warning")}`}
                icon={<Gauge className="h-4 w-4" aria-hidden="true" />}
                tone={statusTone(marketOverview?.status)}
            />
            <OpsMetric
                label={t("API budget")}
                value={formatApiBudget(status)}
                detail={
                    status?.api_budget?.problem
                        ? t("problem")
                        : t("under cap")
                }
                icon={<Database className="h-4 w-4" aria-hidden="true" />}
                tone={status?.api_budget?.problem ? "danger" : "success"}
            />
            <OpsMetric
                label={t("OSRM manager")}
                value={formatOsrm(status)}
                detail={`${formatNumber(status?.osrm_manager?.stale_lock_count ?? 0)} ${t("stale locks")}`}
                icon={<Server className="h-4 w-4" aria-hidden="true" />}
                tone={
                    status?.osrm_manager?.available === false ||
                    Number(status?.osrm_manager?.stale_lock_count ?? 0) > 0
                        ? "danger"
                        : "success"
                }
            />
        </div>
    );
}

function MarketOverview({ status }: { status?: TrafficRolloutStatusResponse }) {
    const t = useT();
    const markets = status?.market_overview?.markets || [];
    return (
        <section className="space-y-3">
            <div className="flex flex-col justify-between gap-2 md:flex-row md:items-end">
                <div>
                    <h2 className="text-base font-semibold">
                        {t("Traffic Markets")}
                    </h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                        {t("Default coefficient mode")}:{" "}
                        {status?.market_overview?.default_traffic_coefficient_mode ||
                            "--"}
                    </p>
                </div>
                <Badge tone={statusTone(status?.market_overview?.status)}>
                    {t(status?.market_overview?.status || "unknown")}
                </Badge>
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
                {markets.map((market) => (
                    <MarketCard key={`${market.market}-${market.city}`} market={market} />
                ))}
            </div>
        </section>
    );
}

function MarketCard({ market }: { market: TrafficMarketStatus }) {
    const t = useT();
    const warnings = market.warnings || [];
    return (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between gap-3">
                    <div>
                        <div className="flex items-center gap-2">
                            <span className="h-2.5 w-2.5 rounded-full bg-primary" />
                            <h3 className="text-sm font-semibold">
                                {market.label}
                            </h3>
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                            {formatMode(market.traffic_mode)} ·{" "}
                            {market.provider}
                        </p>
                    </div>
                    <Badge tone={statusTone(market.status)}>
                        {t(market.status)}
                    </Badge>
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-3">
                    <MiniMetric
                        label={t("Routes")}
                        value={formatNumber(market.route_sample_count ?? 0)}
                    />
                    <MiniMetric
                        label={t("Geo ready")}
                        value={formatPercent(market.geo_route_sample_ratio)}
                    />
                    <MiniMetric
                        label={t("Latest")}
                        value={formatShortDate(market.latest_measured_at)}
                    />
                </div>
                <div className="grid gap-2 text-xs text-muted-foreground md:grid-cols-2">
                    <DetailRow
                        label={t("Active source")}
                        value={market.active_source || "--"}
                    />
                    <DetailRow
                        label={t("Observed providers")}
                        value={
                            market.observed_providers?.length
                                ? market.observed_providers.join(", ")
                                : "--"
                        }
                    />
                    <DetailRow
                        label={t("Fallback")}
                        value={
                            typeof market.fallback_multiplier === "number"
                                ? `${market.fallback_multiplier.toFixed(2)}x`
                                : "--"
                        }
                    />
                    <DetailRow
                        label={t("Sample files")}
                        value={formatNumber(market.sample_file_count ?? 0)}
                    />
                </div>
                {warnings.length ? (
                    <div className="flex flex-wrap gap-2">
                        {warnings.map((warning) => (
                            <Badge key={warning} tone="warning">
                                {formatWarning(warning, t)}
                            </Badge>
                        ))}
                    </div>
                ) : null}
                <div className="divide-y divide-border rounded-md border border-border">
                    {(market.periods || []).map((period) => (
                        <div
                            key={period.period}
                            className="grid gap-2 px-3 py-2 text-xs md:grid-cols-[120px_1fr_1fr]"
                        >
                            <span className="font-medium text-foreground">
                                {formatPeriod(period.period)}
                            </span>
                            <span className="text-muted-foreground">
                                {formatNumber(period.route_sample_count ?? 0)}{" "}
                                {t("routes")} · {formatPercent(period.geo_route_sample_ratio)}
                            </span>
                            <span className="truncate text-muted-foreground">
                                {formatShortDate(period.latest_measured_at)}
                            </span>
                        </div>
                    ))}
                </div>
            </CardContent>
        </Card>
    );
}

function OpsDetails({ status }: { status?: TrafficRolloutStatusResponse }) {
    const t = useT();
    const nextTimer = status?.timers?.next_relevant_timer;
    const problemServices = status?.services?.problem_services || [];
    return (
        <section className="grid gap-3 xl:grid-cols-2">
            <Card>
                <CardHeader>
                    <h2 className="text-sm font-semibold">{t("Timers")}</h2>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                    <DetailLine
                        icon={<Clock3 className="h-4 w-4" aria-hidden="true" />}
                        label={t("Next timer")}
                        value={
                            nextTimer
                                ? `${nextTimer.unit || "--"} · ${nextTimer.next_elapse_local || "--"}`
                                : t("No active timer")
                        }
                    />
                    <DetailLine
                        icon={
                            status?.timers?.problem_count ? (
                                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                            ) : (
                                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                            )
                        }
                        label={t("Timer problems")}
                        value={formatNumber(status?.timers?.problem_count ?? 0)}
                    />
                    <DetailLine
                        icon={
                            problemServices.length ? (
                                <XCircle className="h-4 w-4" aria-hidden="true" />
                            ) : (
                                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                            )
                        }
                        label={t("Service problems")}
                        value={
                            problemServices.length
                                ? problemServices
                                      .map((row) => String(row.unit || "unknown"))
                                      .join(", ")
                                : formatNumber(0)
                        }
                    />
                </CardContent>
            </Card>
            <Card>
                <CardHeader>
                    <h2 className="text-sm font-semibold">
                        {t("Runtime Guards")}
                    </h2>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                    <DetailLine
                        icon={<Database className="h-4 w-4" aria-hidden="true" />}
                        label={t("Provider API calls")}
                        value={status?.endpoint?.provider_api_called ? t("yes") : t("no")}
                    />
                    <DetailLine
                        icon={<Server className="h-4 w-4" aria-hidden="true" />}
                        label={t("OSRM starts")}
                        value={status?.endpoint?.osrm_started ? t("yes") : t("no")}
                    />
                    <DetailLine
                        icon={<Gauge className="h-4 w-4" aria-hidden="true" />}
                        label={t("Running OSRM regions")}
                        value={
                            status?.osrm_manager?.running_regions?.length
                                ? status.osrm_manager.running_regions.join(", ")
                                : formatNumber(0)
                        }
                    />
                </CardContent>
            </Card>
        </section>
    );
}

function OpsMetric({
    label,
    value,
    detail,
    icon,
    tone,
}: {
    label: string;
    value: string;
    detail: string;
    icon: ReactNode;
    tone: BadgeTone;
}) {
    return (
        <Card>
            <CardContent className="flex min-h-32 flex-col justify-between">
                <div className="flex items-center justify-between gap-3 text-muted-foreground">
                    <span className="text-xs font-medium uppercase">
                        {label}
                    </span>
                    {icon}
                </div>
                <div>
                    <Badge tone={tone}>{value}</Badge>
                    <div className="mt-2 line-clamp-2 text-xs text-muted-foreground">
                        {detail}
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2">
            <div className="text-[11px] font-medium uppercase text-muted-foreground">
                {label}
            </div>
            <div className="mt-1 text-sm font-semibold">{value}</div>
        </div>
    );
}

function DetailRow({ label, value }: { label: string; value: string }) {
    return (
        <div className="flex items-start justify-between gap-3">
            <span>{label}</span>
            <span className="text-right font-medium text-foreground">{value}</span>
        </div>
    );
}

function DetailLine({
    icon,
    label,
    value,
}: {
    icon: ReactNode;
    label: string;
    value: string;
}) {
    return (
        <div className="flex items-center justify-between gap-4 rounded-md border border-border px-3 py-2">
            <div className="flex min-w-0 items-center gap-2 text-muted-foreground">
                {icon}
                <span>{label}</span>
            </div>
            <span className="truncate text-right font-medium text-foreground">
                {value}
            </span>
        </div>
    );
}

function LoadingPanel({ label }: { label: string }) {
    return (
        <Card>
            <CardContent className="flex min-h-44 items-center justify-center text-sm text-muted-foreground">
                {label}
            </CardContent>
        </Card>
    );
}

function statusTone(status?: string): BadgeTone {
    if (["ready", "healthy", "ok"].includes(String(status))) {
        return "success";
    }
    if (["waiting", "warning", "checking"].includes(String(status))) {
        return "warning";
    }
    if (["blocked", "error", "failed"].includes(String(status))) {
        return "danger";
    }
    return "neutral";
}

function formatApiBudget(status?: TrafficRolloutStatusResponse) {
    const total = status?.api_budget?.total_estimated_api_call_count;
    const max = status?.api_budget?.max_estimated_api_call_count;
    if (typeof total !== "number" && typeof max !== "number") {
        return "--";
    }
    return `${formatNumber(total ?? 0)} / ${formatNumber(max ?? 0)}`;
}

function formatOsrm(status?: TrafficRolloutStatusResponse) {
    if (status?.osrm_manager?.available === false) {
        return "offline";
    }
    return `${formatNumber(status?.osrm_manager?.running_region_count ?? 0)} running`;
}

function formatPercent(value?: number) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "--";
    return `${Math.round(value * 100)}%`;
}

function formatShortDate(value?: string) {
    if (!value) return "--";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function formatMode(value?: string) {
    return String(value || "--").replace(/_/g, " ");
}

function formatPeriod(value?: string) {
    return String(value || "--").replace(/_/g, " ");
}

function formatWarning(value: string, t: (key: string) => string) {
    if (value === "static_fallback") return t("static fallback");
    if (value.startsWith("missing_")) {
        return `${t("missing")} ${formatPeriod(value.replace("missing_", ""))}`;
    }
    if (value.startsWith("stale_")) {
        return `${t("stale")} ${formatPeriod(value.replace("stale_", ""))}`;
    }
    if (value.startsWith("no_geo_")) {
        return `${t("no geo")} ${formatPeriod(value.replace("no_geo_", ""))}`;
    }
    return value.replace(/_/g, " ");
}
