import { deepEqual, equal } from "node:assert/strict";

import { extractLogResultContent } from "../src/lib/log-detail-content";

const resultOnly = extractLogResultContent(JSON.stringify({
  source: "internal-policy",
  result: "最终回答",
  usage: { total_tokens: 42 },
}));
deepEqual(resultOnly, { text: "最终回答", format: "text" });

const formattedJson = extractLogResultContent(JSON.stringify({
  source: "internal-policy",
  visualSystem: "展示方案",
}));
equal(formattedJson.format, "json");
equal(formattedJson.text, '{\n  "source": "internal-policy",\n  "visualSystem": "展示方案"\n}');

const contentWithMetadata = extractLogResultContent(JSON.stringify({
  content: "最终回答",
  style: "bullet",
}));
equal(contentWithMetadata.format, "json");
equal(contentWithMetadata.text, '{\n  "content": "最终回答",\n  "style": "bullet"\n}');

const plainText = extractLogResultContent("直接返回的模型结果");
deepEqual(plainText, { text: "直接返回的模型结果", format: "text" });

console.log("log detail content tests passed");
