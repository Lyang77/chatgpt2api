import type { ImageTask } from "./api";
import type { StoredImage } from "../store/image-conversations";

function resultIdentity(taskId: string, item: NonNullable<ImageTask["data"]>[number], index: number) {
  const source = item.url || item.b64_json || String(index);
  const compact = source.length > 96 ? `${source.slice(0, 64)}:${source.length}` : source;
  return `${taskId}:result:${encodeURIComponent(compact)}`;
}

export function mergeImageTaskResults(images: StoredImage[], task: ImageTask): StoredImage[] {
  const firstIndex = images.findIndex((image) => image.taskId === task.id);
  if (firstIndex < 0) {
    return images;
  }
  const seed = images[firstIndex];
  const before = images.filter((image, index) => index < firstIndex && image.taskId !== task.id);
  const after = images.filter((image, index) => index > firstIndex && image.taskId !== task.id);
  const results = (task.data || []).flatMap((item, index): StoredImage[] => {
    if (!item.b64_json && !item.url) {
      return [];
    }
    return [{
      ...seed,
      id: resultIdentity(task.id, item, index),
      taskId: task.id,
      status: "success",
      taskStatus: undefined,
      progress: undefined,
      b64_json: item.b64_json,
      url: item.url,
      revised_prompt: item.revised_prompt,
      error: undefined,
      durationMs: task.duration_ms,
    }];
  });

  if (task.status === "queued" || task.status === "running") {
    const elapsedSecs = task.status === "running" && typeof task.elapsed_secs === "number"
      ? task.elapsed_secs
      : undefined;
    results.push({
      ...seed,
      id: `${task.id}:loading`,
      taskId: task.id,
      status: "loading",
      taskStatus: task.status,
      progress: task.progress,
      b64_json: undefined,
      url: undefined,
      error: undefined,
      startTime: task.status === "running" ? seed.startTime || Date.now() : seed.startTime,
      elapsedSecs,
      elapsedUpdatedAt: elapsedSecs != null ? Date.now() : undefined,
      durationMs: task.duration_ms,
    });
  } else if (results.length === 0) {
    results.push({
      ...seed,
      id: `${task.id}:error`,
      taskId: task.id,
      status: "error",
      taskStatus: undefined,
      progress: undefined,
      b64_json: undefined,
      url: undefined,
      error: task.error || "未返回图片数据",
      durationMs: task.duration_ms,
    });
  }

  return [...before, ...results, ...after];
}
