import { useEffect, useState } from "react";
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Pagination,
  Result,
  Space,
  Table,
  Typography,
} from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  correctCaseLegalEntity,
  getEntityCorrectionContext,
  listEntityCorrectionCases,
  type EntityCorrectionContext,
} from "./api";
import { RouteLoading, SectionHeading } from "./shared-ui";
import { createUuid } from "./browserUuid";
import { LegalEntitySelect } from "./LegalEntitySelect";

const { Paragraph, Text } = Typography;

export function EntityCorrectionQueuePage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Array<{case_id: string; title: string; legal_entity_id: string; version: number}>>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [isComposing, setIsComposing] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (isComposing || searchInput.trim() === query) return;
    const timer = window.setTimeout(() => { setQuery(searchInput.trim()); setPage(1); }, 300);
    return () => window.clearTimeout(timer);
  }, [isComposing, query, searchInput]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    listEntityCorrectionCases({ page, pageSize: 20, query }, controller.signal)
      .then((result) => { setItems(result.items); setTotal(result.total); })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, query, retry]);

  useEffect(() => { document.title = "主体纠错 — 合同审查工作台"; }, []);

  return <>
    <Breadcrumb items={[{ title: <Link to="/cases">案件列表</Link> }, { title: "主体纠错" }]} />
    <SectionHeading eyebrow="管理员专用" title="主体纠错队列" />
    <Alert type="warning" showIcon title="仅用于更正已确认的案件主体" description="此队列只显示案件名称、当前主体和版本；不会开放合同材料、审查结果或其他案件详情。" />
    <div className="toolbar">
      <Input
        allowClear
        aria-label="搜索待纠错案件"
        placeholder="搜索案件名称"
        value={searchInput}
        onChange={(event) => setSearchInput(event.target.value)}
        onCompositionStart={() => setIsComposing(true)}
        onCompositionEnd={(event) => { setIsComposing(false); setSearchInput(event.currentTarget.value); }}
      />
      <Text type="secondary" aria-live="polite">{loading ? "正在读取…" : `共 ${total} 个已确认主体的案件`}</Text>
    </div>
    {error && <Alert
      type={error.status === 403 ? "warning" : "error"}
      showIcon
      title={error.status === 403 ? "当前身份不是主体管理员" : "纠错队列暂时无法读取"}
      description={error.status === 403 ? "此入口仅对当前租户的主体管理员开放。" : "请检查连接后重试。"}
      action={error.status !== 403 && <Button onClick={() => setRetry((value) => value + 1)}>重试</Button>}
    />}
    {!error && <Table
      rowKey="case_id"
      loading={loading}
      pagination={false}
      dataSource={items}
      columns={[
        { title: "案件名称", dataIndex: "title" },
        { title: "案件编号", dataIndex: "case_id", render: (id: string) => <Text code>{id}</Text> },
        { title: "当前主体 UUID", dataIndex: "legal_entity_id", render: (id: string) => <Text code>{id}</Text> },
        { title: "版本", dataIndex: "version", render: (version: number) => `v${version}` },
        { title: "操作", render: (_, row: { case_id: string }) => <Button type="link" onClick={() => navigate(`/entity-corrections/${row.case_id}`)}>纠正主体</Button> },
      ]}
      locale={{ emptyText: <Empty description="没有已确认主体的案件" /> }}
      scroll={{ x: 760 }}
    />}
    {!error && total > 20 && <Pagination className="case-pagination" current={page} pageSize={20} total={total} onChange={setPage} />}
  </>;
}

