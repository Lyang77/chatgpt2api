export type ImageThinkingEffort = "" | "low" | "medium" | "high" | "extended";

export const IMAGE_THINKING_EFFORT_OPTIONS = [
  { value: "", label: "关闭" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
  { value: "extended", label: "扩展" },
] as const satisfies ReadonlyArray<{ value: ImageThinkingEffort; label: string }>;

const IMAGE_THINKING_EFFORTS = new Set<ImageThinkingEffort>(
  IMAGE_THINKING_EFFORT_OPTIONS.map((option) => option.value),
);

export function normalizeImageThinkingEffort(value: unknown): ImageThinkingEffort {
  if (value === undefined || value === null) {
    return "high";
  }
  const normalized = String(value).trim().toLowerCase() as ImageThinkingEffort;
  return IMAGE_THINKING_EFFORTS.has(normalized) ? normalized : "high";
}
