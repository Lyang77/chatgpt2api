import { deepEqual, equal } from "node:assert/strict";

import { readLogFilters, writeLogFilters } from "../src/lib/log-filters.ts";

deepEqual(readLogFilters(new URLSearchParams()), {
  type: "call",
  startDate: "",
  endDate: "",
  keyName: "",
  accountEmail: "",
  status: "",
  summary: "",
  model: "",
  endpoint: "",
  batchId: "",
});

const restored = readLogFilters(new URLSearchParams(
  "type=account&start_date=2026-07-01&end_date=2026-07-22&key_name=prod-key&account_email=runner%40example.com&status=failed&summary=%E6%96%87%E7%94%9F%E5%9B%BE&model=gpt-5&endpoint=%2Fv1%2Fresponses&batch_id=batch-1",
));

deepEqual(restored, {
  type: "account",
  startDate: "2026-07-01",
  endDate: "2026-07-22",
  keyName: "prod-key",
  accountEmail: "runner@example.com",
  status: "failed",
  summary: "文生图",
  model: "gpt-5",
  endpoint: "/v1/responses",
  batchId: "batch-1",
});

equal(readLogFilters(new URLSearchParams("type=unknown")).type, "call");

const written = new URLSearchParams(writeLogFilters({
  ...restored,
  type: "call",
  model: "",
  batchId: "",
}));

equal(written.get("type"), null);
equal(written.get("model"), null);
equal(written.get("batch_id"), null);
equal(written.get("start_date"), "2026-07-01");
equal(written.get("account_email"), "runner@example.com");
equal(written.get("status"), "failed");

console.log("log filter tests passed");
