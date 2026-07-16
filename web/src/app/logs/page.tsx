"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Copy, Database, FileText, ImageIcon, LoaderCircle, RefreshCw, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DateRangeFilter } from "@/components/date-range-filter";
import { ImageLightbox } from "@/components/image-lightbox";
import { ImageThumbnail, getImageThumbnailUrl } from "@/components/image-thumbnail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { deleteSystemLogs, fetchSystemLogDetail, fetchSystemLogs, stopSystemLog, type SystemLog } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { extractLogResultContent } from "@/lib/log-detail-content";
import { formatLogDuration } from "@/lib/log-duration";
import { getImageLogSummary } from "@/lib/image-log-summary";

const LogType = {
  Call: "call",
  Account: "account",
} as const;

const typeLabels: Record<string, string> = {
  [LogType.Call]: "调用日志",
  [LogType.Account]: "账号管理日志",
};

function getDetailText(item: SystemLog, key: string) {
  const value = item.detail?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "-";
}

type DetailTab = "result" | "request" | "raw";

function getStringList(value: unknown) {
  const urls = value;
  return Array.isArray(urls) ? urls.filter((url): url is string => typeof url === "string") : [];
}

function getResponseImageUrls(item: SystemLog | null) {
  const responseImageUrls = getStringList(item?.detail?.response_image_urls);
  if (responseImageUrls.length > 0) return responseImageUrls;
  const endpoint = String(item?.detail?.endpoint || "");
  return endpoint.startsWith("/v1/images/") ? getStringList(item?.detail?.urls) : [];
}

function getRequestImageUrls(item: SystemLog | null) {
  return getStringList(item?.detail?.request_urls);
}

