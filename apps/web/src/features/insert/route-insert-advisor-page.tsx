import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, MapPin, PlusCircle, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { getRouteInsertAdvisorCapabilities } from "@/lib/api";
import { useT } from "@/lib/i18n/context";

export function RouteInsertAdvisorPage() {
  const t = useT();
  const capabilitiesQuery = useQuery({
    queryKey: ["route-insert-advisor-capabilities"],
    queryFn: getRouteInsertAdvisorCapabilities,
    staleTime: 60_000,
  });
  const capabilities = capabilitiesQuery.data;
  const sourceCount = capabilities?.supported_sources.length ?? 3;
  const checkCount = capabilities?.candidate_checks.length ?? 7;

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-primary">{t("Planning Tools")}</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">
            {t("Route Insert Advisor")}
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            {t("Find the least disruptive way to add new students into an existing route plan.")}
          </p>
        </div>
        <Link to="/jobs" className={buttonClassName("secondary")}>
          <ListChecks className="h-4 w-4" aria-hidden="true" />
          {t("Audit History")}
        </Link>
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <InfoCard
          icon={<PlusCircle className="h-4 w-4" aria-hidden="true" />}
          title={t("MVP interface")}
          value={capabilitiesQuery.isError ? t("Unavailable") : t("Ready")}
          tone={capabilitiesQuery.isError ? "warning" : "success"}
          detail={t("The first version keeps the original plan unchanged and prepares proposal scoring.")}
        />
        <InfoCard
          icon={<MapPin className="h-4 w-4" aria-hidden="true" />}
          title={t("Supported sources")}
          value={`${sourceCount}`}
          detail={t("Audit jobs, Fleet Planner runs, and workbook uploads are reserved as input sources.")}
        />
        <InfoCard
          icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
          title={t("Candidate checks")}
          value={`${checkCount}`}
          detail={t("Capacity, stop limit, time window, rider impact, and walking-to-stop checks are part of the contract.")}
        />
      </div>

      <section className="rounded-md border border-border bg-surface shadow-sm">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{t("Standard API")}</h2>
        </div>
        <div className="space-y-4 p-4 text-sm text-muted-foreground">
          <div className="grid gap-3 md:grid-cols-2">
            <Endpoint label="GET" path="/api/route-insert-advisor/capabilities" />
            <Endpoint label="POST" path="/api/route-insert-advisor/proposals" />
          </div>
          <p>
            {t("Proposal scoring is intentionally disabled until the insertion algorithm is implemented.")}
          </p>
        </div>
      </section>
    </div>
  );
}

function InfoCard({
  icon,
  title,
  value,
  tone = "success",
  detail,
}: {
  icon: ReactNode;
  title: string;
  value: string;
  tone?: "success" | "warning" | "info";
  detail: string;
}) {
  return (
    <section className="rounded-md border border-border bg-surface p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-normal text-muted-foreground">
          {icon}
          {title}
        </div>
        <Badge tone={tone}>{value}</Badge>
      </div>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">{detail}</p>
    </section>
  );
}

function Endpoint({ label, path }: { label: string; path: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-2">
      <Badge tone="info">{label}</Badge>
      <code className="break-all text-xs text-foreground">{path}</code>
    </div>
  );
}
