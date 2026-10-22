import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Result,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  confirmRunFacts,
  decideHumanTask,
  getAuditRun,
  getCanonicalRenderedPdf,
  getOriginalDocument,
  getParsedDocumentArtifact,
  getRunResults,

  startAuditRun,
  type AuditRun,
  type CheckResult,
  type FactSetSnapshot,
  type ParsedDocumentArtifact,
  type RunEvidence,
  type RunResults,
  type FactConfirmationItem,
  type HumanTask,
} from "./api";
import { ReportComparisonSection, loadReportComparison, comparisonReportMarkdown, type ReportComparison } from "./ReportComparisonSection";
import { blobSha256 } from "./blobSha256";
import { createUuid } from "./browserUuid";
import { ResearchResults, type ResearchReview } from "./ResearchResults";
import { buildRunReportMarkdown, RunReportTooLarge } from "./runReport";

const { Title, Text, Paragraph } = Typography;
const DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

const statusLabels: Record<AuditRun["status"], string> = {
  queued: "等待处理",
  running: "正在处理",
  waiting: "报告已生成 · 含待核实事项",
  completed: "审核结果已生成",
  failed: "处理失败",
  cancelled: "已取消",
  superseded: "输入已更新",
};

const statusColors: Record<AuditRun["status"], string> = {
  queued: "default",
  running: "processing",
  waiting: "warning",
  completed: "success",
  failed: "error",
  cancelled: "default",
  superseded: "default",
};