export function EntityCorrectionPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const [context, setContext] = useState<EntityCorrectionContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [form] = Form.useForm<{ legal_entity_id: string; correction_note: string }>();
  const [request, setRequest] = useState<{ legal_entity_id: string; correction_note: string; expected_case_version: number; idempotency_key: string } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setContext(null);
    setError(null);
    setRequest(null);
    setConfirmOpen(false);
    setUncertain(false);
    setMessage(null);
    form.resetFields();
    getEntityCorrectionContext(caseId, controller.signal).then(setContext).catch((reason: unknown) => {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
      }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [caseId, form]);

  useEffect(() => { document.title = "更正案件主体 — 合同审查工作台"; }, []);

  const stage = (values: { legal_entity_id: string; correction_note: string }) => {
    if (!context) return;
    setRequest((current) => current ?? {
      legal_entity_id: values.legal_entity_id.trim(),
      correction_note: values.correction_note.trim(),
      expected_case_version: context.version,
      idempotency_key: createUuid(),
    });
    setUncertain(false);
    setMessage(null);
    setConfirmOpen(true);
  };

  const submit = async () => {
    if (!context || !request || busy) return;
    setBusy(true);
    try {
      await correctCaseLegalEntity(caseId, request, request.idempotency_key);
      setConfirmOpen(false);
      setRequest(null);
      setUncertain(false);
      setMessage("主体已更正。原材料授权已撤销，既有运行已失效；继续审核需重新上传并授权材料。");
      const latest = await getEntityCorrectionContext(caseId);
      setContext(latest);
      form.resetFields();
    } catch (reason) {
      const apiError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
      if (apiError.status === 409) {
        setRequest(null);
        setConfirmOpen(false);
        setUncertain(false);
        setMessage("案件版本已变化。请刷新主体信息并重新核对后再发起更正。");
        getEntityCorrectionContext(caseId).then(setContext).catch(() => setError(apiError));
      } else if (apiError.status === 403 || apiError.status === 404) {
        setRequest(null);
        setConfirmOpen(false);
        setError(apiError);
      } else {
        setUncertain(true);
        setMessage("更正结果暂未确认。为避免重复生效，请使用同一请求重试；当前请求键和内容已保留。");
      }
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <RouteLoading title="主体纠错" />;
  if (!context || error) return <Result
    status={error?.status === 403 ? "403" : error?.status === 404 ? "404" : "error"}
    title={error?.status === 403 ? "当前身份不是主体管理员" : error?.status === 404 ? "案件不可用" : "主体信息暂时无法读取"}
    extra={<Space>
      <Button onClick={() => navigate("/entity-corrections")}>返回纠错队列</Button>
      <Button onClick={() => window.location.reload()}>重试</Button>
    </Space>}
  />;

  return <>
    <Breadcrumb items={[{ title: <Link to="/entity-corrections">主体纠错队列</Link> }, { title: "更正主体" }]} />
    <SectionHeading eyebrow="管理员专用 · 纠错上下文" title="更正案件主体" />
    <Alert type="info" showIcon title="这是隔离的纠错页面" description="页面仅读取案件 ID、当前主体 UUID 和版本；不请求或显示案件合同材料、运行结果与历史详情。" />
    <Card className="entity-confirmation-card">
      <Descriptions column={1} items={[
        { key: "case", label: "案件 ID", children: <Text code>{context.case_id}</Text> },
        { key: "entity", label: "当前主体 UUID", children: <Text code>{context.legal_entity_id}</Text> },
        { key: "version", label: "案件版本", children: `v${context.version}` },
      ]} />
      {message && <Alert className="document-warning" type="warning" showIcon title={message} />}
      <Form form={form} layout="vertical" noValidate onFinish={stage}>
        <Form.Item
          name="legal_entity_id"
          label="更正后的主体"
          rules={[
            { required: true, message: "请选择更正后的主体。" },
            { pattern: /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i, message: "请输入有效的 UUID。" },
            { validator: (_, value: string | undefined) => value?.trim().toLowerCase() === context.legal_entity_id.toLowerCase() ? Promise.reject(new Error("新主体必须与当前主体不同。")) : Promise.resolve() },
          ]}
        >
          <LegalEntitySelect excludeId={context.legal_entity_id} />
        </Form.Item>
        <Form.Item
          name="correction_note"
          label="纠错依据"
          rules={[{ required: true, whitespace: true, message: "请填写纠错依据。" }, { max: 500, message: "纠错依据不能超过 500 个字符。" }]}
        >
          <Input.TextArea rows={4} maxLength={500} />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit">核对更正影响</Button>
          <Button onClick={() => navigate("/entity-corrections")}>返回队列</Button>
        </Space>
      </Form>
    </Card>
    <Modal
      open={confirmOpen}
      title="确认更正案件主体"
      okText={uncertain ? "使用同一请求重试" : request ? "确认更正" : "请先核对信息"}
      cancelText={uncertain ? "结果确认前不可返回修改" : "返回修改"}
      cancelButtonProps={{ disabled: uncertain }}
      confirmLoading={busy}
      closable={!busy && !uncertain}
      mask={{ closable: !busy && !uncertain }}
      onOk={submit}
      onCancel={() => { if (!busy && !uncertain) { setConfirmOpen(false); setRequest(null); } }}
    >
      {message && <Alert className="document-warning" type="error" showIcon title={message} />}
      <Paragraph>新主体：<Text code>{request?.legal_entity_id}</Text></Paragraph>
      <Paragraph>更正后，当前主体范围内的材料选择和授权会撤销，既有运行与任务会失效；历史快照保留。后续审查需在新主体范围重新上传并授权材料。</Paragraph>
      <Paragraph type="secondary">若请求结果不确定，重试会复用同一幂等请求。案件版本不匹配时必须重新核对。</Paragraph>
    </Modal>
  </>;
}
