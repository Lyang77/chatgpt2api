import { equal } from "node:assert/strict";

import { formatLogDuration } from "../src/lib/log-duration.ts";

const runningLog = {
  id: "running-log",
  time: "2026-07-16 06:29:03",
  type: "call",
  detail: {
    status: "running",
    started_at: "2026-07-16 06:29:03",
  },
};

equal(
  formatLogDuration(runningLog, Date.parse("2026-07-16T06:39:03Z")),
  "600.00 s",
);

console.log("log duration tests passed");
