export type ImageLogSummary = {
  actualCount: number;
  completionReason: string;
  warning: string;
};

export function getImageLogSummary(detail: Record<string, unknown> | null | undefined): ImageLogSummary | null {
  const rawCount = detail?.actual_image_count;
  if (typeof rawCount !== "number" || !Number.isFinite(rawCount) || rawCount < 0) {
    return null;
  }
  const completionReason = typeof detail?.completion_reason === "string" ? detail.completion_reason : "";
  return {
    actualCount: Math.floor(rawCount),
    completionReason,
    warning: completionReason === "timeout_with_results" ? "上游等待超时，已保留现有结果" : "",
  };
}
