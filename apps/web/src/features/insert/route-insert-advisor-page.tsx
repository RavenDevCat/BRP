import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ListChecks, MapPin, PlusCircle, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import {
  getRouteInsertAdvisorCapabilities,
  requestRouteInsertAdvisorProposals,
  type RouteInsertAdvisorProposalResponse,
} from "@/lib/api";
import { useT } from "@/lib/i18n/context";

function asNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function minutes(value: unknown): string {
  return `${Math.round(asNumber(value) / 60)} min`;
}

function meters(value: unknown): string {
  const raw = asNumber(value);
  return raw >= 1000 ? `${(raw / 1000).toFixed(1)} km` : `${Math.round(raw)} m`;
}

export function RouteInsertAdvisorPage() {
  const t = useT();
  const [auditJobId, setAuditJobId] = useState("");
  const [addresses, setAddresses] = useState("");
  const [country, setCountry] = useState("China");
  const [city, setCity] = useState("Shanghai");
  const [walkingThreshold, setWalkingThreshold] = useState("500");
  const [stopLimit, setStopLimit] = useState("");
  const capabilitiesQuery = useQuery({
    queryKey: ["route-insert-advisor-capabilities"],
    queryFn: getRouteInsertAdvisorCapabilities,
    staleTime: 60_000,
  });
  const proposalMutation = useMutation({
    mutationFn: requestRouteInsertAdvisorProposals,
  });
  const capabilities = capabilitiesQuery.data;
  const sourceCount = capabilities?.supported_sources.length ?? 3;
  const checkCount = capabilities?.candidate_checks.length ?? 7;
  const result = proposalMutation.data as RouteInsertAdvisorProposalResponse | undefined;
  const canRun = auditJobId.trim() && addresses.trim() && !proposalMutation.isPending;

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
          title={t("Advisor status")}
          value={capabilitiesQuery.isError ? t("Unavailable") : t("Ready")}
          tone={capabilitiesQuery.isError ? "warning" : "success"}
          detail={t("The original plan stays unchanged; the advisor only returns proposal candidates.")}
        />
        <InfoCard
          icon={<MapPin className="h-4 w-4" aria-hidden="true" />}
          title={t("Supported sources")}
          value={`${sourceCount}`}
          detail={t("This MVP uses Route Audit jobs first; other sources remain reserved.")}
        />
        <InfoCard
          icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
          title={t("Candidate checks")}
          value={`${checkCount}`}
          detail={t("Capacity, stop limit, insertion impact, and walking-to-stop checks are included.")}
        />
      </div>

      <section className="rounded-md border border-border bg-surface shadow-sm">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{t("New student insertion")}</h2>
        </div>
        <form
          className="grid gap-4 p-4 lg:grid-cols-[1fr_320px]"
          onSubmit={(event) => {
            event.preventDefault();
            proposalMutation.mutate({
              source: { audit_job_id: auditJobId.trim() },
              new_stops: addresses,
              constraints: {
                country,
                city,
                walking_threshold_m: Number(walkingThreshold) || 0,
                stop_limit: stopLimit ? Number(stopLimit) : null,
              },
            });
          }}
        >
          <div className="space-y-4">
            <Field label={t("Route Audit job ID")}>
              <input
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                value={auditJobId}
                onChange={(event) => setAuditJobId(event.target.value)}
                placeholder={t("Paste an existing audit job seed")}
              />
            </Field>
            <Field label={t("New student addresses")}>
              <textarea
                className="min-h-32 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                value={addresses}
                onChange={(event) => setAddresses(event.target.value)}
                placeholder={t("One address per line")}
              />
            </Field>
          </div>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("Country")}>
                <input
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                  value={country}
                  onChange={(event) => setCountry(event.target.value)}
                />
              </Field>
              <Field label={t("City")}>
                <input
                  className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                  value={city}
                  onChange={(event) => setCity(event.target.value)}
                />
              </Field>
            </div>
            <Field label={t("Walking threshold")}>
              <input
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                type="number"
                min="0"
                value={walkingThreshold}
                onChange={(event) => setWalkingThreshold(event.target.value)}
              />
            </Field>
            <Field label={t("Stop limit")}>
              <input
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
                type="number"
                min="0"
                value={stopLimit}
                onChange={(event) => setStopLimit(event.target.value)}
                placeholder={t("Use route default")}
              />
            </Field>
            <button className={buttonClassName("primary")} disabled={!canRun} type="submit">
              <PlusCircle className="h-4 w-4" aria-hidden="true" />
              {proposalMutation.isPending ? t("Scoring...") : t("Find proposals")}
            </button>
          </div>
        </form>
      </section>

      {proposalMutation.isError ? (
        <section className="rounded-md border border-warning bg-warning/10 p-4 text-sm text-warning-foreground">
          {proposalMutation.error instanceof Error ? proposalMutation.error.message : t("Proposal request failed.")}
        </section>
      ) : null}

      {result ? <ProposalResults result={result} /> : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-2">
      <span className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function ProposalResults({ result }: { result: RouteInsertAdvisorProposalResponse }) {
  const t = useT();
  const proposals = result.proposals ?? [];
  return (
    <section className="rounded-md border border-border bg-surface shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">{t("Proposal results")}</h2>
        <Badge tone={proposals.length ? "success" : "warning"}>
          {proposals.length ? `${proposals.length}` : t("No candidates")}
        </Badge>
      </div>
      <div className="overflow-auto p-4">
        {proposals.length ? (
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-normal text-muted-foreground">
              <tr>
                <th className="px-3 py-2">{t("Type")}</th>
                <th className="px-3 py-2">{t("Route")}</th>
                <th className="px-3 py-2">{t("Position")}</th>
                <th className="px-3 py-2">{t("Impact")}</th>
                <th className="px-3 py-2">{t("Capacity")}</th>
                <th className="px-3 py-2">{t("Status")}</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((proposal, index) => {
                const type = String(proposal.type || "");
                const isWalk = type === "walk_to_stop";
                const feasible = Boolean(proposal.feasible);
                return (
                  <tr key={`${type}-${proposal.route_id}-${index}`} className="border-b border-border last:border-0">
                    <td className="px-3 py-3">{isWalk ? t("Walk to stop") : t("Insert stop")}</td>
                    <td className="px-3 py-3 font-medium">{String(proposal.route_id || "-")}</td>
                    <td className="px-3 py-3 text-muted-foreground">
                      {isWalk
                        ? String(proposal.target_stop_address || "-")
                        : `${String(proposal.insert_after_address || "-")} -> ${String(proposal.insert_before_address || "-")}`}
                    </td>
                    <td className="px-3 py-3">
                      {isWalk
                        ? meters(proposal.walking_distance_m)
                        : `${minutes(proposal.delta_duration_s)} / ${meters(proposal.delta_distance_m)}`}
                    </td>
                    <td className="px-3 py-3">
                      {String(proposal.capacity_after || "-")}
                      {proposal.capacity_limit ? ` / ${String(proposal.capacity_limit)}` : ""}
                    </td>
                    <td className="px-3 py-3">
                      <Badge tone={feasible ? "success" : "warning"}>
                        {feasible ? t("Feasible") : t("Needs review")}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground">{t("No insertion candidates were returned.")}</p>
        )}
      </div>
    </section>
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
