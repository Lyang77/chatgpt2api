import { deepEqual, equal } from "node:assert/strict";

import {
  formatAccountInflightLimit,
  normalizeAccountImageMaxInflight,
} from "../src/lib/account-inflight-limit.ts";

equal(normalizeAccountImageMaxInflight(undefined), 3);
equal(normalizeAccountImageMaxInflight(null), 3);
equal(normalizeAccountImageMaxInflight(0), 3);
equal(normalizeAccountImageMaxInflight("5"), 5);
equal(normalizeAccountImageMaxInflight(2.9), 2);

deepEqual(
  formatAccountInflightLimit({ image_inflight: 2, image_max_inflight: 5 }),
  { current: 2, maximum: 5, label: "2 / 5" },
);
deepEqual(
  formatAccountInflightLimit({}),
  { current: 0, maximum: 3, label: "0 / 3" },
);

console.log("account inflight limit tests passed");
