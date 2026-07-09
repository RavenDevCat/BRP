import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ListChecks, MapPin, PlusCircle, ShieldCheck, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { InteractiveRouteMap } from "@/features/results/interactive-route-map";
import {
  getRouteInsertAdvisorCapabilities,
  requestRouteInsertAdvisorProposals,
  type JobMapData,
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

function text(value: unknown): string {
  return String(value ?? "").trim();
}

function checkLabel(value: unknown, t: (key: string) => string): string {
  const key = text(value);
  if (key === "capacity") return t("Capacity limit");
  if (key === "stop_limit") return t("Stop limit");
  if (key === "osrm_refine_failed") return t("Road estimate unavailable");
  return key;
}

function proposalNewStopAddress(proposal: Record<string, unknown>): string {
  const newStop = proposal.new_stop as Record<string, unknown> | undefined;
  return text(newStop?.address);
}

function proposalPosition(proposal: Record<string, unknown>, t: (key: string) => string): string {
  return text(proposal.type) === "walk_to_stop"
    ? `${t("Walk to")} ${String(proposal.target_stop_address || "-")}`
    : `${String(proposal.insert_after_address || "-")} -> ${String(proposal.insert_before_address || "-")}`;
}

function proposalImpact(proposal: Record<string, unknown>): string {
  return text(proposal.type) === "walk_to_stop"
    ? meters(proposal.walking_distance_m)
    : `${minutes(proposal.delta_duration_s)} / ${meters(proposal.delta_distance_m)}`;
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

export function RouteInsertAdvisorPage() {
  const t = useT();
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
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
  const sourceCount = capabilities?.supported_sources.length ?? 1;
  const checkCount = capabilities?.candidate_checks.length ?? 7;
  const result = proposalMutation.data as RouteInsertAdvisorProposalResponse | undefined;
  const canRun = Boolean(fileBase64 && addresses.trim() && !proposalMutation.isPending);

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
          detail={t("Upload the same current-plan workbook used by Route Audit.")}
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
              file_name: file?.name || "workbook.xlsx",
              file_base64: fileBase64,
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
            <div className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{t("Current-plan workbook")}</span>
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/60 px-4 py-6 text-center transition hover:border-primary/60 hover:bg-muted">
                <Upload className="mb-3 h-6 w-6 text-primary" aria-hidden="true" />
                <span className="text-sm font-medium">{file?.name || t("Select workbook")}</span>
                <span className="mt-1 text-xs text-muted-foreground">{t("Upload the current-plan workbook used for Route Audit.")}</span>
                <input
                  className="sr-only"
                  type="file"
                  accept=".xlsx,.xlsm"
                  onChange={async (event) => {
                    const nextFile = event.target.files?.[0] ?? null;
                    event.currentTarget.value = "";
                    setFile(nextFile);
                    setFileBase64("");
                    setFileError("");
                    proposalMutation.reset();
                    if (!nextFile) return;
                    try {
                      setFileBase64(await fileToBase64(nextFile));
                    } catch (error) {
                      setFileError(error instanceof Error ? error.message : t("Workbook could not be read."));
                    }
                  }}
                />
              </label>
              {fileError ? <p className="text-xs text-warning-foreground">{fileError}</p> : null}
            </div>
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
  const warnings = Array.isArray(result.geocode_warnings) ? result.geocode_warnings : [];
  const summary = result.summary ?? {};
  const mapData = result.map_data as JobMapData | undefined;
  const bestProposals = Array.from(
    proposals
      .reduce((bestByAddress, proposal) => {
        const address = proposalNewStopAddress(proposal) || `${t("New stop")} ${bestByAddress.size + 1}`;
        if (!bestByAddress.has(address)) bestByAddress.set(address, proposal);
        return bestByAddress;
      }, new Map<string, Record<string, unknown>>())
      .values(),
  );
  return (
    <section className="rounded-md border border-border bg-surface shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">{t("Proposal results")}</h2>
        <Badge tone={proposals.length ? "success" : "warning"}>
          {proposals.length ? `${proposals.length}` : t("No candidates")}
        </Badge>
      </div>
      <div className="overflow-auto p-4">
        <div className="mb-4 grid gap-3 md:grid-cols-3">
          <Metric label={t("Resolved stops")} value={String(summary.new_stop_count ?? 0)} />
          <Metric label={t("Returned proposals")} value={String(summary.proposal_count ?? proposals.length)} />
          <Metric label={t("Road-refined candidates")} value={String(summary.refined_candidate_count ?? 0)} />
          <Metric
            label={t("Geocode warnings")}
            value={String(summary.geocode_warning_count ?? warnings.length)}
            tone={warnings.length ? "warning" : "success"}
          />
        </div>
        {warnings.length ? (
          <div className="mb-4 rounded-md border border-warning bg-warning/10 p-3 text-sm text-warning-foreground">
            <div className="font-semibold">{t("Some addresses could not be resolved.")}</div>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {warnings.map((warning, index) => (
                <li key={`${text(warning.address)}-${index}`}>
                  {text(warning.address) || `${t("Row")} ${index + 1}`}: {text(warning.reason)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {bestProposals.length ? (
          <div className="mb-4 space-y-3">
            <div>
              <h3 className="text-base font-semibold">{t("Best insertion recommendation")}</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {t("Use this first option when you want the least-disruptive insertion for each new address.")}
              </p>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {bestProposals.map((proposal, index) => {
                const feasible = Boolean(proposal.feasible);
                const checks = Array.isArray(proposal.warnings) ? proposal.warnings.map(text).filter(Boolean) : [];
                return (
                  <article key={`${proposalNewStopAddress(proposal)}-${index}`} className="rounded-md border border-primary/30 bg-primary/5 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{t("New stop")}</div>
                        <div className="mt-1 font-semibold">{proposalNewStopAddress(proposal) || "-"}</div>
                      </div>
                      <Badge tone={feasible ? "success" : "warning"}>
                        {feasible ? t("Feasible") : t("Needs review")}
                      </Badge>
                    </div>
                    <div className="mt-4 text-lg font-semibold">
                      {t("Insert into")} {String(proposal.route_id || "-")}
                    </div>
                    <div className="mt-2 text-sm text-muted-foreground">{proposalPosition(proposal, t)}</div>
                    <div className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
                      <Metric label={t("Added impact")} value={proposalImpact(proposal)} />
                      <Metric
                        label={t("Capacity")}
                        value={`${String(proposal.capacity_after || "-")}${proposal.capacity_limit ? ` / ${String(proposal.capacity_limit)}` : ""}`}
                      />
                      <Metric
                        label={t("Stop count")}
                        value={`${String(proposal.stop_count_after || "-")}${proposal.stop_limit ? ` / ${String(proposal.stop_limit)}` : ""}`}
                      />
                    </div>
                    <p className="mt-3 text-sm text-muted-foreground">
                      {checks.length ? checks.map((item) => checkLabel(item, t)).join(", ") : t("No issues")}
                    </p>
                  </article>
                );
              })}
            </div>
          </div>
        ) : null}
        {mapData ? (
          <div className="mb-4 overflow-hidden rounded-md border border-border">
            <div className="border-b border-border px-4 py-3 text-sm font-semibold">{t("Route maps")}</div>
            <div className="h-[520px]">
              <InteractiveRouteMap data={mapData} focusKey={`insert-${mapData.job_id}-${mapData.scenario_key}`} />
            </div>
          </div>
        ) : null}
        {proposals.length ? (
          <div className="overflow-auto rounded-md border border-border">
            <div className="border-b border-border px-4 py-3 text-sm font-semibold">{t("Candidate details")}</div>
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-border text-xs uppercase tracking-normal text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">{t("Type")}</th>
                  <th className="px-3 py-2">{t("New stop")}</th>
                  <th className="px-3 py-2">{t("Route")}</th>
                  <th className="px-3 py-2">{t("Position")}</th>
                  <th className="px-3 py-2">{t("Impact")}</th>
                  <th className="px-3 py-2">{t("Capacity")}</th>
                  <th className="px-3 py-2">{t("Stop count")}</th>
                  <th className="px-3 py-2">{t("Status")}</th>
                  <th className="px-3 py-2">{t("Checks")}</th>
                </tr>
              </thead>
              <tbody>
                {proposals.map((proposal, index) => {
                  const type = String(proposal.type || "");
                  const isWalk = type === "walk_to_stop";
                  const feasible = Boolean(proposal.feasible);
                  const checks = Array.isArray(proposal.warnings) ? proposal.warnings.map(text).filter(Boolean) : [];
                  return (
                    <tr key={`${type}-${proposal.route_id}-${index}`} className="border-b border-border last:border-0">
                      <td className="px-3 py-3">{isWalk ? t("Walk to stop") : t("Insert stop")}</td>
                      <td className="max-w-56 px-3 py-3 text-muted-foreground">{proposalNewStopAddress(proposal) || "-"}</td>
                      <td className="px-3 py-3 font-medium">{String(proposal.route_id || "-")}</td>
                      <td className="px-3 py-3 text-muted-foreground">{proposalPosition(proposal, t)}</td>
                      <td className="px-3 py-3">
                        <div>{proposalImpact(proposal)}</div>
                        <div className="text-xs text-muted-foreground">
                          {isWalk
                            ? t("Walking")
                            : proposal.refined
                              ? t("Road estimate")
                              : t("Direct estimate")}
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        {String(proposal.capacity_after || "-")}
                        {proposal.capacity_limit ? ` / ${String(proposal.capacity_limit)}` : ""}
                      </td>
                      <td className="px-3 py-3">
                        {String(proposal.stop_count_after || "-")}
                        {proposal.stop_limit ? ` / ${String(proposal.stop_limit)}` : ""}
                      </td>
                      <td className="px-3 py-3">
                        <Badge tone={feasible ? "success" : "warning"}>
                          {feasible ? t("Feasible") : t("Needs review")}
                        </Badge>
                      </td>
                      <td className="px-3 py-3 text-muted-foreground">
                        {checks.length ? checks.map((item) => checkLabel(item, t)).join(", ") : t("No issues")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("No insertion candidates were returned.")}</p>
        )}
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  tone = "info",
}: {
  label: string;
  value: string;
  tone?: "success" | "warning" | "info";
}) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="text-xs font-semibold uppercase tracking-normal text-muted-foreground">{label}</div>
      <Badge tone={tone}>{value}</Badge>
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
