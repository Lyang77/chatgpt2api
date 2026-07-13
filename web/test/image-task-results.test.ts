import { deepEqual, equal } from "node:assert/strict";

import { mergeImageTaskResults } from "../src/lib/image-task-results.ts";

const loading = [{
  id: "slot",
  taskId: "task-1",
  status: "loading" as const,
  startTime: 100,
}];

const running = mergeImageTaskResults(loading, {
  id: "task-1",
  status: "running",
  mode: "generate",
  created_at: "2026-07-13 00:00:00",
  updated_at: "2026-07-13 00:00:01",
  data: [{ url: "/one.png" }, { url: "/two.png" }],
  elapsed_secs: 4.5,
});

equal(running.filter((item) => item.status === "success").length, 2);
equal(running.filter((item) => item.status === "loading").length, 1);
equal(new Set(running.map((item) => item.id)).size, 3);
const runningSentinel = running.find((item) => item.status === "loading");
equal(runningSentinel?.startTime, 100);
equal(runningSentinel?.elapsedSecs, 4.5);
equal(typeof runningSentinel?.elapsedUpdatedAt, "number");

const completed = mergeImageTaskResults(running, {
  id: "task-1",
  status: "success",
  mode: "generate",
  created_at: "2026-07-13 00:00:00",
  updated_at: "2026-07-13 00:00:02",
  data: [{ url: "/one.png" }, { url: "/two.png" }, { url: "/three.png" }],
  actual_image_count: 3,
});

equal(completed.length, 3);
equal(completed.every((item) => item.status === "success"), true);
deepEqual(completed.map((item) => item.url), ["/one.png", "/two.png", "/three.png"]);
equal(completed[0].id, running[0].id);

console.log("image task result tests passed");
