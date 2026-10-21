import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { RunResultsPage } from "./RunResultsPage";
import { blobSha256 } from "./blobSha256";

vi.mock("docx-preview", () => ({
  renderAsync: vi.fn(async (_file: Blob, body: HTMLElement) => {
    const page = body.ownerDocument.createElement("section");
    page.className = "docx";
    page.textContent = "合同原件预览内容";
    body.append(page);
  }),
}));

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
  vi.unstubAllGlobals();
});

function renderRoute(route: string) {
  return render(<ConfigProvider><MemoryRouter initialEntries={[route]}><App /></MemoryRouter></ConfigProvider>);
}


async function openReviewControls(kind: "permissions" | "snapshot") {
  fireEvent.click(await screen.findByRole("link", { name: "审核过程" }));
  fireEvent.click(await screen.findByText(kind === "permissions" ? "审核范围与读取授权" : "高级输入设置：用途预览与冻结"));
}

function renderRunResultsRoute(route: string) {
  return render(<ConfigProvider><MemoryRouter initialEntries={[route]}><Routes><Route path="/runs/:runId" element={<RunResultsPage />} /></Routes></MemoryRouter></ConfigProvider>);
}

function parsedPdfArtifact(sourceSha256 = "a".repeat(64)) {
  return {
    source_sha256: sourceSha256,
    index_source_sha256: sourceSha256,
    media_type: "application/pdf",
    parser_version: "test-parser",
    renderer_identity: null,
    pages: ["😀Payment due after acceptance."],
  };
}

describe("M0 prototype boundaries", () => {
  it("shows the source scope while keeping freeze unavailable", async () => {
    renderRoute("/cases/demo");
    expect(await screen.findByText("选择与预览来源")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /预览并冻结/ })).toBeDisabled();
    expect(screen.getByText(/尚未接入真实身份/)).toBeInTheDocument();
  });

  it("exposes a result's evidence backjump", async () => {
    renderRoute("/cases/demo/results");
    expect(await screen.findByText(/未运行模型/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /打开 第 6.2 条/ }));
    expect(await screen.findByText("P. 06")).toBeInTheDocument();
    expect(await screen.findByText("证据定位")).toBeInTheDocument();
  });
});

