import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Result, Space, Spin, Tag, Timeline, Typography } from "antd";
import { ApiError, listCaseLegalEntityHistory, type CaseLegalEntityHistoryEvent, type CaseLegalEntityHistoryPage } from "./api";

const { Text } = Typography;

function dateLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

function eventContent(event: CaseLegalEntityHistoryEvent) {
  const confirmed = event.event_type === "confirmed";
  const corrected = event.event_type === "corrected";
  return <div className="access-event-copy">
    <div className="access-event-heading">
      <strong>{confirmed ? "确认法律主体" : corrected ? "管理员更正法律主体" : "主体事件"}</strong>
      <Tag color={confirmed ? "blue" : corrected ? "gold" : "default"}>{confirmed ? "主体确认" : corrected ? "管理员纠错" : "主体事件"}</Tag>
      <Text type="secondary">{dateLabel(event.occurred_at)}</Text>
    </div>
    {event.previous_legal_entity_id && <Text>更正前：<Text code>{event.previous_legal_entity_id}</Text></Text>}
    <Text>确认后：<Text code>{event.legal_entity_id}</Text></Text>
    <Text type="secondary">操作人 {event.actor_id} · 案件版本 v{event.case_version}</Text>
    <Text type="secondary">{confirmed ? "核对依据" : corrected ? "更正原因" : "操作说明"}：{event.reason}</Text>
    {corrected && <Text type="secondary">
      纠错影响：撤销 {event.revoked_selection_count} 项材料选择、{event.revoked_grant_count} 项材料授权；
      将 {event.superseded_run_count} 次旧运行标记为已替代。旧冻结快照保留。
    </Text>}
  </div>;
}

export function CaseLegalEntityTimeline({ caseId }: { caseId: string }) {
  const [page, setPage] = useState<CaseLegalEntityHistoryPage | null>(null);
  const [pageCaseId, setPageCaseId] = useState<string | null>(null);
  const visiblePage = pageCaseId === caseId ? page : null;
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setLoading(true);
    setLoadingMore(false);
    setError(null);
    void listCaseLegalEntityHistory(caseId, { limit: 20 }, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
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
  }, [caseId, refreshKey]);

  const loadMore = async () => {
    if (!visiblePage?.next_cursor || loadingMore) return;
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await listCaseLegalEntityHistory(caseId, { cursor: visiblePage.next_cursor, limit: 20 }, controller.signal);
      if (!controller.signal.aborted) setPage((current) => current ? { ...next, items: [...current.items, ...next.items] } : next);
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
  return <section className="access-timeline-panel entity-history-panel" aria-labelledby="entity-history-title" aria-busy={loading || loadingMore}>
    <div className="access-timeline-heading">
      <div><span className="eyebrow">主体范围 / 不可变审计记录</span><h3 id="entity-history-title">主体确认与纠错记录</h3></div>
      <Button size="small" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading || loadingMore}>刷新</Button>
    </div>
    <Text type="secondary">记录主体确认及管理员纠错的操作者、时间、前后主体、案件版本与原因；审核人仅可查看，不能在此更改主体。</Text>
    {error && !denied && <Alert type="warning" showIcon title={visiblePage ? "刷新未完成，仍显示上次读取的记录" : "主体审计记录暂时无法读取"} action={<Button size="small" onClick={() => setRefreshKey((value) => value + 1)}>重试</Button>} />}
    {loading && !visiblePage ? <Spin size="small" description="正在读取主体审计记录…" />
      : denied ? <Result status={error.status === 404 ? "404" : "403"} title={error.status === 401 ? "身份验证不可用" : error.status === 404 ? "主体审计记录不可用" : "无权查看主体审计记录"} subTitle="当前身份无法查看此案件的主体确认与纠错记录。" />
        : (visiblePage?.items.length ?? 0) > 0
          ? <Timeline items={(visiblePage?.items ?? []).map((event) => ({ key: event.event_id, color: event.event_type === "confirmed" ? "blue" : event.event_type === "corrected" ? "gold" : "gray", content: eventContent(event) }))} />
          : !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无主体确认记录" />}
    <Space className="access-timeline-footer" size="middle">
      {visiblePage && <Text type="secondary">已加载 {visiblePage.items.length} 条</Text>}
      {visiblePage?.next_cursor && <Button size="small" onClick={() => void loadMore()} loading={loadingMore}>加载更多记录</Button>}
    </Space>
  </section>;
}
