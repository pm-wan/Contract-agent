import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Drawer, Empty, Form, Input, Modal, Space, Table, Tabs, Tag, Typography, message } from "antd";
import { useAuth } from "./AuthContext";
import { SectionHeading } from "./shared-ui";
import { getKnowledgeCatalog, getKnowledgeDocument, setKnowledgeStatus, uploadKnowledgeDocument, type KnowledgeCatalog, type KnowledgeDocument } from "./knowledgeApi";

const labels = { draft: "待确认", published: "审核可用", withdrawn: "已停用" };
type Values = { title: string; kind: "legal" | "company"; issuer: string; scope: string; source_url: string; effective_from?: string; effective_until?: string };
export function CompanyRulesPage() {
  const auth = useAuth();
  const canManage = auth.user?.permissions.includes("company_rules.manage");
  const [catalog, setCatalog] = useState<KnowledgeCatalog | null>(null);
  const [kind, setKind] = useState<"legal" | "company">("company");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [previous, setPrevious] = useState<KnowledgeDocument | null>(null);
  const [preview, setPreview] = useState<KnowledgeDocument | null>(null);
  const [error, setError] = useState("");
  const [form] = Form.useForm<Values>();
  const [toast, holder] = message.useMessage();
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try { setCatalog(await getKnowledgeCatalog(signal)); setError(""); }
    catch (reason) { if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "读取失败。"); }
  }, []);
  useEffect(() => { document.title = "公司知识库 — 合同审查工作台"; const controller = new AbortController(); void refresh(controller.signal); return () => controller.abort(); }, [refresh]);
  const newUpload = (source?: KnowledgeDocument) => {
    setPrevious(source ?? null); setFile(null); setError(""); form.resetFields();
    form.setFieldsValue(source ? { ...source, effective_from: source.effective_from ?? undefined, effective_until: source.effective_until ?? undefined } : { kind, issuer: "", scope: "", source_url: "" }); setOpen(true);
  };
  const upload = async (values: Values) => {
    if (!file) { setError("请选择资料文件。"); return; }
    setBusy(true); setError("");
    try {
      const data = new FormData(); data.append("file", file);
      Object.entries(values).forEach(([key, value]) => { if (value) data.append(key, String(value)); });
      if (previous) data.append("source_key", previous.source_key);
      await uploadKnowledgeDocument(data); setOpen(false); await refresh(); toast.success("文件已解析，请核对原文后设为审核可用。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败。"); }
    finally { setBusy(false); }
  };
  const action = async (item: KnowledgeDocument) => {
    setBusy(true);
    try { await setKnowledgeStatus(item.id, item.status === "draft" ? "publish" : "withdraw"); await refresh(); }
    catch (reason) { toast.error(reason instanceof Error ? reason.message : "操作失败。"); }
    finally { setBusy(false); }
  };
  const showSource = async (id: string) => {
    try { setPreview(await getKnowledgeDocument(id)); }
    catch (reason) { toast.error(reason instanceof Error ? reason.message : "原文读取失败。"); }
  };
  return <main className="company-rules-page">
    {holder}
    <SectionHeading eyebrow="审核依据" title="公司知识库" description="上传法规全文和公司制度文件。审核时，模型根据合同内容提出问题，检索相关原文并进行比对。" extra={canManage && <Button type="primary" onClick={() => newUpload()}>上传资料文件</Button>} />
    {error && !open && <Alert type="error" title={error} showIcon />}
    <Card className="company-rules-alert"><Space wrap>
      <Tag color="blue">本地法规：{catalog?.items.filter((item) => item.kind === "legal" && item.status === "published").length ?? 0} 份可用</Tag>
      <Tag color={catalog?.online_legal_search.configured ? "green" : "gold"}>联网法律查询：{catalog?.online_legal_search.configured ? `已接入 ${catalog.online_legal_search.provider === "deepseek" ? "DeepSeek" : catalog.online_legal_search.provider}` : "待配置检索服务"}</Tag>
    </Space>
      {!catalog?.online_legal_search.configured && <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>联网查询尚未接入当前模型服务。检索失败或缺少依据会记录为待查证。</Typography.Paragraph>}
    </Card>
    <Tabs activeKey={kind} onChange={(key) => setKind(key as "legal" | "company")} items={[{ key: "company", label: "公司制度文件" }, { key: "legal", label: "法律法规全文" }]} />
    <Table<KnowledgeDocument> rowKey="id" loading={!catalog && !error} dataSource={catalog?.items.filter((item) => item.kind === kind)} scroll={{ x: 850 }} pagination={{ pageSize: 8 }} locale={{ emptyText: <Empty description="上传文件，核对原文后启用。" /> }} columns={[
      { title: "资料文件", key: "title", render: (_, item) => <><Button type="link" onClick={() => void showSource(item.id)}>{item.title}</Button><div><Typography.Text type="secondary">v{item.version} · {item.passage_count} 个原文段落</Typography.Text></div></> },
      { title: "适用范围", dataIndex: "scope", render: (value: string) => value || "待核实" },
      { title: "生效期间", key: "dates", render: (_, item) => `${item.effective_from ?? "待核实"} ～ ${item.effective_until ?? "未指定截止日"}` },
      { title: "状态", dataIndex: "status", render: (value: KnowledgeDocument["status"]) => <Tag color={value === "published" ? "green" : "default"}>{labels[value]}</Tag> },
      { title: "操作", key: "actions", render: (_, item) => <Space wrap><Button onClick={() => void showSource(item.id)}>查看原文</Button>{canManage && <><Button onClick={() => newUpload(item)}>上传新版本</Button>{item.status !== "withdrawn" && <Button loading={busy} onClick={() => void action(item)}>{item.status === "draft" ? "设为审核可用" : "停用"}</Button>}</>}</Space> },
    ]} />
    <Modal title={previous ? `上传新版本 · ${previous.title}` : "上传资料文件"} open={open} onCancel={() => !busy && setOpen(false)} onOk={() => form.submit()} confirmLoading={busy} okText="解析并保存" cancelButtonProps={{ disabled: busy }}>
      <Form form={form} layout="vertical" onFinish={(values) => void upload(values)} disabled={busy}>
        <Form.Item name="kind" hidden><Input /></Form.Item>
        <Form.Item label="资料类型"><Tag>{(previous?.kind ?? kind) === "legal" ? "法律法规全文" : "公司制度文件"}</Tag></Form.Item>
        <Form.Item label="文件（PDF、DOCX、TXT、MD，最大10MB）" required><input key={String(open) + previous?.id} type="file" accept=".pdf,.docx,.txt,.md" disabled={busy} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></Form.Item>
        <Form.Item name="title" label="资料名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={255} /></Form.Item>
        <Form.Item name="issuer" label="发布机构 / 公司"><Input maxLength={255} /></Form.Item>
        <Form.Item name="scope" label="适用范围"><Input.TextArea placeholder="例如：公司作为卖方的国内销售合同" /></Form.Item>
        <Space align="start"><Form.Item name="effective_from" label="生效日期"><Input type="date" /></Form.Item><Form.Item name="effective_until" label="失效日期"><Input type="date" /></Form.Item></Space>
        <Form.Item name="source_url" label="原文来源链接"><Input placeholder="https://…" /></Form.Item>
      </Form>{error && <Alert type="error" showIcon title={error} />}
    </Modal>
    <Drawer title={preview?.title} open={!!preview} onClose={() => setPreview(null)} size="large">
      {preview && <><Space wrap><Tag>v{preview.version}</Tag><Tag>{labels[preview.status]}</Tag><a href={`/api/v1/knowledge-documents/${preview.id}/file`}>下载原文件</a>{preview.source_url && <a href={preview.source_url} target="_blank" rel="noreferrer">查看来源网站</a>}</Space>
        <Typography.Paragraph type="secondary">{preview.scope} · 发布机构：{preview.issuer || "未提供"}</Typography.Paragraph>
        {preview.warnings.map((warning) => <Alert key={warning} type="warning" showIcon title={warning} />)}
        {preview.segments?.map((segment, i) => <section key={i} style={{ marginTop: 16 }}><Typography.Text type="secondary">{segment.locator}</Typography.Text><Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{segment.text}</Typography.Paragraph></section>)}
      </>}
    </Drawer>
  </main>;
}
