# 进行中调用日志实时耗时实施计划

> **给 agentic workers：** 必须使用 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 进行中调用日志仅通过浏览器本地时间实时显示耗时，不增加服务器负载。

**架构：** 前端在存在进行中记录时启动本地秒级时钟；`running` 日志使用 `started_at` 加本地时钟计算耗时，终态日志继续使用返回的 `duration_ms`。

**技术栈：** Python `unittest`、React、TypeScript。

## 全局约束

- 不改变后端接口、日志存储或终态日志持久化的耗时。
- 不执行定时 API 请求、数据库写入或 JSON 写入。
- 前端没有现成页面测试运行器；以类型检查和代码搜索验证页面变更。

---

### 任务 1：前端本地计时

**文件：**
- 修改：`web/src/app/logs/page.tsx`

- [ ] **步骤 1：实现秒级显示时钟**

```tsx
const [now, setNow] = useState(() => Date.now());
const hasRunningLogs = items.some((item) => item.detail?.status === "running");

useEffect(() => {
  if (!hasRunningLogs) return;
  const timer = window.setInterval(() => setNow(Date.now()), 1000);
  return () => window.clearInterval(timer);
}, [hasRunningLogs]);
```

`formatDuration` 对运行中日志以 `started_at` 和 `now` 计算耗时；不添加任何 `fetchSystemLogs` 的定时调用。

- [ ] **步骤 2：运行类型检查**

运行：`npx tsc --noEmit`

预期：退出码为 0。

- [ ] **步骤 3：提交**

```bash
git add services/log_service.py test/test_log_store.py web/src/app/logs/page.tsx \
  docs/superpowers/plans/2026-07-10-running-log-duration-live-update.md
git commit -m "fix: refresh running log durations"
```
