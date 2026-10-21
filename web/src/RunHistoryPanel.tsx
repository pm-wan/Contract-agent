import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Result, Space, Spin, Tag, Typography } from "antd";
import { Link } from "react-router-dom";
import { ApiError, listCaseRuns, type AuditRun, type RunPage } from "./api";

const { Text } = Typography;

function resultPath(run: AuditRun) {
  return run.task_type === "upstream_comparison"
    ? `/comparisons/${run.run_id}`
    : `/runs/${run.run_id}`;
}

function runLabel(run: AuditRun) {
  return run.task_type === "upstream_comparison" ? "上下游合同比对" : "主合同审核";
}

const statusCopy: Record<AuditRun["status"], { label: string; color: string }> = {
  queued: { label: "排队中", color: "processing" },
  running: { label: "处理中", color: "processing" },
  waiting: { label: "报告已生成 · 含待核实事项", color: "warning" },
  completed: { label: "审查报告已生成", color: "success" },
  failed: { label: "处理失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
  superseded: { label: "已被新版本替代", color: "default" },
};

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }).format(date);
}

export function RunHistoryPanel({
  caseId,
  view = "history",
  active = true,
}: {
  caseId: string;
  view?: "history" | "results";
  active?: boolean;
}) {
  const [page, setPage] = useState<RunPage | null>(null);
  const [pageCaseId, setPageCaseId] = useState<string | null>(null);
  const visiblePage = pageCaseId === caseId ? page : null;
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setLoading(true);
    setError(null);
    void listCaseRuns(caseId, { limit: 20 }, controller.signal)
      .then((response) => {
        if (!Array.isArray(response.items) || !(response.next_cursor === null || typeof response.next_cursor === "string")) {
          throw new ApiError(502, "invalid_response");
        }
        setPage(response);
        setPageCaseId(caseId);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        const failure = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
        if ([401, 403, 404].includes(failure.status)) setPage(null);
        setError(failure);
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [caseId, refreshKey, active]);

  useEffect(() => {
    if (!active || loading || loadingMore || error || !visiblePage?.items.some((run) => ["queued", "running"].includes(run.status))) return;
    const timer = window.setTimeout(() => setRefreshKey((key) => key + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [active, loading, loadingMore, error, visiblePage]);

  const refresh = () => {
    if (loading || loadingMore) return;
    setRefreshKey((key) => key + 1);
  };

  const loadMore = async () => {
    if (!visiblePage?.next_cursor || loadingMore) return;
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await listCaseRuns(caseId, { cursor: visiblePage.next_cursor, limit: 20 }, controller.signal);
      if (!controller.signal.aborted) {
        setPage((current) => current ? { ...next, items: [...current.items, ...next.items] } : next);
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        const failure = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
        if ([401, 403, 404].includes(failure.status)) setPage(null);
        setError(failure);
      }
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  };

  const denied = error && [401, 403, 404].includes(error.status);
  const runs = visiblePage?.items ?? [];
  const resultRuns = runs.filter((run) => ["completed", "waiting"].includes(run.status));
  const pending = runs.some((run) => ["queued", "running"].includes(run.status));
  return <section aria-labelledby="run-history-title" className="run-history-panel">
    <div className="run-history-heading">
      <div>{view === "results" ? <Text strong id="run-history-title">审核批次</Text> : <Typography.Title level={4} id="run-history-title">审核执行记录</Typography.Title>}</div>
      <Button onClick={refresh} disabled={loading || loadingMore}>{view === "results" ? "刷新结果" : "刷新审核记录"}</Button>
    </div>
    {view === "history" && <Text type="secondary" className="run-history-note">在上方发起主合同审核；排队和执行中的批次会自动刷新，已有结果可在审核结果中查看。</Text>}
    {loading && !visiblePage ? <div className="route-loading" aria-live="polite"><Spin description="正在读取运行历史…" /></div>
      : denied ? <Result status={error.status === 404 ? "404" : "403"} title={error.status === 401 ? "登录状态已失效" : error.status === 404 ? "运行历史不可用" : "无权查看运行历史"} subTitle={error.status === 401 ? "请返回登录页面重新登录。" : "当前身份无法查看此案件的运行记录。"} />
        : error && !visiblePage ? <Result status="500" title="运行历史暂时无法读取" subTitle="检查服务连接后重试。" extra={<Button onClick={refresh}>重试</Button>} />
          : <Card size="small" className="run-history-card">
            {error && <Alert type="warning" showIcon title="刷新未完成，仍显示上次成功读取的记录" action={<Button size="small" onClick={refresh}>重试</Button>} />}
            {loading && visiblePage && <div className="run-history-inline-loading" aria-live="polite"><Spin size="small" description="正在刷新…" /></div>}
            {view === "results" ? resultRuns.length === 0
              ? <Empty description={pending ? "审核正在进行，结果尚未生成" : "暂无已产出的审核结果"} />
              : <div className="case-result-list">{resultRuns.map((run) => <article className="case-result-item" key={run.run_id}>
                <div><Text strong>{runLabel(run)} · {run.run_id.slice(0, 8)}</Text><Tag color={statusCopy[run.status].color}>{statusCopy[run.status].label}</Tag><Text type="secondary">{formatDate(run.created_at)}</Text></div>
                <Link to={resultPath(run)}>查看结果与原文证据</Link>
              </article>)}</div>
            : (visiblePage?.items ?? []).length === 0
              ? <Empty description="当前没有可查看的运行记录" />
              : <div className="run-history-table-scroll">
                <table aria-label="案件运行历史" className="run-history-table">
                  <thead><tr><th scope="col">运行编号</th><th scope="col">状态</th><th scope="col">创建时间</th><th scope="col">结果</th></tr></thead>
                  <tbody>{(visiblePage?.items ?? []).map((run) => <tr key={run.run_id}>
                    <td><Text code>{run.run_id.slice(0, 8)}</Text></td>
                    <td><Tag color={statusCopy[run.status].color}>{statusCopy[run.status].label}</Tag></td>
                    <td>{formatDate(run.created_at)}</td>
                    <td><Link to={resultPath(run)}>{["queued", "running"].includes(run.status) ? "查看执行状态" : "查看运行结果"}</Link></td>
                  </tr>)}</tbody>
                </table>
              </div>}
            {visiblePage?.next_cursor && <Space className="run-history-more"><Button loading={loadingMore} onClick={() => void loadMore()}>加载更多运行</Button></Space>}
            {visiblePage && <Text type="secondary" className="run-history-count">已加载 {visiblePage.items.length} 项运行记录</Text>}
          </Card>}
  </section>;
}
