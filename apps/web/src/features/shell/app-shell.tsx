import type { ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
    Bus,
    Gauge,
    History,
    LayoutDashboard,
    LogOut,
    RefreshCw,
    Ruler,
    ShieldCheck,
    UploadCloud,
} from "lucide-react";
import {
    getAuthConfig,
    getCurrentUser,
    getGoogleGeocodeUsage,
    getHealth,
    type GoogleGeocodeUsage,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n/context";
import { LANGUAGES } from "@/lib/i18n/types";

const primaryNavItems = [
    { to: "/", labelKey: "Dashboard", icon: LayoutDashboard },
    { to: "/new", labelKey: "New Audit", icon: UploadCloud },
    { to: "/jobs", labelKey: "Audit History", icon: History },
];

const sideToolNavItems = [
    { to: "/fleet", labelKey: "Fleet Planner", icon: Bus },
    { to: "/distance", labelKey: "Distance & Cost", icon: Ruler },
];

const mobileNavItems = [
    { to: "/", labelKey: "Home", icon: LayoutDashboard },
    { to: "/new", labelKey: "New Audit", icon: UploadCloud },
    { to: "/jobs", labelKey: "History", icon: History },
    { to: "/distance", labelKey: "Tools", icon: Ruler },
];

const productNameKey = "BRP: Bus Route Planner";
const appVersion = String(import.meta.env.VITE_APP_VERSION || "dev").trim();

export function AppShell({ children }: { children: ReactNode }) {
    const t = useT();
    const pathname = useRouterState({
        select: (state) => state.location.pathname,
    });
    const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });
    const userQuery = useQuery({ queryKey: ["me"], queryFn: getCurrentUser });
    const authQuery = useQuery({
        queryKey: ["auth-config"],
        queryFn: getAuthConfig,
    });
    const googleUsageQuery = useQuery({
        queryKey: ["google-geocode-usage"],
        queryFn: getGoogleGeocodeUsage,
        staleTime: 60_000,
    });
    const isWideWorkspace =
        pathname.startsWith("/jobs") ||
        pathname.startsWith("/fleet") ||
        pathname.startsWith("/distance");
    const googleUsage = googleUsageQuery.data;
    const googleUsagePct =
        googleUsage?.enabled &&
        googleUsage.limit &&
        googleUsage.used !== undefined
            ? Math.round((googleUsage.used / googleUsage.limit) * 100)
            : 0;
    const signOut = () => {
        const hasTesterSession =
            userQuery.data?.test_login ||
            window.localStorage.getItem("brp_test_login") === "1";
        if (hasTesterSession) {
            window.localStorage.removeItem("brp_test_login");
            window.location.assign(resolveAuthUrl("/api/auth/test-logout"));
            return;
        }
        window.location.assign(
            resolveAuthUrl(
                authQuery.data?.logout_url || userQuery.data?.auth?.logout_url,
            ),
        );
    };

    return (
        <div className="min-h-screen bg-background">
            <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-border bg-surface lg:flex lg:flex-col">
                <div className="flex h-16 items-center gap-3 border-b border-border px-5">
                    <img
                        className="h-9 w-9 rounded-md"
                        src="/bus-front.svg"
                        alt=""
                        aria-hidden="true"
                    />
                    <div>
                        <div className="text-sm font-semibold">
                            {t(productNameKey)}
                        </div>
                        <div className="text-xs text-muted-foreground">
                            {t("Planning console")}
                        </div>
                    </div>
                </div>

                <nav className="flex-1 space-y-5 px-3 py-4">
                    <NavGroup
                        title={t("Route Audit")}
                        items={primaryNavItems}
                        pathname={pathname}
                    />
                    <NavGroup
                        title={t("Side Tools")}
                        items={sideToolNavItems}
                        pathname={pathname}
                    />
                </nav>

                <div className="space-y-3 border-t border-border p-4">
                    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                        <span className="font-medium">{t("Version")}</span>
                        <span className="font-mono text-[11px] text-foreground/70">
                            {appVersion}
                        </span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                        <span className="text-xs font-medium text-muted-foreground">
                            {t("Backend")}
                        </span>
                        <Badge
                            tone={
                                healthQuery.data?.status === "ok"
                                    ? "success"
                                    : "warning"
                            }
                        >
                            {t(healthQuery.data?.status || "checking")}
                        </Badge>
                    </div>
                    <div className="flex items-start gap-2 text-xs text-muted-foreground">
                        <ShieldCheck
                            className="mt-0.5 h-3.5 w-3.5 flex-none text-primary"
                            aria-hidden="true"
                        />
                        <span className="break-all">
                            {formatUserLabel(
                                userQuery.data?.email,
                                userQuery.data?.is_admin,
                                t,
                            )}
                        </span>
                    </div>
                    {authQuery.data ? (
                        <div className="text-xs text-muted-foreground">
                            {authQuery.data.display_name}
                            {!authQuery.data.sso_ready
                                ? " · setup pending"
                                : ""}
                        </div>
                    ) : null}
                    <LanguageSwitcher />
                    <Button
                        type="button"
                        variant="secondary"
                        className="w-full"
                        icon={<LogOut className="h-4 w-4" aria-hidden="true" />}
                        onClick={signOut}
                    >
                        {t("Sign out")}
                    </Button>
                </div>
            </aside>

            <div className="lg:pl-64">
                <header className="sticky top-0 z-10 flex min-h-16 items-center justify-between gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur lg:px-6">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2 lg:hidden">
                            <img
                                className="h-6 w-6 rounded"
                                src="/bus-front.svg"
                                alt=""
                                aria-hidden="true"
                            />
                            <span className="text-sm font-semibold">
                                {t(productNameKey)}
                            </span>
                        </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                        {googleUsage?.enabled ? (
                            <GoogleUsagePill
                                usage={googleUsage}
                                percent={googleUsagePct}
                            />
                        ) : null}
                        <Button
                            type="button"
                            variant="secondary"
                            icon={
                                <RefreshCw
                                    className="h-4 w-4"
                                    aria-hidden="true"
                                />
                            }
                            onClick={() => {
                                void healthQuery.refetch();
                                void userQuery.refetch();
                                void authQuery.refetch();
                                void googleUsageQuery.refetch();
                            }}
                        >
                            {t("Refresh")}
                        </Button>
                    </div>
                </header>

                <main
                    className={cn(
                        "mx-auto w-full px-4 py-6 lg:px-6",
                        isWideWorkspace ? "max-w-none" : "max-w-7xl",
                    )}
                >
                    {children}
                </main>

                <nav className="fixed inset-x-0 bottom-0 grid grid-cols-4 border-t border-border bg-surface lg:hidden">
                    {mobileNavItems.map((item) => {
                        const Icon = item.icon;
                        const active =
                            pathname === item.to ||
                            (item.to !== "/" && pathname.startsWith(item.to));
                        return (
                            <Link
                                key={item.to}
                                to={item.to}
                                className={cn(
                                    "flex h-14 flex-col items-center justify-center gap-1 text-xs font-medium text-muted-foreground",
                                    active && "text-primary",
                                )}
                            >
                                <Icon className="h-4 w-4" aria-hidden="true" />
                                {t(item.labelKey)}
                            </Link>
                        );
                    })}
                </nav>
            </div>
        </div>
    );
}

