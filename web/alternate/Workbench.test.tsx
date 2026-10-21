import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../src/api";
import { AlternateWorkbench } from "./Workbench";

const caseRecord: api.CaseRecord = {
  case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  title: "采购服务合同审阅",
  legal_entity_id: null,
  legal_entity_confirmed_at: null,
  status: "draft",
  version: 1,
  created_at: "2026-09-25T01:30:00Z",
  can_correct_legal_entity: false,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/alternate/");
});

describe("alternate case workbench", () => {
  it("loads server cases and opens the selected case without inventing status", async () => {
    vi.spyOn(api, "listCases").mockResolvedValue({ items: [caseRecord], total: 1, page: 1, page_size: 20 });
    vi.spyOn(api, "getCase").mockResolvedValue(caseRecord);
    vi.spyOn(api, "listCaseDocuments").mockResolvedValue([]);

    render(<AlternateWorkbench />);
    expect(await screen.findByRole("heading", { name: /案件台账/ })).toBeInTheDocument();
    expect(screen.getByText("采购服务合同审阅")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开案件：采购服务合同审阅" }));

    expect(await screen.findByRole("heading", { name: "采购服务合同审阅。" })).toBeInTheDocument();
    expect(screen.getByText("签约主体待人工确认")).toBeInTheDocument();
    expect(screen.getByText("上传成功不代表审查授权。授权按材料、用途、字段范围和有效期单独记录。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认主体" })).toBeDisabled();
  });

  it("keeps search conditions in the URL and exposes a clear action", async () => {
    vi.spyOn(api, "listCases").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
    render(<AlternateWorkbench />);

    const search = await screen.findByRole("searchbox", { name: "按案件名称搜索" });
    fireEvent.change(search, { target: { value: "设备采购" } });
    await waitFor(() => expect(new URLSearchParams(window.location.search).get("query")).toBe("设备采购"), { timeout: 1500 });
    fireEvent.click(screen.getAllByRole("button", { name: "清除搜索" })[0]);

    expect(search).toHaveValue("");
    expect(new URLSearchParams(window.location.search).has("query")).toBe(false);
    expect(await within(screen.getByRole("region", { name: "全部案件" })).findByText("台账尚空")).toBeInTheDocument();
  });
});
