import { useEffect, useState } from "react";
import { Alert, Button, Empty, Space, Spin, Tag, Timeline, Typography } from "antd";
import { ApiError, listCaseDocumentAccessEvents, type DocumentAccessEvent, type DocumentAccessEventPage } from "./api";

const { Text } = Typography;

function dateLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function purposeLabel(value: DocumentAccessEvent["permissions"][number]["purpose"]) {
  return value === "contract_review" ? "合同审查" : "上下游比对";
}

function eventDescription(event: DocumentAccessEvent) {
  const permissions = event.permissions.map((permission) =>
    `${purposeLabel(permission.purpose)}；${permission.actor_id.slice(0, 8)}：文本${permission.allow_text ? "允许" : "禁止"}，金额${permission.allow_amount ? "允许" : "禁止"}，至 ${dateLabel(permission.valid_until)}`,
  );
  return (
    <div className="access-event-copy">
      <div className="access-event-heading">
        <strong>{event.action === "selection_granted" ? "已授予材料用途权限" : "已撤销材料权限"}</strong>
        <Tag>{event.action === "selection_granted" ? "授权" : "撤权"}</Tag>
        <Text type="secondary">{dateLabel(event.created_at)}</Text>
      </div>
      <div>{event.file_name}</div>
      <Text type="secondary">操作人 {event.actor_id.slice(0, 8)} · 案件版本 v{event.case_version}</Text>
      {permissions.map((permission) => <Text key={permission} type="secondary">授权范围：{permission}</Text>)}
      {event.reason && <Text type="secondary">撤权原因：{event.reason}</Text>}
      {event.invalidated_run_ids.length > 0 && <Text type="warning">已使 {event.invalidated_run_ids.length} 个运行失效</Text>}
    </div>
  );
}

export function DocumentAccessTimeline({ caseId }: { caseId: string }) {
  const [page, setPage] = useState<DocumentAccessEventPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void listCaseDocumentAccessEvents(caseId, { limit: 20 }, controller.signal)
      .then(setPage)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
          if (reason instanceof ApiError && [401, 403, 404].includes(reason.status)) setPage(null);
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [caseId, refreshKey]);

  const loadMore = async () => {
    if (!page?.next_cursor || loading) return;
    setLoading(true);
    setError(null);
    try {
      const next = await listCaseDocumentAccessEvents(caseId, { limit: 20, cursor: page.next_cursor });
      setPage((current) => current ? { ...next, items: [...current.items, ...next.items] } : next);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="access-timeline-panel" aria-labelledby="access-timeline-title" aria-busy={loading}>
      <div className="access-timeline-heading">
        <div><span className="eyebrow">权限 / 不可变记录</span><h3 id="access-timeline-title">授权记录</h3></div>
        <Button size="small" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading}>刷新</Button>
      </div>
      {error && <Alert
        type={error.status === 403 ? "warning" : "error"}
        showIcon
        title={error.status === 403 ? "授权记录仅对当前审核人开放" : "授权记录暂时无法读取"}
        description={error.status === 403 ? "当前账号没有审核人权限；案件材料状态仍可查看。" : "已保留当前已加载记录，可刷新重试。"}
        action={error.status !== 403 && <Button size="small" onClick={() => setRefreshKey((value) => value + 1)}>重试</Button>}
      />}
      {loading && !page ? <Spin size="small" description="正在读取授权记录…" /> : (page?.items?.length ?? 0) > 0
        ? <Timeline items={(page?.items ?? []).map((event) => ({ key: event.id, content: eventDescription(event), color: event.action === "selection_revoked" ? "red" : "blue" }))} />
        : !error && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无授权或撤权记录" />}
      <Space className="access-timeline-footer" size="middle">
        {page && <Text type="secondary">已加载 {page.items?.length ?? 0} 条</Text>}
        {page?.next_cursor && <Button size="small" onClick={() => void loadMore()} loading={loading}>加载更多记录</Button>}
      </Space>
    </section>
  );
}
