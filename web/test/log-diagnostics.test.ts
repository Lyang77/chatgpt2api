import { deepEqual, equal } from "node:assert/strict";

import {
  getExecutionDiagnosticRows,
  getRequestMetaRows,
} from "../src/lib/log-diagnostics.ts";

deepEqual(
  getRequestMetaRows({
    request_meta: {
      size: "1536x1024",
      stream: true,
      role_counts: { user: 2, assistant: 1 },
    },
  }),
  [
    { key: "size", label: "尺寸", value: "1536x1024" },
    { key: "stream", label: "流式", value: "是" },
    { key: "role_counts", label: "消息角色", value: "user: 2，assistant: 1" },
  ],
);

deepEqual(getRequestMetaRows({}), []);
deepEqual(getRequestMetaRows({ request_meta: { api_key: "SECRET", tools: [{ secret: true }] } }), []);

const executionRows = getExecutionDiagnosticRows({
  requested_model: "gpt-image-2",
  effective_model: "codex-gpt-image-2",
  retry_count: 1,
  cache_hit: false,
  error: "upstream failed",
});
deepEqual(executionRows.slice(0, 4), [
  { key: "requested_model", label: "请求模型", value: "gpt-image-2" },
  { key: "effective_model", label: "实际模型", value: "codex-gpt-image-2" },
  { key: "retry_count", label: "重试次数", value: "1" },
  { key: "cache_hit", label: "缓存命中", value: "否" },
]);
equal(executionRows[4]?.key, "error");
equal(executionRows[4]?.multiline, true);
equal(executionRows[4]?.value, "upstream failed");

console.log("log diagnostics tests passed");
