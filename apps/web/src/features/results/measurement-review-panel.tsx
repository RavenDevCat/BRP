import { useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Loader2, Pause, Play, Plus, RefreshCw, SearchCheck, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { buttonClassName } from "@/components/ui/button-styles";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n/context";
import {
  controlMeasurementReview, createMeasurementReview, getCurrentUser, getMeasurementReview,
  getMeasurementReviewExportUrl, getMeasurementRisk, listMeasurementReviews,
  type JobRecord, type MeasurementReviewMode, type MeasurementReviewRecord, type SideMeasurementSource,
} from "@/lib/api";
import { measurementSourceKey, reviewMatchesSource, reviewActions, reviewBudget, reviewHasNativeResult, reviewIsActive } from "@/lib/measurement-review-state";

const fieldClass = "h-9 rounded-md border border-border bg-surface px-3 text-sm";
const statusLabels: Record<string, string> = {
  queued: "Queued", running: "Measuring", pausing: "Pausing", yielding: "Waiting for ordinary tasks",
  paused: "Paused", canceled: "Canceled", failed: "Failed", succeeded: "Correction ready", needs_review: "Incomplete measurement",
};
const riskLabels: Record<string, string> = {
  missing_unified_measurement: "Missing unified measurement", outdated_measurement_contract: "Outdated measurement contract",
  unresolved_measurement_quality: "Unresolved measurement quality", saved_quality_flags: "Saved measurement warnings",
  source_route_identity_missing: "Missing route identity", source_stop_coordinates_missing: "Missing stop coordinates",
  source_acceptance_inputs_missing: "Missing saved acceptance inputs",
};

type WorkspaceProps = {
  job?: JobRecord;
  source?: SideMeasurementSource | null;
  mode: Exclude<MeasurementReviewMode, "selected_routes">;
  children: (correction: MeasurementReviewRecord | null) => ReactNode;
};

export function MeasurementReviewWorkspace(props: WorkspaceProps) {
  return <ReviewWorkspace key={measurementSourceKey(props.source || props.job?.job_id || "")} {...props} />;
}

function ReviewWorkspace({ job, source, mode, children }: WorkspaceProps) {
  const t = useT();
  const { lang } = useLanguage();
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [budget, setBudget] = useState("500");
  const [confirmed, setConfirmed] = useState(false);
  const requestRef = useRef<{ key: string; budget: number } | null>(null);
  const reference = source || job?.job_id || "";
  const sourceKey = measurementSourceKey(reference);
  const enabled = Boolean(source?.run_id) || Boolean(job && ["succeeded", "failed"].includes(job.status) && job.result);
  const user = useQuery({ queryKey: ["me"], queryFn: getCurrentUser, staleTime: 60_000 });
  const listKey = ["measurement-reviews", sourceKey];
  const risks = useQuery({ queryKey: ["measurement-risk", sourceKey], queryFn: () => getMeasurementRisk(reference), enabled, staleTime: Infinity });
  const list = useQuery({ queryKey: listKey, queryFn: () => listMeasurementReviews(reference), enabled,
    refetchInterval: query => query.state.data?.reviews.some(reviewIsActive) ? 5000 : false });
  const detail = useQuery({ queryKey: [...listKey, selectedId], queryFn: () => getMeasurementReview(reference, selectedId),
    enabled: enabled && Boolean(selectedId), refetchInterval: query => reviewIsActive(query.state.data) ? 5000 : false });
  const rows = list.data?.reviews || [];
  const refresh = async () => { await client.invalidateQueries({ queryKey: listKey }); };
  const start = useMutation({
    mutationFn: (limit: number) => {
      if (!requestRef.current || requestRef.current.budget !== limit) requestRef.current = { key: crypto.randomUUID(), budget: limit };
      return createMeasurementReview(reference, { mode, provider_call_limit: limit,
        request_key: requestRef.current.key, confirm_provider_calls: true });
    },
    onSuccess: async row => { requestRef.current = null; setCreateOpen(false); setConfirmed(false); setSelectedId(row.review_id); await refresh(); },
  });
  const action = useMutation({
    mutationFn: ({ row, command }: { row: MeasurementReviewRecord; command: "pause" | "resume" | "cancel" }) =>
      controlMeasurementReview(reference, row.review_id, command),
    onSuccess: refresh,
  });
  const admin = user.data?.is_admin === true;
  const capability = risks.data?.full_review;
  const canStart = capability?.available === true && capability.mode === mode;
  const limit = reviewBudget(budget);
  const selected = detail.data?.review_id === selectedId && reviewMatchesSource(detail.data, reference) ? detail.data : undefined;
  const error = start.error || action.error || list.error || risks.error;
  const busy = start.isPending || action.isPending;
  const visible = enabled && (admin || rows.length > 0 || Boolean(risks.data?.routes.length));

  if (!visible) return <>{children(null)}</>;
  return <div className="min-w-0 space-y-4">
    <section className="min-w-0 border-y border-border bg-surface px-4 py-4" aria-label={t("Historical measurement review")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><SearchCheck className="h-4 w-4 text-primary" aria-hidden="true" />{t("Historical measurement review")}</h2>
        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" title={t("Refresh reviews")} aria-label={t("Refresh reviews")} disabled={list.isFetching} onClick={() => void refresh()} icon={<RefreshCw className="h-4 w-4" />} />
          {admin ? <Button type="button" variant="secondary" disabled={!canStart || busy} onClick={() => { setCreateOpen(value => !value); setConfirmed(false); }} icon={<Plus className="h-4 w-4" />}>{t("New correction")}</Button> : null}
        </div>
      </div>
      {risks.data?.routes.length ? <details className="mt-3 text-xs">
        <summary className="cursor-pointer font-medium text-amber-800">{formatNumber(risks.data.routes.length)} {t("routes need measurement review")}</summary>
        <ul className="mt-2 max-h-44 space-y-1 overflow-y-auto">
          {risks.data.routes.map(row => <li key={row.route_key} className="break-words"><strong>{row.route_id}</strong> · {[...new Set([...row.risk_reasons, ...row.input_issues])].map(reason => t(riskLabels[reason] || "Unresolved measurement quality")).join("; ")}</li>)}
        </ul>
      </details> : null}
      {admin && capability?.available === false ? <p className="mt-3 text-sm text-amber-800">{capability.reason || t("Full correction is unavailable for this saved input.")}</p> : null}
      {createOpen && admin ? <form className="mt-4 space-y-3 border-t border-border pt-4" onSubmit={event => { event.preventDefault(); if (canStart && confirmed && limit !== null && !busy) start.mutate(limit); }}>
        <div className="flex flex-wrap items-end gap-4">
          <div className="text-sm"><span className="text-muted-foreground">{t("Scope")}: </span>{t(mode === "full_audit" ? "All saved Audit plans" : mode === "full_fleet" ? "All saved Fleet plans" : mode === "full_insert" ? "All saved insert scenarios" : "Complete student time analysis")} · {formatNumber(capability?.scope_summary?.route_count)} {t("routes")}</div>
          <label className="flex flex-col gap-1 text-xs font-medium">{t("API call limit")}<input className={cn(fieldClass, "w-28")} type="number" min="1" max="500" step="1" required value={budget} onChange={event => { setBudget(event.target.value); setConfirmed(false); }} /></label>
        </div>
        <label className="flex items-start gap-2 text-sm leading-5"><input className="mt-1 h-4 w-4 flex-none accent-primary" type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{t("I approve sending this result's saved stop coordinates to AMap within the call limit. The original result is retained.")}</label>
        <Button type="submit" disabled={!canStart || !confirmed || limit === null || busy} icon={start.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}>{t("Start correction")}</Button>
      </form> : null}
      {error ? <p role="alert" className="mt-3 text-sm text-red-700">{(error as Error).message}</p> : null}
      {list.isLoading ? <p className="mt-3 flex items-center gap-2 text-sm"><Loader2 className="h-4 w-4 animate-spin" />{t("Loading reviews")}</p> : null}
      {rows.length ? <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[580px] text-left text-xs"><thead className="border-b border-border text-muted-foreground"><tr><th className="p-2">{t("Created")}</th><th className="p-2">{t("Status")}</th><th className="p-2">{t("API calls")}</th><th className="p-2">{t("Actions")}</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.review_id} className="border-b border-border">
            <td className="p-2"><button className="text-primary underline underline-offset-2" type="button" onClick={() => setSelectedId(row.review_id)}>{formatDateTime(row.created_at)}</button>{row.request.mode === "selected_routes" ? <span className="ml-2 text-amber-800">{t("Route diagnostics only")}</span> : null}</td>
            <td className="p-2">{t(statusLabels[row.status] || "Needs review")}</td><td className="p-2 tabular-nums">{row.api_calls} / {row.request.provider_call_limit}</td>
            <td className="p-2"><div className="flex gap-1">{admin ? reviewActions(row.status).map(command => <Button key={command} type="button" variant="secondary" disabled={busy} title={t(command === "pause" ? "Pause" : command === "resume" ? "Resume" : "Cancel review")} aria-label={t(command === "pause" ? "Pause" : command === "resume" ? "Resume" : "Cancel review")} icon={command === "pause" ? <Pause className="h-4 w-4" /> : command === "resume" ? <Play className="h-4 w-4" /> : <X className="h-4 w-4" />} onClick={() => action.mutate({ row, command })} />) : null}</div></td>
          </tr>)}</tbody></table>
      </div> : null}
      <label className="mt-4 flex flex-col gap-1 text-xs font-medium">{t("Displayed result")}
        <select className={cn(fieldClass, "w-full max-w-lg")} value={selectedId} onChange={event => setSelectedId(event.target.value)}>
          <option value="">{t("Original result")}</option>
          {rows.map(row => <option key={row.review_id} value={row.review_id}>{formatDateTime(row.created_at)} · {t(statusLabels[row.status] || "Needs review")}</option>)}
        </select>
      </label>
      {selectedId && detail.error ? <p role="alert" className="mt-3 text-sm text-red-700">{(detail.error as Error).message}</p> : null}
      {selected ? <div className="mt-4 space-y-3 border-t border-border pt-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2"><Badge tone={selected.status === "needs_review" || selected.status === "failed" ? "warning" : "neutral"}>{t(statusLabels[selected.status] || "Needs review")}</Badge><span className="text-xs text-muted-foreground">{t("Executed")}: {selected.started_at ? formatDateTime(selected.started_at) : t("Not started")}</span></div>
          {reviewHasNativeResult(selected, mode) ? <a href={getMeasurementReviewExportUrl(reference, selected.review_id, lang)} className={buttonClassName("secondary")}><Download className="h-4 w-4" />{t("Export correction Excel")}</a> : null}
        </div>
        {selected.error_code ? <p role="alert" className="text-sm text-red-700">{t("Correction stopped before completion")}: {selected.error_code}</p> : null}
        {selected.result?.status === "partial" ? <p className="text-sm text-amber-800">{t("Measurement is incomplete. Unresolved values are not evidence that a route fits its time window.")}</p> : null}
        {selected.request.mode !== "full_direct_school" ? <p className="text-xs text-muted-foreground">{t("Saved stop assignments and order are unchanged. The solver and minimum-fleet proof were not rerun.")}</p> : null}
        {selected.result?.routes?.length ? <ReviewComparison record={selected} /> : null}
      </div> : null}
    </section>
    {!selectedId ? children(null) : detail.isLoading ? <div role="status" className="flex items-center gap-2 p-4 text-sm"><Loader2 className="h-4 w-4 animate-spin" />{t("Loading corrected result")}</div>
      : selected && reviewHasNativeResult(selected, mode) ? children(selected)
      : <p className="p-4 text-sm text-muted-foreground">{t("No finalized complete-result snapshot is available for this selection.")}</p>}
  </div>;
}

