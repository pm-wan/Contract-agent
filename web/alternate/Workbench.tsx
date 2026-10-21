import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from "react";
import {
  ApiError,
  confirmCaseLegalEntity,
  createCase,
  getCase,
  listCaseDocuments,
  listCases,
  removeCaseDocument,
  uploadCaseDocument,
  type CaseDocument,
  type CaseRecord,
  type AuthProfile,
} from "../src/api";
import { caseStatusLabel, formatDate } from "../src/case-format";
import { createUuid } from "../src/browserUuid";

const pageSizeChoices = [10, 20, 50, 100];
const maximumUploadBytes = 30 * 1024 * 1024;

function statusName(status: CaseDocument["parse_status"]) {
  const names: Record<CaseDocument["parse_status"], string> = {
    queued: "排队解析",
    processing: "正在解析",
    completed: "解析完成",
    needs_material: "需补充材料",
    failed: "解析失败",
  };
  return names[status];
}

function ParseProgress({ status }: { status: CaseDocument["parse_status"] }) {
  const label = status === "queued"
    ? "待开始"
    : status === "processing"
      ? "处理中"
      : status === "completed"
        ? "100%"
        : status === "needs_material"
          ? "100%"
          : "未完成";
  const value = status === "queued"
    ? 0
    : status === "completed" || status === "needs_material"
      ? 100
      : undefined;

  return <div className={`parse-progress parse-progress-${status}`}>
    <div className="parse-progress-track" role="progressbar" aria-label="解析进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-valuetext={label}>
      <span className="parse-progress-fill" />
    </div>
    <span className="parse-progress-label" aria-hidden="true">{label}</span>
  </div>;
}

function roleName(role: CaseDocument["role"]) {
  const names: Record<CaseDocument["role"], string> = {
    primary_contract: "待审主合同",
    attachment: "附件",
    customer_contract: "项目客户合同",
  };
  return names[role];
}

function indexStatusName(status: CaseDocument["index_status"]) {
  const names: Record<CaseDocument["index_status"], string> = {
    missing: "未建索引",
    queued: "等待索引",
    running: "正在索引",
    failed: "索引失败",
    ready: "索引就绪",
    stale: "索引过期",
  };
  return names[status];
}

function authorizationName(status: CaseDocument["authorization_status"]) {
  const names: Record<CaseDocument["authorization_status"], string> = {
    not_authorized: "未授权模型读取",
    authorized: "已授权模型读取",
    expired: "模型读取授权已过期",
  };
  return names[status];
}

function safeErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 401) return "登录状态已失效，请返回登录页面重新登录。";
  if (error.status === 403) return "当前身份无权查看或修改此案件。";
  if (error.status === 404) return "案件暂不可用或已不存在。";
  if (error.status === 409) return "案件状态已变化，请刷新后核对再继续。";
  if (error.status === 413) return "文件超过 30 MiB 服务端限制。";
  if (error.status === 422) return "信息或文件格式未通过服务端校验，请检查后重试。";
  if (error.status === 503) return "此项能力当前未开放或暂不可用。";
  return fallback;
}

function readLocation() {
  return new URLSearchParams(window.location.search);
}

function idempotencyKey() {
  return createUuid();
}

