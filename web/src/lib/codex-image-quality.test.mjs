import assert from "node:assert/strict";
import test from "node:test";

import {
  CODEX_IMAGE_QUALITY_OPTIONS,
  normalizeCodexImageQuality,
} from "./codex-image-quality.ts";

test("normalizes Codex image quality", () => {
  assert.equal(normalizeCodexImageQuality(undefined), "auto");
  assert.equal(normalizeCodexImageQuality(null), "auto");
  assert.equal(normalizeCodexImageQuality(" HIGH "), "high");
  assert.equal(normalizeCodexImageQuality("medium"), "medium");
  assert.equal(normalizeCodexImageQuality("unexpected"), "auto");
});

test("exposes all Codex image quality options", () => {
  assert.deepEqual(
    CODEX_IMAGE_QUALITY_OPTIONS.map((option) => [option.value, option.label]),
    [
      ["auto", "自动"],
      ["low", "低"],
      ["medium", "中"],
      ["high", "高"],
    ],
  );
});
