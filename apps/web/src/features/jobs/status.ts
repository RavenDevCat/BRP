import type { JobSummary } from "@/lib/api";

export type JobStatusTone = "neutral" | "success" | "warning" | "danger" | "info";

export function getJobStatusTone(status: string): JobStatusTone {
  const normalized = status.trim().toLowerCase();
  if (normalized === "succeeded" || normalized === "completed") {
    return "success";
  }
  if (normalized === "queued" || normalized === "running") {
    return "info";
  }
  if (normalized === "failed" || normalized === "canceled") {
    return "danger";
  }
  return "neutral";
}

export function getJobName(job: JobSummary): string {
  const metadata = job.metadata || {};
  const name = metadata.job_name || metadata.name || metadata.workbook_name;
  return typeof name === "string" && name.trim() ? name.trim() : job.job_id;
}
