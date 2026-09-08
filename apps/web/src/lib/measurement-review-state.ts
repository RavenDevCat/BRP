import type { MeasurementReviewMode, MeasurementReviewRecord, MeasurementSource } from "./api";

export function measurementSourceKey(source: MeasurementSource): string {
  return JSON.stringify(typeof source === "string" ? ["job", source] : ["side", source.tool_key, source.run_id]);
}

export function reviewMatchesSource(record: MeasurementReviewRecord, source: MeasurementSource): boolean {
  return typeof source === "string" ? record.source_job_id === source && !record.source_tool_key && !record.source_run_id
    : record.source_job_id === null && record.source_tool_key === source.tool_key && record.source_run_id === source.run_id;
}

export function reviewIsActive(record?: Pick<MeasurementReviewRecord, "status">): boolean {
  return Boolean(record && ["queued", "running", "pausing", "yielding"].includes(record.status));
}

export function reviewActions(status: string): Array<"pause" | "resume" | "cancel"> {
  if (status === "paused") return ["resume", "cancel"];
  if (status === "queued" || status === "running") return ["pause", "cancel"];
  if (status === "pausing" || status === "yielding") return ["cancel"];
  return [];
}

export function reviewHasNativeResult(record: MeasurementReviewRecord, mode: MeasurementReviewMode): boolean {
  if (!["succeeded", "needs_review"].includes(record.status) || record.request.mode !== mode) return false;
  if (!record.result || !["complete", "partial"].includes(record.result.status)) return false;
  if (mode === "full_audit") return record.result.scope === "full_audit_result"
    && Boolean(record.result.audit_result?.structured_results && record.result.audit_result?.current_plan_assessment);
  if (mode === "full_direct_school") return record.result?.scope === "full_direct_school_result" && Array.isArray(record.result.analysis_result?.stops);
  if (mode === "full_fleet") return record.result.scope === "full_fleet_result"
    && Boolean(record.result.native_result?.global_plan_result?.routes?.length || record.result.native_result?.route_preview_result?.routes?.length);
  if (mode === "full_insert") return record.result.scope === "full_insert_result"
    && Boolean(record.result.native_result?.route_insert_result?.scenarios?.length);
  return false;
}

export function reviewBudget(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const number = Number(value);
  return Number.isInteger(number) && number >= 1 && number <= 500 ? number : null;
}
