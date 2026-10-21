import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { CaseLegalEntityTimeline } from "./CaseLegalEntityTimeline";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function event(eventType: "confirmed" | "corrected", id: string) {
  return {
    event_id: id,
    event_type: eventType,
    actor_id: "9b4db69d-19a8-41b6-8ca7-51d7ac17f5fa",
    previous_legal_entity_id: eventType === "corrected" ? "4b174f7b-7203-4bc1-a04c-feb134a12533" : null,
    legal_entity_id: "5e92b7a6-27a6-46bd-a915-859e7f5e159f",
    reason: eventType === "corrected" ? "按管理员核验结果更正" : "已核对签约资料",
    case_version: eventType === "corrected" ? 4 : 3,
    occurred_at: "2026-09-25T01:00:00Z",
    revoked_selection_count: eventType === "corrected" ? 1 : 0,
    revoked_grant_count: eventType === "corrected" ? 2 : 0,
    superseded_run_count: eventType === "corrected" ? 1 : 0,
  };
}

describe("CaseLegalEntityTimeline", () => {
  it("shows immutable confirmation/correction details and appends cursor history", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [event("corrected", "event-2")], next_cursor: "next" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [event("confirmed", "event-1")], next_cursor: null })));
    render(<ConfigProvider><CaseLegalEntityTimeline caseId="case-1" /></ConfigProvider>);

    expect(await screen.findByText("管理员更正法律主体")).toBeInTheDocument();
    expect(screen.getByText(/更正前：/)).toBeInTheDocument();
    expect(screen.getByText(/按管理员核验结果更正/)).toBeInTheDocument();
    expect(screen.getByText(/纠错影响：撤销 1 项材料选择、2 项材料授权/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "加载更多记录" }));
    expect(await screen.findByText("确认法律主体")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[1][0])).toContain("cursor=next");
  });

  it("clears record metadata when the caller is no longer authorized", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [event("confirmed", "event-1")], next_cursor: null })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: "forbidden" }), { status: 403 }));
    render(<ConfigProvider><CaseLegalEntityTimeline caseId="case-1" /></ConfigProvider>);
    expect(await screen.findByText(/已核对签约资料/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷 新" }));
    expect(await screen.findByText("无权查看主体审计记录")).toBeInTheDocument();
    expect(screen.queryByText(/已核对签约资料/)).not.toBeInTheDocument();
  });

  it("keeps same-case history on a temporary failure and refreshes it on retry", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [{ ...event("confirmed", "event-old"), reason: "上次读取的核对依据" }],
        next_cursor: null,
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: "temporarily_unavailable" }), { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [{ ...event("confirmed", "event-new"), reason: "恢复后读取的核对依据" }],
        next_cursor: null,
      })));
    render(<ConfigProvider><CaseLegalEntityTimeline caseId="case-1" /></ConfigProvider>);
    expect(await screen.findByText(/上次读取的核对依据/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷 新" }));
    expect(await screen.findByText("刷新未完成，仍显示上次读取的记录")).toBeInTheDocument();
    expect(screen.getByText(/上次读取的核对依据/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText(/恢复后读取的核对依据/)).toBeInTheDocument();
    expect(screen.queryByText(/上次读取的核对依据/)).not.toBeInTheDocument();
  });

  it("hides the previous case history immediately while the next case loads", async () => {
    let resolveNext: ((response: Response) => void) | undefined;
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [{ ...event("confirmed", "case-1-event"), reason: "旧案件主体核对" }],
        next_cursor: null,
      })))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve; }));
    const view = render(<ConfigProvider><CaseLegalEntityTimeline caseId="case-1" /></ConfigProvider>);
    expect(await screen.findByText(/旧案件主体核对/)).toBeInTheDocument();

    view.rerender(<ConfigProvider><CaseLegalEntityTimeline caseId="case-2" /></ConfigProvider>);
    expect(screen.queryByText(/旧案件主体核对/)).not.toBeInTheDocument();
    await act(async () => {
      resolveNext?.(new Response(JSON.stringify({
        items: [{ ...event("confirmed", "case-2-event"), reason: "新案件主体核对" }],
        next_cursor: null,
      })));
    });
    expect(await screen.findByText(/新案件主体核对/)).toBeInTheDocument();
  });

  it("ignores a late response from the previous case", async () => {
    let resolveStale: ((response: Response) => void) | undefined;
    vi.spyOn(globalThis, "fetch")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve; }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [{ ...event("confirmed", "case-2-event"), reason: "新案件主体核对" }],
        next_cursor: null,
      })));
    const view = render(<ConfigProvider><CaseLegalEntityTimeline caseId="case-1" /></ConfigProvider>);

    view.rerender(<ConfigProvider><CaseLegalEntityTimeline caseId="case-2" /></ConfigProvider>);
    expect(await screen.findByText(/新案件主体核对/)).toBeInTheDocument();
    await act(async () => {
      resolveStale?.(new Response(JSON.stringify({
        items: [{ ...event("corrected", "case-1-stale-event"), reason: "旧案件迟到响应" }],
        next_cursor: null,
      })));
    });
    expect(screen.getByText(/新案件主体核对/)).toBeInTheDocument();
    expect(screen.queryByText(/旧案件迟到响应/)).not.toBeInTheDocument();
  });
});
