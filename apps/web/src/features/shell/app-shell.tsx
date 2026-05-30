import type { ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { History, LayoutDashboard, RefreshCw, Ruler, ShieldCheck, UploadCloud, UsersRound } from "lucide-react";
import { getCurrentUser, getHealth } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

const primaryNavItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/new", label: "New Audit", icon: UploadCloud },
  { to: "/jobs", label: "Audit History", icon: History },
];

const sideToolNavItems = [
  { to: "/distance", label: "Distance & Cost", icon: Ruler },
  { to: "/fleet", label: "Fleet Planner", icon: UsersRound },
];

const mobileNavItems = [
  { to: "/", label: "Home", icon: LayoutDashboard },
  { to: "/new", label: "New Audit", icon: UploadCloud },
  { to: "/jobs", label: "History", icon: History },
  { to: "/distance", label: "Tools", icon: Ruler },
];

const productName = "BRP: Bus Route Planner";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const userQuery = useQuery({ queryKey: ["me"], queryFn: getCurrentUser });
  const isJobsWorkspace = pathname.startsWith("/jobs");

  return (
    <div className="min-h-screen bg-background">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-border bg-surface lg:flex lg:flex-col">
        <div className="flex h-16 items-center gap-3 border-b border-border px-5">
          <img className="h-9 w-9 rounded-md" src="/bus-front.svg" alt="" aria-hidden="true" />
          <div>
            <div className="text-sm font-semibold">{productName}</div>
            <div className="text-xs text-muted-foreground">Planning console</div>
          </div>
        </div>

        <nav className="flex-1 space-y-5 px-3 py-4">
          <NavGroup title="Route Audit" items={primaryNavItems} pathname={pathname} />
          <NavGroup title="Side Tools" items={sideToolNavItems} pathname={pathname} />
        </nav>

        <div className="space-y-3 border-t border-border p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-medium text-muted-foreground">Backend</span>
            <Badge tone={healthQuery.data?.status === "ok" ? "success" : "warning"}>
              {healthQuery.data?.status || "checking"}
            </Badge>
          </div>
          <div className="flex items-start gap-2 text-xs text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-none text-primary" aria-hidden="true" />
            <span className="break-all">
              {userQuery.data?.email || "Resolving user"}
              {userQuery.data?.is_admin ? " · admin" : ""}
            </span>
          </div>
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-10 flex min-h-16 items-center justify-between gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur lg:px-6">
          <div className="min-w-0">
            <div className="flex items-center gap-2 lg:hidden">
              <img className="h-6 w-6 rounded" src="/bus-front.svg" alt="" aria-hidden="true" />
              <span className="text-sm font-semibold">{productName}</span>
            </div>
          </div>
          <Button
            type="button"
            variant="secondary"
            icon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
            onClick={() => {
              void healthQuery.refetch();
              void userQuery.refetch();
            }}
          >
            Refresh
          </Button>
        </header>

        <main className={cn("mx-auto w-full px-4 py-6 lg:px-6", isJobsWorkspace ? "max-w-none" : "max-w-7xl")}>
          {children}
        </main>

        <nav className="fixed inset-x-0 bottom-0 grid grid-cols-4 border-t border-border bg-surface lg:hidden">
          {mobileNavItems.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.to || (item.to !== "/" && pathname.startsWith(item.to));
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
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}

function NavGroup({
  title,
  items,
  pathname,
}: {
  title: string;
  items: Array<{ to: string; label: string; icon: typeof LayoutDashboard }>;
  pathname: string;
}) {
  return (
    <div className="space-y-1">
      <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-normal text-muted-foreground">
        {title}
      </div>
      {items.map((item) => {
        const Icon = item.icon;
        const active = pathname === item.to || (item.to !== "/" && pathname.startsWith(item.to));
        return (
          <Link
            key={item.to}
            to={item.to}
            className={cn(
              "flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-muted-foreground transition",
              active && "bg-muted text-foreground",
              !active && "hover:bg-muted/70 hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            {item.label}
          </Link>
        );
      })}
    </div>
  );
}