describe("M1 live case workflow", () => {
  it("keeps the tenant administrator correction queue separate from member case detail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("legal-entity-correction-cases")) return { ok: true, status: 200, json: async () => ({
        items: [{ case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", title: "受限案件标题", legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", version: 4, created_at: "2026-09-26T01:00:00Z" }],
        total: 1, page: 1, page_size: 20,
      }) } as Response;
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute("/entity-corrections");
    expect(await screen.findByText("受限案件标题")).toBeInTheDocument();
    expect(screen.getByText("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "纠正主体" }));
    expect(await screen.findByRole("heading", { name: "更正案件主体" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/documents"))).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("legal-entity-correction-context"))).toBe(true);
    expect(screen.queryByText("受限案件标题")).not.toBeInTheDocument();
  }, 15000);

  it("creates a case with an empty entity and navigates to primary-contract intake", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "华东设备服务合同审查",
      legal_entity_id: null,
      legal_entity_confirmed_at: null,
      status: "draft",
      version: 1,
      created_at: "2026-09-25T01:30:00Z",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const url = request.url;
      if (url.endsWith("/cases") && request.method === "POST") {
        expect(await request.clone().json()).toEqual({ title: caseRecord.title });
        expect(request.headers.get("Idempotency-Key")).toBeTruthy();
        return { ok: true, status: 201, json: async () => caseRecord } as Response;
      }
      if (url.includes("/documents")) return { ok: true, status: 200, json: async () => [] } as Response;
      if (url.endsWith("/cases") || url.includes("/cases?")) {
        return { ok: true, status: 200, json: async () => ({ items: [], total: 0, page: 1, page_size: 20 }) } as Response;
      }
      return { ok: true, status: 200, json: async () => caseRecord } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute("/cases");
    fireEvent.click(await screen.findByRole("button", { name: "创建第一个案件" }));
    fireEvent.change(await screen.findByLabelText("案件名称"), { target: { value: caseRecord.title } });
    fireEvent.click(screen.getByRole("button", { name: "创建案件并继续准备" }));

    expect(await screen.findByRole("heading", { name: "上传主合同" })).toBeInTheDocument();
    fireEvent.click(screen.getByText(/^确认签约主体 ·/));
    expect(await screen.findByRole("heading", { name: "先上传主合同，再确认签约主体" }, { timeout: 10000 })).toBeInTheDocument();
    expect(screen.getByText("主体确认前只接收待审主合同")).toBeInTheDocument();
    expect(screen.getByText("请先上传待审主合同")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认主体并开放逐份授权" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalled();
  }, 20000);

  it("loads member-visible cases and opens the real case detail route", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "draft",
      version: 1,
      created_at: "2026-09-25T01:30:00Z",
    };
    const documentRecord = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "采购合同.pdf",
      role: "primary_contract",
      media_type: "application/pdf",
      size_bytes: 1200,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      parser_version: "test-parser",
      warnings: [],
      case_version: 2,
      document_status: "staged",
      index_status: "queued",
      selection_id: null,
      selection_status: "not_selected",
      authorization_status: "not_authorized",
      amount_authorized: false,
      authorization_valid_until: null,
      purpose_authorizations: [],
      created_at: "2026-09-25T01:31:00Z",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/documents")
        ? [documentRecord]
        : url.endsWith("/cases") || url.includes("/cases?")
          ? { items: [caseRecord], total: 1, page: 1, page_size: 20 }
          : caseRecord;
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute("/cases");
    fireEvent.click(await screen.findByRole("link", { name: /供应服务合同复核/ }));

    expect(await screen.findByRole("heading", { name: "已上传材料" })).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "案件材料" })).getByText("采购合同.pdf")).toBeInTheDocument();
    await openReviewControls("permissions");
    expect(screen.getByText("未授权模型读取")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.objectContaining({
        url: `${window.location.origin}/api/v1/cases/${caseRecord.case_id}/documents`,
      }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  }, 30000);

  it("lets an authorized tenant entity administrator review and confirm correction impacts", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "管理员主体纠错验证",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      legal_entity_confirmed_at: "2026-09-25T01:30:00Z",
      can_correct_legal_entity: true,
      status: "ready",
      version: 7,
      created_at: "2026-09-25T01:00:00Z",
    };
    const corrected = {
      ...caseRecord,
      legal_entity_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      version: 8,
    };
    const correctionRequests: Request[] = [];
    let correctedOnServer = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      const url = String(input);
      if (url.endsWith("/legal-entity-corrections") && request.method === "POST") {
        correctionRequests.push(request.clone());
        correctedOnServer = true;
        return { ok: true, status: 200, json: async () => corrected } as Response;
      }
      if (url.endsWith("/legal-entity-history")) {
        return { ok: true, status: 200, json: async () => ({ items: [], next_cursor: null }) } as Response;
      }
      if (url.endsWith("/documents")) return { ok: true, status: 200, json: async () => [] } as Response;
      return { ok: true, status: 200, json: async () => correctedOnServer ? corrected : caseRecord } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute(`/cases/${caseRecord.case_id}`);
    fireEvent.click(await screen.findByRole("button", { name: "更正法人主体" }));
    fireEvent.change(screen.getByLabelText("更正后的法人主体 UUID"), {
      target: { value: corrected.legal_entity_id },
    });
    fireEvent.change(screen.getByLabelText("纠错依据（必填）"), {
      target: { value: "已核对签约主体登记资料" },
    });
    fireEvent.click(screen.getByRole("button", { name: "继续核对影响" }));

    expect(await screen.findByText("纠错会立即撤销本案当前审查准备状态")).toBeInTheDocument();
    expect(screen.getByText("新主体范围不会沿用旧材料授权")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认更正并撤销当前准备" }));

    await waitFor(() => expect(correctionRequests).toHaveLength(1));
    expect(correctionRequests[0].headers.get("Idempotency-Key")).toBeTruthy();
    expect(await correctionRequests[0].json()).toEqual({
      legal_entity_id: corrected.legal_entity_id,
      expected_case_version: caseRecord.version,
      correction_note: "已核对签约主体登记资料",
    });
    expect(await screen.findByText(/主体已更正/)).toBeInTheDocument();
  }, 30000);

  it("keeps case details available when only the document catalog is unavailable", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "draft",
      version: 1,
      created_at: "2026-09-25T01:30:00Z",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/documents")) {
        return { ok: false, status: 503, json: async () => ({ code: "service_unavailable" }) } as Response;
      }
      return { ok: true, status: 200, json: async () => caseRecord } as Response;
    }));

    renderRoute(`/cases/${caseRecord.case_id}`);

    expect(await screen.findByRole("heading", { name: caseRecord.title })).toBeInTheDocument();
    expect(screen.getByText("材料目录暂时不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新材料目录" })).toBeInTheDocument();
  }, 30000);

  it("uploads a selected file and reports that upload does not grant authorization", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "draft",
      version: 1,
      created_at: "2026-09-25T01:30:00Z",
    };
    const uploadedDocument = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "采购合同.pdf",
      role: "attachment",
      media_type: "application/pdf",
      size_bytes: 8,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      warnings: [],
      case_version: 2,
      document_status: "staged",
      authorization_status: "not_granted_by_upload",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/documents") ? [] : caseRecord;
      return { ok: true, status: 200, json: async () => body } as Response;
    }));
    const xhrRecord: { role?: FormDataEntryValue | null; key?: string } = {};

    class FakeXMLHttpRequest {
      upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
      status = 201;
      responseText = JSON.stringify(uploadedDocument);
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;

      open() {}
      setRequestHeader(name: string, value: string) {
        if (name === "Idempotency-Key") xhrRecord.key = value;
      }
      send(body: FormData) {
        xhrRecord.role = body.get("role");
        this.upload.onprogress?.({ lengthComputable: true, loaded: 8, total: 8 } as ProgressEvent);
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);

    renderRoute(`/cases/${caseRecord.case_id}`);
    const roleSelect = await screen.findByLabelText("材料用途");
    fireEvent.change(roleSelect, { target: { value: "attachment" } });
    fireEvent.change(screen.getByLabelText("选择 PDF 或 DOCX 文件"), {
      target: { files: [new File(["contract"], "采购合同.pdf", { type: "application/pdf" })] },
    });
    expect(await screen.findByText("采购合同.pdf")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /上传 \d+ 个文件/ }));

    expect(await screen.findByText("已上传 · 未授权")).toBeInTheDocument();
    expect(xhrRecord.role).toBe("attachment");
    expect(xhrRecord.key).toBeTruthy();
  }, 30000);

  it("uploads project-customer contracts into the current case only", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "项目客户合同接收",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "draft",
      version: 1,
      created_at: "2026-09-25T01:30:00Z",
    };
    const uploadedDocument = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "项目客户合同.pdf",
      role: "customer_contract",
      media_type: "application/pdf",
      size_bytes: 8,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      warnings: [],
      case_version: 2,
      document_status: "staged",
      authorization_status: "not_granted_by_upload",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return { ok: true, status: 200, json: async () => url.includes("/documents") ? [] : caseRecord } as Response;
    }));
    let uploadedRole: FormDataEntryValue | null = null;
    class FakeXMLHttpRequest {
      upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
      status = 201;
      responseText = JSON.stringify(uploadedDocument);
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      open() {}
      setRequestHeader() {}
      send(body: FormData) {
        uploadedRole = body.get("role");
        this.upload.onprogress?.({ lengthComputable: true, loaded: 8, total: 8 } as ProgressEvent);
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);

    renderRoute(`/cases/${caseRecord.case_id}`);
    fireEvent.change(await screen.findByLabelText("材料用途"), { target: { value: "customer_contract" } });
    expect(screen.getByText(/只上传到当前案件/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("选择 PDF 或 DOCX 文件"), {
      target: { files: [new File(["contract"], "项目客户合同.pdf", { type: "application/pdf" })] },
    });
    fireEvent.click(await screen.findByRole("button", { name: /上传 \d+ 个文件/ }));

    expect(await screen.findByText("已上传 · 未授权")).toBeInTheDocument();
    expect(uploadedRole).toBe("customer_contract");
  }, 30000);

  it("requires an explicit same-project attestation before authorizing an upstream upload", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "上下游授权确认",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "draft",
      version: 2,
      created_at: "2026-09-25T01:30:00Z",
    };
    const documentRecord = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "项目客户合同.pdf",
      role: "customer_contract",
      media_type: "application/pdf",
      size_bytes: 1200,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      parse_error_code: null,
      parser_version: "test-parser",
      warnings: [],
      case_version: 2,
      document_status: "staged",
      index_status: "queued",
      selection_id: null,
      selection_status: "not_selected",
      authorization_status: "not_authorized",
      amount_authorized: false,
      authorization_valid_until: null,
      purpose_authorizations: [],
      created_at: "2026-09-25T01:31:00Z",
    };
    let currentDocument: Record<string, unknown> = documentRecord;
    let grantBody: Record<string, unknown> | null = null;
    const selectionId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const request = input instanceof Request ? input : null;
      const method = init?.method ?? request?.method;
      if (url.endsWith("/document-selections") && method === "POST") {
        const submitted = request
          ? await request.clone().json() as Record<string, unknown>
          : JSON.parse(String(init?.body)) as Record<string, unknown>;
        grantBody = submitted;
        currentDocument = {
          ...documentRecord,
          case_version: 3,
          document_status: "active",
          selection_id: selectionId,
          selection_status: "selected",
          purpose_authorizations: [{
            purpose: "upstream_comparison",
            allow_text: true,
            allow_amount: false,
            valid_until: String(submitted.valid_until),
            status: "authorized",
          }],
        };
        return { ok: true, status: 201, json: async () => ({
          selection_id: selectionId,
          grant_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
          document_version_id: documentRecord.document_version_id,
          case_id: caseRecord.case_id,
          relation: "upstream",
          purpose: "upstream_comparison",
          allow_text: true,
          allow_amount: false,
          valid_until: String(submitted.valid_until),
          case_version: 3,
        }) } as Response;
      }
      if (url.includes("document-access-events")) {
        return { ok: true, status: 200, json: async () => ({ items: [], next_cursor: null }) } as Response;
      }
      if (url.includes("/documents")) {
        return { ok: true, status: 200, json: async () => [currentDocument] } as Response;
      }
      return { ok: true, status: 200, json: async () => ({ ...caseRecord, version: 3 }) } as Response;
    }));

    renderRoute(`/cases/${caseRecord.case_id}`);
    await openReviewControls("permissions");
    fireEvent.click(await screen.findByRole("button", { name: "关联上游并授权" }));
    expect(screen.getByText(/不会查询项目台账、合同系统或其他案件/)).toBeInTheDocument();
    const confirmButton = screen.getByRole("button", { name: "确认并授权" });
    expect(confirmButton).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /我已核对，该客户合同属于本案同一我方法人项目/ }));
    fireEvent.change(screen.getByLabelText("授权有效期"), { target: { value: "1" } });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);

    expect(await screen.findByText(/上下游比对 · 有效/)).toBeInTheDocument();
    expect(grantBody).toMatchObject({
      document_version_id: documentRecord.document_version_id,
      relation: "upstream",
      purpose: "upstream_comparison",
      confirm_project_relation: true,
      allow_amount: false,
    });
  }, 45000);

  it("requires explicit expiry for authorization and a reason for revocation", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "draft",
      version: 2,
      created_at: "2026-09-25T01:30:00Z",
    };
    const documentRecord = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "采购合同.pdf",
      role: "primary_contract",
      media_type: "application/pdf",
      size_bytes: 1200,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      parse_error_code: null,
      parser_version: "test-parser",
      warnings: [],
      case_version: 2,
      document_status: "staged",
      index_status: "queued",
      selection_id: null,
      selection_status: "not_selected",
      authorization_status: "not_authorized",
      amount_authorized: false,
      authorization_valid_until: null,
      created_at: "2026-09-25T01:31:00Z",
    };
    let currentDocuments: Array<Record<string, unknown>> = [documentRecord];
    let currentCaseVersion = 2;
    const mutation: {
      grantBodies: Record<string, unknown>[];
      revokeBody: Record<string, unknown> | null;
      revokeKey: string | null;
    } = { grantBodies: [], revokeBody: null, revokeKey: null };
    const selectionId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const request = input instanceof Request ? input : null;
      const method = init?.method ?? request?.method;
      if (url.endsWith("/document-selections") && method === "POST") {
        const grantBody = request
          ? await request.clone().json() as Record<string, unknown>
          : JSON.parse(String(init?.body)) as Record<string, unknown>;
        mutation.grantBodies.push(grantBody);
        currentCaseVersion += 1;
        const currentGrants = (currentDocuments[0].purpose_authorizations as Array<Record<string, unknown>>) ?? [];
        const newGrant = {
          purpose: grantBody.purpose,
          allow_text: true,
          allow_amount: grantBody.allow_amount,
          valid_until: grantBody.valid_until,
          status: "authorized",
        };
        currentDocuments = [{
          ...currentDocuments[0],
          case_version: currentCaseVersion,
          document_status: "active",
          selection_id: selectionId,
          selection_status: "selected",
          authorization_status: "authorized",
          amount_authorized: grantBody.allow_amount,
          authorization_valid_until: String(grantBody.valid_until),
          purpose_authorizations: [...currentGrants.filter((grant) => grant.purpose !== grantBody.purpose), newGrant],
        }];
        return {
          ok: true,
          status: 201,
          json: async () => ({
            selection_id: selectionId,
            grant_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            document_version_id: documentRecord.document_version_id,
            case_id: caseRecord.case_id,
            relation: String(grantBody.relation),
            purpose: String(grantBody.purpose),
            allow_text: true,
            allow_amount: Boolean(grantBody.allow_amount),
            valid_until: String(grantBody.valid_until),
            case_version: currentCaseVersion,
          }),
        } as Response;
      }
      if (url.endsWith(`/document-selections/${selectionId}/revoke`)) {
        mutation.revokeBody = request
          ? await request.clone().json() as Record<string, unknown>
          : JSON.parse(String(init?.body)) as Record<string, unknown>;
        mutation.revokeKey = new Headers(init?.headers ?? request?.headers).get("Idempotency-Key");
        currentCaseVersion += 1;
        currentDocuments = [{
          ...currentDocuments[0],
          case_version: currentCaseVersion,
          selection_status: "revoked",
          authorization_status: "not_authorized",
          purpose_authorizations: [],
        }];
        return {
          ok: true,
          status: 200,
          json: async () => ({
            selection_id: selectionId,
            document_version_id: documentRecord.document_version_id,
            case_id: caseRecord.case_id,
            case_version: currentCaseVersion,
            revoked_at: "2026-09-25T02:00:00Z",
            revoked_grant_count: 2,
            invalidated_run_count: 0,
          }),
        } as Response;
      }
      const body = url.endsWith("/documents")
        ? currentDocuments
        : { ...caseRecord, version: currentCaseVersion };
      return { ok: true, status: 200, json: async () => body } as Response;
    }));

    renderRoute(`/cases/${caseRecord.case_id}`);
    await openReviewControls("permissions");
    fireEvent.click(await screen.findByRole("button", { name: "选择并授权" }));
    expect(screen.getByRole("checkbox", { name: /允许模型读取本材料中的金额字段/ })).not.toBeChecked();
    fireEvent.change(screen.getByLabelText("授权有效期"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "确认并授权" }));

    expect(await screen.findByText(/合同审查 · 有效/)).toBeInTheDocument();
    expect(mutation.grantBodies[0]).toMatchObject({
      document_version_id: documentRecord.document_version_id,
      relation: "main",
      purpose: "contract_review",
      confirm_project_relation: false,
      allow_amount: false,
    });
    expect(Date.parse(String(mutation.grantBodies[0].valid_until)) - Date.now()).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "授权上下游比对" }));
    fireEvent.change(screen.getByLabelText("授权有效期"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "确认并授权" }));
    expect(await screen.findByText(/上下游比对 · 有效/)).toBeInTheDocument();
    expect(mutation.grantBodies[1]).toMatchObject({
      document_version_id: documentRecord.document_version_id,
      relation: "main",
      purpose: "upstream_comparison",
      confirm_project_relation: false,
      allow_amount: false,
    });

    fireEvent.click(screen.getByRole("button", { name: "撤销全部授权" }));
    const revokeDialog = screen.getByRole("dialog");
    const revokeButton = within(revokeDialog).getByRole("button", { name: "撤销全部授权" });
    expect(revokeButton).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "撤权原因" }), {
      target: { value: "材料用途已变更" },
    });
    fireEvent.click(revokeButton);

    expect(await screen.findByText(/撤销了 2 项授权/)).toBeInTheDocument();
    expect(mutation.revokeBody).toEqual({ expected_case_version: 4, reason: "材料用途已变更" });
    expect(mutation.revokeKey).toMatch(/^revoke-/);
    expect(screen.getByText("选择已撤销")).toBeInTheDocument();
  }, 60000);

  it("previews selected case files, confirms a frozen snapshot, and discloses the model boundary", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      version: 4,
      created_at: "2026-09-25T01:30:00Z",
    };
    const documentRecord = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "采购合同.pdf",
      role: "primary_contract",
      media_type: "application/pdf",
      size_bytes: 1200,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      parse_error_code: null,
      parser_version: "test-parser",
      warnings: [],
      case_version: 4,
      document_status: "active",
      index_status: "ready",
      selection_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      selection_status: "selected",
      authorization_status: "authorized",
      amount_authorized: true,
      authorization_valid_until: "2026-09-25T05:30:00Z",
      created_at: "2026-09-25T01:31:00Z",
    };
    const preview = {
      preview_id: "11111111-1111-4111-8111-111111111111",
      case_id: caseRecord.case_id,
      purpose: "contract_review",
      case_version: 4,
      manifest_sha256: "b".repeat(64),
      items: [{
        selection_id: documentRecord.selection_id,
        relation: "main",
        status: "ready",
        document_version_id: documentRecord.document_version_id,
        source_sha256: documentRecord.content_hash,
      }],
    };
    const frozen = {
      input_snapshot_id: "22222222-2222-4222-8222-222222222222",
      case_id: caseRecord.case_id,
      purpose: "contract_review",
      case_version: 5,
      manifest_sha256: preview.manifest_sha256,
      document_version_ids: [documentRecord.document_version_id],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/source-previews")) {
        return { ok: true, status: 201, json: async () => preview } as Response;
      }
      if (url.endsWith("/input-snapshots")) {
        const request = input instanceof Request ? input : null;
        const body = request ? await request.clone().json() : JSON.parse(String(init?.body));
        expect(body).toMatchObject({
          preview_id: preview.preview_id,
          expected_case_version: 4,
          manifest_sha256: preview.manifest_sha256,
        });
        return { ok: true, status: 200, json: async () => frozen } as Response;
      }
      const body = url.endsWith("/documents") ? [documentRecord] : caseRecord;
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute(`/cases/${caseRecord.case_id}`);
    await openReviewControls("snapshot");
    fireEvent.click(await screen.findByRole("button", { name: /生成授权预览/ }));
    expect(await screen.findByText("可冻结")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "本次来源预览" })).getByText("采购合同.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认冻结 1 份材料" }));
    expect(await screen.findByRole("dialog", { name: "冻结本次合同审查输入？" })).toBeInTheDocument();
    expect(screen.getByText(/真实财务\/合同系统尚未接入/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认冻结输入" }));

    expect(await screen.findByText("快照已保存")).toBeInTheDocument();
    expect(screen.getByText(/已冻结 · 不可变/)).toBeInTheDocument();
    expect(screen.getByText(/不会启动模型审查或上下游比对/)).toBeInTheDocument();
  }, 45000);

  it("keeps upstream input unfrozen until both same-case contract sides are ready", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      version: 4,
      created_at: "2026-09-25T01:30:00Z",
    };
    const main = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "采购合同.pdf",
      role: "primary_contract",
      media_type: "application/pdf",
      size_bytes: 1200,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      parse_error_code: null,
      parser_version: "test-parser",
      warnings: [],
      case_version: 4,
      document_status: "active",
      index_status: "ready",
      selection_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      selection_status: "selected",
      authorization_status: "authorized",
      amount_authorized: true,
      authorization_valid_until: "2026-09-25T05:30:00Z",
      created_at: "2026-09-25T01:31:00Z",
    };
    const preview = {
      preview_id: "11111111-1111-4111-8111-111111111111",
      case_id: caseRecord.case_id,
      purpose: "upstream_comparison",
      case_version: 4,
      manifest_sha256: "b".repeat(64),
      items: [{
        selection_id: main.selection_id,
        relation: "main",
        status: "ready",
        document_version_id: main.document_version_id,
        source_sha256: main.content_hash,
      }],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/source-previews")) {
        return { ok: true, status: 201, json: async () => preview } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => url.endsWith("/documents") ? [main] : caseRecord,
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute(`/cases/${caseRecord.case_id}`);
    expect(await screen.findByRole("heading", { name: caseRecord.title })).toBeInTheDocument();
    await openReviewControls("snapshot");
    fireEvent.click(screen.getByRole("radio", { name: "上下游比对准备" }));
    fireEvent.click(await screen.findByRole("button", { name: /生成授权预览/ }));

    expect(await screen.findByText("上下游比对材料尚未成对")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认冻结 1 份材料" })).toBeDisabled();
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/input-snapshots"))).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/runs"))).toBe(false);
  }, 45000);

  it("freezes a purpose-bound upstream pair without starting a comparison run", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      version: 4,
      created_at: "2026-09-25T01:30:00Z",
    };
    const main = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "采购合同.pdf",
      role: "primary_contract",
      media_type: "application/pdf",
      size_bytes: 1200,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      parse_error_code: null,
      parser_version: "test-parser",
      warnings: [],
      case_version: 4,
      document_status: "active",
      index_status: "ready",
      selection_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      selection_status: "selected",
      authorization_status: "authorized",
      amount_authorized: true,
      authorization_valid_until: "2026-09-25T05:30:00Z",
      created_at: "2026-09-25T01:31:00Z",
    };
    const customer = {
      ...main,
      document_version_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      file_name: "项目客户合同.pdf",
      role: "customer_contract",
      selection_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
    };
    const preview = {
      preview_id: "11111111-1111-4111-8111-111111111111",
      case_id: caseRecord.case_id,
      purpose: "upstream_comparison",
      case_version: 4,
      manifest_sha256: "b".repeat(64),
      items: [
        {
          selection_id: main.selection_id,
          relation: "main",
          status: "ready",
          document_version_id: main.document_version_id,
          source_sha256: main.content_hash,
        },
        {
          selection_id: customer.selection_id,
          relation: "upstream",
          status: "ready",
          document_version_id: customer.document_version_id,
          source_sha256: customer.content_hash,
        },
      ],
    };
    const frozen = {
      input_snapshot_id: "22222222-2222-4222-8222-222222222222",
      case_id: caseRecord.case_id,
      purpose: "upstream_comparison",
      case_version: 5,
      manifest_sha256: preview.manifest_sha256,
      document_version_ids: [main.document_version_id, customer.document_version_id],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const url = request.url;
      if (url.endsWith("/source-previews")) {
        expect(await request.clone().json()).toEqual({
          purpose: "upstream_comparison",
          task_requires_amount: true,
        });
        return { ok: true, status: 201, json: async () => preview } as Response;
      }
      if (url.endsWith("/input-snapshots")) {
        expect(await request.clone().json()).toMatchObject({
          purpose: "upstream_comparison",
          preview_id: preview.preview_id,
          expected_case_version: 4,
        });
        return { ok: true, status: 200, json: async () => frozen } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => url.endsWith("/documents") ? [main, customer] : caseRecord,
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute(`/cases/${caseRecord.case_id}`);
    expect(await screen.findByRole("heading", { name: caseRecord.title })).toBeInTheDocument();
    await openReviewControls("snapshot");
    fireEvent.click(screen.getByRole("radio", { name: "上下游比对准备" }));
    fireEvent.click(await screen.findByRole("button", { name: /生成授权预览/ }));
    expect(within(await screen.findByRole("list", { name: "本次来源预览" })).getByText("项目客户合同.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认冻结 2 份材料" }));
    expect(await screen.findByRole("dialog", { name: "冻结本次上下游比对输入？" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认冻结输入" }));

    expect(await screen.findByText("快照已保存")).toBeInTheDocument();
    expect(screen.getByText(/上下游比对（含金额字段授权检查）/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/runs"))).toBe(false);
  }, 45000);

  it("does not permit freezing when the server reports restricted amount scope", async () => {
    const caseRecord = {
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      title: "供应服务合同复核",
      legal_entity_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      version: 4,
      created_at: "2026-09-25T01:30:00Z",
    };
    const documentRecord = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: caseRecord.case_id,
      file_name: "采购合同.pdf",
      role: "primary_contract",
      media_type: "application/pdf",
      size_bytes: 1200,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      parse_error_code: null,
      parser_version: "test-parser",
      warnings: [],
      case_version: 4,
      document_status: "active",
      index_status: "ready",
      selection_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      selection_status: "selected",
      authorization_status: "authorized",
      amount_authorized: false,
      authorization_valid_until: "2026-09-25T05:30:00Z",
      created_at: "2026-09-25T01:31:00Z",
    };
    const preview = {
      preview_id: "11111111-1111-4111-8111-111111111111",
      case_id: caseRecord.case_id,
      purpose: "contract_review",
      case_version: 4,
      manifest_sha256: "b".repeat(64),
      items: [{
        selection_id: documentRecord.selection_id,
        relation: "main",
        status: "restricted_amount",
        document_version_id: null,
        source_sha256: null,
      }],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/source-previews")) {
        return { ok: true, status: 201, json: async () => preview } as Response;
      }
      const body = url.endsWith("/documents") ? [documentRecord] : caseRecord;
      return { ok: true, status: 200, json: async () => body } as Response;
    }));

    renderRoute(`/cases/${caseRecord.case_id}`);
    await openReviewControls("snapshot");
    fireEvent.click(await screen.findByRole("button", { name: /生成授权预览/ }));

    expect(await screen.findByText("金额权限或审查范围不足")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认冻结 0 份材料" })).toBeDisabled();
    expect(screen.getByText("当前预览不能冻结")).toBeInTheDocument();
  }, 45000);
});

