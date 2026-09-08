import type { MeasurementReviewMode, MeasurementReviewRecord } from "./api";

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
  return false;
}

export function reviewBudget(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const number = Number(value);
  return Number.isInteger(number) && number >= 1 && number <= 500 ? number : null;
}
