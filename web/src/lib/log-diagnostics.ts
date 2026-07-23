export type LogDiagnosticRow = {
  key: string;
  label: string;
  value: string;
  multiline?: boolean;
};

const REQUEST_META_FIELDS = [
  ["mode", "模式"],
  ["size", "尺寸"],
  ["quality", "质量"],
  ["n", "数量"],
  ["output_format", "输出格式"],
  ["response_format", "响应格式"],
  ["reference_image_count", "参考图数量"],
  ["mask_image_count", "Mask 数量"],
  ["client_task_id", "客户端任务 ID"],
  ["stream", "流式"],
  ["message_count", "消息数量"],
  ["input_item_count", "输入项数量"],
  ["role_counts", "消息角色"],
  ["tool_count", "工具数量"],
  ["image_input_count", "图片输入数量"],
  ["tool_choice_type", "工具选择"],
  ["response_format_type", "文本响应格式"],
  ["reasoning_effort", "推理强度"],
  ["max_tokens", "最大 Token 数"],
  ["max_completion_tokens", "最大完成 Token 数"],
  ["max_output_tokens", "最大输出 Token 数"],
  ["temperature", "Temperature"],
  ["top_p", "Top P"],
  ["store", "存储响应"],
  ["modalities", "模态"],
  ["prompt_chars", "Prompt 字符数"],
  ["input_chars", "输入字符数"],
  ["system_chars", "系统提示字符数"],
] as const;

const EXECUTION_FIELDS = [
  ["requested_model", "请求模型"],
  ["effective_model", "实际模型"],
  ["fallback_reason", "回退原因"],
  ["stage", "执行阶段"],
  ["retry_count", "重试次数"],
  ["queue_wait_ms", "排队耗时（ms）"],
  ["batch_id", "批次 ID"],
  ["image_index", "图片序号"],
  ["image_total", "图片总数"],
  ["conversation_id", "会话 ID"],
  ["cache_hit", "缓存命中"],
  ["actual_image_count", "实际返回数量"],
  ["completion_reason", "完成原因"],
  ["error", "错误"],
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatValue(key: string, value: unknown): string | null {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return value.trim() ? value : null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (Array.isArray(value)) {
    const items = value.filter((item) => typeof item === "string" || typeof item === "number");
    return items.length ? items.join("，") : null;
  }
  if (key === "role_counts" && isRecord(value)) {
    const counts = Object.entries(value)
      .filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]))
      .map(([role, count]) => `${role}: ${count}`);
    return counts.length ? counts.join("，") : null;
  }
  return null;
}

function rowsFromFields(
  source: Record<string, unknown>,
  fields: readonly (readonly [string, string])[],
): LogDiagnosticRow[] {
  return fields.flatMap(([key, label]) => {
    const value = formatValue(key, source[key]);
    if (value === null) return [];
    return [{ key, label, value, ...(key === "error" ? { multiline: true } : {}) }];
  });
}

export function getRequestMetaRows(detail: Record<string, unknown> | undefined): LogDiagnosticRow[] {
  const requestMeta = detail?.request_meta;
  return isRecord(requestMeta) ? rowsFromFields(requestMeta, REQUEST_META_FIELDS) : [];
}

export function getExecutionDiagnosticRows(detail: Record<string, unknown> | undefined): LogDiagnosticRow[] {
  return detail ? rowsFromFields(detail, EXECUTION_FIELDS) : [];
}