describe("M1 run results", () => {
  const runId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const documentId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const quoteHash = "7048f38b33f135df36b3d9d382f167d45cfdda857b616899c9d8c9acd2751f64";
  const run = {
    run_id: runId,
    case_id: caseId,
    input_snapshot_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    status: "completed",
    version: 2,
    created_at: "2026-09-25T01:30:00Z",
  };
  const results = {
    run,
    results: [{
      result_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
      task_result_sha256: "e".repeat(64),
      rule_id: "legal.payment_conditions",
      rule_version: "1.0.0",
      judgment: "undetermined",
      severity: "undetermined",
      summary: "付款条款候选已定位，付款风险判断尚未执行。",
      evidence: [{
        evidence_id: "11111111-1111-4111-8111-111111111111",
        kind: "document_span",
        support: "context",
        source_version: "a".repeat(64),
        document_version_id: documentId,
        page: 1,
        start_offset: 1,
        end_offset: 8,
        quote: "Payment",
        text_hash: quoteHash,
        source_record_ref: null,
      }],
      slot_candidates: [
        {
          slot_id: "payment_clause_present",
          state: "filled",
          value: true,
          evidence_ids: ["11111111-1111-4111-8111-111111111111"],
        },
        {
          slot_id: "payment_schedule",
          state: "filled",
          value: [{ trigger: "验收合格", amount_type: "percentage", percentage: "80" }],
          evidence_ids: ["11111111-1111-4111-8111-111111111111"],
        },
        {
          slot_id: "payment_triggers",
          state: "filled",
          value: [{ stage: "尾款", conditions: ["验收合格"], acceptance_required: true }],
          evidence_ids: ["11111111-1111-4111-8111-111111111111"],
        },
        { slot_id: "prepayment_ratio", state: "human_review", value: null, evidence_ids: [] },
        { slot_id: "payment_risk_judgment", state: "human_review", value: null, evidence_ids: [] },
      ],
      limitations: ["候选内容需要人工核验。"],
      created_at: "2026-09-25T01:31:00Z",
    }],
  };

  it("downloads a non-final report from a fresh authorized result read", async () => {
    let exported: Blob | undefined;
    const createObjectUrl = vi.fn((blob: Blob) => {
      exported = blob;
      return "blob:authorized-run-report";
    });
    const revokeObjectUrl = vi.fn();
    const URLStub = class extends URL {};
    Object.defineProperty(URLStub, "createObjectURL", { value: createObjectUrl });
    Object.defineProperty(URLStub, "revokeObjectURL", { value: revokeObjectUrl });
    vi.stubGlobal("URL", URLStub);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith(`/runs/${runId}`) ? run
        : url.endsWith(`/runs/${runId}/results`) ? results
          : parsedPdfArtifact();
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRunResultsRoute(`/runs/${runId}`);
    fireEvent.click(await screen.findByRole("button", { name: "下载审核摘要" }));

    await waitFor(() => expect(exported).toBeInstanceOf(Blob));
    expect(exported?.type).toBe("text/markdown;charset=utf-8");
    const report = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(exported as Blob);
    });
    expect(report).toContain("不是正式法律意见或签约结论");
    expect(report).toContain("审核范围与数据提示");
    expect(report).toContain("没有问题发现不代表合同无风险");
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith(`/runs/${runId}/results`)))
      .toHaveLength(2);
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    await new Promise((resolve) => window.setTimeout(resolve, 1100));
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:authorized-run-report");
  }, 15000);

  it("does not create a report when the fresh authorization read is denied", async () => {
    const createObjectUrl = vi.fn(() => "blob:must-not-exist");
    const URLStub = class extends URL {};
    Object.defineProperty(URLStub, "createObjectURL", { value: createObjectUrl });
    vi.stubGlobal("URL", URLStub);
    let resultReads = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/runs/${runId}`)) {
        return { ok: true, status: 200, json: async () => run } as Response;
      }
      if (url.endsWith(`/runs/${runId}/results`)) {
        resultReads += 1;
        if (resultReads === 2) {
          return {
            ok: false,
            status: 404,
            json: async () => ({ code: "run_not_available" }),
          } as Response;
        }
        return { ok: true, status: 200, json: async () => results } as Response;
      }
      return { ok: true, status: 200, json: async () => parsedPdfArtifact() } as Response;
    }));

    renderRunResultsRoute(`/runs/${runId}`);
    fireEvent.click(await screen.findByRole("button", { name: "下载审核摘要" }));

    expect(await screen.findByText("当前身份无法读取这份运行或其来源，未生成摘要文件。"))
      .toBeInTheDocument();
    expect(resultReads).toBe(2);
    expect(createObjectUrl).not.toHaveBeenCalled();
  }, 15000);

  it("loads authorized results and verifies a Unicode-safe source excerpt before display", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith(`/runs/${runId}`) ? run
        : url.endsWith(`/runs/${runId}/results`) ? results
          : parsedPdfArtifact();
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRunResultsRoute(`/runs/${runId}`);
    expect(await screen.findByRole("heading", { name: "待人工核验" })).toBeInTheDocument();
    expect(screen.getByText("这是付款条款事实候选，不是完整审查结论")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /核对解析引用/ }));

    expect(await screen.findByText("已复核冻结来源版本、页码、字符偏移及引用文本哈希。此校验不代表法律判断。"))
      .toBeInTheDocument();
    expect(screen.getAllByText("Payment").some((element) => element.tagName === "MARK")).toBe(true);
    expect(fetchMock.mock.calls.some(([input]) => {
      return input instanceof Request &&
        input.url.endsWith(`/api/v1/cases/${caseId}/documents/${documentId}/content?kind=parsed`) &&
        input.cache === "no-store";
    })).toBe(true);
  });

  it("shows structured payment candidates as unverified facts with their bound evidence", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith(`/runs/${runId}`) ? run
        : url.endsWith(`/runs/${runId}/results`) ? results
          : parsedPdfArtifact();
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRunResultsRoute(`/runs/${runId}`);
    expect(await screen.findByText("付款条款事实候选")).toBeInTheDocument();
    expect(screen.getByText("均待人工核验")).toBeInTheDocument();
    expect(screen.getAllByText("验收合格")).toHaveLength(2);
    expect(screen.getByText("付款比例：80%")).toBeInTheDocument();
    expect(screen.getByText("当前处理不会形成付款风险判断。")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "核对付款字段依据（第 1 页）" })[0]);
    expect(await screen.findByText("已复核冻结来源版本、页码、字符偏移及引用文本哈希。此校验不代表法律判断。"))
      .toBeInTheDocument();
    expect(screen.getAllByText("Payment").some((element) => element.tagName === "MARK")).toBe(true);
  });

  it("shows price basis, every source citation, and safe manual next steps", async () => {
    const priceResult = {
      ...structuredClone(results.results[0]),
      result_id: "22222222-2222-4222-8222-222222222222",
      rule_id: "legal.price.definition",
      summary: "价款候选需要人工核对。",
      evidence: [
        ...structuredClone(results.results[0].evidence),
        {
          evidence_id: "33333333-3333-4333-8333-333333333333",
          kind: "document_span",
          support: "direct",
          source_version: "b".repeat(64),
          document_version_id: documentId,
          page: 2,
          start_offset: 0,
          end_offset: 20,
          quote: "Attachment price CNY 1200",
          text_hash: "c".repeat(64),
          source_record_ref: null,
        },
      ],
      slot_candidates: [
        {
          slot_id: "price_basis",
          state: "human_review",
          value: {
            tax_inclusive_total: "1000",
            tax_exclusive_total: "900",
            tax_amount: "100",
            tax_rate: null,
            currency: "CNY",
          },
          evidence_ids: ["11111111-1111-4111-8111-111111111111"],
        },
        {
          slot_id: "price_sources",
          state: "conflicted",
          value: [
            { source_type: "contract_body", amount: "1000", currency: "CNY", priority: "primary" },
            { source_type: "attachment", amount: "1200", currency: "CNY", priority: "secondary" },
          ],
          evidence_ids: [
            "11111111-1111-4111-8111-111111111111",
            "33333333-3333-4333-8333-333333333333",
          ],
        },
        {
          slot_id: "payment_basis",
          state: "human_review",
          value: [{ stage: "验收", denominator: "unspecified", percentage: "100", fixed_amount: null }],
          evidence_ids: ["11111111-1111-4111-8111-111111111111"],
        },
      ],
      limitations: ["价款来源不一致，需人工核对。"],
    };
    const openPriceTask = {
      task_id: "44444444-4444-4444-8444-444444444444",
      case_id: caseId,
      run_id: runId,
      type: "price_review",
      status: "open",
      title: "核对合同价款",
      reason: "price_basis_requires_review",
      required_role: "reviewer",
      decision: null,
      version: 1,
      created_at: "2026-09-29T00:00:00Z",
    };
    const completedPriceTask = {
      ...openPriceTask,
      status: "completed",
      decision: "request_material",
      version: 2,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : null;
      const url = request?.url ?? String(input);
      const body = url.includes("/human-tasks?") ? { items: [openPriceTask], next_cursor: null }
        : url.endsWith("/decisions") ? completedPriceTask
          : url.endsWith(`/runs/${runId}`) ? run
        : url.endsWith(`/runs/${runId}/results`) ? { ...results, results: [priceResult] }
          : parsedPdfArtifact();
      if ((init?.method ?? request?.method) === "POST") {
        const decisionBody = init?.body
          ? JSON.parse(String(init.body))
          : await request!.clone().json();
        expect(decisionBody).toMatchObject({
          version: 1,
          decision: "request_material",
          reason: "请提供签署版价款附件。",
        });
        expect(new Headers(init?.headers ?? request?.headers).get("Idempotency-Key")).toMatch(/[0-9a-f-]{36}/i);
      }
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRunResultsRoute(`/runs/${runId}`);
    expect(await screen.findByRole("region", { name: "合同价款口径" })).toBeInTheDocument();
    expect(screen.getAllByText("1000 CNY").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("900 CNY")).toBeInTheDocument();
    expect(screen.getByText("附件")).toBeInTheDocument();
    expect(screen.getByText("1200 CNY")).toBeInTheDocument();
    expect(screen.getByText("分母：未明确")).toBeInTheDocument();
    expect(screen.getByText("Attachment price CNY 1200")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回案件材料与复核记录" })).toHaveAttribute(
      "href",
      `/cases/${caseId}?view=background`,
    );
    expect(screen.getByText(/本页不会确认价款金额或代替人工批准/)).toBeInTheDocument();
    expect(await screen.findByText("待处理人工复核任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "请求补充材料" }));
    fireEvent.change(screen.getByRole("textbox", { name: "复核记录理由" }), {
      target: { value: "请提供签署版价款附件。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存复核记录" }));
    expect(await screen.findByText("已记录补充材料请求")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input, init]) =>
      (init?.method ?? (input instanceof Request ? input.method : undefined)) === "POST",
    )).toBe(true);
  });

  it("lets a reviewer confirm a cited fact into an immutable snapshot", async () => {
    const evidenceId = "11111111-1111-4111-8111-111111111111";
    const confirmableResults = {
      ...structuredClone(results),
      can_confirm_facts: true,
      results: [{
        ...structuredClone(results.results[0]),
        fact_candidates: [{
          predicate: "contract.contract_type",
          state: "candidate",
          value: "service",
          evidence_ids: [evidenceId],
        }],
      }],
    };
    const factSnapshot = {
      fact_set_snapshot_id: "22222222-2222-4222-8222-222222222222",
      case_id: caseId,
      input_snapshot_id: run.input_snapshot_id,
      source_run_id: runId,
      version: 1,
      facts: { "contract.contract_type": { value: "service" } },
      content_hash: "b".repeat(64),
      created_by: "33333333-3333-4333-8333-333333333333",
      reason: "已核对合同类型。",
      created_at: "2026-09-25T01:32:00Z",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init);
      if (request.url.endsWith(`/runs/${runId}`)) {
        return { ok: true, status: 200, json: async () => run } as Response;
      }
      if (request.url.endsWith(`/runs/${runId}/results`)) {
        return { ok: true, status: 200, json: async () => confirmableResults } as Response;
      }
      if (request.url.endsWith(`/runs/${runId}/fact-set-snapshots`) && request.method === "POST") {
        expect(request.headers.get("Idempotency-Key")).toBeTruthy();
        expect(await request.clone().json()).toEqual({
          confirmations: [{ predicate: "contract.contract_type", value: "service", evidence_ids: [evidenceId] }],
          reason: "合同首页明确写明为服务合同。",
        });
        return { ok: true, status: 201, json: async () => factSnapshot } as Response;
      }
      return { ok: true, status: 200, json: async () => parsedPdfArtifact() } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRunResultsRoute(`/runs/${runId}`);
    fireEvent.click(await screen.findByRole("button", { name: "确认此项事实" }));
    expect(await screen.findByText("确认合同类型")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("确认后的值"), { target: { value: "service" } });
    fireEvent.change(screen.getByLabelText("确认理由"), {
      target: { value: "合同首页明确写明为服务合同。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认并保存快照" }));

    expect(await screen.findByText("已保存事实集快照 v1")).toBeInTheDocument();
    expect(screen.getByText("合同类型：service")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "按确认事实重新运行" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/fact-set-snapshots"))).toBe(true);
  }, 15000);

  it("does not infer a missing clause when an older result has no slot candidates", async () => {
    const legacyResults = structuredClone(results);
    Reflect.deleteProperty(legacyResults.results[0], "slot_candidates");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith(`/runs/${runId}`) ? run : legacyResults;
      return { ok: true, status: 200, json: async () => body } as Response;
    }));

    renderRunResultsRoute(`/runs/${runId}`);
    expect(await screen.findByText("付款条款候选已定位，付款风险判断尚未执行。"))
      .toBeInTheDocument();
    expect(screen.queryByText("付款条款事实候选")).not.toBeInTheDocument();
    expect(screen.getByText("Payment")).toBeInTheDocument();
  });

  it("opens the authorized PDF original at the cited page and revokes its temporary URL", async () => {
    const pdf = new Blob(["%PDF-1.7"], { type: "application/pdf" });
    const createObjectUrl = vi.fn(() => "blob:verified-pdf");
    const revokeObjectUrl = vi.fn();
    const URLStub = class extends URL {};
    Object.defineProperty(URLStub, "createObjectURL", { value: createObjectUrl });
    Object.defineProperty(URLStub, "revokeObjectURL", { value: revokeObjectUrl });
    vi.stubGlobal("URL", URLStub);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/runs/${runId}`)) return { ok: true, status: 200, json: async () => run } as Response;
      if (url.endsWith(`/runs/${runId}/results`)) return { ok: true, status: 200, json: async () => results } as Response;
      if (url.endsWith("kind=original")) return {
        ok: true,
        status: 200,
        headers: new Headers({ "Content-Type": "application/pdf", "Content-Length": String(pdf.size) }),
        blob: async () => pdf,
      } as Response;
      return { ok: true, status: 200, json: async () => parsedPdfArtifact() } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRunResultsRoute(`/runs/${runId}`);
    fireEvent.click(await screen.findByRole("button", { name: /核对解析引用/ }));
    expect(await screen.findByText("已复核冻结来源版本、页码、字符偏移及引用文本哈希。此校验不代表法律判断。"))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 PDF 原件预览" }));

    const frame = await screen.findByTitle("PDF 原件，第 1 页");
    expect(frame).toHaveAttribute("src", "blob:verified-pdf#page=1&toolbar=1&navpanes=0");
    expect(await screen.findByText("原件已定位到第 1 页；PDF 内未绘制引用高亮，请核对上下文。"))
      .toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/cases/${caseId}/documents/${documentId}/content?kind=original`,
      expect.objectContaining({ cache: "no-store" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭页码视图" }));
    await waitFor(() => expect(screen.queryByTitle("PDF 原件，第 1 页")).not.toBeInTheDocument());
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:verified-pdf");
  }, 30000);

  it("opens the frozen canonical PDF for an authorized DOCX citation", async () => {
    const docx = new Blob(["authorized-docx"], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    const sourceHash = await blobSha256(docx);
    const canonicalPdf = new Blob([new TextEncoder().encode("%PDF-canonical-render-1")], { type: "application/pdf" });
    const canonicalPdfHash = await blobSha256(canonicalPdf);
    const createObjectUrl = vi.fn(() => "blob:canonical-pdf");
    const revokeObjectUrl = vi.fn();
    const URLStub = class extends URL {};
    Object.defineProperty(URLStub, "createObjectURL", { value: createObjectUrl });
    Object.defineProperty(URLStub, "revokeObjectURL", { value: revokeObjectUrl });
    vi.stubGlobal("URL", URLStub);
    const docxResults = structuredClone(results);
    docxResults.results[0].evidence[0].source_version = sourceHash;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/runs/${runId}`)) return { ok: true, status: 200, json: async () => run } as Response;
      if (url.endsWith(`/runs/${runId}/results`)) return { ok: true, status: 200, json: async () => docxResults } as Response;
      if (url.endsWith("kind=rendered")) return {
        ok: true,
        status: 200,
        headers: new Headers({ "Content-Type": "application/pdf", "Content-Length": String(canonicalPdf.size) }),
        blob: async () => canonicalPdf,
      } as Response;
      return { ok: true, status: 200, json: async () => ({
        ...parsedPdfArtifact(sourceHash),
        index_source_sha256: canonicalPdfHash,
        media_type: docx.type,
        renderer_identity: "test-renderer:1",
      }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRunResultsRoute(`/runs/${runId}`);
    fireEvent.click(await screen.findByRole("button", { name: /核对解析引用/ }));
    expect(await screen.findByText("已复核冻结来源版本、页码、字符偏移及引用文本哈希。此校验不代表法律判断。"))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开规范 PDF 页码视图" }));
    const frame = await screen.findByTitle("DOCX 规范 PDF，第 1 页");
    expect(frame).toHaveAttribute("src", "blob:canonical-pdf#page=1&toolbar=1&navpanes=0");
    expect(await screen.findByText(/已打开冻结的规范 PDF 第 1 页（test-renderer:1）/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭页码视图" }));
    await waitFor(() => expect(screen.queryByTitle("DOCX 规范 PDF，第 1 页")).not.toBeInTheDocument());
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:canonical-pdf");
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/cases/${caseId}/documents/${documentId}/content?kind=rendered`,
      expect.objectContaining({ cache: "no-store" }),
    );
  }, 15000);

  it("does not display result text when current source authorization is denied", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/runs/${runId}`)) {
        return { ok: true, status: 200, json: async () => run } as Response;
      }
      return { ok: false, status: 403, json: async () => ({ code: "result_source_not_authorized" }) } as Response;
    }));

    renderRunResultsRoute(`/runs/${runId}`);
    expect(await screen.findByText("无法读取审核详情")).toBeInTheDocument();
    expect(screen.getByText(/为保护材料，不显示受限结果/)).toBeInTheDocument();
    expect(screen.queryByText("Payment")).not.toBeInTheDocument();
    expect(screen.queryByText("验收合格")).not.toBeInTheDocument();
  });
});
