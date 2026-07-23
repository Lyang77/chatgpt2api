import { doesNotMatch, match } from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/app/logs/page.tsx", import.meta.url), "utf8");

match(source, /import \{[^}]*readLogFilters[^}]*writeLogFilters[^}]*\} from "@\/lib\/log-filters";/);
match(source, /import \{[^}]*getExecutionDiagnosticRows[^}]*getRequestMetaRows[^}]*\} from "@\/lib\/log-diagnostics";/);
match(source, /<TableHead>模型<\/TableHead>/);
match(source, /getDetailText\(item, "model"\)/);
doesNotMatch(source, /<TableHead>类型<\/TableHead>/);
doesNotMatch(source, /typeLabels\[item\.type\]/);

match(source, /className="flex-1 overflow-y-auto px-6 py-5"/);
doesNotMatch(source, /<pre className="[^"]*(?:max-h|overflow-auto)[^"]*"/);
match(source, /title="执行诊断"/);
match(source, /title="请求参数"/);

console.log("log page contract tests passed");
