import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HumanTasksPage } from "./HumanTasksPage";

afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  return render(<ConfigProvider><MemoryRouter><HumanTasksPage /></MemoryRouter></ConfigProvider>);
}

const firstTask = {
  task_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  case_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  run_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  type: "fact_review",
  status: "open",
  title: "确认合同事实",
  reason: "fact_conflict",
  required_role: "reviewer",
  decision: null,
  version: 1,
  created_at: "2026-09-25T01:30:00Z",
};

describe("authorized human task queue", () => {
  it("shows real task associations but does not offer blind fact confirmation", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(`${window.location.origin}/api/v1/human-tasks?limit=20&status=open`);
      return {
        ok: true,
        status: 200,
        json: async () => ({ items: [firstTask], next_cursor: null }),
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("确认合同事实")).toBeInTheDocument();
    expect(screen.getByText("合同审核人")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看运行结果" })).toHaveAttribute(
      "href",
      `/runs/${firstTask.run_id}`,
    );
    expect(screen.getByText(/价款复核任务可在运行结果页/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认事实|提交决定/ })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  }, 15000);

  it("appends cursor pages without implying an arbitrary page number", async () => {
    const secondTask = {
      ...firstTask,
      task_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      run_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ items: [firstTask], next_cursor: "next-page-token" }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ items: [secondTask], next_cursor: null }),
      } as Response);
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByRole("link", { name: "查看运行结果" });
    fireEvent.click(screen.getByRole("button", { name: "加载更多任务" }));

    await waitFor(() => {
      expect(screen.getAllByRole("link", { name: "查看运行结果" })).toHaveLength(2);
    });
    expect(String(fetchMock.mock.calls[1][0])).toContain("cursor=next-page-token");
  });

  it("distinguishes a truly empty queue from a failed request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ items: [], next_cursor: null }),
    }) as Response));

    renderPage();

    expect(await screen.findByText("当前没有分配给你的待复核任务")).toBeInTheDocument();
    expect(screen.getByText("已加载 0 项待复核任务")).toBeInTheDocument();
  });

  it("removes previously visible task metadata after authorization is denied", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ items: [firstTask], next_cursor: null }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        json: async () => ({ code: "forbidden" }),
      } as Response);
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    expect(await screen.findByText("确认合同事实")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新队列" }));

    expect(await screen.findByText("无权查看人工任务")).toBeInTheDocument();
    expect(screen.queryByText("确认合同事实")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看运行结果" })).not.toBeInTheDocument();
  });
});
