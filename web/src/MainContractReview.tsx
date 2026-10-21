import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Form, Input, Modal, Space, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import {
  ApiError, confirmCaseLegalEntity, freezeCaseSources, previewCaseSources,
  selectCaseDocument, startAuditRun,
  type CaseDocument, type CaseRecord, type FrozenInput, type SourcePreview,
} from "./api";
import { createUuid } from "./browserUuid";
import { DocumentParseStatus } from "./DocumentParseStatus";
import { LegalEntitySelect } from "./LegalEntitySelect";

type ReviewValues = { legal_entity_id: string; verification_note: string; consent: boolean; hours: string };
type ReviewAttempt = {
  documentId: string;
  entity?: { legal_entity_id: string; verification_note: string; expected_case_version: number; key: string };
  grant?: { document_version_id: string; relation: "main"; purpose: "contract_review"; confirm_project_relation: false; allow_amount: true; valid_until: string };
  preview?: SourcePreview;
  frozen?: FrozenInput;
  runKey: string;
};

export function MainContractReview({ caseRecord, documents, unavailable, busy, onBusyChange, onRefresh, onMaterials }: {
  caseRecord: CaseRecord;
  documents: CaseDocument[];
  unavailable: boolean;
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onRefresh: (signal?: AbortSignal) => Promise<{ caseRecord: CaseRecord; documents: CaseDocument[] }>;
  onMaterials: () => void;
}) {
  const navigate = useNavigate();
  const [form] = Form.useForm<ReviewValues>();
  const [open, setOpen] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<ReviewAttempt | null>(null);
  const [chosenId, setChosenId] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const alive = useRef(true);
  const submitting = useRef(false);
  const contracts = documents.filter((item) => item.role === "primary_contract" && ["staged", "active"].includes(item.document_status));
  const selectedMain = contracts.find((item) => item.selection_status === "selected");
  const main = selectedMain ?? contracts.find((item) => item.document_version_id === chosenId) ?? (contracts.length === 1 ? contracts[0] : undefined);
  const authorization = main?.purpose_authorizations?.find((item) => item.purpose === "contract_review");
  const authorized = authorization
    ? authorization.status === "authorized" && authorization.allow_text && authorization.allow_amount && Date.parse(authorization.valid_until) > Date.now()
    : main?.authorization_status === "authorized" && main.amount_authorized && Date.parse(main.authorization_valid_until ?? "") > Date.now();
  const amountMissing = authorization?.status === "authorized" && !authorization.allow_amount
    || main?.authorization_status === "authorized" && !main.amount_authorized;
  const processing = contracts.some((item) => ["queued", "processing"].includes(item.parse_status) || ["queued", "running"].includes(item.index_status));
  const ready = main && ["completed", "needs_material"].includes(main.parse_status) && main.index_status === "ready";
  const attachments = documents.filter((item) => item.role === "attachment" && item.selection_status === "selected");

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    if (!processing || busy) return;
    const controller = new AbortController();
    let timer: number;
    const refresh = async () => {
      try { await onRefresh(controller.signal); }
      catch { if (!controller.signal.aborted) setError("材料状态更新失败，请点击刷新状态后继续。"); return; }
      if (!controller.signal.aborted) timer = window.setTimeout(() => void refresh(), 2500);
    };
    timer = window.setTimeout(() => void refresh(), 2500);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [processing, busy, onRefresh, refreshKey]);

  const submit = async (values: ReviewValues) => {
    if (!main || submitting.current) return;
    submitting.current = true;
    onBusyChange(true);
    setError(null);
    let command = attempt;
    let step = "材料核验";
    const update = (next: ReviewAttempt) => { command = next; if (alive.current) setAttempt(next); };
    const progress = (text: string) => { step = text; if (alive.current) setStage(text); };
    try {
      progress("材料核验");
      const latest = await onRefresh();
      if (!alive.current) return;
      const currentMain = latest.documents.find((item) => item.document_version_id === main.document_version_id);
      if (!currentMain || !["completed", "needs_material"].includes(currentMain.parse_status) || currentMain.index_status !== "ready") {
        throw new Error("主合同尚未处理完成，请等待材料就绪后重试。");
      }
      if (!command) update({ documentId: main.document_version_id, runKey: createUuid() });
      if (!command) return;
      if (latest.caseRecord.legal_entity_id === null) {
        progress("确认签约主体");
        if (!command.entity) update({ ...command, entity: {
          legal_entity_id: values.legal_entity_id.trim(), verification_note: values.verification_note.trim(),
          expected_case_version: latest.caseRecord.version, key: createUuid(),
        } });
        const entity = command.entity!;
        await confirmCaseLegalEntity(caseRecord.case_id, {
          legal_entity_id: entity.legal_entity_id, verification_note: entity.verification_note,
          expected_case_version: entity.expected_case_version,
        }, entity.key);
        if (!alive.current) return;
      }
      const currentGrant = currentMain.purpose_authorizations?.find((item) => item.purpose === "contract_review");
      const hasGrant = currentGrant
        ? currentGrant.status === "authorized" && currentGrant.allow_text && currentGrant.allow_amount && Date.parse(currentGrant.valid_until) > Date.now()
        : currentMain.authorization_status === "authorized" && currentMain.amount_authorized && Date.parse(currentMain.authorization_valid_until ?? "") > Date.now();
      if (!command.frozen && (command.grant || !hasGrant || currentMain.selection_status !== "selected")) {
        progress("授权本次审核");
        if (!command.grant) {
          if (!values.consent || !["1", "4", "8", "24"].includes(values.hours)) {
            setAttempt(null);
            throw new Error("授权状态已变化，请确认文本与金额读取授权，并选择有效期。");
          }
          update({ ...command, grant: {
            document_version_id: main.document_version_id, relation: "main", purpose: "contract_review",
            confirm_project_relation: false, allow_amount: true,
            valid_until: new Date(Date.now() + Number(values.hours) * 3600000).toISOString(),
          } });
        }
        await selectCaseDocument(caseRecord.case_id, command.grant!);
        if (!alive.current) return;
      }
      if (!command.frozen) {
        progress("核对审核范围");
        if (!command.preview) update({ ...command, preview: await previewCaseSources(caseRecord.case_id) });
        if (!alive.current) return;
        const preview = command.preview!;
        if (!preview.items.length || preview.items.some((item) => item.status !== "ready") || !preview.items.some((item) => item.relation === "main" && item.document_version_id === main.document_version_id)) {
          update({ ...command, preview: undefined });
          throw new Error("本次材料未全部就绪。请在“背景与材料”核对解析与索引，在“审核范围与读取授权”核对权限，再重试。");
        }
        progress("保存本次审核输入");
        update({ ...command, frozen: await freezeCaseSources(caseRecord.case_id, preview) });
        if (!alive.current) return;
        await onRefresh();
        if (!alive.current) return;
      }
      progress("创建审核运行");
      const run = await startAuditRun(caseRecord.case_id, command.frozen!.input_snapshot_id, command.runKey);
      if (alive.current) navigate(`/runs/${run.run_id}`);
    } catch (reason) {
      if (!alive.current) return;
      if (reason instanceof ApiError) {
        const message = reason.status === 503
          ? reason.code === "review_execution_disabled" ? "当前服务未开启开发审核，请设置 CONTRACT_REVIEW_EXECUTION_ENABLED=true 后使用同一请求重试。" : "审核服务或模型配置不可用，请检查 API 与审核 Worker 后重试。"
          : reason.status === 409 ? "案件或材料授权已变化。请重新核对材料后发起审核。"
            : [401, 403, 404].includes(reason.status) ? "当前身份无法完成此项审核，请刷新案件并核对权限。"
              : "连接结果待核实。重试将复用已保存的输入和同一运行请求，避免重复提交。";
        setError(`${step}未完成：${message}`);
        if (reason.status === 409) setAttempt(null);
      } else setError(reason instanceof Error ? reason.message : "审核未启动，请刷新材料状态后重试。");
    } finally {
      submitting.current = false;
      if (alive.current) { setStage(""); onBusyChange(false); }
    }
  };

  return <Card className="main-contract-review" aria-labelledby="main-review-title">
    <div className="main-review-heading"><div><span className="eyebrow">主合同 / 审核准备</span><Typography.Title level={4} id="main-review-title">发起主合同审核</Typography.Title></div><Tag>{main ? ready ? "材料已就绪" : main.parse_status === "failed" || main.index_status === "failed" ? "材料处理失败" : "材料尚未就绪" : contracts.length ? "待选择主合同" : "待上传"}</Tag></div>
    {contracts.length > 1 && !selectedMain && <div className="review-contract-choice"><label htmlFor="review-main-document">选择本次主合同</label><select id="review-main-document" value={chosenId} disabled={busy || !!attempt} onChange={(event) => setChosenId(event.target.value)}><option value="">请选择一份主合同</option>{contracts.map((item) => <option key={item.document_version_id} value={item.document_version_id}>{item.file_name}</option>)}</select></div>}
    {main ? <div className="main-review-file"><Typography.Text strong>{main.file_name}</Typography.Text><DocumentParseStatus status={main.parse_status} errorCode={main.parse_error_code} /><Typography.Text type="secondary">{main.index_status === "ready" ? "原文定位已就绪" : main.index_status === "failed" ? "原文定位处理失败，请核对索引状态" : main.index_status === "stale" ? "原文定位已过期，请重新处理" : "正在准备原文定位"}</Typography.Text></div> : <Typography.Paragraph type="secondary">上传一份 PDF 或 DOCX 主合同，系统处理完成后即可发起审核。</Typography.Paragraph>}
    {main && <div className="review-preflight"><span>签约主体：{caseRecord.legal_entity_id ? "已确认" : "尚未确认"}</span><span>文本与金额读取授权：{authorized ? "已授权" : "尚需确认"}</span></div>}
    {caseRecord.legal_entity_id === null && <Alert type="info" showIcon title="请先完成主合同与主体确认" description="审核前需要先在第一步确认签约主体，再补充材料并核对范围。" />}
    {attachments.length > 0 && <Typography.Paragraph type="secondary">本次还包括已选入的 {attachments.length} 份附件。可在下方审核范围中核对。</Typography.Paragraph>}
    {main && !ready && <Typography.Paragraph type="secondary">解析与原文定位完成后开放审核。处理失败时请核对材料状态；刷新页面不会重新上传。</Typography.Paragraph>}
    {amountMissing && <Alert type="warning" showIcon title="当前主合同授权不含金额" description="本次审核需要文本与金额读取授权。请在下方“审核范围与读取授权”中撤销旧授权并重新授权；系统不会直接扩大已有权限。" />}
    {unavailable && <Alert type="warning" showIcon title="材料目录尚未核实，请先刷新状态" />}
    {error && !open && <Alert type="error" showIcon title={error} />}
    {contracts.length === 0 && <Button onClick={onMaterials}>前往上传主合同</Button>}
    {contracts.length > 0 && <Space wrap className="main-review-actions">
      <Button type="primary" size="large" disabled={!ready || !caseRecord.legal_entity_id || busy || unavailable || !!amountMissing} onClick={() => { setOpen(true); setError(null); }}>开始审核</Button>
      {main && <Button disabled={busy} onClick={() => { setError(null); void onRefresh().then(() => setRefreshKey((key) => key + 1)).catch(() => setError("状态刷新失败，请检查服务连接后重试。")); }}>刷新状态</Button>}
      {contracts.length > 0 && <Button disabled={busy} onClick={onMaterials}>管理背景与材料</Button>}
    </Space>}
    <Typography.Paragraph type="secondary" className="main-review-scope">模型将理解合同内容，检索本地法规、联网法律资料和公司制度，分别展示比对结果与原文依据。仅使用公司知识库中已启用的文件。</Typography.Paragraph>
    <Modal open={open} title="开始主合同审核" okText={stage || (attempt ? "使用同一请求继续" : "确认并开始审核")} cancelText="暂不审核" confirmLoading={!!stage} onOk={() => form.submit()} onCancel={() => { if (!stage) setOpen(false); }} closable={!stage} mask={{ closable: !stage }} cancelButtonProps={{ disabled: !!stage }}>
      <Typography.Paragraph>本次使用“{main?.file_name}”{attachments.length ? `及 ${attachments.length} 份已选附件` : ""}。确认后系统保存固定材料输入并创建审核运行。</Typography.Paragraph>
      <Form name="main-contract-review" form={form} layout="vertical" noValidate onFinish={(values) => void submit(values)} disabled={!!stage || !!attempt}>
        {caseRecord.legal_entity_id === null && <><Typography.Paragraph type="secondary">首次审核需要先选择已核实的签约主体，并记录本案核对依据。</Typography.Paragraph><Form.Item name="legal_entity_id" label="签约主体" rules={[{ required: true, message: "请选择已核实的签约主体。" }]}><LegalEntitySelect disabled={!!stage || !!attempt} /></Form.Item><Form.Item name="verification_note" label="主体核对依据" rules={[{ required: true, whitespace: true, message: "请记录主体核对依据。" }, { max: 500, message: "核对依据最多 500 字。" }]}><Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} maxLength={500} /></Form.Item></>}
        {!authorized && <><Form.Item name="consent" valuePropName="checked" rules={[{ validator: (_, value) => value ? Promise.resolve() : Promise.reject(new Error("请明确确认本次读取授权。")) }]}><Checkbox>允许当前审核人将此主合同的文本与金额字段用于本次合同审核</Checkbox></Form.Item><Form.Item name="hours" label="读取授权有效期" rules={[{ required: true, message: "请选择授权有效期。" }]}><select className="review-validity" aria-label="读取授权有效期" disabled={!!stage || !!attempt}><option value="">请选择有效期</option><option value="1">1 小时</option><option value="4">4 小时</option><option value="8">8 小时</option><option value="24">24 小时</option></select></Form.Item></>}
      </Form>
      {error && <Alert type="error" showIcon title={error} />}
      {stage && <div role="status" aria-live="polite">正在{stage}…</div>}
    </Modal>
  </Card>;
}