function ReviewComparison({ record }: { record: MeasurementReviewRecord }) {
  const t = useT();
  const metric = (value: number | null | undefined, divisor = 1) => typeof value === "number" && Number.isFinite(value) ? formatNumber(Math.round(value / divisor * 10) / 10) : t("Not available");
  const conclusionLabels: Record<string, string> = {
    direct_over_limit_rider_count: "Students over the direct-trip limit",
    route_only_over_limit_rider_count: "Students over the shared-route limit only",
    additional_removal_rider_count: "Additional students suggested for removal",
    routes_over_window_final_count: "Routes still exceeding the window after removal",
    route_window_data_review_count: "Routes awaiting measurement review",
  };
  return <details className="text-xs">
    <summary className="cursor-pointer font-semibold">{t("Measurement differences")} · {record.result!.routes.length} {t("routes")}</summary>
    <p className="my-3 text-muted-foreground">{t("Fresh traffic sample; stop order unchanged. This is not a controlled before/after experiment.")}</p>
    {record.result?.conclusion_changes ? <div className="mb-4 overflow-x-auto"><table className="w-full min-w-[580px] text-left">
      <thead className="border-b border-border text-muted-foreground"><tr>{["Conclusion", "Original result", "Corrected result", "Change"].map(label => <th key={label} className="p-2">{t(label)}</th>)}</tr></thead>
      <tbody>{Object.entries(conclusionLabels).map(([key, label]) => {
        const change = record.result!.conclusion_changes![key];
        return change ? <tr key={key} className="border-b border-border"><td className="p-2">{t(label)}</td><td className="p-2">{metric(change.before)}</td><td className="p-2">{metric(change.after)}</td><td className="p-2">{metric(change.delta)}</td></tr> : null;
      })}</tbody>
    </table></div> : null}
    <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="border-b border-border text-muted-foreground"><tr>
      {["Plan / route", "Before min", "Corrected min", "Change min", "Before km", "Corrected km", "Measured at", "Status"].map(label => <th key={label} className="p-2">{t(label)}</th>)}
    </tr></thead><tbody>{record.result!.routes.map(row => <tr key={row.route_key} className="border-b border-border">
      <td className="p-2">{row.plan_key ? t(row.plan_key === "global_plan_result" ? "Optimized Plan" : row.plan_key === "route_preview_result" ? "Grouped Route Preview" : row.plan_key === "recommended" ? "Recommended plan" : "Alternative plan") : t(row.route_key.startsWith("time_constrained:") ? "Strict Plan" : row.route_key.startsWith("exception_preserving:") ? "Protected Plan" : "Current Plan")} · {row.route_id}{row.route_key.endsWith(":base") ? ` (${t("Original route")})` : row.route_key.endsWith(":selected") ? ` (${t("Selected insert plan")})` : ""}</td>
      <td className="p-2">{metric(row.before.total_duration_s, 60)}</td><td className="p-2">{metric(row.after.total_duration_s, 60)}</td><td className="p-2">{metric(row.delta.total_duration_s, 60)}</td>
      <td className="p-2">{metric(row.before.distance_m, 1000)}</td><td className="p-2">{metric(row.after.distance_m, 1000)}</td><td className="p-2">{row.after.captured_at ? formatDateTime(row.after.captured_at) : t("Not available")}</td><td className="p-2">{t(row.status === "verified" ? "Measured" : "Needs review")}</td>
    </tr>)}</tbody></table></div>
  </details>;
}
