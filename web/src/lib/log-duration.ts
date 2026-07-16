type LogWithDuration = {
  detail?: Record<string, unknown>;
};

function parseStartedAt(startedAt: string) {
  const normalized = startedAt.replace(" ", "T");
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized);
  return new Date(hasTimezone ? normalized : `${normalized}Z`).getTime();
}

export function formatLogDuration(item: LogWithDuration, now: number) {
  if (item.detail?.status === "running") {
    const startedAt = item.detail?.started_at;
    if (typeof startedAt === "string") {
      const startedAtMs = parseStartedAt(startedAt);
      if (!Number.isNaN(startedAtMs)) {
        return `${(Math.max(0, now - startedAtMs) / 1000).toFixed(2)} s`;
      }
    }
  }
  const value = item.detail?.duration_ms;
  return typeof value === "number" ? `${(value / 1000).toFixed(2)} s` : "-";
}
