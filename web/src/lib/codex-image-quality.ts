export type CodexImageQuality = "auto" | "low" | "medium" | "high";

export const CODEX_IMAGE_QUALITY_OPTIONS = [
  { value: "auto", label: "自动" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
] as const satisfies ReadonlyArray<{ value: CodexImageQuality; label: string }>;

const CODEX_IMAGE_QUALITIES = new Set<CodexImageQuality>(
  CODEX_IMAGE_QUALITY_OPTIONS.map((option) => option.value),
);

export function normalizeCodexImageQuality(value: unknown): CodexImageQuality {
  const normalized = String(value ?? "auto").trim().toLowerCase() as CodexImageQuality;
  return CODEX_IMAGE_QUALITIES.has(normalized) ? normalized : "auto";
}