function formatUserLabel(
    email?: string,
    isAdmin?: boolean,
    t?: (key: string) => string,
) {
    const userLabel = email || (t ? t("Resolving user") : "Resolving user");
    return `${userLabel}${isAdmin ? " · admin" : ""}`;
}

function resolveAuthUrl(url?: string) {
    const nextUrl = url || "/cdn-cgi/access/logout";
    if (/^https?:\/\//i.test(nextUrl)) {
        return nextUrl;
    }
    if (typeof window === "undefined") {
        return nextUrl;
    }
    return `${window.location.origin}${nextUrl.startsWith("/") ? nextUrl : `/${nextUrl}`}`;
}

function GoogleUsagePill({
    usage,
    percent,
}: {
    usage: GoogleGeocodeUsage;
    percent: number;
}) {
    return (
        <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-muted/50 px-2 text-xs text-muted-foreground">
            <Gauge
                className="h-3.5 w-3.5 flex-none text-primary"
                aria-hidden="true"
            />
            <span className="font-medium">Google</span>
            <span className="font-semibold text-foreground">
                {formatNumber(usage.used)} / {formatNumber(usage.limit)}
            </span>
            <span
                className={cn(
                    "hidden sm:inline",
                    percent >= 90 && "text-amber-700",
                )}
            >
                {percent}%
            </span>
        </div>
    );
}

function NavGroup({
    title,
    items,
    pathname,
}: {
    title: string;
    items: Array<{
        to: string;
        labelKey: string;
        icon: typeof LayoutDashboard;
    }>;
    pathname: string;
}) {
    const t = useT();
    return (
        <div className="space-y-1">
            <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-normal text-muted-foreground">
                {title}
            </div>
            {items.map((item) => {
                const Icon = item.icon;
                const active =
                    pathname === item.to ||
                    (item.to !== "/" && pathname.startsWith(item.to));
                return (
                    <Link
                        key={item.to}
                        to={item.to}
                        className={cn(
                            "flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-muted-foreground transition",
                            active && "bg-muted text-foreground",
                            !active &&
                                "hover:bg-muted/70 hover:text-foreground",
                        )}
                    >
                        <Icon className="h-4 w-4" aria-hidden="true" />
                        {t(item.labelKey)}
                    </Link>
                );
            })}
        </div>
    );
}

function LanguageSwitcher() {
    const { lang, setLang, switchEnabled } = useLanguage();

    if (!switchEnabled) return null;

    return (
        <div className="flex items-center gap-1 rounded-md border border-border bg-muted/50 p-0.5">
            {LANGUAGES.map(({ code, label }) => (
                <button
                    key={code}
                    type="button"
                    className={cn(
                        "flex-1 rounded px-2 py-1 text-xs font-medium transition",
                        lang === code
                            ? "bg-surface text-foreground shadow-sm"
                            : "text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => setLang(code)}
                >
                    {label}
                </button>
            ))}
        </div>
    );
}