function getDetailValue(item: SystemLog | null, key: string) {
  const value = item?.detail?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function DetailImageGallery({ title, urls, onOpen }: { title: string; urls: string[]; onOpen: (index: number) => void }) {
  if (urls.length === 0) return null;
  return (
    <section className="space-y-3 border-t border-stone-100 pt-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-stone-800">{title}</h3>
        <span className="text-xs text-stone-400">{urls.length} 张</span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
        {urls.map((url, index) => (
          <button
            key={`${url}-${index}`}
            type="button"
            className="aspect-square overflow-hidden rounded-lg border border-stone-200 bg-stone-100 transition hover:border-stone-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-stone-400"
            onClick={() => onOpen(index)}
            aria-label={`预览${title}第 ${index + 1} 张`}
          >
            <ImageThumbnail src={url} thumbnailSrc={getImageThumbnailUrl(url)} className="h-full w-full" />
          </button>
        ))}
      </div>
    </section>
  );
}

function getStatus(item: SystemLog) {
  const status = item.detail?.status;
  if (status === "success") return "成功";
  if (status === "failed") return "失败";
  if (status === "running") return "进行中";
  if (status === "stopped") return "已停止";
  return "-";
}

function LogsContent() {
  const [items, setItems] = useState<SystemLog[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [type, setType] = useState<string>(LogType.Call);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [keyName, setKeyName] = useState("");
  const [accountEmail, setAccountEmail] = useState("");
  const [status, setStatus] = useState("");
  const [summary, setSummary] = useState("");
  const [model, setModel] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [batchId, setBatchId] = useState("");
  const [stoppingId, setStoppingId] = useState("");
  const [detailLog, setDetailLog] = useState<SystemLog | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [detailTab, setDetailTab] = useState<DetailTab>("result");
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxImages, setLightboxImages] = useState<Array<{ id: string; src: string }>>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isDeleting, setIsDeleting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [deletingItems, setDeletingItems] = useState<SystemLog[]>([]);
  const responseImageUrls = getResponseImageUrls(detailLog);
  const requestImageUrls = getRequestImageUrls(detailLog);
  const imageLogSummary = getImageLogSummary(detailLog?.detail);
  const responseResult = useMemo(
    () => extractLogResultContent(detailLog?.detail?.response_text),
    [detailLog],
  );
  const responseTextTruncated = detailLog?.detail?.response_text_truncated === true;
  const isCallLog = type === LogType.Call;
  const hasRunningCallLogs = isCallLog && items.some((item) => item.detail?.status === "running");
  const currentRows = items;
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const currentPageSelected = currentRows.length > 0 && currentRows.every((item) => selectedSet.has(item.id));

  const loadLogs = async (targetPage?: number, targetPageSize?: number) => {
    setIsLoading(true);
    try {
      const data = await fetchSystemLogs({
        type,
        start_date: startDate,
        end_date: endDate,
        page: targetPage ?? page,
        page_size: targetPageSize ?? pageSize,
        key_name: keyName,
        account_email: accountEmail,
        status,
        summary,
        model,
        endpoint,
        batch_id: batchId,
      });
      setItems(data.items);
      setTotal(data.total);
      setTotalPages(data.total_pages);
      setSelectedIds((current) => current.filter((id) => data.items.some((item) => item.id === id)));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载日志失败");
    } finally {
      setIsLoading(false);
    }
  };

  const clearFilters = () => {
    setStartDate("");
    setEndDate("");
    setKeyName("");
    setAccountEmail("");
    setStatus("");
    setSummary("");
    setModel("");
    setEndpoint("");
    setBatchId("");
  };

  const handleStop = async (item: SystemLog) => {
    if (!window.confirm("停止该图片子任务？本地将不再等待或交付后续结果。")) return;
    setStoppingId(item.id);
    try {
      await stopSystemLog(item.id);
      toast.success("已请求停止任务");
      await loadLogs();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "停止任务失败");
    } finally {
      setStoppingId("");
    }
  };

  const openDetail = async (item: SystemLog) => {
    setDetailLog(item);
    setDetailOpen(true);
    setDetailTab("result");
    setIsDetailLoading(true);
    try {
      const fullDetail = await fetchSystemLogDetail(item.id);
      setDetailLog(fullDetail);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载日志详情失败");
    } finally {
      setIsDetailLoading(false);
    }
  };

  const openImages = (urls: string[], index: number) => {
    setLightboxImages(urls.map((url, imageIndex) => ({ id: `${imageIndex}`, src: url })));
    setLightboxIndex(index);
    setLightboxOpen(true);
  };

  const openLogImage = (item: SystemLog, index: number) => {
    openImages(getResponseImageUrls(item), index);
  };

  const toggleIds = (ids: string[], checked: boolean) => {
    setSelectedIds((current) => checked ? Array.from(new Set([...current, ...ids])) : current.filter((id) => !ids.includes(id)));
  };

  const confirmDelete = async () => {
    const ids = deletingItems.map((item) => item.id);
    if (ids.length === 0) return;
    setIsDeleting(true);
    try {
      const data = await deleteSystemLogs(ids);
      toast.success(`已删除 ${data.removed} 条日志`);
      setDeletingItems([]);
      setSelectedIds((current) => current.filter((id) => !ids.includes(id)));
      if (detailLog && ids.includes(detailLog.id)) {
        setDetailOpen(false);
        setDetailLog(null);
      }
      // 删除后保持当前页，如果当前页没数据了且不是第1页，回退到前一页
      const nextPage = items.length <= ids.length && page > 1 ? page - 1 : page;
      setPage(nextPage);
      await loadLogs(nextPage);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除日志失败");
    } finally {
      setIsDeleting(false);
    }
  };

  useEffect(() => {
    setPage(1);
    void loadLogs(1);
  }, [type, startDate, endDate]);

  useEffect(() => {
    if (!hasRunningCallLogs) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasRunningCallLogs]);

  return (
    <section className="space-y-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Logs</div>
          <h1 className="text-2xl font-semibold tracking-tight">日志管理</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select value={type} onValueChange={setType}>
            <SelectTrigger className="h-10 w-[150px] rounded-xl border-stone-200 bg-white"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={LogType.Call}>调用日志</SelectItem>
              <SelectItem value={LogType.Account}>账号管理日志</SelectItem>
            </SelectContent>
          </Select>
          <DateRangeFilter startDate={startDate} endDate={endDate} onChange={(start, end) => { setStartDate(start); setEndDate(end); }} />
          <input
            type="text"
            placeholder="令牌名称"
            value={keyName}
            onChange={(e) => setKeyName(e.target.value)}
            className="h-10 w-[140px] rounded-xl border border-stone-200 bg-white px-3 text-sm outline-none focus:border-stone-400"
          />
          <input
            type="text"
            placeholder="执行账号"
            value={accountEmail}
            onChange={(e) => setAccountEmail(e.target.value)}
            className="h-10 w-[180px] rounded-xl border border-stone-200 bg-white px-3 text-sm outline-none focus:border-stone-400"
          />
          <Select value={status || "all"} onValueChange={(v) => setStatus(v === "all" ? "" : v)}>
            <SelectTrigger className="h-10 w-[100px] rounded-xl border-stone-200 bg-white"><SelectValue placeholder="状态" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="success">成功</SelectItem>
              <SelectItem value="failed">失败</SelectItem>
              <SelectItem value="running">进行中</SelectItem>
              <SelectItem value="stopped">已停止</SelectItem>
            </SelectContent>
          </Select>
          <input type="text" placeholder="模型" value={model} onChange={(e) => setModel(e.target.value)} className="h-10 w-[140px] rounded-xl border border-stone-200 bg-white px-3 text-sm outline-none focus:border-stone-400" />
          <input type="text" placeholder="接口" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} className="h-10 w-[170px] rounded-xl border border-stone-200 bg-white px-3 text-sm outline-none focus:border-stone-400" />
          <input type="text" placeholder="批次 ID" value={batchId} onChange={(e) => setBatchId(e.target.value)} className="h-10 w-[150px] rounded-xl border border-stone-200 bg-white px-3 text-sm outline-none focus:border-stone-400" />
          <Select value={summary || "all"} onValueChange={(value) => setSummary(value === "all" ? "" : value)}>
            <SelectTrigger className="h-10 w-[140px] rounded-xl border-stone-200 bg-white"><SelectValue placeholder="简述类型" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部简述</SelectItem>
              <SelectItem value="文生图">文生图</SelectItem>
              <SelectItem value="prompt生成">prompt生成</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={clearFilters} className="h-10 rounded-xl border-stone-200 bg-white px-4 text-stone-700">
            清除筛选条件
          </Button>
          <Button onClick={() => { setPage(1); void loadLogs(1); }} disabled={isLoading} className="h-10 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800">
            {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
            查询
          </Button>
        </div>
      </div>

      <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-5 py-4">
            <div className="flex flex-wrap items-center gap-3 text-sm text-stone-600">
              <span>共 {total} 条</span>
              <label className="flex items-center gap-2">
                <Checkbox checked={currentPageSelected} onCheckedChange={(checked) => toggleIds(currentRows.map((item) => item.id), Boolean(checked))} />
                本页全选
              </label>
              {selectedIds.length > 0 ? <span>已选 {selectedIds.length} 条</span> : null}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="ghost" className="h-8 rounded-lg px-3 text-stone-500" onClick={() => void loadLogs()} disabled={isLoading}>
                <RefreshCw className={`size-4 ${isLoading ? "animate-spin" : ""}`} />
                刷新
              </Button>
              <button type="button" className="text-sm text-stone-500 hover:text-stone-900 disabled:text-stone-300" onClick={() => setSelectedIds([])} disabled={selectedIds.length === 0 || isDeleting}>
                取消选择
              </button>
              <Button variant="outline" className="h-8 rounded-lg border-rose-200 bg-white px-3 text-rose-600 hover:bg-rose-50" onClick={() => setDeletingItems(items.filter((item) => selectedSet.has(item.id)))} disabled={selectedIds.length === 0 || isDeleting}>
                <Trash2 className="size-4" />
                删除所选
              </Button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <Table className="min-w-[900px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12"></TableHead>
                  <TableHead>时间</TableHead>
                  <TableHead>类型</TableHead>
                  {isCallLog ? <TableHead>令牌名称</TableHead> : null}
                  {isCallLog ? <TableHead>执行账号</TableHead> : null}
                  {isCallLog ? <TableHead>调用耗时</TableHead> : null}
                  {isCallLog ? <TableHead>状态</TableHead> : null}
                  {isCallLog ? <TableHead className="w-36">图片</TableHead> : null}
                  <TableHead>简述</TableHead>
                  <TableHead className="w-40">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {currentRows.map((item) => {
                  const urls = getResponseImageUrls(item);
                  return (
                    <TableRow key={item.id} className="text-stone-600">
                      <TableCell>
                        <Checkbox checked={selectedSet.has(item.id)} onCheckedChange={(checked) => toggleIds([item.id], Boolean(checked))} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{item.time}</TableCell>
                      <TableCell><Badge variant="secondary" className="rounded-md">{typeLabels[item.type] || item.type}</Badge></TableCell>
                      {isCallLog ? <TableCell>{getDetailText(item, "key_name")}</TableCell> : null}
                      {isCallLog ? <TableCell className="max-w-[160px] truncate">{getDetailText(item, "account_email")}</TableCell> : null}
                      {isCallLog ? <TableCell>{formatLogDuration(item, now)}</TableCell> : null}
                      {isCallLog ? (
                        <TableCell>
                          <Badge variant={item.detail?.status === "failed" ? "danger" : item.detail?.status === "running" ? "warning" : item.detail?.status === "stopped" ? "secondary" : "success"} className="rounded-md">
                            {getStatus(item)}
                          </Badge>
                        </TableCell>
                      ) : null}
                      {isCallLog ? (
                        <TableCell>
                          {urls.length ? (
                            <div className="flex items-center gap-1.5">
                              {urls.slice(0, 3).map((url, imageIndex) => (
                                <button
                                  key={`${url}-${imageIndex}`}
                                  type="button"
                                  className="relative size-9 overflow-hidden rounded-lg border border-stone-200 bg-stone-100"
                                  onClick={() => openLogImage(item, imageIndex)}
                                  title="预览图片"
                                >
                                  <ImageThumbnail src={url} thumbnailSrc={getImageThumbnailUrl(url)} className="h-full w-full" />
                                </button>
                              ))}
                              {urls.length > 3 ? <span className="text-xs text-stone-400">+{urls.length - 3}</span> : null}
                            </div>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-xs text-stone-400">
                              <ImageIcon className="size-3.5" />
                              -
                            </span>
                          )}
                        </TableCell>
                      ) : null}
                      <TableCell className="max-w-[420px] truncate text-stone-500">{item.summary || "-"}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Button variant="ghost" className="h-8 rounded-lg px-3 text-stone-600" onClick={() => openDetail(item)}>
                            查看详情
                          </Button>
                          {item.detail?.status === "running" && String(item.detail?.endpoint || "").startsWith("/v1/images/") ? (
                            <Button variant="ghost" className="h-8 rounded-lg px-3 text-amber-700 hover:bg-amber-50" onClick={() => void handleStop(item)} disabled={stoppingId === item.id}>
                              停止
                            </Button>
                          ) : null}
                          <Button variant="ghost" className="h-8 rounded-lg px-3 text-rose-600 hover:bg-rose-50 hover:text-rose-700" onClick={() => setDeletingItems([item])}>
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <div className="flex items-center justify-between gap-2 border-t border-stone-100 px-4 py-3 text-sm text-stone-500">
            <div className="flex items-center gap-2">
              <span>每页</span>
              <Select value={String(pageSize)} onValueChange={(value) => { const size = Number(value); setPageSize(size); setPage(1); void loadLogs(1, size); }}>
                <SelectTrigger className="h-8 w-[70px] rounded-lg border-stone-200 bg-white text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10</SelectItem>
                  <SelectItem value="20">20</SelectItem>
                  <SelectItem value="50">50</SelectItem>
                  <SelectItem value="100">100</SelectItem>
                </SelectContent>
              </Select>
              <span>条</span>
            </div>
            <div className="flex items-center gap-2">
              <span>第 {page} / {Math.max(1, totalPages)} 页，共 {total} 条</span>
              <Button variant="outline" size="icon" className="size-9 rounded-lg border-stone-200 bg-white" disabled={page <= 1} onClick={() => { setPage((v) => Math.max(1, v - 1)); void loadLogs(page - 1); }}>
                <ChevronLeft className="size-4" />
              </Button>
              <Button variant="outline" size="icon" className="size-9 rounded-lg border-stone-200 bg-white" disabled={page >= totalPages} onClick={() => { setPage((v) => v + 1); void loadLogs(page + 1); }}>
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
          {!isLoading && items.length === 0 ? <div className="px-6 py-14 text-center text-sm text-stone-500">没有找到日志</div> : null}
        </CardContent>
      </Card>
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="flex h-[min(88vh,860px)] w-[min(92vw,920px)] flex-col overflow-hidden rounded-2xl p-0">
          <DialogHeader className="shrink-0 border-b border-stone-100 px-6 py-5">
            <DialogTitle>日志详情</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {isDetailLoading ? (
              <div className="flex h-full items-center justify-center">
                <LoaderCircle className="size-5 animate-spin text-stone-400" />
              </div>
            ) : (
            <div className="space-y-4">
              <div className="grid overflow-hidden rounded-lg border border-stone-200 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  ["接口", getDetailValue(detailLog, "endpoint")],
                  ["模型", getDetailValue(detailLog, "model")],
                  ["执行账号", getDetailValue(detailLog, "account_email")],
                  ["状态", getStatus(detailLog || { id: "", time: "", type: "" })],
                  ["调用耗时", formatLogDuration(detailLog || { id: "", time: "", type: "" }, now)],
                  ["开始时间", getDetailValue(detailLog, "started_at")],
                  ["结束时间", getDetailValue(detailLog, "ended_at")],
                  ["Key", getDetailValue(detailLog, "key_name")],
                ].map(([label, value]) => (
                  <div key={label} className="min-h-20 border-b border-r border-stone-100 px-4 py-3 last:border-r-0 sm:nth-[2n]:border-r-0 lg:nth-[4n]:border-r-0 lg:nth-last-[n+1]:border-b-0">
                    <div className="text-xs text-stone-400">{label}</div>
                    <div className="mt-1 truncate text-sm font-medium text-stone-800" title={value || "-"}>{value || "-"}</div>
                  </div>
                ))}
              </div>

              <div className="overflow-hidden rounded-lg border border-stone-200 bg-white">
                <div className="flex border-b border-stone-100 px-3">
                  {([
                    ["result", "结果内容", ImageIcon],
                    ["request", "请求内容", FileText],
                    ["raw", "原始数据", Database],
                  ] as const).map(([tab, label, Icon]) => (
                    <button
                      key={tab}
                      type="button"
                      className={`relative flex h-11 items-center gap-2 px-3 text-sm transition ${detailTab === tab ? "font-medium text-stone-900" : "text-stone-500 hover:text-stone-800"}`}
                      onClick={() => setDetailTab(tab)}
                      aria-selected={detailTab === tab}
                    >
                      <Icon className="size-4" />
                      {label}
                      {detailTab === tab ? <span className="absolute inset-x-3 bottom-0 h-0.5 bg-stone-900" /> : null}
                    </button>
                  ))}
                </div>

                {detailTab === "result" ? (
                  <div className="space-y-5 p-4">
                    {imageLogSummary ? (
                      <section className="rounded-lg border border-stone-200 bg-stone-50 px-4 py-3">
                        <div className="text-sm font-medium text-stone-800">
                          实际返回数量：{imageLogSummary.actualCount}
                        </div>
                        {imageLogSummary.warning ? (
                          <p className="mt-1 text-xs leading-5 text-amber-700">{imageLogSummary.warning}</p>
                        ) : null}
                      </section>
                    ) : null}
                    {responseResult.text ? (
                      <section className="space-y-3">
                        <div className="flex items-center justify-between gap-3">
                          <h3 className="text-sm font-medium text-stone-800">最终结果</h3>
                          <button
                            type="button"
                            className="inline-flex size-8 items-center justify-center rounded-md text-stone-500 hover:bg-stone-100 hover:text-stone-800"
                            onClick={() => { void navigator.clipboard.writeText(responseResult.text); toast.success("已复制到剪贴板"); }}
                            title="复制结果"
                            aria-label="复制结果"
                          >
                            <Copy className="size-4" />
                          </button>
                        </div>
                        <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-stone-50 p-4 text-sm leading-6 text-stone-700">
                          {responseResult.text}
                        </pre>
                        {responseTextTruncated ? (
                          <p className="text-xs leading-5 text-amber-700">返回内容已截断，日志仅保留前 12,000 个字符。</p>
                        ) : null}
                      </section>
                    ) : responseImageUrls.length === 0 ? (
                      <div className="py-12 text-center text-sm text-stone-400">该调用没有可展示的最终结果</div>
                    ) : null}
                    <DetailImageGallery title="返回图片" urls={responseImageUrls} onOpen={(index) => openImages(responseImageUrls, index)} />
                  </div>
                ) : null}

                {detailTab === "request" ? (
                  <div className="space-y-5 p-4">
                    {getDetailValue(detailLog, "request_text") ? (
                      <section className="space-y-3">
                        <div className="flex items-center justify-between gap-3">
                          <h3 className="text-sm font-medium text-stone-800">请求文本</h3>
                          <button
                            type="button"
                            className="inline-flex size-8 items-center justify-center rounded-md text-stone-500 hover:bg-stone-100 hover:text-stone-800"
                            onClick={() => { const text = getDetailValue(detailLog, "request_text"); void navigator.clipboard.writeText(text); toast.success("已复制到剪贴板"); }}
                            title="复制请求"
                            aria-label="复制请求"
                          >
                            <Copy className="size-4" />
                          </button>
                        </div>
                        <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-stone-50 p-4 text-sm leading-6 text-stone-700">
                          {getDetailValue(detailLog, "request_text")}
                        </pre>
                      </section>
                    ) : requestImageUrls.length === 0 ? (
                      <div className="py-12 text-center text-sm text-stone-400">该调用没有记录请求内容</div>
                    ) : null}
                    <DetailImageGallery title="请求图片" urls={requestImageUrls} onOpen={(index) => openImages(requestImageUrls, index)} />
                  </div>
                ) : null}

                {detailTab === "raw" ? (
                  <div className="p-4">
                    <pre className="max-h-[460px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-stone-50 p-4 text-xs leading-5 text-stone-700">
                      {JSON.stringify(detailLog?.detail || {}, null, 2)}
                    </pre>
                  </div>
                ) : null}
              </div>
            </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
      <ImageLightbox
        images={lightboxImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />
      <Dialog open={deletingItems.length > 0} onOpenChange={(open) => (!open ? setDeletingItems([]) : null)}>
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>{deletingItems.length === 1 ? "删除日志" : "删除所选日志"}</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              确认删除 {deletingItems.length} 条日志吗？删除后无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => setDeletingItems([])} disabled={isDeleting}>
              取消
            </Button>
            <Button className="rounded-xl bg-rose-600 text-white hover:bg-rose-700" onClick={() => void confirmDelete()} disabled={isDeleting || deletingItems.length === 0}>
              {isDeleting ? <LoaderCircle className="size-4 animate-spin" /> : null}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export default function LogsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin") {
    return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;
  }
  return <LogsContent />;
}
