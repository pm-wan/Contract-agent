import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResearchResults, type ResearchReview } from "./ResearchResults";

const review: ResearchReview = {
  summary: "需要复核。", understood_contract: "本合同约定交货与验收。", reviewed_topics: ["交货"],
  snapshot_sha256: "a".repeat(64), sources_sha256: "b".repeat(64), limitations: ["尚缺少公司制度。"],
  searches: [{ channel: "legal_local", query: "交付条款", keywords: ["交付"], source_ids: ["local:1"], searched_at: "2026-10-20T00:00:00Z" }],
  sources: [{ source_id: "local:1", origin: "local", kind: "legal", title: "法规全文", url: "https://www.gov.cn/",
    locator: "第一条", text: "依约交付。", content_sha256: "c".repeat(64), version: 1 }],
  findings: [{ domain: "legal", issue_type: "drafting", title: "交货条款待核对", status: "human_review", severity: "medium",
    reasoning: "约定需要核对。", applicability: "适用范围需核实。", next_action: "核对附件。", unresolved_facts: ["交付地点未确认。"],
    contract_evidence: [{ document_version_id: "contract-1", page: 2, quote: "卖方负责交货。" }], source_evidence: [{ source_id: "local:1", quote: "依约交付。" }] },
    { domain: "company", issue_type: "materials", title: "公司制度依据不足", status: "needs_material", severity: "undetermined",
      reasoning: "缺少制度。", applicability: "待核实。", next_action: "上传制度。", unresolved_facts: [], contract_evidence: [], source_evidence: [] }],
};
describe("ResearchResults", () => {
  it("shows contract and source evidence and opens the verified contract position", () => {
    const open = vi.fn(); render(<ResearchResults review={review} onContractQuote={open} />);
    expect(screen.getByText("卖方负责交货。")).toBeInTheDocument();
    expect(screen.getByText("依约交付。")).toBeInTheDocument();
    expect(screen.getByText("交付地点未确认。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开原文位置" }));
    expect(open).toHaveBeenCalledWith(review.findings[0].contract_evidence[0]);
    expect(screen.getByRole("link", { name: "查看原文来源" })).toHaveAttribute("href", "https://www.gov.cn/");
  });
  it("shows missing company sources without a passing conclusion", async () => {
    render(<ResearchResults review={review} />);
    fireEvent.click(screen.getByRole("tab", { name: "公司制度层面" }));
    expect(await screen.findByText("公司制度依据不足")).toBeInTheDocument();
    expect(screen.getByText("需补充材料")).toBeInTheDocument();
  });
});