function formatElapsedTime(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}小时${minutes}分`;
  if (minutes > 0) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

const judgmentLabels: Record<CheckResult["judgment"], string> = {
  passed: "候选信息已形成",
  issue_found: "候选信息需要核对",
  needs_material: "需要补充材料",
  undetermined: "暂未形成明确候选",
  not_applicable: "当前检查不适用",
  human_review: "需要人工复核",
};

const ruleLabels: Record<string, string> = {
  "legal.payment_conditions": "付款条款候选",
  "legal.price.definition": "合同价款口径",
};

const slotLabels: Record<string, string> = {
  payment_clause_present: "付款约定",
  payment_schedule: "付款阶段",
  payment_triggers: "付款触发条件",
  prepayment_ratio: "验收前付款比例",
  payment_risk_judgment: "付款风险判断",
  price_basis: "价款金额口径",
  price_sources: "价款来源",
  payment_basis: "付款分母与阶段金额",
  price_basis_judgment: "价款口径核对结果",
};

const priceSourceLabels: Record<string, string> = {
  contract_body: "合同正文",
  price_table: "价款表",
  attachment: "附件",
  supplementary_agreement: "补充协议",
};

const denominatorLabels: Record<string, string> = {
  contract_total: "合同总价",
  tax_exclusive_total: "未税价",
  stage_amount: "阶段金额",
  unspecified: "未明确",
};

const slotStateLabels: Record<string, string> = {
  filled: "已抽取，待核对",
  needs_material: "需要补充材料",
  conflicted: "候选存在冲突",
  undetermined: "尚无法判定",
  human_review: "需要人工复核",
};

const factLabels: Record<string, string> = {
  "contract.contract_type": "合同类型",
  "contract.party_role": "签约角色",
  "contract.jurisdiction": "司法辖区",
  "contract.total_amount": "合同总价",
};
type FactCandidate = NonNullable<CheckResult["fact_candidates"]>[number];
type SlotCandidate = NonNullable<CheckResult["slot_candidates"]>[number];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describeSlotValue(slotId: string, value: unknown): ReactNode {
  if (slotId === "payment_risk_judgment") {
    return <Text type="secondary">当前处理不会形成付款风险判断。</Text>;
  }
  if (value === null || value === undefined) {
    return <Text type="secondary">本次未形成可核验的候选值。</Text>;
  }
  if (slotId === "payment_clause_present") {
    if (value === true) return <Text>原文中定位到付款约定。</Text>;
    if (value === false) {
      return <Text>候选标记为未定位；这不代表合同缺少付款约定。</Text>;
    }
    return <Text type="secondary">候选值格式暂不可展示。</Text>;
  }
  if (slotId === "prepayment_ratio") {
    return typeof value === "string"
      ? <Text>比例候选：{value}（待核验）</Text>
      : <Text type="secondary">候选值格式暂不可展示。</Text>;
  }
  if (slotId === "payment_schedule") {
    if (!Array.isArray(value)) return <Text type="secondary">候选值格式暂不可展示。</Text>;
    if (value.length === 0) return <Text type="secondary">没有付款阶段候选。</Text>;
    return <ul className="run-slot-value-list">
      {value.map((entry, index) => {
        if (!isRecord(entry)) return <li key={index}>第 {index + 1} 项格式暂不可展示</li>;
        const details: string[] = [];
        if (entry.amount_type === "percentage" && typeof entry.percentage === "string") {
          details.push(`付款比例：${entry.percentage}%`);
        } else if (entry.amount_type === "fixed") {
          if (typeof entry.amount === "string") {
            details.push(`固定金额：${entry.amount}${typeof entry.currency === "string" ? ` ${entry.currency}` : ""}`);
          } else details.push("固定金额待核验");
        } else if (entry.amount_type === "residual") {
          details.push("剩余款项");
        } else {
          details.push("付款金额形式待核验");
        }
        return <li key={index}>
          <span>{typeof entry.trigger === "string" ? entry.trigger : `付款阶段 ${index + 1}`}</span>
          <span className="run-slot-value-detail">{details.join(" · ")}</span>
        </li>;
      })}
    </ul>;
  }
  if (slotId === "payment_triggers") {
    if (!Array.isArray(value)) return <Text type="secondary">候选值格式暂不可展示。</Text>;
    if (value.length === 0) return <Text type="secondary">没有付款触发条件候选。</Text>;
    return <ul className="run-slot-value-list">
      {value.map((entry, index) => {
        if (!isRecord(entry)) return <li key={index}>第 {index + 1} 项格式暂不可展示</li>;
        const conditions = Array.isArray(entry.conditions)
          ? entry.conditions.filter((item): item is string => typeof item === "string")
          : [];
        const requirements = [
          entry.acceptance_required === true ? "需验收" : null,
          entry.acceptance_required === false ? "候选未要求验收" : null,
          entry.invoice_required === true ? "需提供发票" : null,
          entry.invoice_required === false ? "候选未要求发票" : null,
        ].filter((item): item is string => item !== null);
        return <li key={index}>
          <span>{typeof entry.stage === "string" ? entry.stage : `触发条件 ${index + 1}`}</span>
          {conditions.length > 0 && <span className="run-slot-value-detail">{conditions.join("；")}</span>}
          {requirements.length > 0 && <span className="run-slot-value-detail">{requirements.join(" · ")}</span>}
        </li>;
      })}
    </ul>;
  }
  return <Text type="secondary">候选值格式暂不可展示。</Text>;
}

function priceBasisSection(
  result: CheckResult,
  caseId: string,
  openEvidence: (evidence: RunEvidence) => void,
  reviewTask: HumanTask | null,
  openReviewDecision: (decision: "record_review" | "request_material") => void,
  taskError: string | null,
): ReactNode {
  if (result.rule_id !== "legal.price.definition") return null;
  const candidates = new Map((result.slot_candidates ?? []).map((candidate) => [candidate.slot_id, candidate]));
  const basis = candidates.get("price_basis");
  const basisValue = isRecord(basis?.value) ? basis.value : {};
  const sources = candidates.get("price_sources");
  const sourceValues = Array.isArray(sources?.value) ? sources.value : [];
  const payment = candidates.get("payment_basis");
  const paymentValues = Array.isArray(payment?.value) ? payment.value : [];
  const judgment = candidates.get("price_basis_judgment");
  const judgmentValue = isRecord(judgment?.value) ? judgment.value : {};
  const reasons = Array.isArray(judgmentValue.reasons)
    ? judgmentValue.reasons.filter((reason): reason is string => typeof reason === "string")
    : [];
  const priceLimitations = result.limitations.filter((limitation) =>
    /价款|税额|金额|分母|币种/.test(limitation),
  );
  const hasUnspecifiedDenominator = paymentValues.some(
    (item) => isRecord(item) && item.denominator === "unspecified",
  );
  const requiresReview = [basis, sources, payment, judgment].some(
    (candidate) => candidate && ["needs_material", "conflicted", "human_review", "undetermined"].includes(candidate.state),
  ) || hasUnspecifiedDenominator;
  const candidateLinks = (candidate: SlotCandidate | undefined) => {
    if (!candidate || candidate.evidence_ids.length === 0) {
      return <Text type="secondary">没有可回跳的原文引用；不能据此采信候选值。</Text>;
    }
    return candidate.evidence_ids.map((evidenceId) => {
      const evidence = result.evidence.find((item) => item.evidence_id === evidenceId);
      return evidence
        ? <Button key={evidenceId} type="link" size="small" onClick={() => openEvidence(evidence)}>
            核对{evidence.page ? `第 ${evidence.page} 页` : "原文"}
          </Button>
        : <Text key={evidenceId} type="warning">证据编号不在当前结果中</Text>;
    });
  };
  const display = (value: unknown) =>
    typeof value === "string" && value.trim() ? value : "未形成候选";
  const amount = (value: unknown, currency: unknown) =>
    `${display(value)}${typeof currency === "string" && currency.trim() ? ` ${currency}` : ""}`;

  return <section className="price-basis-panel" aria-label="合同价款口径">
    <div className="price-basis-heading">
      <div>
        <Text strong>合同价款口径</Text>
        <Text type="secondary">金额按原文候选展示，不代表已确认或规则结论</Text>
      </div>
      <Tag color={requiresReview ? "warning" : "default"}>
        {requiresReview ? "待人工核对" : "候选待复核"}
      </Tag>
    </div>
    <dl className="price-basis-facts">
      <div><dt>含税价</dt><dd>{amount(basisValue.tax_inclusive_total, basisValue.currency)}</dd></div>
      <div><dt>未税价</dt><dd>{amount(basisValue.tax_exclusive_total, basisValue.currency)}</dd></div>
      <div><dt>税额</dt><dd>{amount(basisValue.tax_amount, basisValue.currency)}</dd></div>
      <div><dt>税率</dt><dd>{display(basisValue.tax_rate)}</dd></div>
    </dl>
    <div className="price-basis-citations">{candidateLinks(basis)}</div>
    <div className="price-basis-source-list" aria-label="价款来源逐项核对">
      <Text strong>合同中的金额来源</Text>
      {sourceValues.length === 0
        ? <Text type="secondary">没有形成价款来源候选。</Text>
        : sourceValues.map((source, index) => {
            if (!isRecord(source)) return <Text key={index} type="warning">第 {index + 1} 项格式无法读取。</Text>;
            return <div className="price-basis-source" key={`${String(source.source_type)}-${index}`}>
              <div>
                <Text>{priceSourceLabels[String(source.source_type)] ?? "其他来源"}</Text>
                <Text strong>{amount(source.amount, source.currency)}</Text>
                <Text type="secondary">{source.priority === "primary" ? "主要来源候选" : "其他来源候选"}</Text>
              </div>
            </div>;
          })}
      {sourceValues.length > 0 && <div className="price-basis-citations">{candidateLinks(sources)}</div>}
    </div>
    <div className="price-basis-payment-list" aria-label="付款分母和阶段金额">
      <Text strong>付款分母与阶段金额</Text>
      {paymentValues.length === 0
        ? <Text type="secondary">没有形成付款分母候选。</Text>
        : paymentValues.map((stage, index) => {
            if (!isRecord(stage)) return <Text key={index} type="warning">第 {index + 1} 项格式无法读取。</Text>;
            const amountDetails = [
              typeof stage.percentage === "string" ? `比例 ${stage.percentage}%` : null,
              typeof stage.fixed_amount === "string" ? `固定金额 ${amount(stage.fixed_amount, stage.currency)}` : null,
              stage.residual === true ? "剩余款项" : null,
            ].filter((value): value is string => value !== null);
            return <div className="price-basis-source" key={`${String(stage.stage)}-${index}`}>
              <div>
                <Text>{typeof stage.stage === "string" ? stage.stage : `付款阶段 ${index + 1}`}</Text>
                <Text strong>{amountDetails.join(" · ") || "金额形式未明确"}</Text>
                <Text type={stage.denominator === "unspecified" ? "warning" : "secondary"}>
                  分母：{denominatorLabels[String(stage.denominator)] ?? "未识别"}
                </Text>
              </div>
            </div>;
          })}
      {paymentValues.length > 0 && <div className="price-basis-citations">{candidateLinks(payment)}</div>}
    </div>
    {(reasons.length > 0 || requiresReview) && <Alert
      className="price-basis-review"
      type="warning"
      showIcon
      title={reasons.length > 0 ? "需人工核对的原因" : "需人工核对后再使用"}
      description={<>
        {[...new Set([...reasons, ...priceLimitations])].length > 0 && <ul>
          {[...new Set([...reasons, ...priceLimitations])].map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}
        </ul>}
        <Text>请核对上述所有来源位置；需要补充文件时，返回案件材料页面后重新冻结并发起新运行。本页不会确认价款金额或代替人工批准。</Text>
        <div><Link to={`/cases/${encodeURIComponent(caseId)}?view=background`}>返回案件材料与复核记录</Link></div>
      </>}
    />}
    {reviewTask?.type === "price_review" && reviewTask.status === "open" && <div className="price-review-actions">
      <Text strong>待处理人工复核任务</Text>
      <Text type="secondary">任务 {reviewTask.task_id.slice(0, 8)} · 请先核对上方所有原文引用。以下操作只记审阅动作，不改变规则状态。</Text>
      <Space wrap>
        <Button onClick={() => openReviewDecision("record_review")}>记录已核对，结论仍待定</Button>
        <Button type="primary" onClick={() => openReviewDecision("request_material")}>请求补充材料</Button>
      </Space>
    </div>}
    {reviewTask?.type === "price_review" && reviewTask.status === "completed" && <Alert
      type="success"
      showIcon
      title={reviewTask.decision === "request_material" ? "已记录补充材料请求" : "已记录人工核对动作"}
      description={<>
        <Text>该记录不会改变价款候选或付款规则结论。补充文件请在案件材料中选择并重新冻结，再发起新运行。</Text>
        <div><Link to={`/cases/${encodeURIComponent(caseId)}?view=background`}>前往案件材料</Link></div>
      </>}
    />}
    {taskError && <Alert type="warning" showIcon title={taskError} />}
  </section>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function errorMessage(error: ApiError) {
  if (error.status === 401) return "身份验证不可用。请重新登录或联系系统管理员。";
  if (error.status === 403 && ["run_source_access_changed", "result_source_not_authorized"].includes(error.code)) {
    return "本次审核引用的一份材料已撤销或授权失效。为保护材料，不显示受限结果。请确认当前材料版本及授权后重新发起审核。";
  }
  if (error.status === 403) return "当前账号无权查看此运行或材料来源。请联系案件审核人核实案件成员身份和材料授权。";
  if (error.status === 404) return "运行不存在或当前身份不可见。";
  if (error.status === 503) return "结果暂时无法读取；来源权限复核未完成。请稍后重试。";
  return "运行信息暂时无法读取。检查服务连接后重试。";
}

async function sha256Hex(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function validatedExcerpt(
  artifact: ParsedDocumentArtifact,
  evidence: RunEvidence,
): Promise<{ before: string; quote: string; after: string } | null> {
  if (
    evidence.source_version !== artifact.source_sha256
    || !Number.isInteger(evidence.page)
    || !Number.isInteger(evidence.start_offset)
    || !Number.isInteger(evidence.end_offset)
    || !evidence.page || evidence.page < 1
    || !evidence.quote
    || !evidence.text_hash
    || !Array.isArray(artifact.pages)
    || typeof evidence.start_offset !== "number" || evidence.start_offset < 0
    || typeof evidence.end_offset !== "number" || evidence.end_offset <= evidence.start_offset
  ) return null;
  const pageText = artifact.pages[evidence.page - 1];
  if (typeof pageText !== "string") return null;
  const codepoints = Array.from(pageText);
  const excerpt = codepoints.slice(evidence.start_offset, evidence.end_offset).join("");
  if (excerpt !== evidence.quote || await sha256Hex(excerpt) !== evidence.text_hash) return null;
  return {
    before: codepoints.slice(0, evidence.start_offset).join(""),
    quote: excerpt,
    after: codepoints.slice(evidence.end_offset).join(""),
  };
}

export function RunResultsPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<AuditRun | null>(null);
  const [data, setData] = useState<RunResults | null>(null);
  const [reportComparison, setReportComparison] = useState<ReportComparison | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setReportComparison(null);
    let timer: number | undefined;
    const refresh = async () => {
      if (!data) return;
      try {
        const value = await loadReportComparison(data, controller.signal);
        if (controller.signal.aborted) return;
        setReportComparison(value);
        if (value.notice.includes("正在执行")) timer = window.setTimeout(() => void refresh(), 5000);
      } catch { if (!controller.signal.aborted) setReportComparison({response:null, notice:"上下游比对结果暂时无法读取，报告尚未包含该部分。"}); }
    };
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [data]);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryCount, setRetryCount] = useState(0);
  const [pollingTimedOut, setPollingTimedOut] = useState(false);
  const [clockNow, setClockNow] = useState(() => Date.now());
  const [selectedEvidence, setSelectedEvidence] = useState<RunEvidence | null>(null);
  const [artifact, setArtifact] = useState<ParsedDocumentArtifact | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [excerpt, setExcerpt] = useState<{ before: string; quote: string; after: string } | null>(null);
  const [showOriginalPreview, setShowOriginalPreview] = useState(false);
  const [originalDocumentUrl, setOriginalDocumentUrl] = useState<string | null>(null);
  const [originalDocumentType, setOriginalDocumentType] = useState<string | null>(null);
  const [originalDocumentLoading, setOriginalDocumentLoading] = useState(false);
  const [originalDocumentError, setOriginalDocumentError] = useState<string | null>(null);
  const [factForm] = Form.useForm<{ value: string; reason: string }>();
  const [factToConfirm, setFactToConfirm] = useState<{
    resultId: string;
    candidate: FactCandidate;
  } | null>(null);
  const [factConfirmBusy, setFactConfirmBusy] = useState(false);
  const [factConfirmError, setFactConfirmError] = useState<string | null>(null);
  const [factConfirmRequest, setFactConfirmRequest] = useState<{
    body: { confirmations: FactConfirmationItem[]; reason: string };
    key: string;
  } | null>(null);
  const [factSetSnapshot, setFactSetSnapshot] = useState<FactSetSnapshot | null>(null);
  const [followupRunError, setFollowupRunError] = useState<string | null>(null);
  const [followupRunBusy, setFollowupRunBusy] = useState(false);
  const [followupRunRequestKey, setFollowupRunRequestKey] = useState<string | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [priceReviewTask, setPriceReviewTask] = useState<HumanTask | null>(null);
  const [priceReviewTaskError] = useState<string | null>(null);
  const [priceReviewDecision, setPriceReviewDecision] = useState<"record_review" | "request_material" | null>(null);
  const [priceReviewReason, setPriceReviewReason] = useState("");
  const [priceReviewBusy, setPriceReviewBusy] = useState(false);
  const [priceReviewError, setPriceReviewError] = useState<string | null>(null);
  const [priceReviewRequest, setPriceReviewRequest] = useState<{
    taskId: string;
    version: number;
    decision: "record_review" | "request_material";
    reason: string;
    key: string;
  } | null>(null);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setRun(null);
    setData(null);
    setSelectedEvidence(null);
    setShowOriginalPreview(false);
    setOriginalDocumentLoading(false);
    setOriginalDocumentUrl(null);
    setOriginalDocumentType(null);
    setOriginalDocumentError(null);
    setPollingTimedOut(false);
    setReportError(null);
    let pollCount = 0;

    const poll = async () => {
      try {
        const current = await getAuditRun(runId, controller.signal);
        if (!active) return;
        setRun(current);
        if (current.status === "completed" || current.status === "waiting") {
          const result = await getRunResults(runId, controller.signal);
          if (active) {
            setData(result);
            if (current.status === "completed") {
              timer = window.setTimeout(() => void poll(), 30_000);
              return;
            }
          }
        }
        if (["failed", "cancelled", "superseded"].includes(current.status)) return;
        pollCount += 1;
        if (pollCount >= 120) {
          setPollingTimedOut(true);
          return;
        }
        timer = window.setTimeout(() => void poll(), 2500);
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        if (active) {
          // A failed current-permission recheck must never leave derived text on screen.
          setData(null);
          setSelectedEvidence(null);
          setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    void poll();
    return () => {
      active = false;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [runId, retryCount]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [run?.run_id, run?.status]);

  useEffect(() => {
    document.title = "审核详情 — 合同审查工作台";
    const heading = document.querySelector<HTMLElement>(".content-inner h2");
    heading?.setAttribute("tabindex", "-1");
    heading?.focus({ preventScroll: true });
  }, [runId]);

  useEffect(() => {
    if (!selectedEvidence || !run?.case_id) return;
    let active = true;
    const controller = new AbortController();
    setEvidenceLoading(true);
    setArtifact(null);
    setExcerpt(null);
    setEvidenceError(null);
    if (!selectedEvidence.document_version_id) {
      setEvidenceError("该引用不是文档页引用，暂不支持原文回跳。");
      setEvidenceLoading(false);
      return () => controller.abort();
    }
    getParsedDocumentArtifact(run.case_id, selectedEvidence.document_version_id, controller.signal)
      .then(async (document) => {
        if (!active) return;
        setArtifact(document);
        const checked = await validatedExcerpt(document, selectedEvidence);
        if (active && checked) setExcerpt(checked);
        else if (active) setEvidenceError("来源版本、页码、偏移或引用校验不一致；未显示原文片段。");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        if (active) {
          const apiError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
          setEvidenceError(apiError.status === 403 || apiError.status === 404
            ? "当前身份无法读取该来源；引用内容已隐藏。请返回结果页刷新权限状态。"
            : "来源原文暂时无法读取。请重试或返回结果页。 ");
        }
      })
      .finally(() => { if (active) setEvidenceLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [selectedEvidence, run?.case_id]);

  useEffect(() => {
    if (!showOriginalPreview || !selectedEvidence?.document_version_id || !run?.case_id) return;
    let active = true;
    let objectUrl: string | null = null;
    const controller = new AbortController();
    setOriginalDocumentLoading(true);
    setOriginalDocumentError(null);
    setOriginalDocumentUrl(null);
    const isDocx = artifact?.media_type === DOCX_MEDIA_TYPE;
    const getPagePdf = isDocx ? getCanonicalRenderedPdf : getOriginalDocument;
    void getPagePdf(run.case_id, selectedEvidence.document_version_id, controller.signal)
      .then(async (original) => {
        if (!active) return;
        if (isDocx) {
          if (!artifact?.index_source_sha256 || await blobSha256(original) !== artifact.index_source_sha256) {
            throw new ApiError(0, "canonical_render_hash_mismatch");
          }
        }
        setOriginalDocumentType("application/pdf");
        objectUrl = URL.createObjectURL(original);
        setOriginalDocumentUrl(objectUrl);
      })
      .catch((reason: unknown) => {
        if (!active || (reason instanceof DOMException && reason.name === "AbortError")) return;
        const apiError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
        setOriginalDocumentError(apiError.status === 401 || apiError.status === 403 || apiError.status === 404
          ? "当前身份无法读取该材料页码视图。请返回结果页刷新权限状态。"
          : apiError.status === 415
            ? "该材料暂不支持页码视图；已校验的解析文本仍可供核对。"
            : apiError.status === 413
              ? "页码视图超过页面预览大小限制；请联系管理员处理。"
              : apiError.code === "canonical_render_hash_mismatch"
                ? "规范化 PDF 与被冻结的引用版本不一致，已隐藏该文件。请刷新结果后重试。"
                : "材料页码视图暂时无法读取，请稍后重试。 ");
      })
      .finally(() => { if (active) setOriginalDocumentLoading(false); });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [showOriginalPreview, selectedEvidence, run?.case_id, artifact]);

  const status = run?.status;
  const terminalStatus = status && ["failed", "cancelled", "superseded"].includes(status);
  const createdAtMs = run ? Date.parse(run.created_at) : Number.NaN;
  const elapsedSeconds = Number.isFinite(createdAtMs)
    ? Math.max(0, Math.floor((clockNow - createdAtMs) / 1000))
    : null;
  const message = error ? errorMessage(error) : "";
  const citationCount = data?.results.reduce((total, result) => total + result.evidence.length, 0) ?? 0;
  const firstCitationResultId = data?.results.find((result) => result.evidence.length > 0)?.result_id;

  const openEvidence = (item: RunEvidence) => {
    setShowOriginalPreview(false);
    setOriginalDocumentLoading(false);
    setOriginalDocumentUrl(null);
    setOriginalDocumentType(null);
    setOriginalDocumentError(null);
    setSelectedEvidence(item);
  };

  const openFactConfirmation = (
    resultId: string,
    candidate: FactCandidate,
  ) => {
    setFactToConfirm({ resultId, candidate });
    setFactConfirmError(null);
    setFactConfirmRequest(null);
    factForm.setFieldsValue({ value: candidate.value ?? "", reason: "" });
  };

  const submitFactConfirmation = async (values: { value: string; reason: string }) => {
    if (!factToConfirm || !runId) return;
    const body = factConfirmRequest?.body ?? {
      confirmations: [{
        predicate: factToConfirm.candidate.predicate,
        value: values.value.trim(),
        evidence_ids: factToConfirm.candidate.evidence_ids,
      }],
      reason: values.reason.trim(),
    };
    const request = factConfirmRequest ?? { body, key: createUuid() };
    setFactConfirmRequest(request);
    setFactConfirmBusy(true);
    setFactConfirmError(null);
    try {
      const snapshot = await confirmRunFacts(runId, request.body, request.key);
      setFactSetSnapshot(snapshot);
      setFactConfirmRequest(null);
      setFactToConfirm(null);
      factForm.resetFields();
    } catch (reason) {
      const apiError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
      setFactConfirmError(apiError.status === 404
        ? "运行或引用当前不可访问。请刷新结果并确认来源权限。"
        : apiError.status === 409
          ? "候选、运行状态或快照版本已变化。请关闭窗口并刷新后重新核对。"
          : apiError.status === 403
            ? "当前账号没有确认合同事实的权限。"
            : apiError.status === 503
              ? "事实确认服务暂时不可用。请稍后重试。"
              : "连接结果暂未确认。使用同一请求重试可避免重复生成快照。",
      );
      if (apiError.status >= 400 && apiError.status < 500) setFactConfirmRequest(null);
    } finally {
      setFactConfirmBusy(false);
    }
  };

  const openPriceReviewDecision = (decision: "record_review" | "request_material") => {
    if (!priceReviewTask || priceReviewTask.type !== "price_review" || priceReviewTask.status !== "open") return;
    setPriceReviewDecision(decision);
    setPriceReviewReason("");
    setPriceReviewError(null);
    setPriceReviewRequest(null);
  };

  const submitPriceReviewDecision = async () => {
    if (!priceReviewTask || !priceReviewDecision) return;
    const reason = priceReviewRequest?.reason ?? priceReviewReason.trim();
    if (!reason) {
      setPriceReviewError("请记录核对依据或需要补充的材料。此操作只记录流程，不改变规则结论。");
      return;
    }
    const request = priceReviewRequest ?? {
      taskId: priceReviewTask.task_id,
      version: priceReviewTask.version,
      decision: priceReviewDecision,
      reason,
      key: createUuid(),
    };
    setPriceReviewRequest(request);
    setPriceReviewBusy(true);
    setPriceReviewError(null);
    try {
      const updated = await decideHumanTask(
        request.taskId,
        { version: request.version, decision: request.decision, reason: request.reason },
        request.key,
      );
      setPriceReviewTask(updated);
      setPriceReviewDecision(null);
      setPriceReviewReason("");
      setPriceReviewRequest(null);
    } catch (reasonValue) {
      const requestError = reasonValue instanceof ApiError ? reasonValue : new ApiError(0, "request_failed");
      setPriceReviewError(requestError.status === 401 || requestError.status === 403
        ? "当前身份无法完成复核记录；请刷新结果和任务权限。"
        : requestError.status === 404
          ? "任务已不可见或来源权限已变化；请刷新结果。"
          : requestError.status === 409
            ? "任务版本已变化；请刷新后核对当前任务状态。"
            : "复核记录暂未确认保存。可使用同一请求安全重试。 ");
      if (requestError.status === 409 || requestError.status === 403 || requestError.status === 404) {
        setPriceReviewRequest(null);
        if (requestError.status === 409) setRetryCount((count) => count + 1);
      }
    } finally {
      setPriceReviewBusy(false);
    }
  };

  const startFollowupRun = async () => {
    if (!run?.case_id || !run.input_snapshot_id || !factSetSnapshot) return;
    setFollowupRunBusy(true);
    setFollowupRunError(null);
    const requestKey = followupRunRequestKey ?? createUuid();
    setFollowupRunRequestKey(requestKey);
    try {
      const started = await startAuditRun(
        run.case_id,
        run.input_snapshot_id,
        requestKey,
      );
      navigate(`/runs/${started.run_id}`);
    } catch (reason) {
      const apiError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
      setFollowupRunError(apiError.status === 503
        ? "事实集快照已保存；当前服务端未开放新运行，原有结果保持不变。"
        : apiError.status === 409
          ? "案件材料或授权范围已变化。请返回案件重新冻结材料后再运行。"
          : "新运行暂未启动。检查案件材料和授权后重试。",
      );
    } finally {
      setFollowupRunBusy(false);
    }
  };

  const downloadRunReport = async () => {
    if (!run || !["completed", "waiting"].includes(run.status)) return;
    setReportBusy(true);
    setReportError(null);
    try {
      // Recheck current server authorization immediately before creating the local file.
      const current = await getRunResults(run.run_id);
      if (!["completed", "waiting"].includes(current.run.status)) {
        setReportError("运行状态已变化，请刷新后再生成摘要。");
        return;
      }
      const comparison = await loadReportComparison(current);
      const markdown = buildRunReportMarkdown(current) + comparisonReportMarkdown(comparison);
      if (new TextEncoder().encode(markdown).byteLength > 4 * 1024 * 1024) throw new RunReportTooLarge();
      const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
      const urlApi = URL;
      const objectUrl = urlApi.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `contract-review-${run.run_id}.md`;
      link.style.display = "none";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => urlApi.revokeObjectURL(objectUrl), 1000);
    } catch (reason) {
      if (reason instanceof RunReportTooLarge) {
        setReportError("摘要超过 4 MiB 下载上限，未生成文件。请联系系统管理员评估结果范围。");
        return;
      }
      const apiError = reason instanceof ApiError ? reason : new ApiError(0, "request_failed");
      setReportError(apiError.status === 403 || apiError.status === 404
        ? "当前身份无法读取这份运行或其来源，未生成摘要文件。"
        : apiError.status === 503
          ? "服务端当前无法完成来源权限复核，未生成摘要文件。请稍后重试。"
          : "运行摘要生成失败；没有创建文件，请重试。",
      );
    } finally {
      setReportBusy(false);
    }
  };

  return <div className="review-report">
    <nav aria-label="页面层级"><Breadcrumb items={[
      { title: <Link to="/cases">案件列表</Link> },
      { title: run ? <Link to={`/cases/${run.case_id}?view=results`}>案件详情</Link> : "案件详情" },
      { title: "审核详情" },
    ]} /></nav>

    <header className="review-dossier-header">
      <div className="review-dossier-heading">
        <span className="eyebrow">CONTRACT REVIEW REPORT · 合同审查</span>
        <Title level={2}>合同审查报告</Title>
        <Text type="secondary">{run ? `审核批次 · ${formatDate(run.created_at)}` : "正在读取本次审核"}</Text>
      </div>
      <div className="review-dossier-actions">
        {run && <Tag color={statusColors[run.status]}>{statusLabels[run.status]}</Tag>}
        {run && ["completed", "waiting"].includes(run.status) && data && <Button loading={reportBusy} onClick={() => void downloadRunReport()}>下载审查报告</Button>}
      </div>
    </header>

    {reportError && <Alert className="run-boundary-notice" type="warning" showIcon title={reportError} />}
    {run && <span className="visually-hidden" role="status" aria-live="polite">运行状态：{statusLabels[run.status]}</span>}
    {error && <Result
      status={error.status === 403 ? "403" : error.status === 404 ? "404" : "500"}
      title={error.status === 403 && error.code === "run_source_access_changed" ? "材料授权已变更，审核结果已隐藏" : error.status === 403 ? "无法读取审核详情" : error.status === 404 ? "审核记录不可用" : "服务暂时不可用"}
      subTitle={message}
      extra={<Space><Button onClick={() => setRetryCount((count) => count + 1)}>重试</Button><Link to="/cases">返回案件列表</Link></Space>}
    />}
    {!error && loading && <div className="route-loading" aria-live="polite"><Spin size="large" description="正在读取审核状态…" /></div>}
    {!error && run && <>
      <section className="review-dossier-cover" aria-label="本次审核摘要">
        <div className="review-dossier-cover-top">
          <div>
            <span className="review-dossier-section-label">本次审核</span>
            <strong>{data ? `${data.results.length} 项结果记录` : statusLabels[run.status]}</strong>
          </div>
          <div>
            <span className="review-dossier-section-label">原文引用</span>
            <strong>{data ? `${citationCount} 处` : "读取中"}</strong>
          </div>
          <details className="review-dossier-archive">
            <summary>案卷编号与输入版本</summary>
            <dl>
              <div><dt>运行编号</dt><dd><Text code>{run.run_id}</Text></dd></div>
              <div><dt>输入快照</dt><dd>{run.input_snapshot_id ? <Text code>{run.input_snapshot_id}</Text> : "不可用"}</dd></div>
            </dl>
          </details>
        </div>
        <Alert
          className="review-dossier-boundary"
          type="warning"
          showIcon
          title={data?.results.some((item) => item.research_review) ? "法律与公司制度比对已按本次合同内容展开" : "本批次展示付款与价款候选信息"}
          description={data?.results.some((item) => item.research_review) ? "逐项查看合同原文、检索依据、适用性和处理建议；资料缺失及检索失败列在待核实事项中。" : "此批次使用旧版条款定位流程。新发起的审核会执行合同理解与资料检索比对。"}
        />
      </section>

      {data?.results.filter((item) => item.research_review).map((item) => <ResearchResults key={item.result_id} review={item.research_review as unknown as ResearchReview} onContractQuote={(quote) => {
        const evidence = item.evidence.find((source) => source.document_version_id === quote.document_version_id && source.page === quote.page && source.quote === quote.quote);
        if (evidence) openEvidence(evidence);
      }} />)}

      {data && <ReportComparisonSection value={reportComparison} />}

      {!error && run && (status === "queued" || status === "running") && <Card className={`run-progress-card run-progress-card-${status}${pollingTimedOut ? " run-progress-card-paused" : ""}`}>
        <div className="run-progress-heading">
          <div className="run-progress-heading-title">
            {!pollingTimedOut && <Spin size="small" aria-hidden="true" />}
            <div>
              <strong>{status === "queued" ? "审核任务已排队" : "正在分析合同内容"}</strong>
              <Text type="secondary">{status === "queued" ? "等待审核服务开始处理" : "审核服务正在处理本次固定材料输入"}</Text>
            </div>
          </div>
          {elapsedSeconds !== null && <Tag color={status === "running" ? "processing" : "default"}>从提交起已用时 {formatElapsedTime(elapsedSeconds)}</Tag>}
        </div>
        <div className="run-progress-track" role="progressbar" aria-label="审核进度" aria-valuetext={`${statusLabels[status]}${pollingTimedOut ? "，自动状态查询已暂停" : ""}`}>
          <span className="run-progress-indicator" aria-hidden="true" />
        </div>
        <Paragraph type="secondary" className="run-progress-note">{pollingTimedOut
          ? "自动状态查询已暂停；下方可手动刷新，查看最新运行状态。"
          : "页面会自动更新状态。进度条表示任务仍在排队或处理中，不代表剩余时间百分比。"}</Paragraph>
      </Card>}
      {run.status === "waiting" && data && <Alert
        className="review-dossier-waiting"
        type="info"
        showIcon
        title="审查报告已生成"
        description="需要进一步核实的事项已列入报告，请结合合同原文及引用依据阅读。"
      />}
      {terminalStatus && <Alert
        className="run-boundary-notice"
        type={status === "failed" ? "error" : "info"}
        showIcon
        title={status === "failed" ? "本次处理失败" : status === "cancelled" ? "本次审核已取消" : "输入已更新，本次结果已失效"}
        description="当前没有可展示的审核结果。请返回案件确认材料版本和运行状态。"
      />}
      {data && !error && !data.results.some((item) => item.research_review) && <>
        {factSetSnapshot && <Alert
          id="review-fact-snapshot"
          className="review-fact-snapshot"
          type="success"
          showIcon
          title={`已保存事实集快照 v${factSetSnapshot.version}`}
          description={<div className="fact-snapshot-receipt">
            <Text>确认值绑定当前冻结材料，只用于后续新运行；本次结果不会改变。</Text>
            <Text>确认人：<Text code>{factSetSnapshot.created_by}</Text> · {formatDate(factSetSnapshot.created_at)}</Text>
            <Text>理由：{factSetSnapshot.reason}</Text>
            <Text>快照编号：<Text code>{factSetSnapshot.fact_set_snapshot_id}</Text></Text>
            <ul>{Object.entries(factSetSnapshot.facts).map(([predicate, raw]) => {
              const value = isRecord(raw) && typeof raw.value === "string" ? raw.value : "已保存";
              return <li key={predicate}>{factLabels[predicate] ?? predicate}：{value}</li>;
            })}</ul>
          </div>}
          action={<Button loading={followupRunBusy} disabled={followupRunBusy} onClick={() => void startFollowupRun()}>按确认事实重新运行</Button>}
        />}
        {followupRunError && <Alert className="run-boundary-notice" type="warning" showIcon title={followupRunError} />}

        <div className="review-dossier-layout">
          <main className="review-dossier-main">
            <section id="review-result-list" aria-labelledby="review-result-list-title">
              <div className="review-section-heading">
                <div><span className="eyebrow">本次输出</span><Title level={3} id="review-result-list-title">审查发现与建议</Title></div>
                <Text type="secondary">{data.results.length} 项</Text>
              </div>
              {data.results.length === 0
                ? <Empty className="run-empty" description="本次审核没有返回可展示结果" />
                : <div className="review-result-list">{data.results.map((result, index) => <article
                    className="review-result"
                    id={`review-result-${result.result_id}`}
                    key={result.result_id}
                  >
                    <header className="review-result-heading">
                      <span className="review-result-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                      <div>
                        <span className="eyebrow">{ruleLabels[result.rule_id] ?? "检查结果"}</span>
                        <Title level={4}>{judgmentLabels[result.judgment]}</Title>
                      </div>
                      <Tag color="warning">待人工核验</Tag>
                    </header>
                    {result.summary && <Paragraph className="review-result-summary">{result.summary}</Paragraph>}
                    {priceBasisSection(
                      result,
                      data.run.case_id,
                      openEvidence,
                      priceReviewTask,
                      openPriceReviewDecision,
                      priceReviewTaskError,
                    )}
                    {result.fact_set_snapshot_id && <Alert
                      className="run-boundary-notice"
                      type="info"
                      showIcon
                      title="本次运行绑定的事实集快照"
                      description={<Text code>{result.fact_set_snapshot_id}</Text>}
                    />}
                    {result.fact_candidates && result.fact_candidates.length > 0 && <section className="run-slot-section" aria-label="合同事实候选">
                      <div className="run-slot-heading"><Text strong>合同事实候选</Text><Text type="secondary">待确认；未用于规则适用性</Text></div>
                      <dl className="run-slot-list">{result.fact_candidates.map((candidate) => <div className="run-slot-row" key={candidate.predicate}>
                        <dt><Text strong>{factLabels[candidate.predicate] ?? candidate.predicate}</Text><Tag color="warning">{candidate.state === "candidate" ? "候选，待确认" : "尚未识别"}</Tag></dt>
                        <dd>{candidate.value ?? "未形成有直接引文支持的候选值"}</dd>
                        <div className="run-slot-citations">{candidate.evidence_ids.map((id) => {
                          const item = result.evidence.find((entry) => entry.evidence_id === id);
                          return item ? <Button key={id} type="link" size="small" onClick={() => openEvidence(item)}>核对合同事实依据{item.page ? `（第 ${item.page} 页）` : ""}</Button> : null;
                        })}</div>
                        {data.can_confirm_facts && run.status === "completed" && candidate.state === "candidate"
                          && candidate.evidence_ids.length > 0 && <Button
                            type="default"
                            onClick={() => openFactConfirmation(result.result_id, candidate)}
                          >确认此项事实</Button>}
                      </div>)}</dl>
                    </section>}
                    {result.rule_id !== "legal.price.definition" && result.slot_candidates && <section className="run-slot-section" aria-label="付款条款事实候选">
                      <div className="run-slot-heading">
                        <Text strong>付款条款事实候选</Text>
                        <Text type="secondary">均待人工核验</Text>
                      </div>
                      {result.slot_candidates.length === 0
                        ? <Paragraph className="run-slot-empty" type="secondary">
                            本次运行没有返回结构化付款字段候选；这不代表合同缺少相关约定。
                          </Paragraph>
                        : <dl className="run-slot-list">
                            {result.slot_candidates.map((candidate) => <div className="run-slot-row" key={candidate.slot_id}>
                              <dt>
                                <Text strong>{slotLabels[candidate.slot_id] ?? "其他付款字段"}</Text>
                                <Tag color={candidate.state === "human_review" || candidate.state === "conflicted" ? "warning" : "default"}>
                                  {slotStateLabels[candidate.state] ?? "待核验"}
                                </Tag>
                              </dt>
                              <dd>{describeSlotValue(candidate.slot_id, candidate.value)}</dd>
                              <div className="run-slot-citations">
                                {candidate.evidence_ids.map((evidenceId) => {
                                  const item = result.evidence.find((evidenceItem) => evidenceItem.evidence_id === evidenceId);
                                  if (!item) return null;
                                  if (item.kind !== "document_span" || !item.document_version_id) {
                                    return <Text key={evidenceId} type="secondary">已绑定来源记录，暂不支持原文回跳</Text>;
                                  }
                                  return <Button
                                    key={evidenceId}
                                    type="link"
                                    size="small"
                                    onClick={() => openEvidence(item)}
                                  >核对付款字段依据{item.page ? `（第 ${item.page} 页）` : ""}</Button>;
                                })}
                                {candidate.state === "filled" && candidate.evidence_ids.length === 0 &&
                                  <Text type="warning">缺少可核对引用，不能据此采信候选值。</Text>}
                              </div>
                            </div>)}
                          </dl>}
                    </section>}
                    <section className="review-citations" id={`review-citations-${result.result_id}`} aria-label="引用与原文位置">
                      <div className="review-citations-heading">
                        <Text strong>引用与原文位置</Text>
                        <Text type="secondary">{result.evidence.length} 处</Text>
                      </div>
                      {result.evidence.length > 0
                        ? <ol className="review-citation-list">{result.evidence.map((item, evidenceIndex) => <li className="review-citation" key={item.evidence_id}>
                            <span className="review-citation-marker">{item.page ? `P.${String(item.page).padStart(2, "0")}` : `R.${String(evidenceIndex + 1).padStart(2, "0")}`}</span>
                            <div className="review-citation-content">
                              <div className="review-citation-meta">
                                <Tag>{item.support === "context" ? "上下文引用" : item.kind === "source_record" ? "背景来源" : "合同原文"}</Tag>
                                <Text type="secondary">{item.page ? `第 ${item.page} 页` : "页码未提供"}</Text>
                              </div>
                              {item.quote && <blockquote>{item.quote}</blockquote>}
                              {item.source_record_ref && <Text code>{item.source_record_ref}</Text>}
                              {item.document_version_id && item.kind === "document_span"
                                ? <Button type="link" onClick={() => openEvidence(item)}>打开来源位置 <span aria-hidden>›</span></Button>
                                : <Text className="review-citation-unavailable" type="secondary">此来源暂不支持原文回跳</Text>}
                            </div>
                          </li>)}</ol>
                        : <Alert type="info" showIcon title="本次没有可回跳的原文引用" description="不能据此认定合同缺少付款约定。" />}
                    </section>
                    {result.limitations.length > 0 && <details className="run-limitations">
                      <summary>本项处理范围与限制（{result.limitations.length}）</summary>
                      <ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
                    </details>}
                  </article>)}</div>}
            </section>
          </main>
          <aside className="review-dossier-index" aria-label="本次审核索引">
            <span className="eyebrow">报告目录</span>
            <a href="#review-result-list">审查发现 <span>{data.results.length}</span></a>
            {data.results.map((result, index) => <a
              className="review-dossier-index-subitem"
              href={`#review-result-${result.result_id}`}
              key={result.result_id}
            >{String(index + 1).padStart(2, "0")} · {ruleLabels[result.rule_id] ?? "检查结果"}</a>)}
            <a href={firstCitationResultId ? `#review-citations-${firstCitationResultId}` : "#review-result-list"}>引用位置 <span>{citationCount}</span></a>
            {factSetSnapshot && <a href="#review-fact-snapshot">事实集快照</a>}
          </aside>
        </div>
      </>}
      {pollingTimedOut && <Alert
        className="run-boundary-notice"
        type="warning"
        showIcon
        title="自动状态查询已暂停"
        description="运行超过 5 分钟仍未结束。可以安全刷新状态；此操作只读取运行，不会重复启动任务。"
        action={<Button onClick={() => setRetryCount((count) => count + 1)}>刷新状态</Button>}
      />}
    </>}
    <Modal
      open={!!factToConfirm}
      title={factToConfirm ? `确认${factLabels[factToConfirm.candidate.predicate] ?? "合同事实"}` : "确认合同事实"}
      okText={factConfirmRequest ? "使用同一请求重试" : "确认并保存快照"}
      cancelText="取消"
      confirmLoading={factConfirmBusy}
      onOk={() => factForm.submit()}
      onCancel={() => {
        if (factConfirmBusy) return;
        setFactToConfirm(null);
        setFactConfirmRequest(null);
        setFactConfirmError(null);
        factForm.resetFields();
      }}
      closable={!factConfirmBusy}
      mask={{ closable: !factConfirmBusy }}
      forceRender
      destroyOnHidden={false}
    >
      {factToConfirm && <>
        <Alert
          type="warning"
          showIcon
          title="请对照原文确认"
          description="确认记录会保存审核人、理由、引用和材料快照版本。它不会修改本次运行；新运行才会采用确认值。"
        />
        <div className="fact-confirm-evidence">
          <Text strong>候选原文</Text>
          {factToConfirm.candidate.evidence_ids.map((id) => {
            const evidence = data?.results
              .find((result) => result.result_id === factToConfirm.resultId)
              ?.evidence.find((item) => item.evidence_id === id);
            return evidence?.quote
              ? <blockquote key={id}>{evidence.quote}</blockquote>
              : <Text key={id} type="secondary">绑定原文暂不可用，请刷新结果后重试。</Text>;
          })}
        </div>
        {factConfirmError && <Alert className="document-warning" type="error" showIcon title={factConfirmError} />}
        <Form
          form={factForm}
          layout="vertical"
          noValidate
          onFinish={(values: { value: string; reason: string }) => void submitFactConfirmation(values)}
        >
          <Form.Item
            label="确认后的值"
            name="value"
            extra="可保留候选文本或填写经核实的标准值；后续规则按此值执行。"
            rules={[{ required: true, whitespace: true, message: "请填写确认后的事实值。" }, { max: 500, message: "事实值不能超过 500 个字符。" }]}
          >
            <Input maxLength={500} disabled={factConfirmBusy || !!factConfirmRequest} />
          </Form.Item>
          <Form.Item
            label="确认理由"
            name="reason"
            rules={[{ required: true, whitespace: true, message: "请记录确认理由。" }, { max: 4000, message: "确认理由不能超过 4000 个字符。" }]}
          >
            <Input.TextArea
              rows={3}
              maxLength={4000}
              disabled={factConfirmBusy || !!factConfirmRequest}
              style={{ resize: "none" }}
            />
          </Form.Item>
        </Form>
      </>}
    </Modal>
    <Modal
      open={priceReviewDecision !== null}
      title={priceReviewDecision === "request_material" ? "记录补充材料请求" : "记录人工核对"}
      okText={priceReviewRequest ? "使用同一请求重试" : "保存复核记录"}
      cancelText="取消"
      confirmLoading={priceReviewBusy}
      cancelButtonProps={{ disabled: priceReviewBusy || !!priceReviewRequest }}
      onOk={() => void submitPriceReviewDecision()}
      onCancel={() => {
        if (priceReviewBusy || priceReviewRequest) return;
        setPriceReviewDecision(null);
        setPriceReviewReason("");
        setPriceReviewError(null);
      }}
      closable={!priceReviewBusy && !priceReviewRequest}
      mask={{ closable: !priceReviewBusy && !priceReviewRequest }}
    >
      <Alert
        type="warning"
        showIcon
        title="此记录不确认金额，也不改变规则结论"
        description={priceReviewDecision === "request_material"
          ? "请写明需要补充的文件或信息。保存后可返回案件材料页继续处理。"
          : "请记录已核对的范围或仍待解决的问题。系统不会据此判定价款符合或不符合。"}
      />
      <Input.TextArea
        aria-label="复核记录理由"
        className="price-review-reason"
        value={priceReviewReason}
        onChange={(event) => setPriceReviewReason(event.target.value)}
        maxLength={4000}
        showCount
        autoSize={{ minRows: 3, maxRows: 8 }}
        disabled={priceReviewBusy || !!priceReviewRequest}
        placeholder={priceReviewDecision === "request_material"
          ? "例如：请提供签署版价款附件及适用的补充协议。"
          : "记录核对了哪些原文位置，以及仍待核实的事项。"}
      />
      {priceReviewError && <Alert className="price-review-error" type="error" showIcon title={priceReviewError} />}
    </Modal>
    <Drawer
      open={!!selectedEvidence}
      onClose={() => { setShowOriginalPreview(false); setOriginalDocumentLoading(false); setOriginalDocumentUrl(null); setOriginalDocumentType(null); setOriginalDocumentError(null); setSelectedEvidence(null); }}
      title="核对解析文本"
      size={minDrawerWidth()}
      destroyOnHidden
    >
      {selectedEvidence && <>
        <Descriptions size="small" column={1} items={[
          { key: "doc", label: "材料版本", children: <Text code>{selectedEvidence.document_version_id}</Text> },
          { key: "page", label: "页码", children: selectedEvidence.page ? `第 ${selectedEvidence.page} 页` : "未提供" },
          { key: "source", label: "原件哈希", children: <Text code>{selectedEvidence.source_version}</Text> },
        ]} />
        {evidenceLoading && <div className="evidence-loading" aria-live="polite"><Spin description="正在读取并校验引用来源…" /></div>}
        {evidenceError && <Alert type="warning" showIcon title="暂不能展示原文" description={evidenceError} />}
        {excerpt && <><Alert type="info" showIcon title={artifact?.media_type === DOCX_MEDIA_TYPE ? "以下页码和引用来自已冻结的 DOCX 规范化 PDF 呈现；Word 本身不保证跨环境固定分页。" : "以下为规范化解析文本，不保留 PDF 原始版式；请对照文件核验上下文。"} /><div className="verified-source-text" aria-label="已校验的解析文本"><p>{excerpt.before}<mark>{excerpt.quote}</mark>{excerpt.after}</p></div></>}
        {artifact && excerpt && <Text className="evidence-verified-note" type="secondary">已复核冻结来源版本、页码、字符偏移及引用文本哈希。此校验不代表法律判断。</Text>}
        {artifact && excerpt && selectedEvidence.page && <div className="original-document-tools">
          <Button
            loading={originalDocumentLoading}
            disabled={originalDocumentLoading}
            onClick={() => {
              if (showOriginalPreview) {
                setShowOriginalPreview(false);
                setOriginalDocumentUrl(null);
                setOriginalDocumentType(null);
                setOriginalDocumentError(null);
              } else setShowOriginalPreview(true);
            }}
          >{showOriginalPreview ? "关闭页码视图" : artifact?.media_type === DOCX_MEDIA_TYPE ? "打开规范 PDF 页码视图" : "打开 PDF 原件预览"}</Button>
          {originalDocumentError && <Alert type="warning" showIcon title="暂不能打开页码视图" description={originalDocumentError} />}
          {originalDocumentUrl && originalDocumentType === "application/pdf" && <>
            <Alert type="info" showIcon title={artifact?.media_type === DOCX_MEDIA_TYPE
              ? `已打开冻结的规范 PDF 第 ${selectedEvidence.page} 页（${artifact.renderer_identity ?? "渲染器身份缺失"}）；PDF 内未绘制引用高亮，请对照已校验引文。`
              : `原件已定位到第 ${selectedEvidence.page} 页；PDF 内未绘制引用高亮，请核对上下文。`} />
            <iframe
              className="original-pdf-frame"
              title={`${artifact?.media_type === DOCX_MEDIA_TYPE ? "DOCX 规范 PDF" : "PDF 原件"}，第 ${selectedEvidence.page} 页`}
              src={`${originalDocumentUrl}#page=${selectedEvidence.page}&toolbar=1&navpanes=0`}
            />
          </>}
        </div>}
      </>}
    </Drawer>
  </div>;
}

function minDrawerWidth() {
  return Math.min(560, Math.max(320, window.innerWidth - 24));
}
