import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { DocumentAccessTimeline } from "./DocumentAccessTimeline";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function event(action: "selection_granted" | "selection_revoked", id: string) {
  return {
    id,
    case_id: "case-1",
    document_version_id: "document-1",
    file_name: "主合同.pdf",
    selection_id: "selection-1",
    actor_id: "reviewer-12345678",
    action,
    permissions: [{
      actor_id: "reviewer-12345678",
      purpose: "contract_review",
      allow_text: true,
      allow_amount: false,
      valid_until: "2026-09-25T02:00:00Z",
    }],
    reason: action === "selection_revoked" ? "材料不再纳入本次审查" : null,
    case_version: action === "selection_revoked" ? 4 : 3,
    invalidated_run_ids: action === "selection_revoked" ? ["run-1"] : [],
    created_at: "2026-09-25T01:00:00Z",
  };
}

describe("DocumentAccessTimeline", () => {
  it("shows authorized access and revocation history and appends cursor pages", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [event("selection_revoked", "event-2")], next_cursor: "next-page" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [event("selection_granted", "event-1")], next_cursor: null })));
    render(<ConfigProvider><DocumentAccessTimeline caseId="case-1" /></ConfigProvider>);

    expect(await screen.findByText(/材料不再纳入本次审查/)).toBeInTheDocument();
    expect(screen.getByText(/已使\s*1\s*个运行失效/)).toBeInTheDocument();
    expect(screen.getByText(/合同审查/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "加载更多记录" }));
    expect(await screen.findByText("已授予材料用途权限")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1][0])).toContain("cursor=next-page");
  });

  it("does not expose authorization history to non-reviewers", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ code: "forbidden" }), { status: 403 }));
    render(<ConfigProvider><DocumentAccessTimeline caseId="case-1" /></ConfigProvider>);
    expect(await screen.findByText("授权记录仅对当前审核人开放")).toBeInTheDocument();
    expect(screen.queryByText("主合同.pdf")).not.toBeInTheDocument();
  });
});
