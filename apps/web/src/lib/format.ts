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
