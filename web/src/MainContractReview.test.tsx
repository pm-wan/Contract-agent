import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { MainContractReview } from "./MainContractReview";

vi.mock("./api", async (original) => ({
  ...await original<typeof api>(),
  confirmCaseLegalEntity: vi.fn(), selectCaseDocument: vi.fn(), previewCaseSources: vi.fn(),
  freezeCaseSources: vi.fn(), startAuditRun: vi.fn(),
}));

const caseRecord: api.CaseRecord = {
  case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", title: "主合同", legal_entity_id: null,
  legal_entity_confirmed_at: null, status: "draft", version: 2, created_at: "2026-10-12T00:00:00Z",
  can_correct_legal_entity: false,
};
const main: api.CaseDocument = {
  document_version_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", case_id: caseRecord.case_id,
  file_name: "主合同.pdf", role: "primary_contract", media_type: "application/pdf", size_bytes: 1000,
  content_hash: "a".repeat(64), parse_status: "completed", parse_error_code: null, parser_version: "test",
  warnings: [], case_version: 2, document_status: "staged", index_status: "ready", index_error_code: null,
  selection_id: null, selection_status: "not_selected", authorization_status: "not_authorized",
  amount_authorized: false, authorization_valid_until: null, purpose_authorizations: [], created_at: caseRecord.created_at,
};
const preview: api.SourcePreview = {
  preview_id: "preview", case_id: caseRecord.case_id, purpose: "contract_review", case_version: 4,
  manifest_sha256: "a".repeat(64), items: [{ selection_id: "selection", relation: "main", status: "ready", document_version_id: main.document_version_id }],
};
const frozen: api.FrozenInput = {
  input_snapshot_id: "snapshot", case_id: caseRecord.case_id, purpose: "contract_review", case_version: 5,
  manifest_sha256: preview.manifest_sha256, document_version_ids: [main.document_version_id],
};

beforeEach(() => {
  vi.mocked(api.confirmCaseLegalEntity).mockResolvedValue({ ...caseRecord, legal_entity_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", version: 3 });
  vi.mocked(api.selectCaseDocument).mockResolvedValue({} as api.SelectedDocument);
  vi.mocked(api.previewCaseSources).mockResolvedValue(preview);
  vi.mocked(api.freezeCaseSources).mockResolvedValue(frozen);
  vi.mocked(api.startAuditRun).mockResolvedValue({ run_id: "run", case_id: caseRecord.case_id, input_snapshot_id: "snapshot", status: "queued", version: 1, created_at: caseRecord.created_at });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function renderReview(record = caseRecord, document = main) {
  const onRefresh = vi.fn(async () => ({ caseRecord: { ...record, version: 8 }, documents: [document] }));
  render(<ConfigProvider><MemoryRouter initialEntries={["/case"]}><Routes>
    <Route path="/case" element={<MainContractReview caseRecord={record} documents={[document]} unavailable={false} busy={false} onBusyChange={vi.fn()} onRefresh={onRefresh} onMaterials={vi.fn()} />} />
    <Route path="/runs/:runId" element={<h1>审核运行已打开</h1>} />
  </Routes></MemoryRouter></ConfigProvider>);
  return onRefresh;
}

async function authorize() {
  fireEvent.click(screen.getByRole("button", { name: "开始审核" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("读取授权有效期"), { target: { value: "4" } });
}

describe("主合同审核主线", () => {
  it("在一次确认后按最新案件版本确认主体、授权、冻结并进入运行", async () => {
    renderReview();
    await authorize();
    fireEvent.change(screen.getByLabelText("签约主体 UUID"), { target: { value: "cccccccc-cccc-4ccc-8ccc-cccccccccccc" } });
    fireEvent.change(screen.getByLabelText("主体核对依据"), { target: { value: "已核对签约资料" } });
    fireEvent.click(screen.getByRole("button", { name: "确认并开始审核" }));
    expect(await screen.findByRole("heading", { name: "审核运行已打开" })).toBeInTheDocument();
    expect(api.confirmCaseLegalEntity).toHaveBeenCalledWith(caseRecord.case_id, expect.objectContaining({ expected_case_version: 8 }), expect.any(String));
    expect(api.selectCaseDocument).toHaveBeenCalledWith(caseRecord.case_id, expect.objectContaining({ document_version_id: main.document_version_id, purpose: "contract_review", allow_amount: true }));
    expect(api.startAuditRun).toHaveBeenCalledWith(caseRecord.case_id, "snapshot", expect.any(String));
  });

  it("运行提交结果不确定时复用输入和请求键，防止重复冻结与重复运行", async () => {
    vi.mocked(api.startAuditRun).mockRejectedValueOnce(new api.ApiError(0, "network_error"));
    renderReview({ ...caseRecord, legal_entity_id: "entity" });
    await authorize();
    fireEvent.click(screen.getByRole("button", { name: "确认并开始审核" }));
    expect(await screen.findByText(/连接结果待核实/)).toBeInTheDocument();
    const first = vi.mocked(api.startAuditRun).mock.calls[0];
    fireEvent.click(screen.getByRole("button", { name: "使用同一请求继续" }));
    expect(await screen.findByRole("heading", { name: "审核运行已打开" })).toBeInTheDocument();
    expect(api.freezeCaseSources).toHaveBeenCalledTimes(1);
    expect(api.selectCaseDocument).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.startAuditRun).mock.calls[1]).toEqual(first);
  });

  it("拒绝未确认授权的提交，并阻止未就绪材料和金额权限不足的审核", async () => {
    renderReview({ ...caseRecord, legal_entity_id: "entity" });
    fireEvent.click(screen.getByRole("button", { name: "开始审核" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认并开始审核" }));
    expect(await screen.findByText("请明确确认本次读取授权。")).toBeInTheDocument();
    expect(api.startAuditRun).not.toHaveBeenCalled();
    cleanup();
    renderReview(caseRecord, { ...main, index_status: "failed" });
    expect(screen.getByRole("button", { name: "开始审核" })).toBeDisabled();
    cleanup();
    renderReview(caseRecord, { ...main, authorization_status: "authorized", amount_authorized: false });
    expect(screen.getByRole("button", { name: "开始审核" })).toBeDisabled();
  });

  it("已存在有效授权时直接冻结，不要求重复授权", async () => {
    renderReview({ ...caseRecord, legal_entity_id: "entity" }, { ...main, selection_status: "selected", authorization_status: "authorized", amount_authorized: true, authorization_valid_until: "2099-01-01T00:00:00Z" });
    fireEvent.click(screen.getByRole("button", { name: "开始审核" }));
    await waitFor(() => expect(screen.queryByRole("checkbox")).not.toBeInTheDocument());
    fireEvent.click(await screen.findByRole("button", { name: "确认并开始审核" }));
    expect(await screen.findByRole("heading", { name: "审核运行已打开" })).toBeInTheDocument();
    expect(api.selectCaseDocument).not.toHaveBeenCalled();
  });
});
