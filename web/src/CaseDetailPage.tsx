import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Progress,
  Radio,
  Result,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import CloudUploadOutlined from "@ant-design/icons/es/icons/CloudUploadOutlined";
import DeleteOutlined from "@ant-design/icons/es/icons/DeleteOutlined";
import FileOutlined from "@ant-design/icons/es/icons/FileOutlined";
import LockOutlined from "@ant-design/icons/es/icons/LockOutlined";
import UploadOutlined from "@ant-design/icons/es/icons/UploadOutlined";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AsyncModuleBoundary } from "./AsyncModuleBoundary";
import {
  ApiError,
  confirmCaseLegalEntity,
  correctCaseLegalEntity,
  freezeCaseSources,
  getCase,
  listCaseDocuments,
  removeCaseDocument,
  previewCaseSources,
  revokeCaseDocumentSelection,
  selectCaseDocument,
  startUpstreamComparison,
  uploadCaseDocument,
  type CaseDocument,
  type CaseRecord,
  type FrozenInput,
  type SourcePreview,
} from "./api";
import { caseStatusLabel, formatDate } from "./case-format";
import { createUuid } from "./browserUuid";
import { DocumentParseStatus } from "./DocumentParseStatus";
import { SectionHeading } from "./shared-ui";
import { MainContractReview } from "./MainContractReview";
import { contractFileError } from "./contract-file";
import { LegalEntitySelect } from "./LegalEntitySelect";

const { Paragraph, Text, Title } = Typography;

const RunHistoryPanel = lazy(() =>
  import("./RunHistoryPanel").then(({ RunHistoryPanel: Panel }) => ({ default: Panel })),
);
const DocumentAccessTimeline = lazy(() =>
  import("./DocumentAccessTimeline").then(({ DocumentAccessTimeline: Panel }) => ({ default: Panel })),
);
const CaseLegalEntityTimeline = lazy(() =>
  import("./CaseLegalEntityTimeline").then(({ CaseLegalEntityTimeline: Panel }) => ({ default: Panel })),
);

function PanelLoading({ label }: { label: string }) {
  return <div className="route-loading" role="status" aria-live="polite"><Spin description={`正在载入${label}…`} /></div>;
}

type QueuedFile = {
  id: string;
  file: File;
  idempotencyKey: string;
  status: "queued" | "invalid" | "sending" | "processing" | "uploaded" | "failed";
  progress: number;
  error?: string;
};

type DocumentPurpose = "contract_review" | "upstream_comparison";

type SelectionRequest = {
  document_version_id: string;
  relation: "main" | "attachment" | "upstream";
  purpose: DocumentPurpose;
  confirm_project_relation: boolean;
  allow_amount: boolean;
  valid_until: string;
};

type RevocationRequest = {
  expected_case_version: number;
  reason: string;
  idempotencyKey: string;
};

type RemovalRequest = {
  expected_case_version: number;
  reason: string;
  idempotencyKey: string;
};

function uploadErrorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return "上传未完成，请重试。";
  if (error.status === 0) return "连接中断，上传结果待核实；材料目录已刷新，可使用同一文件安全重试。";
  if (error.status === 401) return "登录状态已失效，请返回登录页面重新登录。";
  if (error.status === 403) return "当前身份无权向此案件上传材料。";
  if (error.status === 409) return "案件状态已变化，无法继续上传。刷新案件后再试。";
  if (error.status === 413) return "文件超过服务端大小限制（30 MiB）。";
  if (error.status === 422) return "文件无法解析或格式不受支持，请检查文件后重试。";
  if (error.status === 503) return "材料服务暂不可用，请稍后重试。";
  return "上传未完成，请刷新材料目录后重试。";
}

function grantErrorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return "授权未完成；请刷新材料目录后重试。";
  if (error.status === 0) return "连接中断，授权结果待核实。刷新材料目录后，请使用相同授权设置重试。";
  if (error.status === 401) return "登录状态已失效，请返回登录页面重新登录。";
  if (error.status === 403) return "当前身份没有此案件的审核授权权限。";
  if (error.status === 409) return "案件状态或已有授权已变化。刷新后核对当前选择；已有权限不能被静默覆盖。";
  if (error.status === 422) return "项目合同关系确认未完成，或授权期限无效。请核对后重新提交。";
  return "授权服务暂不可用。请刷新材料目录后使用相同授权设置重试。";
}

function revokeErrorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return "撤权未完成；请刷新材料目录后重试。";
  if (error.status === 0) return "连接中断，撤权结果待核实。请刷新目录，并使用相同原因安全重试。";
  if (error.status === 401) return "登录状态已失效，请返回登录页面重新登录。";
  if (error.status === 403) return "只有当前案件审核人可以撤销材料权限。";
  if (error.status === 409) return "案件版本或授权状态已变化。刷新后重新核对，再发起撤权。";
  if (error.status === 422) return "请填写有效的撤权原因（最多 500 字）。";
  return "撤权服务暂不可用。请刷新材料目录后使用相同原因安全重试。";
}

function removalErrorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return "材料移除未完成；请刷新材料目录后重试。";
  if (error.status === 0) return "连接中断，移除结果待核实。系统正在刷新材料目录，请按提示继续。";
  if (error.status === 401) return "登录状态已失效，请返回登录页面重新登录。";
  if (error.status === 403) return "当前身份无权移除此案件的材料。";
  if (error.status === 404) return "材料已不存在或不属于本案，请刷新材料目录。";
  if (error.status === 409) return "案件版本或材料状态已变化。请核对最新材料列表后重试。";
  if (error.status === 422) return "请填写有效的移除原因（最多 500 字）。";
  return "材料服务暂不可用。请刷新材料目录后重试。";
}

function previewErrorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return "材料状态暂时无法核验，请刷新目录后重试。";
  if (error.status === 0 || error.status >= 500) return "来源服务暂时不可用。材料没有冻结；请稍后重试。";
  if (error.status === 401) return "登录状态已失效，请返回登录页面重新登录。";
  if (error.status === 403) return "当前身份无权预览此案件的审查材料。";
  if (error.status === 409) return "案件或授权状态已变化，请刷新材料目录后重新预览。";
  return "当前材料无法生成预览，请检查材料状态后重试。";
}

function freezeErrorMessage(error: unknown) {
  if (!(error instanceof ApiError)) return "冻结状态待核实；请重新加载案件并核对服务端快照。";
  if (error.status === 0 || error.status >= 500) return "连接中断，冻结结果待核实。请重新预览；服务端会核对相同快照，避免重复冻结。";
  if (error.status === 401) return "登录状态已失效，请返回登录页面重新登录。";
  if (error.status === 403) return "当前身份无权冻结此案件输入。";
  if (error.status === 409) return "材料、授权或案件版本已变化。旧预览已作废，请重新预览。";
  return "当前预览不能冻结，请检查材料状态后重新预览。";
}

function sourceStatusLabel(status: SourcePreview["items"][number]["status"]) {
  const labels: Record<SourcePreview["items"][number]["status"], string> = {
    ready: "可冻结",
    no_permission: "当前审核人无读取授权",
    version_unavailable: "材料版本不可用",
    wrong_legal_entity: "法律主体不一致",
    invalid_relation: "材料关系不匹配",
    relation_unverified: "尚未记录案件内关系确认",
    restricted_amount: "金额权限或审查范围不足",
    index_unavailable: "索引未就绪或版本已过期",
    parse_pending: "材料仍在解析",
    parse_failed: "材料解析失败",
  };
  return labels[status];
}

function sourceRelationLabel(relation: string) {
  const labels: Record<string, string> = {
    main: "待审主合同",
    attachment: "签约附件",
    upstream: "项目客户合同",
    background: "历史合同背景",
  };
  return labels[relation] ?? "案件材料";
}

function statusLabel(value: CaseDocument["index_status"]) {
  const labels: Record<CaseDocument["index_status"], string> = {
    missing: "未建索引",
    queued: "等待索引",
    running: "正在索引",
    failed: "索引失败",
    ready: "索引就绪",
    stale: "索引过期",
  };
  return labels[value];
}

function indexErrorDescription(value: CaseDocument["index_error_code"]): string | null {
  if (!value) return null;
  const descriptions: Record<NonNullable<CaseDocument["index_error_code"]>, string> = {
    index_media_unsupported: "当前索引服务不支持此文件格式，暂不能用于审查。",
    index_storage_backend_mismatch: "材料存储配置不匹配，请联系管理员处理。",
    index_rendered_source_missing: "DOCX 分页索引文件暂不可用；请联系管理员重新处理。",
    index_processing_error: "索引处理失败；请联系管理员重新处理。",
    index_commit_error: "索引状态未能保存；刷新后仍失败请联系管理员。",
  };
  return descriptions[value];
}

function selectionLabel(document: CaseDocument) {
  const value = document.selection_status;
  if (value === "selected" && document.role === "customer_contract") {
    return "已关联 · 同项目人工确认";
  }
  return value === "selected" ? "已选入" : value === "revoked" ? "选择已撤销" : "未选入";
}

function purposeAuthorization(document: CaseDocument, purpose: DocumentPurpose) {
  const authorization = document.purpose_authorizations?.find((item) => item.purpose === purpose);
  if (authorization) return authorization;
  if (purpose === "contract_review" && ["authorized", "expired"].includes(document.authorization_status)) {
    return {
      purpose,
      allow_text: true,
      allow_amount: document.amount_authorized,
      valid_until: document.authorization_valid_until ?? "",
      status: document.authorization_status === "authorized" ? "authorized" : "expired",
    } as const;
  }
  return null;
}

function nextGrantPurpose(document: CaseDocument): DocumentPurpose | null {
  const orderedPurposes: DocumentPurpose[] = document.role === "customer_contract"
    ? ["upstream_comparison"]
    : document.role === "attachment"
      ? ["contract_review"]
      : ["contract_review", "upstream_comparison"];
  return orderedPurposes.find((purpose) => purposeAuthorization(document, purpose)?.status !== "authorized") ?? null;
}

function purposeName(purpose: DocumentPurpose) {
  return purpose === "contract_review" ? "合同审查" : "上下游比对";
}

