export const DEFAULT_ACCOUNT_IMAGE_MAX_INFLIGHT = 3;

export function normalizeAccountImageMaxInflight(value: unknown): number {
  const parsed = Math.floor(Number(value));
  return Number.isFinite(parsed) && parsed >= 1
    ? parsed
    : DEFAULT_ACCOUNT_IMAGE_MAX_INFLIGHT;
}

export function formatAccountInflightLimit(account: {
  image_inflight?: unknown;
  image_max_inflight?: unknown;
}) {
  const parsedCurrent = Math.floor(Number(account.image_inflight));
  const current = Number.isFinite(parsedCurrent) && parsedCurrent >= 0 ? parsedCurrent : 0;
  const maximum = normalizeAccountImageMaxInflight(account.image_max_inflight);
  return {
    current,
    maximum,
    label: `${current} / ${maximum}`,
  };
}
