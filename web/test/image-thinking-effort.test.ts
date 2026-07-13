import { deepEqual, equal } from "node:assert/strict";

import {
  IMAGE_THINKING_EFFORT_OPTIONS,
  normalizeImageThinkingEffort,
} from "../src/lib/image-thinking-effort.ts";

equal(normalizeImageThinkingEffort(undefined), "high");
equal(normalizeImageThinkingEffort(null), "high");
equal(normalizeImageThinkingEffort(""), "");
equal(normalizeImageThinkingEffort("low"), "low");
equal(normalizeImageThinkingEffort("medium"), "medium");
equal(normalizeImageThinkingEffort("high"), "high");
equal(normalizeImageThinkingEffort("extended"), "extended");
equal(normalizeImageThinkingEffort(" HIGH "), "high");
equal(normalizeImageThinkingEffort("unexpected"), "high");

deepEqual(
  IMAGE_THINKING_EFFORT_OPTIONS.map((option) => option.value),
  ["", "low", "medium", "high", "extended"],
);

console.log("image thinking effort tests passed");