function authorizationLabel(document: CaseDocument) {
  const purposeAuthorizations = (document.purpose_authorizations ?? []).slice();
  if (purposeAuthorizations.length === 0) {
    const legacy = purposeAuthorization(document, "contract_review");
    if (legacy) purposeAuthorizations.push(legacy);
  }
  if (purposeAuthorizations.length === 0) {
    return document.selection_status === "selected"
      ? "已建立关系 · 当前没有有效用途授权"
      : "未授权模型读取";
  }
  return (
    <div className="authorization-state">
      {purposeAuthorizations.map((authorization) => {
        const current = authorization.status === "authorized";
        return <div className="purpose-authorization" key={authorization.purpose}>
          <Tag color={current ? "success" : "warning"}>
            {purposeName(authorization.purpose)} · {current ? "有效" : "已到期"}
          </Tag>
          <small>
            文本{authorization.allow_text ? "已授权" : "未授权"} · 金额{authorization.allow_amount ? "已授权" : "未授权"}
            {authorization.valid_until ? ` · ${current ? "有效至" : "到期于"} ${formatDate(authorization.valid_until)}` : ""}
          </small>
        </div>;
      })}
    </div>
  );
}

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const [viewParams, setViewParams] = useSearchParams();
  const requestedView = viewParams.get("view");
  const activeView = requestedView === "materials" || requestedView === "review" || requestedView === "results" ? requestedView : "background";
  const [caseRecord, setCaseRecord] = useState<CaseRecord | null>(null);
  const [documents, setDocuments] = useState<CaseDocument[]>([]);
  const [catalogError, setCatalogError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [role, setRole] = useState<"primary_contract" | "attachment" | "customer_contract">("primary_contract");
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [mainReviewBusy, setMainReviewBusy] = useState(false);

  const [grantTarget, setGrantTarget] = useState<CaseDocument | null>(null);
  const [grantPurpose, setGrantPurpose] = useState<DocumentPurpose>("contract_review");
  const [grantAllowAmount, setGrantAllowAmount] = useState(false);
  const [confirmProjectRelation, setConfirmProjectRelation] = useState(false);
  const [grantHours, setGrantHours] = useState("");
  const [grantRequest, setGrantRequest] = useState<SelectionRequest | null>(null);
  const [grantRequests, setGrantRequests] = useState<Record<string, SelectionRequest>>({});
  const [grantError, setGrantError] = useState<string | null>(null);
  const [grantBusy, setGrantBusy] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<CaseDocument | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const [revokeRequest, setRevokeRequest] = useState<RevocationRequest | null>(null);
  const [revokeRequests, setRevokeRequests] = useState<Record<string, RevocationRequest>>({});
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<CaseDocument | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [removeRequest, setRemoveRequest] = useState<RemovalRequest | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [permissionNotice, setPermissionNotice] = useState<{ type: "success" | "warning"; text: string } | null>(null);
  const [sourcePreview, setSourcePreview] = useState<SourcePreview | null>(null);
  const [sourcePurpose, setSourcePurpose] = useState<SourcePreview["purpose"]>("contract_review");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewRequestRef = useRef<AbortController | null>(null);
  const activeCaseIdRef = useRef(caseId);
  const [freezeTarget, setFreezeTarget] = useState<SourcePreview | null>(null);
  const [freezeDialogOpen, setFreezeDialogOpen] = useState(false);
  const [freezeBusy, setFreezeBusy] = useState(false);
  const [freezeError, setFreezeError] = useState<string | null>(null);
  const [frozenInput, setFrozenInput] = useState<FrozenInput | null>(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const comparisonRequestRef = useRef<{ snapshotId: string; key: string } | null>(null);
  const [entityForm] = Form.useForm<{ legal_entity_id: string; verification_note: string }>();
  const [entityConfirmOpen, setEntityConfirmOpen] = useState(false);
  const [entityConfirmBusy, setEntityConfirmBusy] = useState(false);
  const [entityConfirmAttempted, setEntityConfirmAttempted] = useState(false);
  const [entityConfirmError, setEntityConfirmError] = useState<string | null>(null);
  const [entityConfirmRequest, setEntityConfirmRequest] = useState<{
    legal_entity_id: string;
    expected_case_version: number;
    verification_note: string;
    idempotency_key: string;
  } | null>(null);
  const [entityCorrectionOpen, setEntityCorrectionOpen] = useState(false);
  const [entityCorrectionBusy, setEntityCorrectionBusy] = useState(false);
  const [entityCorrectionRequest, setEntityCorrectionRequest] = useState<{
    legal_entity_id: string;
    expected_case_version: number;
    correction_note: string;
    idempotency_key: string;
  } | null>(null);
  const [entityCorrectionError, setEntityCorrectionError] = useState<string | null>(null);
  const [entityCorrectionForm] = Form.useForm<{ legal_entity_id: string; correction_note: string }>();

  const refreshCaseAndDocuments = useCallback(async (signal?: AbortSignal) => {
    try {
      const [latest, items] = await Promise.all([getCase(caseId, signal), listCaseDocuments(caseId, signal)]);
      if (signal?.aborted || activeCaseIdRef.current !== caseId) throw new DOMException("Aborted", "AbortError");
      setCaseRecord(latest);
      setDocuments(items);
      setCatalogError(false);
      return { caseRecord: latest, documents: items };
    } catch (reason) {
      if (activeCaseIdRef.current === caseId && reason instanceof ApiError && [401, 403, 404].includes(reason.status)) {
        setDocuments([]); setCaseRecord(null); setError(reason); setMainReviewBusy(false);
      }
      throw reason;
    }
  }, [caseId]);
  const refreshDocuments = async (signal?: AbortSignal) => {
    await refreshCaseAndDocuments(signal);
  };

  const clearSourceSnapshot = () => {
    setSourcePreview(null);
    setFrozenInput(null);
    setFreezeTarget(null);
    setFreezeDialogOpen(false);
    setFreezeError(null);
  };

  useEffect(() => {
    activeCaseIdRef.current = caseId;
    previewRequestRef.current?.abort();
    previewRequestRef.current = null;
    setSourcePreview(null);
    setFrozenInput(null);
    setFreezeTarget(null);
    setFreezeDialogOpen(false);
    setFreezeError(null);
    setSourcePurpose("contract_review");
    setPreviewBusy(false);
    setPreviewError(null);
    setFreezeBusy(false);
    return () => {
      previewRequestRef.current?.abort();
      previewRequestRef.current = null;
    };
  }, [caseId]);

  const changeSourcePurpose = (purpose: SourcePreview["purpose"]) => {
    if (purpose === sourcePurpose) return;
    clearSourceSnapshot();
    setPreviewError(null);
    setSourcePurpose(purpose);
  };

  const stageEntityConfirmation = (values: { legal_entity_id: string; verification_note: string }) => {
    if (!caseRecord || caseRecord.legal_entity_id !== null) return;
    const request = entityConfirmRequest ?? {
      legal_entity_id: values.legal_entity_id.trim(),
      expected_case_version: caseRecord.version,
      verification_note: values.verification_note.trim(),
      idempotency_key: createUuid(),
    };
    setEntityConfirmAttempted(false);
    setEntityConfirmRequest(request);
    setEntityConfirmError(null);
    setEntityConfirmOpen(true);
  };

  const confirmLegalEntity = async () => {
    if (!caseRecord || !entityConfirmRequest || entityConfirmBusy) return;
    setEntityConfirmAttempted(true);
    setEntityConfirmBusy(true);
    setEntityConfirmError(null);
    clearSourceSnapshot();
    try {
      const updated = await confirmCaseLegalEntity(
        caseId,
        {
          legal_entity_id: entityConfirmRequest.legal_entity_id,
          expected_case_version: entityConfirmRequest.expected_case_version,
          verification_note: entityConfirmRequest.verification_note,
        },
        entityConfirmRequest.idempotency_key,
      );
      setCaseRecord(updated);
      setEntityConfirmOpen(false);
      setEntityConfirmRequest(null);
      setEntityConfirmAttempted(false);
      entityForm.resetFields();
      setPermissionNotice({ type: "success", text: "法人主体已确认。案件材料现可按该主体范围逐份选择并授权。" });
      try {
        await refreshDocuments();
        setCatalogError(false);
      } catch {
        setCatalogError(true);
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        try {
          const latest = await getCase(caseId);
          if (latest.legal_entity_id === entityConfirmRequest.legal_entity_id) {
            setCaseRecord(latest);
            setEntityConfirmOpen(false);
            setEntityConfirmRequest(null);
            setEntityConfirmAttempted(false);
            entityForm.resetFields();
            setPermissionNotice({ type: "success", text: "服务端已确认该主体绑定；案件状态已刷新。" });
            try {
              await refreshDocuments();
              setCatalogError(false);
            } catch {
              setCatalogError(true);
            }
          } else {
            setCaseRecord(latest);
            setEntityConfirmRequest(null);
            setEntityConfirmAttempted(false);
            setEntityConfirmError("案件版本已变化。主体尚未确认，请核对案件后重新提交。表单内容仍保留。 ");
          }
        } catch {
          setEntityConfirmError("案件版本或主体状态已变化，当前确认结果待核实。请刷新案件后检查主体状态。");
        }
      } else {
        setEntityConfirmError(reason instanceof ApiError && reason.status === 403
          ? "当前身份无权确认此案件主体。"
          : "主体确认结果暂未核实。保留当前请求并重试会复用同一命令。",
        );
        if (reason instanceof ApiError && reason.status > 0 && reason.status < 500) {
          setEntityConfirmRequest(null);
          setEntityConfirmAttempted(false);
        }
      }
    } finally {
      setEntityConfirmBusy(false);
    }
  };

  const stageEntityCorrection = (values: { legal_entity_id: string; correction_note: string }) => {
    if (!caseRecord?.can_correct_legal_entity || !caseRecord.legal_entity_id) return;
    setEntityCorrectionRequest({
      legal_entity_id: values.legal_entity_id.trim(),
      expected_case_version: caseRecord.version,
      correction_note: values.correction_note.trim(),
      idempotency_key: createUuid(),
    });
    setEntityCorrectionError(null);
  };

  const submitEntityCorrection = async () => {
    if (!caseRecord?.can_correct_legal_entity || !entityCorrectionRequest || entityCorrectionBusy) return;
    setEntityCorrectionBusy(true);
    setEntityCorrectionError(null);
    try {
      await correctCaseLegalEntity(caseId, {
        legal_entity_id: entityCorrectionRequest.legal_entity_id,
        expected_case_version: entityCorrectionRequest.expected_case_version,
        correction_note: entityCorrectionRequest.correction_note,
      }, entityCorrectionRequest.idempotency_key);
      setCaseRecord(await getCase(caseId));
      setEntityCorrectionOpen(false);
      setEntityCorrectionRequest(null);
      entityCorrectionForm.resetFields();
      clearSourceSnapshot();
      setPermissionNotice({ type: "success", text: "主体已更正；原选择与授权已撤销，既有运行已失效。请上传新版本材料并重新准备审查。" });
      try {
        await refreshDocuments();
        setCatalogError(false);
      } catch {
        setCatalogError(true);
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        try {
          const latest = await getCase(caseId);
          const entityChanged = latest.legal_entity_id !== caseRecord.legal_entity_id;
          setCaseRecord(latest);
          setEntityCorrectionRequest(null);
          if (!latest.can_correct_legal_entity) {
            setEntityCorrectionOpen(false);
            setPermissionNotice({
              type: "warning",
              text: "主体纠错权限已变化，案件与审计记录已刷新。请联系租户管理员后再处理。",
            });
            return;
          }
          if (entityChanged) {
            clearSourceSnapshot();
            try {
              await refreshDocuments();
              setCatalogError(false);
            } catch {
              setCatalogError(true);
            }
          }
          setEntityCorrectionError(latest.legal_entity_id === entityCorrectionRequest.legal_entity_id
            ? "案件当前主体与目标 UUID 一致，但本次请求因版本冲突未能确认为本命令完成。请先查看主体审计记录，不要重复提交。"
            : "案件版本或主体已变化。案件与材料目录已刷新；请核对主体审计记录后再决定是否重新发起纠错。表单内容仍保留。");
        } catch {
          setEntityCorrectionError("更正结果暂未核实。请刷新案件与主体审计记录后再决定是否重试。");
        }
      } else if (reason instanceof ApiError && reason.status === 0) {
        setEntityCorrectionError("连接中断，更正结果待核实。请重试以复用同一请求，或刷新案件及审计记录核对结果。");
      } else {
        if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) {
          setEntityCorrectionRequest(null);
        }
        setEntityCorrectionError(reason instanceof ApiError && reason.status === 403
          ? "当前身份无权更正此案件主体。"
          : "主体更正未完成。请刷新案件状态后重试。");
      }
    } finally {
      setEntityCorrectionBusy(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    Promise.all([
      getCase(caseId, controller.signal).then(setCaseRecord),
      listCaseDocuments(caseId, controller.signal)
        .then((items) => {
          setDocuments(items);
          setCatalogError(false);
        })
        .catch((reason: unknown) => {
          if (!(reason instanceof DOMException && reason.name === "AbortError")) {
            setCatalogError(true);
          }
        }),
    ])
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [caseId, retryCount]);

  const hasPendingParse = documents.some((document) => document.parse_status === "queued" || document.parse_status === "processing");
  useEffect(() => {
    if (!caseId || !hasPendingParse) return;
    let active = true;
    let timer = 0;
    const refreshParseStatuses = async () => {
      try {
        const latest = await listCaseDocuments(caseId);
        if (active) setDocuments(latest);
      } catch {
        // Keep the last known state and retry while parsing remains pending.
      }
      if (active) timer = window.setTimeout(refreshParseStatuses, 3000);
    };
    timer = window.setTimeout(refreshParseStatuses, 3000);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [caseId, hasPendingParse]);

  useEffect(() => {
    if (caseRecord?.legal_entity_id === null) setRole("primary_contract");
  }, [caseRecord?.legal_entity_id]);

  useEffect(() => {
    document.title = caseRecord
      ? `${caseRecord.title} — 合同审查工作台`
      : "案件详情 — 合同审查工作台";
  }, [caseRecord]);
  useEffect(() => {
    if (!loading) {
      const heading = document.querySelector<HTMLElement>(`#case-view-${activeView} h3`) ?? document.querySelector<HTMLElement>(".content-inner h2");
      heading?.setAttribute("tabindex", "-1");
      heading?.focus({ preventScroll: true });
    }
  }, [caseId, loading, activeView]);

  const chooseFiles = (files: FileList | null) => {
    if (!files?.length) return;
    const additions = Array.from(files).map((file): QueuedFile => {
      const invalid = contractFileError(file);
      return {
        id: createUuid(),
        file,
        idempotencyKey: createUuid(),
        status: invalid ? "invalid" : "queued",
        progress: 0,
        error: invalid,
      };
    });
    setQueue((current) => [...current, ...additions]);
  };

  const updateQueueItem = (id: string, update: Partial<QueuedFile>) => {
    setQueue((current) => current.map((item) => item.id === id ? { ...item, ...update } : item));
  };

  const runUploads = async () => {
    if (isUploading) return;
    const pending = queue.filter((item) => item.status === "queued" || item.status === "failed");
    if (!pending.length) return;
    clearSourceSnapshot();
    setIsUploading(true);
    for (const item of pending) {
      updateQueueItem(item.id, { status: "sending", progress: 0, error: undefined });
      try {
        await uploadCaseDocument(caseId, item.file, role, item.idempotencyKey, (percent) => {
          updateQueueItem(item.id, {
            status: percent >= 100 ? "processing" : "sending",
            progress: percent,
          });
        });
        updateQueueItem(item.id, { status: "uploaded", progress: 100 });
        try {
          await refreshDocuments();
          setCatalogError(false);
        } catch {
          setCatalogError(true);
        }
      } catch (reason) {
        updateQueueItem(item.id, { status: "failed", error: uploadErrorMessage(reason) });
        try {
          await refreshDocuments();
          setCatalogError(false);
        } catch {
          setCatalogError(true);
        }
      }
    }
    setIsUploading(false);
  };

  const clearGrantRequest = (documentId: string) => {
    setGrantRequests((current) => {
      const next = { ...current };
      delete next[documentId];
      return next;
    });
    setGrantRequest(null);
  };

  const openGrantDialog = (document: CaseDocument) => {
    const saved = grantRequests[document.document_version_id] ?? null;
    setGrantTarget(document);
    setGrantPurpose(saved?.purpose ?? nextGrantPurpose(document) ?? "contract_review");
    setGrantAllowAmount(saved?.allow_amount ?? false);
    setConfirmProjectRelation(saved?.confirm_project_relation ?? false);
    setGrantHours(saved
      ? String(Math.max(1, Math.round((Date.parse(saved.valid_until) - Date.now()) / 3_600_000)))
      : "");
    setGrantRequest(saved);
    setGrantError(null);
  };

  const submitGrant = async () => {
    if (!grantTarget) return;
    const relation: SelectionRequest["relation"] = grantTarget.role === "primary_contract"
      ? "main"
      : grantTarget.role === "customer_contract"
        ? "upstream"
        : "attachment";
    if (relation === "upstream" && !confirmProjectRelation) {
      setGrantError("请先确认该客户合同属于本案同一我方法人项目。");
      return;
    }
    if (!grantRequest && !grantHours) {
      setGrantError("请选择授权有效期后继续。");
      return;
    }
    const request = grantRequest ?? {
      document_version_id: grantTarget.document_version_id,
      relation,
      purpose: grantPurpose,
      confirm_project_relation: relation === "upstream" && confirmProjectRelation,
      allow_amount: grantAllowAmount,
      valid_until: new Date(Date.now() + Number(grantHours) * 3_600_000).toISOString(),
    };
    setGrantRequest(request);
    setGrantRequests((current) => ({ ...current, [grantTarget.document_version_id]: request }));
    setGrantBusy(true);
    clearSourceSnapshot();
    setGrantError(null);
    try {
      const selected = await selectCaseDocument(caseId, request);
      setCaseRecord((current) => current ? { ...current, version: selected.case_version } : current);
      setPermissionNotice({
        type: "success",
        text: `已为“${purposeName(selected.purpose)}”授权当前审核人读取${selected.allow_amount ? "文本与金额" : "文本（不含金额）"}，有效至 ${formatDate(selected.valid_until)}。此授权不会向其他人共享。`,
      });
      clearGrantRequest(grantTarget.document_version_id);
      setGrantTarget(null);
      setGrantHours("");
      try {
        await refreshDocuments();
        setCatalogError(false);
      } catch {
        setCatalogError(true);
      }
    } catch (reason) {
      setGrantError(grantErrorMessage(reason));
      try {
        const latest = await listCaseDocuments(caseId);
        setDocuments(latest);
        const current = latest.find((item) => item.document_version_id === request.document_version_id);
        const currentPurposeAuthorization = current
          ? purposeAuthorization(current, request.purpose)
          : null;
        const matchingAuthorization = currentPurposeAuthorization
          && currentPurposeAuthorization.allow_amount === request.allow_amount
          && Date.parse(currentPurposeAuthorization.valid_until) === Date.parse(request.valid_until);
        if (currentPurposeAuthorization?.status === "authorized" && matchingAuthorization) {
          const updatedCase = await getCase(caseId);
          setCaseRecord(updatedCase);
          setPermissionNotice({ type: "success", text: `服务端已确认“${purposeName(request.purpose)}”授权生效；案件和材料状态已刷新。` });
          clearGrantRequest(request.document_version_id);
          setGrantTarget(null);
          setGrantError(null);
        } else if (currentPurposeAuthorization?.status === "expired" && matchingAuthorization) {
          setCaseRecord(await getCase(caseId));
          clearGrantRequest(request.document_version_id);
          setGrantTarget(null);
          setPermissionNotice({
            type: "warning",
            text: `“${purposeName(request.purpose)}”授权已确认但现已到期，权限保持关闭。请重新发起新的授权。`,
          });
          setGrantError(null);
        } else if (currentPurposeAuthorization?.status === "authorized") {
          clearGrantRequest(request.document_version_id);
          setGrantTarget(null);
          setPermissionNotice({
            type: "warning",
            text: `该材料已有“${purposeName(request.purpose)}”授权，但范围或有效期不同；系统未覆盖既有权限。若需调整，请撤销该材料的全部授权后重新选择。`,
          });
          setGrantError(null);
        } else if (reason instanceof ApiError && reason.status !== 0 && reason.status < 500) {
          clearGrantRequest(request.document_version_id);
        }
        setCatalogError(false);
      } catch {
        setCatalogError(true);
      }
    } finally {
      setGrantBusy(false);
    }
  };

  const openRevokeDialog = (document: CaseDocument) => {
    if (!document.selection_id) return;
    const saved = revokeRequests[document.selection_id] ?? null;
    setRevokeTarget(document);
    setRevokeReason(saved?.reason ?? "");
    setRevokeRequest(saved);
    setRevokeError(null);
  };

  const clearRevokeRequest = (selectionId: string) => {
    setRevokeRequests((current) => {
      const next = { ...current };
      delete next[selectionId];
      return next;
    });
    setRevokeRequest(null);
  };

  const submitRevoke = async () => {
    if (!revokeTarget?.selection_id) return;
    if (!revokeRequest && !revokeReason.trim()) {
      setRevokeError("请填写撤销该材料用途授权的原因。");
      return;
    }
    const request = revokeRequest ?? {
      expected_case_version: caseRecord?.version ?? revokeTarget.case_version,
      reason: revokeReason.trim(),
      idempotencyKey: `revoke-${createUuid()}`,
    };
    setRevokeRequest(request);
    setRevokeRequests((current) => ({ ...current, [revokeTarget.selection_id as string]: request }));
    setRevokeBusy(true);
    clearSourceSnapshot();
    setRevokeError(null);
    try {
      const revoked = await revokeCaseDocumentSelection(
        caseId,
        revokeTarget.selection_id,
        { expected_case_version: request.expected_case_version, reason: request.reason },
        request.idempotencyKey,
      );
      setCaseRecord((current) => current ? { ...current, version: revoked.case_version } : current);
      setPermissionNotice({
        type: "warning",
        text: `权限已撤销；撤销了 ${revoked.revoked_grant_count} 项授权，并使 ${revoked.invalidated_run_count} 个进行中的运行失效。`,
      });
      clearRevokeRequest(revokeTarget.selection_id);
      setRevokeTarget(null);
      setRevokeReason("");
      try {
        await refreshDocuments();
        setCatalogError(false);
      } catch {
        setCatalogError(true);
      }
    } catch (reason) {
      setRevokeError(revokeErrorMessage(reason));
      try {
        const latest = await listCaseDocuments(caseId);
        setDocuments(latest);
        const current = latest.find((item) => item.document_version_id === revokeTarget.document_version_id);
        if (current?.selection_status === "revoked") {
          const updatedCase = await getCase(caseId);
          setCaseRecord(updatedCase);
          setPermissionNotice({ type: "warning", text: "服务端已确认该材料权限已撤销，案件状态已刷新。" });
          clearRevokeRequest(revokeTarget.selection_id);
          setRevokeTarget(null);
          setRevokeError(null);
        } else if (reason instanceof ApiError && reason.status !== 0 && reason.status < 500) {
          clearRevokeRequest(revokeTarget.selection_id);
        }
        setCatalogError(false);
      } catch {
        setCatalogError(true);
      }
    } finally {
      setRevokeBusy(false);
    }
  };

  const openRemoveDialog = (document: CaseDocument) => {
    setRemoveTarget(document);
    setRemoveReason("");
    setRemoveRequest(null);
    setRemoveError(null);
  };

  const closeRemoveDialog = () => {
    if (removeBusy) return;
    setRemoveTarget(null);
    setRemoveReason("");
    setRemoveRequest(null);
    setRemoveError(null);
  };

  const submitRemove = async () => {
    if (!removeTarget) return;
    if (!removeRequest && !removeReason.trim()) {
      setRemoveError("请填写移除此材料的原因。");
      return;
    }
    const request = removeRequest ?? {
      expected_case_version: caseRecord?.version ?? removeTarget.case_version,
      reason: removeReason.trim(),
      idempotencyKey: `remove-${createUuid()}`,
    };
    setRemoveRequest(request);
    setRemoveBusy(true);
    setRemoveError(null);
    clearSourceSnapshot();
    try {
      const removed = await removeCaseDocument(
        caseId,
        removeTarget.document_version_id,
        { expected_case_version: request.expected_case_version, reason: request.reason },
        request.idempotencyKey,
      );
      setCaseRecord((current) => current ? { ...current, version: removed.case_version } : current);
      setPermissionNotice({
        type: "warning",
        text: `材料已从本案移除；撤销了 ${removed.revoked_selection_count} 项材料选择和 ${removed.revoked_grant_count} 项授权，并使 ${removed.invalidated_run_count} 个进行中的审核运行失效。`,
      });
      setRemoveTarget(null);
      setRemoveReason("");
      setRemoveRequest(null);
      try {
        await refreshDocuments();
        setCatalogError(false);
      } catch {
        setCatalogError(true);
      }
    } catch (reason) {
      setRemoveError(removalErrorMessage(reason));
      try {
        const refreshed = await refreshCaseAndDocuments();
        if (!refreshed.documents.some((item) => item.document_version_id === removeTarget.document_version_id)) {
          setPermissionNotice({ type: "warning", text: "服务器已确认该材料已从本案移除，案件状态已刷新。" });
          setRemoveTarget(null);
          setRemoveReason("");
          setRemoveRequest(null);
          setRemoveError(null);
        } else if (reason instanceof ApiError && reason.status !== 0 && reason.status < 500) {
          setRemoveRequest(null);
        }
      } catch {
        setCatalogError(true);
      }
    } finally {
      setRemoveBusy(false);
    }
  };

  const runSourcePreview = async () => {
    if (previewBusy || freezeBusy || isUploading) return;
    previewRequestRef.current?.abort();
    const controller = new AbortController();
    previewRequestRef.current = controller;
    setPreviewBusy(true);
    setPreviewError(null);
    setFreezeError(null);
    setFrozenInput(null);
    setSourcePreview(null);
    setFreezeTarget(null);
    setFreezeDialogOpen(false);
    try {
      const preview = await previewCaseSources(caseId, sourcePurpose, controller.signal);
      if (!controller.signal.aborted && activeCaseIdRef.current === caseId) {
        setSourcePreview(preview);
      }
    } catch (reason) {
      if (!controller.signal.aborted && activeCaseIdRef.current === caseId) {
        setPreviewError(previewErrorMessage(reason));
      }
    } finally {
      if (previewRequestRef.current === controller) previewRequestRef.current = null;
      if (!controller.signal.aborted && activeCaseIdRef.current === caseId) setPreviewBusy(false);
    }
  };

  const submitFreeze = async () => {
    if (!freezeTarget || freezeBusy) return;
    const submittedCaseId = caseId;
    setFreezeBusy(true);
    setFreezeError(null);
    try {
      const frozen = await freezeCaseSources(caseId, freezeTarget);
      if (activeCaseIdRef.current !== submittedCaseId) return;
      setFrozenInput(frozen);
      setCaseRecord((current) => current ? { ...current, version: frozen.case_version, status: "ready" } : current);
      setSourcePreview(null);
      setFreezeTarget(null);
      setFreezeDialogOpen(false);
    } catch (reason) {
      if (activeCaseIdRef.current !== submittedCaseId) return;
      setFreezeError(freezeErrorMessage(reason));
      if (reason instanceof ApiError && reason.status === 409) {
        setSourcePreview(null);
        setFreezeTarget(null);
        setFreezeDialogOpen(false);
        try {
          const latestCase = await getCase(caseId);
          if (activeCaseIdRef.current === submittedCaseId) setCaseRecord(latestCase);
        } catch {
          // Keep the conflict visible; a full case reload remains available above.
        }
      }
    } finally {
      if (activeCaseIdRef.current === submittedCaseId) setFreezeBusy(false);
    }
  };

  const submitUpstreamComparison = async () => {
    if (!frozenInput || frozenInput.purpose !== "upstream_comparison" || comparisonBusy) return;
    const submittedCaseId = caseId;
    if (comparisonRequestRef.current?.snapshotId !== frozenInput.input_snapshot_id) {
      comparisonRequestRef.current = {
        snapshotId: frozenInput.input_snapshot_id,
        key: `upcmp-${createUuid()}`,
      };
    }
    setComparisonBusy(true);
    setComparisonError(null);
    try {
      const run = await startUpstreamComparison(
        caseId,
        frozenInput.input_snapshot_id,
        comparisonRequestRef.current.key,
      );
      if (activeCaseIdRef.current !== submittedCaseId) return;
      navigate(`/comparisons/${run.run_id}`);
    } catch (reason) {
      if (activeCaseIdRef.current !== submittedCaseId) return;
      if (reason instanceof ApiError && reason.status === 503) {
        setComparisonError("比对执行服务尚未启用或模型配置不可用；冻结快照仍已保存，可稍后重试。");
      } else if (reason instanceof ApiError && reason.status === 409) {
        setComparisonError("冻结输入已变化或材料状态冲突。请刷新案件并重新预览、冻结后再发起比对。");
      } else if (reason instanceof ApiError && reason.status === 404) {
        setComparisonError("当前账号无法使用此冻结输入，可能是材料授权已失效。请重新核对材料和授权。");
      } else {
        setComparisonError("发起上下游比对失败，请重试；重试会复用同一请求编号，避免重复创建任务。");
      }
    } finally {
      if (activeCaseIdRef.current === submittedCaseId) setComparisonBusy(false);
    }
  };

  const routeError = error?.status === 401
    ? { status: "403" as const, title: "登录状态已失效", subTitle: "请返回登录页面重新登录。" }
    : error?.status === 403
      ? { status: "403" as const, title: "无权查看此案件", subTitle: "当前身份无权读取案件或材料信息。" }
      : error?.status === 404
        ? { status: "404" as const, title: "案件不可用", subTitle: "案件不存在或当前身份不可见。" }
        : { status: "500" as const, title: "案件暂时无法读取", subTitle: "检查服务连接后重试。" };

  if (loading) return <div className="route-loading" aria-live="polite"><Spin size="large" description="正在读取案件…" /></div>;
  if (error || !caseRecord) return <>
    <nav aria-label="页面层级"><Breadcrumb items={[{ title: <Link to="/cases">案件列表</Link> }, { title: "案件详情" }]} /></nav>
    <Result {...routeError} extra={<Space><Button onClick={() => setRetryCount((count) => count + 1)}>重试</Button><Link to="/cases">返回案件列表</Link></Space>} />
  </>;

  const validQueueCount = queue.filter((item) => item.status === "queued" || item.status === "failed").length;
  const primaryContractUploaded = documents.some((document) =>
    document.role === "primary_contract"
    && (document.document_status === "staged" || document.document_status === "active"),
  );
  const canOpenMaterials = caseRecord.legal_entity_id !== null;
  const canOpenReview = canOpenMaterials && primaryContractUploaded;
  const primarySelectionExists = documents.some((document) =>
    document.role === "primary_contract" && document.selection_status === "selected",
  );
  const previewReadyRelations = new Set(
    sourcePreview?.items.filter((item) => item.status === "ready").map((item) => item.relation) ?? [],
  );
  const previewHasMain = previewReadyRelations.has("main");
  const previewHasUpstream = previewReadyRelations.has("upstream");
  const previewPairMissing = sourcePreview?.purpose === "upstream_comparison"
    && (!previewHasMain || !previewHasUpstream);
  const previewCanFreeze = Boolean(
    sourcePreview
    && sourcePreview.items.length > 0
    && sourcePreview.items.every((item) => item.status === "ready")
    && previewHasMain
    && !previewPairMissing,
  );
  const snapshotControlsBusy = mainReviewBusy || previewBusy || freezeBusy || isUploading || grantBusy || revokeBusy || removeBusy || entityConfirmBusy || entityCorrectionBusy;
  const documentsWithReviewWarnings = documents.filter((document) =>
    (document.parse_status === "completed" || document.parse_status === "needs_material")
    && document.warnings.length > 0,
  );
  const uploadPanel = <section className="upload-panel" aria-labelledby="upload-title">
        <span className="eyebrow">{primaryContractUploaded ? "材料 / 安全接收" : "主线第一步"}</span>
        <Title level={4} id="upload-title">{primaryContractUploaded ? "上传材料" : "上传主合同"}</Title>
        <Paragraph type="secondary">支持 PDF、DOCX，单个文件最大 30 MiB。上传后服务端解析并排队建索引；上传本身不代表已授权。</Paragraph>
        {(primaryContractUploaded || caseRecord.legal_entity_id !== null) && <>
          <label className="upload-role-label" htmlFor="upload-role">材料用途</label>
          <select id="upload-role" value={role} disabled={snapshotControlsBusy || queue.some((item) => item.status === "queued" || item.status === "failed")} onChange={(event) => setRole(event.target.value as "primary_contract" | "attachment" | "customer_contract")}>
            <option value="primary_contract">待审主合同</option>
            <option value="attachment" disabled={caseRecord.legal_entity_id === null}>签约附件（确认主体后开放）</option>
            <option value="customer_contract" disabled={caseRecord.legal_entity_id === null}>同项目客户合同（确认主体后开放）</option>
          </select>
        </>}
        <button
          type="button"
          className="upload-dropzone"
          onClick={() => document.getElementById("case-document-picker")?.click()}
          disabled={snapshotControlsBusy}
          onDragOver={(event) => { event.preventDefault(); event.currentTarget.classList.add("is-dragging"); }}
          onDragLeave={(event) => event.currentTarget.classList.remove("is-dragging")}
          onDrop={(event) => { event.preventDefault(); event.currentTarget.classList.remove("is-dragging"); chooseFiles(event.dataTransfer.files); }}
        >
          <CloudUploadOutlined />
          <strong>选择或拖放 PDF、DOCX 文件</strong>
        </button>
        <input
          id="case-document-picker"
          aria-label="选择 PDF 或 DOCX 文件"
          className="visually-hidden"
          type="file"
          accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          multiple
          onChange={(event) => { chooseFiles(event.currentTarget.files); event.currentTarget.value = ""; }}
        />
        {queue.length > 0 && <div className="upload-queue" aria-label="待上传文件" aria-live="polite">
          <div className="queue-heading"><Text strong>文件队列</Text><Text type="secondary">{queue.length} 个文件</Text></div>
          {queue.map((item) => <div className="queue-item" key={item.id}>
            <div className="queue-file"><FileOutlined /><div><strong>{item.file.name}</strong><small>{formatBytes(item.file.size)}</small></div></div>
            {item.status === "sending" || item.status === "processing" ? <div className="queue-progress"><Progress percent={item.progress} size="small" status={item.status === "processing" ? "active" : "normal"} /><span>{item.status === "processing" ? "等待服务器接收确认" : "正在上传"}</span></div>
              : item.status === "uploaded" ? <Tag color="success">已上传 · 未授权</Tag>
                : item.status === "failed" || item.status === "invalid" ? <div className="queue-error"><span>{item.error}</span>{!isUploading && <Button type="link" danger onClick={() => setQueue((current) => current.filter((queued) => queued.id !== item.id))}>移除</Button>}</div>
                  : <Button type="link" disabled={isUploading} onClick={() => setQueue((current) => current.filter((queued) => queued.id !== item.id))}>移除</Button>}
          </div>)}
          <Button type="primary" icon={<UploadOutlined />} loading={isUploading} disabled={!validQueueCount || snapshotControlsBusy && !isUploading} onClick={() => void runUploads()} block>
            {isUploading ? "正在提交材料" : `上传 ${validQueueCount} 个文件`}
          </Button>
        </div>}
        <Alert
          className="upload-boundary"
          type="info"
          showIcon
          title={caseRecord.legal_entity_id === null ? "当前仅能上传主合同" : "上传与授权分开"}
          description={caseRecord.legal_entity_id === null
            ? "上传不会自动提取或确认法人主体，也不会授予任何读取权限。"
            : role === "customer_contract"
              ? "客户合同只上传到当前案件，不会从其他案件调取。上传后仍需审核人确认它属于同一我方法人项目并单独授权；系统不会查询项目台账或合同系统核验关系。"
              : "新材料会进入暂存状态。请由审核人之后单独确认材料范围和用途；这里不会启动合同审查。"}
        />
      </section>;
  const documentColumns: import("antd").TableColumnsType<CaseDocument> = [
            { key: "file", title: "文件", dataIndex: "file_name", render: (name: string, document) => <div className="document-name"><FileOutlined /><span>{name}</span><small>{document.role === "primary_contract" ? "待审主合同" : document.role === "customer_contract" ? "项目客户合同" : "签约附件"} · {formatBytes(document.size_bytes)}</small></div> },
            { key: "parse", title: "解析", dataIndex: "parse_status", render: (value: CaseDocument["parse_status"], document: CaseDocument) => <DocumentParseStatus status={value} errorCode={document.parse_error_code} /> },
            { key: "index", title: "索引", dataIndex: "index_status", render: (value: CaseDocument["index_status"], document: CaseDocument) => {
              const detail = indexErrorDescription(document.index_error_code);
              return <div className="authorization-state"><Tag color={value === "ready" ? "success" : value === "failed" || value === "stale" ? "error" : "default"}>{statusLabel(value)}</Tag>{detail && <small>{detail}</small>}</div>;
            } },
            { key: "selection", title: "选择状态", dataIndex: "selection_status", render: (_: CaseDocument["selection_status"], document: CaseDocument) => selectionLabel(document) },
            { key: "authorization", title: "各用途授权", dataIndex: "authorization_status", render: (_: CaseDocument["authorization_status"], document: CaseDocument) => authorizationLabel(document) },
            { key: "version", title: "版本", dataIndex: "case_version", render: (value: number) => `v${value}` },
            { key: "document-action", title: "操作", render: (_: unknown, document: CaseDocument) => <Button danger type="link" icon={<DeleteOutlined />} disabled={snapshotControlsBusy} onClick={() => openRemoveDialog(document)}>移除材料</Button> },
            { title: "权限操作", key: "permission-action", render: (_: unknown, document: CaseDocument) => {
              if (caseRecord.legal_entity_id === null) {
                return <Tooltip title="先确认本案法人主体，才能建立合法人范围内的材料授权。"><span><Button disabled>等待主体确认</Button></span></Tooltip>;
              }
              if (document.selection_status === "selected" && document.selection_id) {
                const missingPurpose = nextGrantPurpose(document);
                return <Space orientation="vertical" size="small" align="start">
                  {missingPurpose && <Button disabled={snapshotControlsBusy} onClick={() => openGrantDialog(document)}>
                    {missingPurpose === "upstream_comparison" ? "授权上下游比对" : "授权合同审查"}
                  </Button>}
                  <Button danger disabled={snapshotControlsBusy} onClick={() => openRevokeDialog(document)}>撤销全部授权</Button>
                </Space>;
              }
              if (document.selection_status === "not_selected" && ["staged", "active"].includes(document.document_status)) {
                const isBlockedMain = document.role === "primary_contract" && primarySelectionExists;
                return isBlockedMain
                  ? <Tooltip title="案件已有主合同选择。先撤销当前主合同权限，再选择新的主合同。"><span><Button disabled>选择并授权</Button></span></Tooltip>
                  : <Button disabled={snapshotControlsBusy} onClick={() => openGrantDialog(document)}>
                    {document.role === "customer_contract" ? "关联上游并授权" : "选择并授权"}
                  </Button>;
              }
              return <Text type="secondary">—</Text>;
            } },
          ];
  const documentTable = (permissions: boolean, data: CaseDocument[] = documents) => <Table<CaseDocument>
    aria-label={permissions ? "本次审核材料与授权" : "案件材料"}
    rowKey="document_version_id" dataSource={data}
    pagination={{ pageSize: 10, hideOnSinglePage: true }}
    locale={{ emptyText: <Empty description="还没有上传材料" /> }}
    columns={documentColumns.filter((column) => (permissions
      ? ["file", "selection", "authorization", "permission-action"]
      : ["file", "parse", "index", "version", "document-action"]).includes(String(column.key)))}
    scroll={{ x: permissions ? 900 : 720 }} />;
  return <>
    <nav aria-label="页面层级"><Breadcrumb items={[{ title: <Link to="/cases">案件列表</Link> }, { title: caseRecord.title }]} /></nav>
    <SectionHeading eyebrow="案件详情" title={caseRecord.title} extra={<Tag color="processing">{caseStatusLabel(caseRecord.status)}</Tag>} />
    <nav className="case-view-nav" aria-label="案件处理步骤">
      <Link to="?view=background" aria-current={activeView === "background" ? "page" : undefined}>主合同与主体</Link>
      <Link to={canOpenMaterials ? "?view=materials" : "?view=background"} aria-disabled={!canOpenMaterials} aria-current={activeView === "materials" ? "page" : undefined} onClick={(event) => { if (!canOpenMaterials) event.preventDefault(); }}>补充材料</Link>
      <Link to={canOpenReview ? "?view=review" : "?view=background"} aria-disabled={!canOpenReview} aria-current={activeView === "review" ? "page" : undefined} onClick={(event) => { if (!canOpenReview) event.preventDefault(); }}>授权并开始审核</Link>
      <Link to="?view=results" aria-current={activeView === "results" ? "page" : undefined}>审核结果</Link>
    </nav>
    <section id="case-view-background" hidden={activeView !== "background"} className="case-view-panel" aria-labelledby="case-background-title">
      <div className="case-view-heading"><Title level={3} id="case-background-title">主合同与主体</Title><Paragraph type="secondary">上传待审主合同，再从主体台账选择并确认签约主体。主体确认后，案件材料范围将锁定到该主体。 </Paragraph></div>
      <div className="case-background-meta"><Text>签约主体：{caseRecord.legal_entity_id ? "已确认" : "待确认"}</Text><Text type="secondary">创建于 {formatDate(caseRecord.created_at)}</Text></div>
    {caseRecord.legal_entity_id === null && <details className="case-disclosure case-subject-preparation" open={primaryContractUploaded}><summary>确认签约主体 · {primaryContractUploaded ? "可确认" : "请先上传主合同"}</summary><Card className="entity-confirmation-card" size="small">
      <div className="entity-confirmation-heading"><div><span className="eyebrow">案件范围 / 主体待确认</span><Title level={4}>先上传主合同，再确认签约主体</Title></div><Tag color="warning">尚未授权</Tag></div>
      <Alert
        type="warning"
        showIcon
        title="主体确认前只接收待审主合同"
        description="此时不能上传附件、选择材料、授予读取权限或冻结审查输入。请先上传待审主合同，再从主体台账选择已核实的签约主体并记录本案核对依据。"
      />
      {!primaryContractUploaded && <Alert
        type="info"
        showIcon
        title={catalogError ? "无法核实主合同上传状态" : "请先上传待审主合同"}
        description={catalogError ? "材料目录不可用期间，暂不能确认主体；请刷新材料目录后重试。" : "主体确认开放前，案件中至少需要一份待审主合同。"}
      />}
      <Form
        className="entity-confirmation-form"
        form={entityForm}
        layout="vertical"
        noValidate
        onFinish={(values) => stageEntityConfirmation(values)}
      >
        <Form.Item
          label="主体台账中的已核实主体"
          name="legal_entity_id"
          rules={[
            { required: true, message: "请选择主体台账中已核实的法人主体。" },
            { pattern: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, message: "请输入标准 UUID 格式。" },
          ]}
        >
          <LegalEntitySelect disabled={entityConfirmBusy || !!entityConfirmRequest} />
        </Form.Item>
        <Form.Item
          label="核对依据"
          name="verification_note"
          rules={[{ required: true, whitespace: true, message: "请简要记录主体核对依据。" }, { max: 500, message: "核对依据不能超过 500 个字符。" }]}
        >
          <Input.TextArea
            disabled={entityConfirmBusy || !!entityConfirmRequest}
            aria-describedby="entity-verification-help"
            autoSize={{ minRows: 2, maxRows: 4 }}
            maxLength={500}
            placeholder="例如：已与本次签约主体资料核对"
          />
        </Form.Item>
        <Text id="entity-verification-help" type="secondary">确认记录会保存操作者、时间、案件版本和核对说明。确认后主体锁定，审核人不能自行更改；如确认有误，仅授权管理员可按纠错流程更正并留审计记录。</Text>
        {entityConfirmError && <Alert className="document-warning" type="error" showIcon title={entityConfirmError} />}
        <Button type="primary" disabled={!primaryContractUploaded || catalogError || entityConfirmBusy || snapshotControlsBusy} onClick={() => entityForm.submit()}>
          确认主体并开放逐份授权
        </Button>
      </Form>
    </Card></details>}
    {caseRecord.legal_entity_id !== null && caseRecord.can_correct_legal_entity && <div className="entity-correction-action">
      <Button onClick={() => { setEntityCorrectionError(null); setEntityCorrectionOpen(true); }} disabled={snapshotControlsBusy}>
        更正法人主体
      </Button>
      <Text type="secondary">管理员操作；即使已有授权或冻结输入，也会撤销当前准备状态并保留历史审计记录。</Text>
    </div>}

      {primaryContractUploaded && caseRecord.legal_entity_id !== null && <div className="case-entity-current">
        <Text strong>当前签约主体：{caseRecord.legal_entity_name || "台账未登记"}</Text>
        <Text code>{caseRecord.legal_entity_id}</Text>
      </div>}
      {!primaryContractUploaded && <div className="case-material-layout case-material-intake">{uploadPanel}</div>}
      {primaryContractUploaded && <section className="document-list-panel case-primary-documents" aria-labelledby="primary-documents-title">
        <Title level={4} id="primary-documents-title">主合同</Title>
        {documentTable(false, documents.filter((document) => document.role === "primary_contract"))}
        {catalogError && <Alert className="document-warning" type="warning" showIcon title="材料目录暂时不可用" description="案件信息可查看，但材料状态未能读取。未获取准确目录状态前请勿重复上传。" action={<Button onClick={() => void refreshDocuments().then(() => setCatalogError(false)).catch(() => setCatalogError(true))}>刷新材料目录</Button>} />}
        {documentsWithReviewWarnings.filter((document) => document.role === "primary_contract").length > 0 && <Alert className="document-warning" type="warning" showIcon title="主合同需要人工核对" description={documentsWithReviewWarnings.filter((document) => document.role === "primary_contract").map((document) => document.file_name + "：" + document.warnings.join("、")).join("；")} />}
      </section>}
      {primaryContractUploaded && caseRecord.legal_entity_id !== null && <div className="case-step-next">
        <span>主合同和签约主体已准备好。下一步添加附件或关联合同。</span>
        <Button type="primary" onClick={() => { setRole("attachment"); setViewParams({ view: "materials" }); }}>继续：补充材料</Button>
      </div>}
      <details className="case-disclosure"><summary>案件档案：编号、主体标识与版本</summary>
    <div className="case-summary">
      <div><span>案件编号</span><strong className="summary-id">{caseRecord.case_id}</strong></div>
      <div><span>法律主体 ID</span><strong className="summary-id">{caseRecord.legal_entity_id ?? "待确认"}</strong>{caseRecord.legal_entity_confirmed_at && <small>确认于 {formatDate(caseRecord.legal_entity_confirmed_at)}</small>}</div>
      <div><span>案件版本</span><strong>v{caseRecord.version}</strong></div>
      <div><span>创建时间</span><strong>{formatDate(caseRecord.created_at)}</strong></div>
    </div>
      </details>
      <Paragraph className="case-capability-note" type="secondary">项目与交易背景的填写尚未接入；历史合同参考、财务与经营信息来源尚未接入。</Paragraph>
    </section>
    <section id="case-view-materials" hidden={activeView !== "materials"} className="case-view-panel" aria-labelledby="case-materials-title">
      <div className="case-view-heading"><Title level={3} id="case-materials-title">补充材料</Title><Paragraph type="secondary">主体确认后，再添加签约附件或同一项目的客户合同。</Paragraph></div>
      {caseRecord.legal_entity_id === null
        ? <Alert type="warning" showIcon title="先完成主合同与主体确认" description="补充材料必须归属已确认的法人主体。请返回第一步上传主合同并选择主体。" action={<Button onClick={() => setViewParams({ view: "background" })}>返回主合同与主体</Button>} />
        : <>
          <div className="case-material-guide">
            <div><Text strong>待审主合同</Text><span>本次审查的核心文件。</span></div>
            <div><Text strong>签约附件</Text><span>补充同一签约主体的履约与交付要求。</span></div>
            <div><Text strong>项目客户合同</Text><span>准备上下游比对时，需确认同项目关系。</span></div>
          </div>
          <div className="case-material-layout case-material-intake">
            <section className="document-list-panel" aria-labelledby="documents-title">
              <Title level={4} id="documents-title">已上传材料</Title>
              {documentTable(false)}
              {catalogError && <Alert className="document-warning" type="warning" showIcon title="材料目录暂时不可用" description="案件信息可查看，但材料状态未能读取。请刷新材料目录后再继续。" action={<Button onClick={() => void refreshDocuments().then(() => setCatalogError(false)).catch(() => setCatalogError(true))}>刷新材料目录</Button>} />}
              {documentsWithReviewWarnings.length > 0 && <Alert className="document-warning" type="warning" showIcon title="部分材料需要人工核对" description={documentsWithReviewWarnings.map((document) => document.file_name + "：" + document.warnings.join("、")).join("；")} />}
            </section>
            {uploadPanel}
          </div>
          <div className="case-step-next">
            <span>材料上传、解析和原文定位状态可在列表中核对。</span>
            <Button type="primary" onClick={() => setViewParams({ view: "review" })}>继续：授权并开始审核</Button>
          </div>
        </>}
    </section>
    <section id="case-view-review" hidden={activeView !== "review"} className="case-view-panel" aria-labelledby="case-review-title">
      <div className="case-view-heading"><Title level={3} id="case-review-title">授权并开始审核</Title><Paragraph type="secondary">核对本次读取授权与材料范围后，发起审核并查看执行状态。</Paragraph></div>
      <MainContractReview key={caseRecord.case_id} caseRecord={caseRecord} documents={documents}
        unavailable={catalogError} busy={snapshotControlsBusy} onBusyChange={setMainReviewBusy}
        onRefresh={refreshCaseAndDocuments} onMaterials={() => setViewParams({ view: "materials" })} />
      <details className="case-disclosure">
        <summary>审核范围与读取授权</summary>
        <Paragraph type="secondary">主合同在开始审核时确认读取授权。补充材料需逐份选入；本次审核会纳入已选择的签约附件。</Paragraph>
        {permissionNotice && <Alert
          className="document-warning"
          type={permissionNotice.type}
          showIcon
          title={permissionNotice.type === "success" ? "权限状态已更新" : "权限状态需留意"}
          description={permissionNotice.text}
          closable
          onClose={() => setPermissionNotice(null)}
        />}
        {documentTable(true)}
      </details>
      <details className="case-disclosure">
        <summary>高级输入设置：用途预览与冻结</summary>
    <section aria-labelledby="snapshot-title" className="snapshot-workflow">
      <SectionHeading
        eyebrow="审查输入 / 服务端复核"
        title="预览并冻结本次材料"
        level={4}
        id="snapshot-title"
        extra={sourcePreview && <Tag color="processing">预览 v{sourcePreview.case_version} · 服务端 10 分钟有效</Tag>}
      />
      <Paragraph type="secondary">
        仅纳入本案已选择的材料。金额敏感审查要求对应材料明确授权金额；服务端会再次核验身份、授权、案件版本、文件哈希和索引。真实外部财务/合同系统来源尚未接入。
      </Paragraph>
      <div className="snapshot-purpose-control">
        <Text strong>本次输入用途</Text>
        <Radio.Group
          aria-label="本次输入用途"
          optionType="button"
          buttonStyle="solid"
          value={sourcePurpose}
          disabled={snapshotControlsBusy || !!freezeTarget}
          onChange={(event) => changeSourcePurpose(event.target.value as SourcePreview["purpose"])}
        >
          <Radio.Button value="contract_review">合同审查材料</Radio.Button>
          <Radio.Button value="upstream_comparison">上下游比对准备</Radio.Button>
        </Radio.Group>
      </div>
      <Alert
        type="info"
        showIcon
        title={sourcePurpose === "upstream_comparison" ? "仅准备上下游比对输入，不会启动比对" : "仅准备合同审查输入，不会启动审查"}
        description={sourcePurpose === "upstream_comparison"
          ? "只检查本案待审主合同与经审核人确认属于同一项目、并单独授权上下游比对的客户合同；两侧材料都就绪后才能冻结。跨案件搜索和外部合同系统核验均未开放。"
          : "只检查本案已授权的主合同及合同审查材料。可在上方主合同审核中发起开发审核。"}
      />
      {caseRecord.legal_entity_id === null && <Alert
        type="warning"
        showIcon
        title="等待法人主体确认"
        description="可以先上传主合同；请先核实并确认主体，再逐份选择材料和授权。"
      />}
      {caseRecord.legal_entity_id !== null && !primarySelectionExists && <Alert
        type="info"
        showIcon
        title="先选择并授权本案主合同"
        description="上传不会自动纳入审查。请在材料列表中选择一份主合同，并确认当前审核人的文本/金额权限。"
      />}
      {previewError && <Alert className="document-warning" type="error" showIcon title={previewError} />}
      {freezeError && <Alert className="document-warning" type="warning" showIcon title={freezeError} />}
      <Space wrap className="snapshot-actions">
        <Button
          type="primary"
          icon={<LockOutlined />}
          loading={previewBusy}
          disabled={!primarySelectionExists || catalogError || snapshotControlsBusy || !!freezeTarget}
          onClick={() => void runSourcePreview()}
        >
          {previewBusy ? "正在核验授权与材料…" : "生成授权预览"}
        </Button>
        {sourcePreview && !frozenInput && !freezeTarget && <Button
          disabled={!previewCanFreeze || snapshotControlsBusy}
          onClick={() => { setFreezeTarget(sourcePreview); setFreezeDialogOpen(true); setFreezeError(null); }}
        >
          确认冻结 {sourcePreview.items.filter((item) => item.status === "ready").length} 份材料
        </Button>}
        {freezeTarget && !frozenInput && <Button
          type="primary"
          loading={freezeBusy}
          disabled={snapshotControlsBusy && !freezeBusy}
          onClick={() => setFreezeDialogOpen(true)}
        >
          {freezeBusy ? "正在冻结快照…" : "继续确认同一预览"}
        </Button>}
      </Space>
      {sourcePreview && <>
        {sourcePreview.items.length === 0
          ? <Empty className="preview-empty" description="当前用途下没有已选择并授权的材料" />
          : <ul className="preview-source-list" aria-label="本次来源预览">
            {sourcePreview.items.map((item) => {
              const document = documents.find((candidate) => candidate.selection_id === item.selection_id);
              return <li key={item.selection_id}>
                <div><strong>{document?.file_name ?? `材料 ${item.selection_id.slice(0, 8)}`}</strong><span>{sourceRelationLabel(item.relation)}</span></div>
                <Tag color={item.status === "ready" ? "success" : item.status === "restricted_amount" ? "warning" : "error"}>
                  {sourceStatusLabel(item.status)}
                </Tag>
              </li>;
            })}
          </ul>}
        {previewPairMissing && <Alert
          type="warning"
          showIcon
          title="上下游比对材料尚未成对"
          description="冻结前必须同时有本案待审主合同和项目客户合同，且两份材料都通过本次授权、主体、解析和索引核验。请在本案上传客户合同、确认同项目关系并授予上下游比对权限后重新预览。"
        />}
        {sourcePreview.items.length > 0 && !previewCanFreeze && !previewPairMissing && <Alert
          type="warning"
          showIcon
          title="当前预览不能冻结"
          description="先处理每一项未就绪状态（材料权限、金额授权、法律主体或索引），再生成新的授权预览。服务端不会跳过受限材料。"
        />}
      </>}
      {frozenInput && <Card className="frozen-input-card" size="small">
        <div className="frozen-input-heading"><Tag color="success">已冻结 · 不可变</Tag><strong>输入快照 v{frozenInput.case_version}</strong></div>
        <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
          { key: "snapshot", label: "快照编号", children: <Text code>{frozenInput.input_snapshot_id}</Text> },
          { key: "count", label: "纳入材料", children: `${frozenInput.document_version_ids.length} 份` },
          { key: "manifest", label: "输入清单哈希", children: <Text code>{frozenInput.manifest_sha256.slice(0, 16)}…</Text> },
          { key: "purpose", label: "用途", children: `${purposeName(frozenInput.purpose)}（含金额字段授权检查）` },
        ]} />
        {frozenInput.purpose === "upstream_comparison" ? <>
          <Alert type="info" showIcon title="比对输入已冻结" description="确认主合同与上游客户合同都在快照中后，即可发起逐份双合同比对。每个结果会展示两侧原文证据和复核限制。" />
          {comparisonError && <Alert className="document-warning" type="warning" showIcon title={comparisonError} />}
          <Button type="primary" loading={comparisonBusy} onClick={() => void submitUpstreamComparison()}>
            {comparisonBusy ? "正在创建比对任务…" : "发起上下游合同比对"}
          </Button>
        </> : <Alert type="info" showIcon title="快照已保存" description="冻结只保存当前用途的不可变输入；发起主合同审核请使用上方主合同审核入口。" />}
      </Card>}
    </section>


      </details>
      <details className="case-disclosure">
        <summary>操作记录：主体确认与材料授权</summary>
    {caseRecord.legal_entity_id !== null && <AsyncModuleBoundary pageName="主体审计记录">
      <Suspense fallback={<PanelLoading label="主体审计记录" />}>
        <CaseLegalEntityTimeline key={`${caseRecord.case_id}:${caseRecord.version}`} caseId={caseRecord.case_id} />
      </Suspense>
    </AsyncModuleBoundary>}
        <AsyncModuleBoundary pageName="授权记录">
          <Suspense fallback={<PanelLoading label="授权记录" />}>
            <DocumentAccessTimeline key={caseRecord.case_id} caseId={caseRecord.case_id} />
          </Suspense>
        </AsyncModuleBoundary>
      </details>
    </section>
    <section id="case-view-results" hidden={activeView !== "results"} className="case-view-panel" aria-labelledby="case-results-title">
      <div className="case-view-heading"><Title level={3} id="case-results-title">审核结果</Title><Paragraph type="secondary">按审核批次查看已产出的候选结果、审查范围与原文证据。</Paragraph></div>
    </section>
    <div hidden={activeView === "background"}>
      <AsyncModuleBoundary pageName={activeView === "results" ? "审核结果" : "审核记录"}>
        <Suspense fallback={<PanelLoading label="审核记录" />}>
          <RunHistoryPanel key={`${caseRecord.case_id}:${caseRecord.version}`} caseId={caseRecord.case_id} active={activeView !== "background"} view={activeView === "results" ? "results" : "history"} />
        </Suspense>
      </AsyncModuleBoundary>
    </div>
    <Modal
      open={!!grantTarget}
      title={grantRequest ? "重试材料用途授权" : `确认材料选择与${purposeName(grantPurpose)}授权`}
      onCancel={() => { if (!grantBusy) { setGrantTarget(null); setGrantError(null); } }}
      onOk={() => void submitGrant()}
      okText={grantRequest ? "使用相同权限重试" : "确认并授权"}
      cancelText="取消"
      confirmLoading={grantBusy}
      okButtonProps={{ disabled: !grantRequest && (!grantHours || (grantTarget?.role === "customer_contract" && !confirmProjectRelation)) }}
      cancelButtonProps={{ disabled: grantBusy }}
      closable={!grantBusy}
      mask={{ closable: !grantBusy }}
    >
      {grantTarget && <Space orientation="vertical" size="middle" className="permission-dialog-content">
        <Alert
          type="warning"
          showIcon
          title={`确认后，当前审核人可将材料用于${purposeName(grantPurpose)}`}
          description="授权只对当前登录审核人有效，最长 24 小时；每种用途分别授权。金额权限默认关闭，上传与索引就绪不等于授权。"
        />
        <Descriptions size="small" column={1} items={[
          { key: "file", label: "材料", children: grantTarget.file_name },
          { key: "relation", label: "关系", children: grantTarget.role === "primary_contract" ? "本案待审主合同" : grantTarget.role === "customer_contract" ? "本案项目客户合同" : "本案签约附件" },
          { key: "purpose", label: "用途", children: purposeName(grantPurpose) },
          { key: "actor", label: "授权对象", children: "当前登录用户（仅案件审核人可操作）" },
        ]} />
        {grantTarget.role === "customer_contract" && <>
          <Checkbox
            checked={confirmProjectRelation}
            disabled={grantBusy || !!grantRequest}
            onChange={(event) => { setConfirmProjectRelation(event.target.checked); setGrantError(null); }}
          >
            我已核对，该客户合同属于本案同一我方法人项目
          </Checkbox>
          <Paragraph type="secondary" className="permission-boundary-copy">
            系统会记录本案审核人的人工确认及时间，但不会查询项目台账、合同系统或其他案件来独立验证关系。
          </Paragraph>
        </>}
        <Checkbox
          checked={grantAllowAmount}
          disabled={grantBusy || !!grantRequest}
          onChange={(event) => { setGrantAllowAmount(event.target.checked); setGrantError(null); }}
        >
          允许模型读取本材料中的金额字段
        </Checkbox>
        <label className="permission-field" htmlFor="grant-validity">授权有效期</label>
        <select
          id="grant-validity"
          value={grantHours}
          disabled={grantBusy || !!grantRequest}
          onChange={(event) => { setGrantHours(event.target.value); setGrantError(null); }}
        >
          <option value="">请选择有效期</option>
          <option value="1">1 小时</option>
          <option value="4">4 小时</option>
          <option value="8">8 小时</option>
          <option value="24">24 小时（最长）</option>
        </select>
        {grantError && <Alert type="error" showIcon title={grantError} />}
      </Space>}
    </Modal>
    <Modal
      open={!!revokeTarget}
      title="撤销材料选择与全部授权？"
      onCancel={() => { if (!revokeBusy) { setRevokeTarget(null); setRevokeError(null); } }}
      onOk={() => void submitRevoke()}
      okText={revokeRequest ? "使用相同原因重试撤权" : "撤销全部授权"}
      cancelText="保留授权"
      okButtonProps={{ danger: true, disabled: !revokeRequest && !revokeReason.trim() }}
      cancelButtonProps={{ disabled: revokeBusy }}
      confirmLoading={revokeBusy}
      closable={!revokeBusy}
      mask={{ closable: !revokeBusy }}
    >
      {revokeTarget && <Space orientation="vertical" size="middle" className="permission-dialog-content">
        <Alert
          type="error"
          showIcon
          title={`将撤销“${revokeTarget.file_name}”的本案选择与全部用途的文本、金额授权`}
          description="此操作会增加案件版本，并使引用此材料的进行中审查运行失效。需要时可以重新授权，但旧运行不会恢复。"
        />
        <label className="permission-field" htmlFor="revoke-reason">撤权原因（必填）</label>
        <Input.TextArea
          id="revoke-reason"
          aria-label="撤权原因"
          value={revokeReason}
          maxLength={500}
          showCount
          autoSize={{ minRows: 3, maxRows: 5 }}
          disabled={revokeBusy || !!revokeRequest}
          placeholder="说明为何不再将此材料用于本案审查"
          onChange={(event) => { setRevokeReason(event.target.value); setRevokeError(null); }}
        />
        {revokeError && <Alert type="error" showIcon title={revokeError} />}
      </Space>}
    </Modal>
    <Modal
      open={!!removeTarget}
      title="从本案移除这份资料？"
      onCancel={closeRemoveDialog}
      onOk={() => void submitRemove()}
      okText={removeRequest ? "使用相同请求重试" : "确认移除"}
      cancelText="保留资料"
      okButtonProps={{ danger: true, disabled: !removeRequest && !removeReason.trim() }}
      cancelButtonProps={{ disabled: removeBusy }}
      confirmLoading={removeBusy}
      closable={!removeBusy}
      mask={{ closable: !removeBusy }}
    >
      {removeTarget && <Space orientation="vertical" size="middle" className="permission-dialog-content">
        <Alert
          type="warning"
          showIcon
          title={`“${removeTarget.file_name}”将从本案材料目录移除`}
          description="系统会撤销该资料的全部选择和读取授权，并使正在使用它的审核运行失效。历史审核与审计记录保留；文件对象会按存储清理策略回收。"
        />
        <label className="permission-field" htmlFor="remove-document-reason">移除原因（必填）</label>
        <Input.TextArea
          id="remove-document-reason"
          aria-label="移除原因"
          value={removeReason}
          maxLength={500}
          showCount
          autoSize={{ minRows: 3, maxRows: 5 }}
          disabled={removeBusy || !!removeRequest}
          placeholder="说明为何要将此资料从本案移除"
          onChange={(event) => { setRemoveReason(event.target.value); setRemoveError(null); }}
        />
        {removeError && <Alert type="error" showIcon title={removeError} />}
      </Space>}
    </Modal>
    <Modal
      open={entityConfirmOpen}
      title="确认法人主体并开放案件范围？"
      onCancel={() => {
        if (!entityConfirmBusy && !entityConfirmAttempted) {
          setEntityConfirmOpen(false);
          setEntityConfirmRequest(null);
        }
      }}
      onOk={() => void confirmLegalEntity()}
      okText={entityConfirmAttempted ? "使用同一确认请求重试" : "确认主体并继续"}
      cancelText="返回核对"
      confirmLoading={entityConfirmBusy}
      cancelButtonProps={{ disabled: entityConfirmBusy || entityConfirmAttempted }}
      closable={!entityConfirmBusy && !entityConfirmAttempted}
      mask={{ closable: !entityConfirmBusy && !entityConfirmAttempted }}
    >
      {entityConfirmRequest && <Space orientation="vertical" size="middle" className="permission-dialog-content">
        <Alert
          type="warning"
          showIcon
          title="确认后，该主体将成为本案的范围边界"
          description="案件已上传的主合同会绑定到此主体；之后才可上传附件、逐份选择并授权。确认后主体锁定，审核人不能自行更改；如有误，仅授权管理员可按纠错流程更正并留审计记录。"
        />
        <Descriptions size="small" column={1} items={[
          { key: "case", label: "案件", children: caseRecord.title },
          { key: "entity", label: "法人主体 UUID", children: <Text code>{entityConfirmRequest.legal_entity_id}</Text> },
          { key: "version", label: "案件版本", children: `v${entityConfirmRequest.expected_case_version}` },
          { key: "basis", label: "核对依据", children: entityConfirmRequest.verification_note },
        ]} />
        {entityConfirmError && <Alert type="error" showIcon title={entityConfirmError} />}
      </Space>}
    </Modal>
    <Modal
      open={entityCorrectionOpen}
      title="申请更正法人主体"
      onCancel={() => {
        if (!entityCorrectionBusy && !entityCorrectionRequest) {
          setEntityCorrectionOpen(false);
          if (entityCorrectionError) setEntityCorrectionError(null);
        }
      }}
      onOk={() => entityCorrectionRequest ? void submitEntityCorrection() : void entityCorrectionForm.submit()}
      okText={entityCorrectionRequest ? entityCorrectionError ? "使用同一更正请求重试" : "确认更正并撤销当前准备" : "继续核对影响"}
      cancelText={entityCorrectionRequest ? "关闭" : "取消"}
      confirmLoading={entityCorrectionBusy}
      cancelButtonProps={{ disabled: entityCorrectionBusy || !!entityCorrectionRequest }}
      closable={!entityCorrectionBusy && !entityCorrectionRequest}
      mask={{ closable: !entityCorrectionBusy && !entityCorrectionRequest }}
      okButtonProps={{ danger: !!entityCorrectionRequest }}
    >
      {!entityCorrectionRequest ? <Form
        form={entityCorrectionForm}
        layout="vertical"
        noValidate
        onFinish={stageEntityCorrection}
      >
        <Form.Item
          label="更正后的主体"
          name="legal_entity_id"
          rules={[
            { required: true, message: "请选择更正后的法人主体。" },
            { pattern: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, message: "请输入标准 UUID 格式。" },
            { validator: (_, value: string | undefined) => value?.trim().toLowerCase() === caseRecord.legal_entity_id?.toLowerCase() ? Promise.reject(new Error("新主体必须与当前主体不同。")) : Promise.resolve() },
          ]}
        >
          <LegalEntitySelect disabled={entityCorrectionBusy} excludeId={caseRecord.legal_entity_id ?? undefined} />
        </Form.Item>
        <Form.Item
          label="纠错依据（必填）"
          name="correction_note"
          rules={[{ required: true, whitespace: true, message: "请记录主体纠错依据。" }, { max: 500, message: "纠错依据不能超过 500 个字符。" }]}
        >
          <Input.TextArea disabled={entityCorrectionBusy} maxLength={500} showCount autoSize={{ minRows: 3, maxRows: 5 }} placeholder="说明核对了哪些签约资料以及原主体为何有误" />
        </Form.Item>
        {entityCorrectionError && <Alert type="error" showIcon title={entityCorrectionError} />}
      </Form> : <Space orientation="vertical" size="middle" className="permission-dialog-content">
        <Alert
          type="error"
          showIcon
          title="纠错会立即撤销本案当前审查准备状态"
          description="所有材料选择及文本/金额授权将撤销；既有审查运行（含已完成运行）标记为过期，未完成任务失效。原始材料版本、冻结快照、结果和纠错审计仍保留为历史记录，不会改写。"
        />
        <Alert
          type="warning"
          showIcon
          title="新主体范围不会沿用旧材料授权"
          description="当前已上传材料版本保留原主体绑定，不会自动迁移或继续用于新主体。需要按新主体重新上传材料版本，再重新选择、授权、预览和冻结；后续审核须重新确认材料范围和授权。"
        />
        <Descriptions size="small" column={1} items={[
          { key: "old", label: "当前主体 UUID", children: <Text code>{caseRecord.legal_entity_id}</Text> },
          { key: "new", label: "更正为", children: <Text code>{entityCorrectionRequest.legal_entity_id}</Text> },
          { key: "version", label: "基于案件版本", children: `v${entityCorrectionRequest.expected_case_version}` },
          { key: "reason", label: "纠错依据", children: entityCorrectionRequest.correction_note },
        ]} />
        {entityCorrectionError && <Alert type="error" showIcon title={entityCorrectionError} />}
      </Space>}
    </Modal>
    <Modal
      open={freezeDialogOpen && !!freezeTarget}
      title={`冻结本次${freezeTarget ? purposeName(freezeTarget.purpose) : "审查"}输入？`}
      onCancel={() => { if (!freezeBusy) setFreezeDialogOpen(false); }}
      onOk={() => void submitFreeze()}
      okText={freezeBusy ? "正在冻结" : freezeError ? "使用同一预览重试冻结" : "确认冻结输入"}
      cancelText="返回核对"
      confirmLoading={freezeBusy}
      cancelButtonProps={{ disabled: freezeBusy }}
      closable={!freezeBusy}
      mask={{ closable: !freezeBusy }}
    >
      {freezeTarget && <Space orientation="vertical" size="middle" className="permission-dialog-content">
        <Alert
          type="warning"
          showIcon
          title="冻结后生成不可变输入版本"
          description="服务端会在事务中再次核验案件版本、材料哈希、当前审核人授权和索引状态。任一项变化都会拒绝冻结；请重新预览，不会使用旧快照绕过撤权。"
        />
        <Descriptions size="small" column={1} items={[
          { key: "case-version", label: "预览案件版本", children: `v${freezeTarget.case_version}` },
          { key: "materials", label: "材料数量", children: `${freezeTarget.items.length} 份，且均已通过本次预览` },
          { key: "hash", label: "输入清单哈希", children: <Text code>{freezeTarget.manifest_sha256.slice(0, 16)}…</Text> },
          { key: "purpose", label: "输入用途", children: purposeName(freezeTarget.purpose) },
          { key: "external", label: "外部来源", children: "仅含本案上传材料；真实财务/合同系统尚未接入" },
        ]} />
        {freezeError && <Alert type="error" showIcon title={freezeError} />}
      </Space>}
    </Modal>
  </>;
}

function formatBytes(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MiB` : `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}
