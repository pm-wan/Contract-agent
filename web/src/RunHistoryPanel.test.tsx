import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunHistoryPanel } from "./RunHistoryPanel";

afterEach(() => vi.unstubAllGlobals());

const caseId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const run = {
  run_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  case_id: caseId,
  input_snapshot_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  status: "completed",
  version: 3,
  created_at: "2026-09-25T01:30:00Z",
};

function renderPanel(view: "history" | "results" = "history") {
  return render(<ConfigProvider><MemoryRouter><RunHistoryPanel caseId={caseId} view={view} /></MemoryRouter></ConfigProvider>);
}

describe("case run history", () => {
  it("keeps pending runs out of the produced-results view", async () => {
    const pending = { ...run, run_id: "pending-run", status: "running" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [pending, run], next_cursor: null }) } as Response));
    renderPanel("results");
    const resultLink = await screen.findByRole("link", { name: "查看审核结果与原文证据" });
    expect(resultLink).toHaveAttribute("href", `/runs/${run.run_id}`);
    expect(screen.queryByText("pending-run")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows authorized run metadata and links to its read-only result route", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(`${window.location.origin}/api/v1/cases/${caseId}/runs?limit=20`);
      return { ok: true, status: 200, json: async () => ({ items: [run], next_cursor: null }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();
    expect(await screen.findByText("候选结果已生成")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看运行结果" })).toHaveAttribute("href", `/runs/${run.run_id}`);
    expect(screen.getByText(/在上方发起主合同审核/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /启动|开始运行/ })).not.toBeInTheDocument();
  });

  it("appends cursor results without fabricating page numbers", async () => {
    const second = { ...run, run_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", version: 2 };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ items: [run], next_cursor: "opaque-cursor" }) } as Response)
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ items: [second], next_cursor: null }) } as Response);
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();
    await screen.findByRole("button", { name: "加载更多运行" });
    fireEvent.click(screen.getByRole("button", { name: "加载更多运行" }));
    await waitFor(() => expect(screen.getAllByRole("link", { name: "查看运行结果" })).toHaveLength(2));
    expect(String(fetchMock.mock.calls[1][0])).toContain("cursor=opaque-cursor");
  });

  it("distinguishes a genuinely empty list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ items: [], next_cursor: null }),
    }) as Response));
    renderPanel();
    expect(await screen.findByText("当前没有可查看的运行记录")).toBeInTheDocument();
  });

  it("clears previously rendered metadata after access is denied", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ items: [run], next_cursor: null }) } as Response)
      .mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ code: "forbidden" }) } as Response);
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();
    expect(await screen.findByText("候选结果已生成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新审核记录" }));
    expect(await screen.findByText("无权查看运行历史")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看运行结果" })).not.toBeInTheDocument();
  });

  it("does not flash the previous case history while switching cases", async () => {
    const secondCaseId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
    let resolveNext: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ items: [run], next_cursor: null }) } as Response)
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveNext = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<ConfigProvider><MemoryRouter><RunHistoryPanel caseId={caseId} /></MemoryRouter></ConfigProvider>);
    expect(await screen.findByText("候选结果已生成")).toBeInTheDocument();
    view.rerender(<ConfigProvider><MemoryRouter><RunHistoryPanel caseId={secondCaseId} /></MemoryRouter></ConfigProvider>);
    expect(screen.queryByRole("link", { name: "查看运行结果" })).not.toBeInTheDocument();
    resolveNext?.({ ok: true, status: 200, json: async () => ({ items: [], next_cursor: null }) } as Response);
    expect(await screen.findByText("当前没有可查看的运行记录")).toBeInTheDocument();
  });

  it("retains the last successful list on a transient refresh failure", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ items: [run], next_cursor: null }) } as Response)
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ code: "service_unavailable" }) } as Response);
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();
    expect(await screen.findByText("候选结果已生成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新审核记录" }));
    expect(await screen.findByText("刷新未完成，仍显示上次成功读取的记录")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看运行结果" })).toBeInTheDocument();
  });
});
