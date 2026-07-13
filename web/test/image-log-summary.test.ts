import { deepEqual, equal } from "node:assert/strict";

import { getImageLogSummary } from "../src/lib/image-log-summary.ts";

deepEqual(getImageLogSummary({
  actual_image_count: 3,
  completion_reason: "upstream_completed",
}), {
  actualCount: 3,
  completionReason: "upstream_completed",
  warning: "",
});

deepEqual(getImageLogSummary({
  actual_image_count: 2,
  completion_reason: "timeout_with_results",
}), {
  actualCount: 2,
  completionReason: "timeout_with_results",
  warning: "上游等待超时，已保留现有结果",
});

equal(getImageLogSummary({ endpoint: "/v1/chat/completions" }), null);

console.log("image log summary tests passed");
