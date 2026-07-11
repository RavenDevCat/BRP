import type { ReactNode } from "react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
    CheckCircle2,
    Clipboard,
    MapPinned,
    RefreshCw,
    Server,
    ShieldCheck,
} from "lucide-react";
import {
    getCurrentUser,
    getHealth,
    getProviderStatus,
    type ProviderMarketStatus,
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
    const healthQuery = useQuery({
        queryKey: ["health"],
        queryFn: getHealth,
        enabled: isAdmin,
        staleTime: 30_000,
        refetchInterval: 60_000,
    });
    const statusQuery = useQuery({
        queryKey: ["provider-status", "admin-page"],
        queryFn: getProviderStatus,
        enabled: isAdmin,
        staleTime: 30_000,
        refetchInterval: 60_000,
    });
    const status = statusQuery.data;

    if (userQuery.isLoading) {
        return <LoadingPanel label={t("Loading admin status")} />;
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

    const healthStatus = healthQuery.data?.status || (healthQuery.isLoading ? "checking" : "unknown");
    return (
        <div className="space-y-6 pb-16 lg:pb-0">
            <section className="flex flex-col justify-between gap-4 xl:flex-row xl:items-end">
                <div>
                    <p className="text-sm font-medium text-primary">{t("Admin")}</p>
                    <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">
                        {t("Operations Status")}
                    </h1>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Badge tone={statusTone(status?.status)}>{t(status?.status || "checking")}</Badge>
                        <Badge tone="neutral">{t("Read-only")}</Badge>
                        <Badge tone={status?.provider_api_called ? "danger" : "success"}>
                            {t("Provider calls")}: {status?.provider_api_called ? t("yes") : t("no")}
                        </Badge>
                    </div>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button
                        type="button"
                        variant="secondary"
                        icon={<RefreshCw className={statusQuery.isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} aria-hidden="true" />}
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
                <EmptyState title={t("Provider status could not load")} detail={(statusQuery.error as Error).message} />
            ) : statusQuery.isLoading ? (
                <LoadingPanel label={t("Loading provider diagnostics")} />
            ) : (
                <>
                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                        <OpsMetric
                            label={t("Backend")}
                            value={t(healthStatus)}
                            detail={t("Service health")}
                            icon={<CheckCircle2 className="h-4 w-4" aria-hidden="true" />}
                            tone={statusTone(healthStatus)}
                        />
                        <OpsMetric
                            label={t("Direct providers")}
                            value={formatNumber(status?.market_count ?? 0)}
                            detail={status?.warning_markets?.length ? `${t("Review")}: ${status.warning_markets.join(", ")}` : t("All configured")}
                            icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
                            tone={status?.warning_markets?.length ? "warning" : "success"}
                        />
                        <OpsMetric
                            label={t("OSRM manager")}
                            value={`${formatNumber(status?.osrm_manager?.running_region_count ?? 0)} ${t("running")}`}
                            detail={`${formatNumber(status?.osrm_manager?.stale_lock_count ?? 0)} ${t("stale locks")}`}
                            icon={<Server className="h-4 w-4" aria-hidden="true" />}
                            tone={Number(status?.osrm_manager?.stale_lock_count ?? 0) > 0 ? "danger" : "success"}
                        />
                        <OpsMetric
                            label={t("Status mode")}
                            value={t("Read-only")}
                            detail={t("No provider calls or OSRM starts")}
                            icon={<MapPinned className="h-4 w-4" aria-hidden="true" />}
                            tone="info"
                        />
                    </div>
                    <ProviderMarkets markets={status?.markets || []} />
                </>
            )}
        </div>
    );
}

function ProviderMarkets({ markets }: { markets: ProviderMarketStatus[] }) {
    const t = useT();
    return (
        <section className="space-y-3">
            <h2 className="text-base font-semibold">{t("Traffic providers")}</h2>
            <div className="grid gap-3 xl:grid-cols-2">
                {markets.map((market) => (
                    <Card key={market.market}>
                        <CardHeader>
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <h3 className="text-sm font-semibold">{market.label}</h3>
                                    <p className="mt-1 text-xs text-muted-foreground">{market.market} · {market.provider}</p>
                                </div>
                                <Badge tone={statusTone(market.status)}>{t(market.status)}</Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="grid gap-2 text-sm sm:grid-cols-2">
                            <DetailRow label={t("Configured")} value={market.configured ? t("yes") : t("no")} />
                            <DetailRow label={t("Timing source")} value={market.timing_source} />
                        </CardContent>
                    </Card>
                ))}
            </div>
        </section>
    );
}

function OpsMetric({ label, value, detail, icon, tone }: {
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
                    <span className="text-xs font-medium uppercase">{label}</span>
                    {icon}
                </div>
                <div>
                    <Badge tone={tone}>{value}</Badge>
                    <div className="mt-2 line-clamp-2 text-xs text-muted-foreground">{detail}</div>
                </div>
            </CardContent>
        </Card>
    );
}

function DetailRow({ label, value }: { label: string; value: string }) {
    return (
        <div className="flex items-start justify-between gap-3 rounded-md border border-border px-3 py-2">
            <span className="text-muted-foreground">{label}</span>
            <span className="text-right font-medium text-foreground">{value}</span>
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
    if (["ready", "healthy", "ok"].includes(String(status))) return "success";
    if (["waiting", "warning", "checking"].includes(String(status))) return "warning";
    if (["blocked", "error", "failed"].includes(String(status))) return "danger";
    return "neutral";
}