function byteSize(size: number) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function AlternateWorkbench({
  user,
  onSignOut,
}: {
  user?: AuthProfile;
  onSignOut?: () => void;
} = {}) {
  const [locationParams, setLocationParams] = useState(readLocation);
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [totalCases, setTotalCases] = useState(0);
  const [caseLoading, setCaseLoading] = useState(true);
  const [caseError, setCaseError] = useState("");
  const [retryCount, setRetryCount] = useState(0);
  const [queryDraft, setQueryDraft] = useState(locationParams.get("query") ?? "");
  const [composing, setComposing] = useState(false);
  const [selectedCase, setSelectedCase] = useState<CaseRecord | null>(null);
  const [documents, setDocuments] = useState<CaseDocument[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createTitle, setCreateTitle] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [createUncertain, setCreateUncertain] = useState(false);
  const [uploadRole, setUploadRole] = useState<CaseDocument["role"]>("primary_contract");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadUncertain, setUploadUncertain] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadMessage, setUploadMessage] = useState("");
  const [legalEntityId, setLegalEntityId] = useState("");
  const [verificationNote, setVerificationNote] = useState("");
  const [entityBusy, setEntityBusy] = useState(false);
  const [entityUncertain, setEntityUncertain] = useState(false);
  const [entityMessage, setEntityMessage] = useState("");
  const [removeTarget, setRemoveTarget] = useState<CaseDocument | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeUncertain, setRemoveUncertain] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const [removeNotice, setRemoveNotice] = useState("");
  const createDialog = useRef<HTMLDialogElement>(null);
  const entityDialog = useRef<HTMLDialogElement>(null);
  const removeDialog = useRef<HTMLDialogElement>(null);
  const createTitleInput = useRef<HTMLInputElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const caseHeading = useRef<HTMLHeadingElement>(null);
  const mainContent = useRef<HTMLElement>(null);
  const createKey = useRef<string | null>(null);
  const uploadRequests = useRef(new Map<string, { file: File; role: CaseDocument["role"]; key: string }>());
  const entityRequests = useRef(new Map<string, {
    legalEntityId: string;
    verificationNote: string;
    expectedCaseVersion: number;
    key: string;
  }>());
  const removeRequests = useRef(new Map<string, {
    expectedCaseVersion: number;
    reason: string;
    key: string;
  }>());

  const selectedId = locationParams.get("case");
  const query = locationParams.get("query") ?? "";
  const page = Math.max(1, Number(locationParams.get("page") ?? 1) || 1);
  const pageSize = pageSizeChoices.includes(Number(locationParams.get("page_size")))
    ? Number(locationParams.get("page_size"))
    : 20;
  const pageCount = Math.max(1, Math.ceil(totalCases / pageSize));

  const navigate = useCallback((next: URLSearchParams, replace = false) => {
    const queryString = next.toString();
    const nextUrl = `${window.location.pathname}${queryString ? `?${queryString}` : ""}`;
    window.history[replace ? "replaceState" : "pushState"]({}, "", nextUrl);
    setLocationParams(new URLSearchParams(next));
  }, []);

  useEffect(() => {
    const handlePopState = () => setLocationParams(readLocation());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => setQueryDraft(query), [query]);

  const hasPendingParse = documents.some((document) => document.parse_status === "queued" || document.parse_status === "processing");
  useEffect(() => {
    if (!selectedId || !hasPendingParse) return;
    let active = true;
    let timer = 0;
    const refreshParseStatuses = async () => {
      try {
        const latest = await listCaseDocuments(selectedId);
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
  }, [hasPendingParse, selectedId]);

  useEffect(() => {
    if (mainContent.current) {
      mainContent.current.scrollTop = 0;
      mainContent.current.scrollLeft = 0;
    }
  }, [selectedId]);

  useEffect(() => {
    if (composing || queryDraft.trim() === query) return;
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(locationParams);
      const normalized = queryDraft.trim();
      if (normalized) next.set("query", normalized);
      else next.delete("query");
      next.set("page", "1");
      navigate(next, true);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [composing, locationParams, navigate, query, queryDraft]);

  useEffect(() => {
    if (selectedId) return;
    const controller = new AbortController();
    setCaseLoading(true);
    setCaseError("");
    listCases({ page, pageSize, query }, controller.signal)
      .then((result) => {
        setCases(result.items);
        setTotalCases(result.total);
        if (result.page !== page) {
          const next = new URLSearchParams(locationParams);
          next.set("page", String(result.page));
          navigate(next, true);
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setCaseError(safeErrorMessage(error, "案件列表暂时无法读取，请检查连接后重试。"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setCaseLoading(false);
      });
    return () => controller.abort();
  }, [locationParams, navigate, page, pageSize, query, retryCount, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedCase(null);
      setDocuments([]);
      setDetailError("");
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError("");
    Promise.all([
      getCase(selectedId, controller.signal),
      listCaseDocuments(selectedId, controller.signal),
    ])
      .then(([caseRecord, documentPage]) => {
        setSelectedCase(caseRecord);
        setDocuments(documentPage);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setDetailError(safeErrorMessage(error, "案件信息暂时无法读取，请重试。"));
        setSelectedCase(null);
        setDocuments([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [retryCount, selectedId]);

  useEffect(() => {
    const pendingUpload = selectedId ? uploadRequests.current.get(selectedId) : undefined;
    setUploadFile(pendingUpload?.file ?? null);
    setUploadRole(pendingUpload?.role ?? "primary_contract");
    setUploadUncertain(Boolean(pendingUpload));
    setUploadMessage(pendingUpload ? "此文件的提交结果待核实。刷新材料目录后，可用相同请求安全重试。" : "");
    const pendingEntity = selectedId ? entityRequests.current.get(selectedId) : undefined;
    setEntityUncertain(Boolean(pendingEntity));
    setEntityMessage("");
    setLegalEntityId(pendingEntity?.legalEntityId ?? "");
    setVerificationNote(pendingEntity?.verificationNote ?? "");
  }, [selectedId]);

  useEffect(() => {
    document.title = selectedId && selectedCase
      ? `${selectedCase.title} — 案件案牍`
      : "案件案牍 — 合同审查";
    if (selectedId && selectedCase) caseHeading.current?.focus({ preventScroll: true });
  }, [selectedCase, selectedId]);

  const visibleRange = useMemo(() => {
    if (!totalCases) return "当前没有案件";
    const first = (page - 1) * pageSize + 1;
    const last = Math.min(page * pageSize, totalCases);
    return `显示 ${first}–${last} 项，共 ${totalCases} 项`;
  }, [page, pageSize, totalCases]);

  function openCreateDialog() {
    if (!createUncertain) {
      setCreateError("");
      setCreateTitle("");
    }
    setCreating(true);
  }

  useEffect(() => {
    if (!creating || !createDialog.current || createDialog.current.open) return;
    createDialog.current.showModal();
    createTitleInput.current?.focus();
  }, [creating]);

  function closeCreateDialog() {
    createDialog.current?.close();
    setCreating(false);
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedTitle = createTitle.trim();
    if (!normalizedTitle) {
      setCreateError("请填写案件名称。");
      createTitleInput.current?.focus();
      return;
    }
    setCreateBusy(true);
    setCreateError("");
    try {
      createKey.current ??= idempotencyKey();
      const created = await createCase(normalizedTitle, createKey.current);
      createKey.current = null;
      setCreateUncertain(false);
      closeCreateDialog();
      const next = new URLSearchParams(locationParams);
      next.set("case", created.case_id);
      navigate(next);
    } catch (error) {
      if (error instanceof ApiError && error.status === 0) setCreateUncertain(true);
      else {
        createKey.current = null;
        setCreateUncertain(false);
      }
      setCreateError(safeErrorMessage(error, "案件未创建。网络结果不确定时，请先返回列表核对。"));
    } finally {
      setCreateBusy(false);
    }
  }

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCase || !uploadFile) {
      setUploadMessage("请先选择一份 PDF 或 DOCX 文件。");
      return;
    }
    const extension = uploadFile.name.split(".").pop()?.toLowerCase();
    if (uploadFile.size === 0 || uploadFile.size > maximumUploadBytes || !["pdf", "docx"].includes(extension ?? "")) {
      setUploadMessage(uploadFile.size > maximumUploadBytes
        ? "文件超过 30 MiB 限制。"
        : "文件为空或格式不支持，请选择 PDF 或 DOCX。");
      return;
    }
    if (!selectedCase.legal_entity_confirmed_at && uploadRole !== "primary_contract") {
      setUploadMessage("签约主体确认前，只能上传待审主合同。");
      return;
    }
    setUploadBusy(true);
    setUploadProgress(0);
    setUploadMessage("文件正在上传；上传完成不代表已授权模型读取。");
    const pendingRequest = uploadRequests.current.get(selectedCase.case_id);
    if (pendingRequest && (pendingRequest.file !== uploadFile || pendingRequest.role !== uploadRole)) {
      setUploadMessage("此案件有一份提交结果待核实的文件。请先刷新材料目录，并使用原文件与用途安全重试。");
      setUploadBusy(false);
      return;
    }
    try {
      const request = pendingRequest ?? { file: uploadFile, role: uploadRole, key: idempotencyKey() };
      uploadRequests.current.set(selectedCase.case_id, request);
      await uploadCaseDocument(selectedCase.case_id, uploadFile, uploadRole, request.key, setUploadProgress);
      uploadRequests.current.delete(selectedCase.case_id);
      setUploadUncertain(false);
      setUploadFile(null);
      setUploadMessage("上传已确认，正在刷新材料目录…");
      try {
        const [latestCase, refreshed] = await Promise.all([
          getCase(selectedCase.case_id),
          listCaseDocuments(selectedCase.case_id),
        ]);
        setSelectedCase(latestCase);
        setDocuments(refreshed);
        setUploadMessage("上传已确认。解析和授权状态请以材料列表为准。");
      } catch {
        setUploadMessage("上传已确认，但材料目录未能刷新；请使用“刷新列表”查看当前状态。");
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 0) setUploadUncertain(true);
      else {
        uploadRequests.current.delete(selectedCase.case_id);
        setUploadUncertain(false);
      }
      setUploadMessage(safeErrorMessage(error, "上传状态尚未确认。刷新材料列表核对后再重试。"));
    } finally {
      setUploadBusy(false);
    }
  }

  function openRemoveDialog(document: CaseDocument) {
    const request = removeRequests.current.get(document.document_version_id);
    setRemoveTarget(document);
    setRemoveReason(request?.reason ?? "");
    setRemoveUncertain(Boolean(request));
    setRemoveError("");
    setRemoveNotice("");
  }

  function closeRemoveDialog() {
    if (!removeBusy) removeDialog.current?.close();
  }

  async function submitRemoval(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCase || !removeTarget) return;
    let request = removeRequests.current.get(removeTarget.document_version_id);
    if (!request) {
      const reason = removeReason.trim();
      if (!reason) {
        setRemoveError("请填写移除此资料的原因。");
        return;
      }
      request = { expectedCaseVersion: selectedCase.version, reason, key: idempotencyKey() };
      removeRequests.current.set(removeTarget.document_version_id, request);
    }
    setRemoveBusy(true);
    setRemoveError("");
    try {
      const removed = await removeCaseDocument(
        selectedCase.case_id,
        removeTarget.document_version_id,
        { expected_case_version: request.expectedCaseVersion, reason: request.reason },
        request.key,
      );
      removeRequests.current.delete(removeTarget.document_version_id);
      setRemoveUncertain(false);
      try {
        const [latestCase, latestDocuments] = await Promise.all([
          getCase(selectedCase.case_id),
          listCaseDocuments(selectedCase.case_id),
        ]);
        setSelectedCase(latestCase);
        setDocuments(latestDocuments);
      } catch {
        setRetryCount((count) => count + 1);
      }
      setRemoveNotice(`资料已从本案移除；撤销 ${removed.revoked_selection_count} 项选择和 ${removed.revoked_grant_count} 项授权，${removed.invalidated_run_count} 个进行中的审核运行已失效。`);
      removeDialog.current?.close();
    } catch (error) {
      setRemoveError(safeErrorMessage(error, "材料移除未完成，请刷新列表后重试。"));
      if (error instanceof ApiError && error.status === 0) {
        setRemoveUncertain(true);
      } else {
        removeRequests.current.delete(removeTarget.document_version_id);
        setRemoveUncertain(false);
      }
      try {
        const [latestCase, latestDocuments] = await Promise.all([
          getCase(selectedCase.case_id),
          listCaseDocuments(selectedCase.case_id),
        ]);
        setSelectedCase(latestCase);
        setDocuments(latestDocuments);
        if (!latestDocuments.some((document) => document.document_version_id === removeTarget.document_version_id)) {
          removeRequests.current.delete(removeTarget.document_version_id);
          setRemoveNotice("服务器已确认该资料已从本案移除，材料目录已刷新。");
          setRemoveError("");
          setRemoveUncertain(false);
          removeDialog.current?.close();
        }
      } catch {
        setRetryCount((count) => count + 1);
      }
    } finally {
      setRemoveBusy(false);
    }
  }

  useEffect(() => {
    if (!creating && createDialog.current?.open) createDialog.current.close();
  }, [creating]);

  useEffect(() => {
    if (!selectedId || !selectedCase?.legal_entity_confirmed_at) return;
    if (entityDialog.current?.open) entityDialog.current.close();
  }, [selectedCase, selectedId]);

  useEffect(() => {
    if (removeTarget && removeDialog.current && !removeDialog.current.open) {
      removeDialog.current.showModal();
    } else if (!removeTarget && removeDialog.current?.open) {
      removeDialog.current.close();
    }
  }, [removeTarget]);

  function openEntityDialog() {
    const pendingRequest = selectedCase ? entityRequests.current.get(selectedCase.case_id) : undefined;
    setEntityUncertain(Boolean(pendingRequest));
    setEntityMessage("");
    setLegalEntityId(pendingRequest?.legalEntityId ?? "");
    setVerificationNote(pendingRequest?.verificationNote ?? "");
    entityDialog.current?.showModal();
  }

  async function submitEntityConfirmation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCase) return;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(legalEntityId.trim())) {
      setEntityMessage("请输入有效的签约主体 UUID。");
      return;
    }
    if (!verificationNote.trim()) {
      setEntityMessage("请记录核对签约主体所依据的资料或过程。");
      return;
    }
    setEntityBusy(true);
    setEntityMessage("");
    try {
      const request = entityRequests.current.get(selectedCase.case_id) ?? {
          legalEntityId: legalEntityId.trim(),
          verificationNote: verificationNote.trim(),
          expectedCaseVersion: selectedCase.version,
          key: idempotencyKey(),
        };
      entityRequests.current.set(selectedCase.case_id, request);
      await confirmCaseLegalEntity(selectedCase.case_id, {
        legal_entity_id: request.legalEntityId,
        expected_case_version: request.expectedCaseVersion,
        verification_note: request.verificationNote,
      }, request.key);
      entityRequests.current.delete(selectedCase.case_id);
      setEntityUncertain(false);
      const updated = await getCase(selectedCase.case_id);
      setSelectedCase(updated);
      entityDialog.current?.close();
    } catch (error) {
      if (error instanceof ApiError && error.status === 0) setEntityUncertain(true);
      else {
        entityRequests.current.delete(selectedCase.case_id);
        setEntityUncertain(false);
      }
      setEntityMessage(safeErrorMessage(error, "主体确认未完成，请刷新案件并核对版本后重试。"));
    } finally {
      setEntityBusy(false);
    }
  }

  function changePage(nextPage: number, nextSize = pageSize) {
    const next = new URLSearchParams(locationParams);
    next.set("page", String(nextPage));
    next.set("page_size", String(nextSize));
    navigate(next);
  }

  function clearSearch() {
    const next = new URLSearchParams(locationParams);
    next.delete("query");
    next.set("page", "1");
    navigate(next, true);
    setQueryDraft("");
    searchInput.current?.focus();
  }

  function returnToList() {
    const next = new URLSearchParams(locationParams);
    next.delete("case");
    navigate(next);
  }

  return (
    <div className="alt-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="side-rail" aria-label="主导航">
        <a className="seal" href="/alternate/" aria-label="案牍工作台首页"><span>牍</span></a>
        <nav className="rail-nav" aria-label="工作区">
          <button className="rail-button is-current" type="button" aria-current="page" title="案件案卷" onClick={returnToList}>
            <span aria-hidden="true">▤</span><span className="rail-label">案件</span>
          </button>
          <button className="rail-button rail-button-disabled" type="button" disabled title="独立版本目前包含案件台账与详情"><span aria-hidden="true">⌁</span><span className="rail-label">复核</span></button>
          <a className="rail-button" href="/" title="旧版工作台"><span aria-hidden="true">↗</span><span className="rail-label">旧版</span></a>
        </nav>
        <div className="rail-bottom"><span className="rail-dot" aria-hidden="true" /><span>本机环境</span></div>
      </aside>

      <div className="alt-frame">
        <header className="topbar">
          <div className="topbar-brand"><span className="brand-kicker">本机 / 审阅工作区</span><span>合同案牍</span></div>
          <div className="topbar-meta">
            <span className="online-dot" aria-hidden="true" />
            <span className="workspace-label">开发工作区</span>
            <span className="topbar-divider" />
            <span className="account-name" title={user?.username}>{user?.display_name ?? "审阅人"}</span>
            {onSignOut && <button className="account-signout" type="button" onClick={onSignOut}>退出登录</button>}
          </div>
        </header>

        <main id="main-content" ref={mainContent} className="main-content">
          {selectedId ? (
            <CaseWorkspace
              caseRecord={selectedCase}
              documents={documents}
              loading={detailLoading}
              error={detailError}
              headingRef={caseHeading}
              onBack={returnToList}
              onRetry={() => setRetryCount((count) => count + 1)}
              onConfirmEntity={openEntityDialog}
              uploadRole={uploadRole}
              setUploadRole={setUploadRole}
              uploadFile={uploadFile}
              setUploadFile={setUploadFile}
              uploadBusy={uploadBusy}
              uploadUncertain={uploadUncertain}
              uploadProgress={uploadProgress}
              uploadMessage={uploadMessage}
              onUpload={submitUpload}
              onRemove={openRemoveDialog}
              removeBusy={removeBusy}
              removeNotice={removeNotice}
            />
          ) : (
            <>
              <div className="page-overline"><span className="overline-mark" />工作台 <span>/</span> 案件台账</div>
              <section className="page-heading">
                <div>
                  <h1>案件台账<span className="heading-period">。</span></h1>
                  <p>从主合同开始，核对主体、材料与审查范围。</p>
                </div>
                <button className="button button-primary" type="button" onClick={openCreateDialog}>
                  <span aria-hidden="true">＋</span> 新建案件
                </button>
              </section>

              <div className="ledger-note" role="note">
                <span className="note-index">工作顺序</span>
                <span>建立案件 <b>→</b> 上传主合同 <b>→</b> 人工确认签约主体 <b>→</b> 选择并授权材料</span>
                <span className="note-tail">上传不等于授权</span>
              </div>

              <section className="list-panel" aria-labelledby="case-list-heading">
                <div className="list-panel-head">
                  <div><span className="section-kicker">案件台账</span><h2 id="case-list-heading">全部案件</h2></div>
                  <div className="list-tools">
                    <div className="search-box">
                      <span className="search-icon" aria-hidden="true">⌕</span>
                      <label className="visually-hidden" htmlFor="case-search">按案件名称搜索</label>
                      <input
                        id="case-search"
                        ref={searchInput}
                        value={queryDraft}
                        onChange={(event) => setQueryDraft(event.target.value)}
                        onCompositionStart={() => setComposing(true)}
                        onCompositionEnd={() => setComposing(false)}
                        onKeyDown={(event) => { if (event.key === "Escape") clearSearch(); }}
                        placeholder="搜索案件名称"
                        type="search"
                      />
                      {queryDraft && <button type="button" className="clear-search" aria-label="清除搜索" onClick={clearSearch}>×</button>}
                    </div>
                    <button className="button button-quiet" type="button" onClick={() => setRetryCount((count) => count + 1)} disabled={caseLoading}>
                      <span aria-hidden="true">↻</span> 刷新
                    </button>
                  </div>
                </div>

                {caseError ? (
                  <div className="state-card state-error" role="alert">
                    <span className="state-symbol" aria-hidden="true">!</span>
                    <div><strong>台账暂不可用</strong><p>{caseError}</p></div>
                    <button className="button button-outline" type="button" onClick={() => setRetryCount((count) => count + 1)}>重试</button>
                  </div>
                ) : caseLoading ? (
                  <div className="state-card" role="status" aria-live="polite"><span className="spinner" aria-hidden="true" /><span>正在读取案件台账…</span></div>
                ) : cases.length === 0 ? (
                  <div className="empty-state">
                    <div className="empty-emblem" aria-hidden="true">牍</div>
                    <h3>{query ? "没有匹配的案件" : "台账尚空"}</h3>
                    <p>{query ? "试试缩短关键词，或清除搜索条件。" : "先建立一个案件，再上传待审主合同。"}</p>
                    {query ? <button className="button button-outline" type="button" onClick={clearSearch}>清除搜索</button> : <button className="button button-primary" type="button" onClick={openCreateDialog}>建立首个案件 <span aria-hidden="true">↗</span></button>}
                  </div>
                ) : (
                  <>
                    <div className="table-scroll">
                      <table className="case-table">
                        <thead><tr><th scope="col">案件名称</th><th scope="col">签约主体</th><th scope="col">当前状态</th><th scope="col">建立时间</th><th scope="col"><span className="visually-hidden">打开</span></th></tr></thead>
                        <tbody>
                          {cases.map((caseRecord) => (
                            <tr key={caseRecord.case_id}>
                              <td><button className="case-link" type="button" onClick={() => { const next = new URLSearchParams(locationParams); next.set("case", caseRecord.case_id); navigate(next); }}><span className="case-stamp" aria-hidden="true">案</span><span className="case-title-wrap"><strong>{caseRecord.title}</strong><small>版本 {caseRecord.version} · {caseRecord.case_id.slice(0, 8)}</small></span></button></td>
                              <td>{caseRecord.legal_entity_confirmed_at ? <span className="entity-value">{caseRecord.legal_entity_id}</span> : <span className="text-muted">待人工确认</span>}</td>
                              <td><span className={`status-chip ${caseRecord.legal_entity_confirmed_at ? "status-ready" : "status-pending"}`}><span className="status-dot" />{caseStatusLabel(caseRecord.status)}</span></td>
                              <td className="date-cell">{formatDate(caseRecord.created_at)}</td>
                              <td><button className="row-open" type="button" aria-label={`打开案件：${caseRecord.title}`} onClick={() => { const next = new URLSearchParams(locationParams); next.set("case", caseRecord.case_id); navigate(next); }}>↗</button></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="table-footer">
                      <span>{visibleRange}</span>
                      <div className="pagination-controls">
                        <label htmlFor="page-size">每页</label>
                        <select id="page-size" value={pageSize} onChange={(event) => changePage(1, Number(event.target.value))}>
                          {pageSizeChoices.map((size) => <option key={size} value={size}>{size} 项</option>)}
                        </select>
                        <button className="page-button" type="button" aria-label="上一页" disabled={page <= 1} onClick={() => changePage(page - 1)}>←</button>
                        <span className="page-count">{page} / {pageCount}</span>
                        <button className="page-button" type="button" aria-label="下一页" disabled={page >= pageCount} onClick={() => changePage(page + 1)}>→</button>
                      </div>
                    </div>
                  </>
                )}
              </section>

              <footer className="page-footnote"><span>案牍 / 内部审阅</span><span>范围与授权由服务端核验</span></footer>
            </>
          )}
        </main>
      </div>

      <dialog ref={createDialog} className="app-dialog" onClose={() => setCreating(false)}>
        <form className="dialog-form" noValidate onSubmit={submitCreate}>
          <div className="dialog-topline"><span className="dialog-stamp">新</span><button className="dialog-close" type="button" aria-label="关闭" onClick={closeCreateDialog}>×</button></div>
          <span className="section-kicker">新建案卷</span>
          <h2>建立案件</h2>
          <p className="dialog-intro">案件建立后，先上传待审主合同。签约主体须由工作人员核对确认。</p>
          <label className="field-label" htmlFor="case-title">案件名称</label>
          <input ref={createTitleInput} id="case-title" className="text-field" value={createTitle} maxLength={200} disabled={createUncertain} onChange={(event) => setCreateTitle(event.target.value)} aria-invalid={Boolean(createError)} aria-describedby={createError ? "create-error" : "case-title-hint"} />
          <p id="case-title-hint" className="field-hint">使用便于团队识别的项目或合同名称。</p>
          {createError && <p id="create-error" className="field-error" role="alert">{createError}</p>}
          <div className="dialog-actions"><button className="button button-outline" type="button" onClick={closeCreateDialog} disabled={createBusy}>返回核对</button><button className="button button-primary" type="submit" disabled={createBusy}>{createBusy ? "正在建立…" : createUncertain ? "使用同一请求重试" : "建立案件"}</button></div>
        </form>
      </dialog>

      <dialog ref={entityDialog} className="app-dialog entity-dialog" onClose={() => setEntityMessage("")}>
        <form className="dialog-form" noValidate onSubmit={submitEntityConfirmation}>
          <div className="dialog-topline"><span className="dialog-stamp stamp-warm">核</span><button className="dialog-close" type="button" aria-label="关闭" onClick={() => entityDialog.current?.close()}>×</button></div>
          <span className="section-kicker">人工核验</span>
          <h2>确认签约主体</h2>
          <div className="caution-copy"><strong>确认后主体将锁定。</strong><span>请根据签约资料核对；不要让模型推断法人。确认后可继续上传附件并开展逐份授权。</span></div>
          <label className="field-label" htmlFor="entity-id">签约主体 UUID</label>
          <input id="entity-id" className="text-field" value={legalEntityId} onChange={(event) => setLegalEntityId(event.target.value)} autoComplete="off" spellCheck={false} autoFocus disabled={entityBusy || entityUncertain} aria-invalid={Boolean(entityMessage)} aria-describedby="entity-id-hint" />
          <p id="entity-id-hint" className="field-hint">填写已核验的内部主体标识；不确定时先取消并核实。</p>
          <label className="field-label" htmlFor="verification-note">核对依据</label>
          <textarea id="verification-note" className="text-field text-area resize-none" value={verificationNote} maxLength={500} onChange={(event) => setVerificationNote(event.target.value)} disabled={entityBusy || entityUncertain} aria-describedby="verification-hint" />
          <p id="verification-hint" className="field-hint">记录所依据的签约资料或核验过程（最多 500 字）。</p>
          {entityMessage && <p className="field-error" role="alert">{entityMessage}</p>}
          <div className="dialog-actions"><button className="button button-outline" type="button" onClick={() => entityDialog.current?.close()} disabled={entityBusy}>暂不确认</button><button className="button button-warm" type="submit" disabled={entityBusy}>{entityBusy ? "正在核验…" : entityUncertain ? "使用同一请求重试" : "确认并锁定主体"}</button></div>
        </form>
      </dialog>

      <dialog ref={removeDialog} className="app-dialog" onClose={() => { setRemoveTarget(null); setRemoveError(""); setRemoveUncertain(false); }}>
        <form className="dialog-form" noValidate onSubmit={submitRemoval}>
          <div className="dialog-topline"><span className="dialog-stamp stamp-warm">移</span><button className="dialog-close" type="button" aria-label="关闭" onClick={closeRemoveDialog} disabled={removeBusy}>×</button></div>
          <span className="section-kicker">材料管理</span>
          <h2>从本案移除资料</h2>
          <div className="caution-copy"><strong>{removeTarget?.file_name}</strong><span>移除后会撤销该资料的选择和读取授权，并使使用它的进行中审核失效。历史审核与审计记录保留；文件对象按存储清理策略回收。</span></div>
          <label className="field-label" htmlFor="remove-document-reason">移除原因</label>
          <textarea id="remove-document-reason" className="text-field text-area" value={removeReason} maxLength={500} onChange={(event) => setRemoveReason(event.target.value)} disabled={removeBusy || removeUncertain} aria-describedby="remove-reason-hint" />
          <p id="remove-reason-hint" className="field-hint">此原因会记入本案审计记录（最多 500 字）。</p>
          {removeUncertain && <p className="inline-message" role="note">移除结果待核实。请使用同一请求重试。</p>}
          {removeError && <p className="field-error" role="alert">{removeError}</p>}
          <div className="dialog-actions"><button className="button button-outline" type="button" onClick={closeRemoveDialog} disabled={removeBusy}>保留资料</button><button className="button button-warm" type="submit" disabled={removeBusy || (!removeUncertain && !removeReason.trim())}>{removeBusy ? "正在移除…" : removeUncertain ? "使用同一请求重试" : "确认移除"}</button></div>
        </form>
      </dialog>
    </div>
  );
}

function CaseWorkspace({
  caseRecord,
  documents,
  loading,
  error,
  headingRef,
  onBack,
  onRetry,
  onConfirmEntity,
  uploadRole,
  setUploadRole,
  uploadFile,
  setUploadFile,
  uploadBusy,
  uploadUncertain,
  uploadProgress,
  uploadMessage,
  onUpload,
  onRemove,
  removeBusy,
  removeNotice,
}: {
  caseRecord: CaseRecord | null;
  documents: CaseDocument[];
  loading: boolean;
  error: string;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onBack: () => void;
  onRetry: () => void;
  onConfirmEntity: () => void;
  uploadRole: CaseDocument["role"];
  setUploadRole: (role: CaseDocument["role"]) => void;
  uploadFile: File | null;
  setUploadFile: (file: File | null) => void;
  uploadBusy: boolean;
  uploadUncertain: boolean;
  uploadProgress: number;
  uploadMessage: string;
  onUpload: (event: FormEvent<HTMLFormElement>) => void;
  onRemove: (document: CaseDocument) => void;
  removeBusy: boolean;
  removeNotice: string;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const isConfirmed = Boolean(caseRecord?.legal_entity_confirmed_at);

  return (
    <>
      <div className="page-overline"><button className="crumb-link" type="button" onClick={onBack}>案件台账</button><span>/</span><span>案件详情</span></div>
      {loading ? <div className="state-card detail-loading" role="status" aria-live="polite"><span className="spinner" aria-hidden="true" />正在打开案卷…</div> : error ? (
        <div className="state-card state-error" role="alert"><span className="state-symbol" aria-hidden="true">!</span><div><strong>案卷暂不可用</strong><p>{error}</p></div><button className="button button-outline" type="button" onClick={onRetry}>重试</button></div>
      ) : caseRecord ? (
        <>
          <section className="detail-heading">
          <div className="detail-title-line"><button className="back-button" type="button" aria-label="返回案件台账" onClick={onBack}>←</button><div><span className="section-kicker">案件编号 / {caseRecord.case_id.slice(0, 8).toUpperCase()}</span><h1 ref={headingRef} tabIndex={-1}>{caseRecord.title}<span className="heading-period">。</span></h1></div></div>
            <span className={`status-chip ${isConfirmed ? "status-ready" : "status-pending"}`}><span className="status-dot" />{caseStatusLabel(caseRecord.status)}</span>
          </section>

          <section className={`entity-banner ${isConfirmed ? "entity-confirmed" : "entity-unconfirmed"}`} aria-labelledby="entity-status-title">
            <a className="button button-primary" href={`/cases/${encodeURIComponent(caseRecord.case_id)}`}>继续审核此主合同</a>
            <div className="entity-symbol" aria-hidden="true">{isConfirmed ? "✓" : "核"}</div>
            <div className="entity-banner-copy"><span className="section-kicker">签约主体</span><h2 id="entity-status-title">{isConfirmed ? "签约主体已确认" : "签约主体待人工确认"}</h2><p>{isConfirmed ? caseRecord.legal_entity_id : "上传主合同后，根据签约资料核对实际签约法人；确认前不能添加其他材料或授权审查。"}</p></div>
            {!isConfirmed && <button className="button button-warm" type="button" onClick={onConfirmEntity} disabled={documents.length === 0}>确认主体 <span aria-hidden="true">↗</span></button>}
          </section>

          <div className="detail-grid">
            <section className="detail-panel materials-panel" aria-labelledby="materials-title">
              <div className="panel-heading"><div><span className="section-kicker">材料目录</span><h2 id="materials-title">案卷材料 <span className="count-mark">{documents.length}</span></h2></div><button className="button button-quiet" type="button" onClick={onRetry}>刷新列表</button></div>
              {removeNotice && <p className="inline-message" role="status" aria-live="polite">{removeNotice}</p>}
              {documents.length ? <div className="document-list">
                {documents.map((document) => <article className="document-row" key={document.document_version_id}>
                  <span className="file-mark" aria-hidden="true">{document.media_type.includes("pdf") ? "PDF" : "DOC"}</span>
                  <div className="document-main"><strong title={document.file_name}>{document.file_name}</strong><div className="document-meta"><span>{roleName(document.role)}</span><span>{byteSize(document.size_bytes)}</span><span>版本 {document.case_version}</span></div></div>
                  <div className="document-states"><div className="parse-progress-block"><span className={`parse-state parse-${document.parse_status}`}><span className="status-dot" />{statusName(document.parse_status)}</span><ParseProgress status={document.parse_status} /></div><span className={`index-state index-${document.index_status}`}>索引 · {indexStatusName(document.index_status)}</span><span className={`auth-state auth-${document.authorization_status}`}>{authorizationName(document.authorization_status)}</span></div>
                  <button className="button button-warm document-remove" type="button" onClick={() => onRemove(document)} disabled={removeBusy || uploadBusy} aria-label={`从本案移除 ${document.file_name}`}>移除</button>
                </article>)}
              </div> : <div className="materials-empty"><span aria-hidden="true">＋</span><strong>尚未上传材料</strong><p>先放入待审主合同，主体确认与授权另行办理。</p></div>}
              <div className="upload-box">
                <div className="upload-box-title"><span className="section-kicker">加入案卷</span><h3>添加材料</h3></div>
                {!isConfirmed && <p className="upload-gate">主体确认前仅接受待审主合同。</p>}
                <form noValidate onSubmit={onUpload}>
                  <label className="field-label" htmlFor="document-role">材料用途</label>
                  <select id="document-role" className="text-field role-select" value={uploadRole} onChange={(event) => setUploadRole(event.target.value as CaseDocument["role"])} disabled={!isConfirmed || uploadUncertain}>
                    <option value="primary_contract">待审主合同</option>
                    {isConfirmed && <><option value="attachment">附件</option><option value="customer_contract">项目客户合同</option></>}
                  </select>
                  <button className={`file-picker ${uploadFile ? "has-file" : ""}`} type="button" onClick={() => fileInput.current?.click()} disabled={uploadBusy || uploadUncertain}>
                    <span className="picker-icon" aria-hidden="true">↑</span><span><strong>{uploadFile?.name ?? "选择 PDF 或 DOCX"}</strong><small>{uploadFile ? byteSize(uploadFile.size) : "单个文件最大 30 MiB"}</small></span><span className="picker-action">浏览</span>
                  </button>
                  <input ref={fileInput} className="visually-hidden" type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)} aria-label="选择 PDF 或 DOCX 文件" disabled={uploadBusy || uploadUncertain} />
                  {uploadBusy && <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={uploadProgress}><span style={{ width: `${uploadProgress}%` }} /></div>}
                  {uploadMessage && <p className="inline-message" role="status" aria-live="polite">{uploadMessage}</p>}
                  {uploadUncertain && <p className="inline-message" role="note">提交结果不确定。请先刷新材料目录，再使用同一文件与用途安全重试；请求键会保持不变。</p>}
                  <button className="button button-primary upload-submit" type="submit" disabled={!uploadFile || uploadBusy}>{uploadBusy ? `上传中 ${uploadProgress}%` : uploadUncertain ? "使用同一请求重试" : "上传到本案件"}</button>
                </form>
                <p className="privacy-note"><span aria-hidden="true">◇</span> 上传成功不代表审查授权。授权按材料、用途、字段范围和有效期单独记录。</p>
              </div>
            </section>

            <aside className="detail-aside">
              <section className="detail-panel facts-panel" aria-labelledby="facts-title"><span className="section-kicker">案卷记录</span><h2 id="facts-title">案卷信息</h2><dl className="facts-list"><div><dt>案件状态</dt><dd>{caseStatusLabel(caseRecord.status)}</dd></div><div><dt>创建时间</dt><dd>{formatDate(caseRecord.created_at)}</dd></div><div><dt>当前版本</dt><dd>V{caseRecord.version}</dd></div><div><dt>案件编号</dt><dd className="mono-value">{caseRecord.case_id}</dd></div></dl></section>
              <section className="guardrail-card"><span className="guardrail-icon" aria-hidden="true">!</span><div><strong>每一步都可追溯</strong><p>主体由工作人员确认；材料上传、读取授权与输入冻结是彼此独立的操作。</p></div></section>
              <p className="detail-caption">此案卷记录由服务器返回。未接入的资料源不会显示为审查结论。</p>
            </aside>
          </div>
        </>
      ) : null}
    </>
  );
}
