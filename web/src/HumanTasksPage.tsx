import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Result, Space, Spin, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Link } from "react-router-dom";
import { ApiError, listHumanTasks, type HumanTask, type HumanTaskPage } from "./api";

const { Title, Text } = Typography;

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function queueError(error: ApiError) {
  if (error.status === 401) {
    return { status: "403" as const, title: "登录状态已失效", detail: "请返回登录页面重新登录。" };
  }
  if (error.status === 403) {
    return { status: "403" as const, title: "无权访问人工任务", detail: "当前账号没有人工任务权限，请联系系统管理员分配角色。" };
  }
  if (error.status === 404) {
    return { status: "404" as const, title: "人工任务队列不可用", detail: "任务可能已关闭或当前身份不可见。" };
  }
  return { status: "500" as const, title: "人工任务暂时无法读取", detail: "检查服务连接后重试。" };
}

export function HumanTasksPage() {
  const [page, setPage] = useState<HumanTaskPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [errorMode, setErrorMode] = useState<"initial" | "refresh" | "more">("initial");
  const [refreshKey, setRefreshKey] = useState(0);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    document.title = "人工任务 — 合同审查工作台";
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setLoading(true);
    setError(null);
    void listHumanTasks({ status: "open", limit: 20 }, controller.signal)
      .then((response) => setPage(response))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          const requestError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
          if ([401, 403, 404].includes(requestError.status)) setPage(null);
          setError(requestError);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refreshKey]);

  const refresh = () => {
    if (loading || loadingMore) return;
    setErrorMode(page ? "refresh" : "initial");
    setRefreshKey((value) => value + 1);
  };

  const loadMore = async () => {
    if (!page?.next_cursor || loadingMore) return;
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await listHumanTasks(
        { status: "open", limit: 20, cursor: page.next_cursor },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setPage((current) => current
          ? { ...next, items: [...current.items, ...next.items] }
          : next);
      }
    } catch (reason) {
      if (!controller.signal.aborted) {
        const requestError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
        if ([401, 403, 404].includes(requestError.status)) setPage(null);
        setErrorMode("more");
        setError(requestError);
      }
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  };

  const columns: ColumnsType<HumanTask> = [
    {
      title: "任务",
      dataIndex: "title",
      key: "title",
      width: 210,
      render: (_title: string, task) => (
        <div className="human-task-title">
          <Text strong>{task.title}</Text>
          <Text type="secondary" code>任务 {task.task_id.slice(0, 8)}</Text>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: () => <Tag color="warning">等待复核</Tag>,
    },
    {
      title: "所需角色",
      dataIndex: "required_role",
      key: "required_role",
      width: 150,
      render: (role: string) => role === "reviewer" ? "合同审核人" : role === "finance_reviewer" ? "财务审核人" : "指定案件成员",
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 175,
      render: formatDate,
    },
    {
      title: "关联运行",
      key: "run",
      width: 150,
      render: (_value: unknown, task) => <Link to={`/runs/${encodeURIComponent(task.run_id)}`}>查看运行结果</Link>,
    },
  ];

  if (loading && !page) {
    return <div className="route-loading" aria-live="polite"><Spin size="large" description="正在读取待复核任务…" /></div>;
  }

  if (error && !page) {
    const view = queueError(error);
    return <Result
      status={view.status}
      title={view.title}
      subTitle={view.detail}
      extra={<Space><Button onClick={refresh}>重试</Button><Link to="/cases">返回案件列表</Link></Space>}
    />;
  }

  return (
    <main className="human-tasks-page" aria-labelledby="human-tasks-title">
      <header className="human-tasks-heading">
        <div><span className="eyebrow">人工复核 / 当前账号</span><Title level={2} id="human-tasks-title">待处理任务</Title></div>
        <Button onClick={refresh} disabled={loading || loadingMore}>刷新队列</Button>
      </header>
      <Alert
        className="human-task-context-notice"
        type="warning"
        showIcon
        title="请在关联运行中核对任务上下文"
        description="价款复核任务可在运行结果页记录核对动作或请求补充材料；这些记录不会确认金额或改变规则结论。事实确认仍需在结果页对照具体证据完成。"
      />
      {error && page && <Alert
        className="human-task-refresh-error"
        type="error"
        showIcon
        title={errorMode === "more" ? "加载更多失败，已保留当前队列" : "队列刷新失败，以下为上次读取内容"}
        description={queueError(error).detail}
        action={<Button
          size="small"
          onClick={errorMode === "more" ? () => void loadMore() : refresh}
          disabled={loading || loadingMore}
        >重试</Button>}
      />}
      <section aria-label="待复核任务列表" className="human-task-table-region" aria-busy={loading || loadingMore}>
        <div className="human-task-list-meta">
          <Text type="secondary">已加载 {page?.items.length ?? 0} 项待复核任务</Text>
          {(loading || loadingMore) && <Spin size="small" aria-label="正在更新任务列表" />}
        </div>
        <Table<HumanTask>
          aria-label="待复核任务"
          rowKey="task_id"
          columns={columns}
          dataSource={page?.items ?? []}
          pagination={false}
          scroll={{ x: 820 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有分配给你的待复核任务" /> }}
        />
      </section>
      {page?.next_cursor && <div className="human-task-load-more">
        <Button onClick={() => void loadMore()} loading={loadingMore} disabled={loading || loadingMore}>
          加载更多任务
        </Button>
      </div>}
    </main>
  );
}
