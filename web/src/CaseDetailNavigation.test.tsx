import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { CaseDetailPage } from "./CaseDetailPage";

vi.mock("./api", async (original) => ({
  ...await original<typeof api>(), getCase: vi.fn(), listCaseDocuments: vi.fn(),
  listCaseRuns: vi.fn(), listCaseDocumentAccessEvents: vi.fn(),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const record = {
  case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", title: "采购合同准备", legal_entity_id: null,
  legal_entity_confirmed_at: null, status: "draft", version: 2, created_at: "2026-10-12T00:00:00Z",
} as api.CaseRecord;
const document = {
  document_version_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", case_id: record.case_id,
  content_hash: "a".repeat(64), parse_error_code: null, parser_version: "synthetic",
  index_error_code: null, selection_id: null, authorization_valid_until: null, created_at: record.created_at,
  file_name: "主合同.pdf", role: "primary_contract", media_type: "application/pdf", size_bytes: 1024,
  parse_status: "completed", index_status: "ready", document_status: "staged", warnings: [],
  selection_status: "not_selected", authorization_status: "not_authorized", amount_authorized: false,
  purpose_authorizations: [], case_version: 2,
} satisfies api.CaseDocument;
function Location() { return <output aria-label="当前路径">{useLocation().search}</output>; }
function setup() {
  vi.mocked(api.getCase).mockResolvedValue(record);
  vi.mocked(api.listCaseDocuments).mockResolvedValue([document]);
  vi.mocked(api.listCaseRuns).mockResolvedValue({ items: [], next_cursor: null });
  vi.mocked(api.listCaseDocumentAccessEvents).mockResolvedValue({ items: [], next_cursor: null });
  return render(<ConfigProvider><MemoryRouter initialEntries={[`/cases/${record.case_id}`]}>
    <Location /><Routes><Route path="/cases/:caseId" element={<CaseDetailPage />} /></Routes>
  </MemoryRouter></ConfigProvider>);
}

describe("案件详情信息分区", () => {
  it("按材料、过程和结果导航，并在切换后保留主体核对草稿", async () => {
    setup();
    const nav = await screen.findByRole("navigation", { name: "案件详情分区" });
    expect(within(nav).getByRole("link", { name: "背景与材料" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("button", { name: "开始审核" })).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "案件材料" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "权限操作" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/^确认签约主体 ·/));
    fireEvent.change(screen.getByLabelText("核对依据"), { target: { value: "已核对营业执照" } });
    fireEvent.click(within(nav).getByRole("link", { name: "审核过程" }));
    expect(screen.getByRole("button", { name: "开始审核" })).toBeEnabled();
    expect(screen.queryByRole("heading", { name: "上传材料" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("当前路径")).toHaveTextContent("?view=review");
    fireEvent.click(screen.getByText("审核范围与读取授权"));
    expect(screen.getByRole("table", { name: "本次审核材料与授权" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "等待主体确认" })).toBeDisabled();
    fireEvent.click(within(nav).getByRole("link", { name: "审核结果" }));
    expect(await screen.findByText("暂无已产出的审核结果")).toBeVisible();
    expect(screen.queryByRole("button", { name: "开始审核" })).not.toBeInTheDocument();
    fireEvent.click(within(nav).getByRole("link", { name: "背景与材料" }));
    expect(screen.getByLabelText("核对依据")).toHaveValue("已核对营业执照");
  }, 20000);
});
