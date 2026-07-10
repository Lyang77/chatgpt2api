export type LogResultContent = {
  text: string;
  format: "text" | "json";
};

function textValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function responseTextFromContent(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (!item || typeof item !== "object") return "";
        const record = item as Record<string, unknown>;
        return textValue(record.text);
      })
      .filter(Boolean)
      .join("\n\n");
  }
  return "";
}

function responseTextFromOpenAiEnvelope(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  const record = value as Record<string, unknown>;
  const choices = record.choices;
  if (Array.isArray(choices)) {
    for (const choice of choices) {
      if (!choice || typeof choice !== "object") continue;
      const message = (choice as Record<string, unknown>).message;
      if (message && typeof message === "object") {
        const text = responseTextFromContent((message as Record<string, unknown>).content);
        if (text) return text;
      }
    }
  }

  const output = record.output;
  if (Array.isArray(output)) {
    for (const item of output) {
      if (!item || typeof item !== "object") continue;
      const text = responseTextFromContent((item as Record<string, unknown>).content);
      if (text) return text;
    }
  }

  return "";
}

export function extractLogResultContent(responseText: unknown): LogResultContent {
  const text = textValue(responseText);
  if (!text) return { text: "", format: "text" };

  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed === "string") return { text: parsed, format: "text" };
    const record = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
    const resultText = textValue(record?.result) || textValue(record?.answer) || responseTextFromOpenAiEnvelope(parsed);
    if (resultText) return { text: resultText, format: "text" };
    return { text: JSON.stringify(parsed, null, 2), format: "json" };
  } catch {
    return { text, format: "text" };
  }
}
