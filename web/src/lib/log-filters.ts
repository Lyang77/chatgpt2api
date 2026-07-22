export type LogFilters = {
  type: "call" | "account";
  startDate: string;
  endDate: string;
  keyName: string;
  accountEmail: string;
  status: string;
  summary: string;
  model: string;
  endpoint: string;
  batchId: string;
};

export function readLogFilters(searchParams: URLSearchParams): LogFilters {
  const type = searchParams.get("type");
  return {
    type: type === "account" ? "account" : "call",
    startDate: searchParams.get("start_date") || "",
    endDate: searchParams.get("end_date") || "",
    keyName: searchParams.get("key_name") || "",
    accountEmail: searchParams.get("account_email") || "",
    status: searchParams.get("status") || "",
    summary: searchParams.get("summary") || "",
    model: searchParams.get("model") || "",
    endpoint: searchParams.get("endpoint") || "",
    batchId: searchParams.get("batch_id") || "",
  };
}

export function writeLogFilters(filters: LogFilters) {
  const searchParams = new URLSearchParams();
  const entries = [
    ["type", filters.type === "call" ? "" : filters.type],
    ["start_date", filters.startDate],
    ["end_date", filters.endDate],
    ["key_name", filters.keyName],
    ["account_email", filters.accountEmail],
    ["status", filters.status],
    ["summary", filters.summary],
    ["model", filters.model],
    ["endpoint", filters.endpoint],
    ["batch_id", filters.batchId],
  ] as const;

  entries.forEach(([key, value]) => {
    if (value) searchParams.set(key, value);
  });
  return searchParams.toString();
}
