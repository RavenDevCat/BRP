export function formatDateTime(value: unknown): string {
  if (!value || typeof value !== "string") {
    return "Not recorded";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatNumber(value: unknown): string {
  const numericValue = toFiniteNumber(value);
  if (!Number.isFinite(numericValue)) {
    return "Not available";
  }
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(numericValue);
}

export function formatDistanceKmFromMeters(value: unknown): string {
  const numericValue = toFiniteNumber(value);
  if (!Number.isFinite(numericValue)) {
    return "Not available";
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(numericValue / 1000)} km`;
}

export function formatDurationMinFromSeconds(value: unknown): string {
  const numericValue = toFiniteNumber(value);
  if (!Number.isFinite(numericValue)) {
    return "Not available";
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(numericValue / 60)} min`;
}

export function formatRuntime(startedAt: unknown, finishedAt: unknown): string {
  if (!startedAt || typeof startedAt !== "string") {
    return "Not recorded";
  }
  const start = new Date(startedAt).getTime();
  const finish = typeof finishedAt === "string" && finishedAt ? new Date(finishedAt).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(finish) || finish < start) {
    return "Not recorded";
  }
  const totalSeconds = Math.round((finish - start) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 1) {
    return `${seconds} sec`;
  }
  if (minutes < 60) {
    return seconds ? `${minutes} min ${seconds} sec` : `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} hr ${remainingMinutes} min` : `${hours} hr`;
}

export function formatPercent(value: unknown, scale = 1): string {
  const numericValue = toFiniteNumber(value);
  if (!Number.isFinite(numericValue)) {
    return "Not available";
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(numericValue * scale)}%`;
}

export function toTitle(value: string): string {
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function toFiniteNumber(value: unknown): number {
  if (value === null || value === undefined || value === "") {
    return Number.NaN;
  }
  return typeof value === "number" ? value : Number(value);
}
