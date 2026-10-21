import { describe, expect, it } from "vitest";
import { highlightUniqueDocxQuote } from "./highlightDocxEvidence";

describe("highlightUniqueDocxQuote", () => {
  it("marks an exact quote split across inline formatting", () => {
    const page = document.createElement("section");
    page.innerHTML = '<p>付款条件由 <strong>双方确认</strong> 后生效。</p>';

    expect(highlightUniqueDocxQuote(page, "付款条件由 双方确认 后生效。"))
      .toBe("highlighted");
    expect(page.querySelector('mark[data-cited-evidence="true"]')?.textContent)
      .toBe("付款条件由 双方确认 后生效。");
  });

  it("does not choose between repeated quotes", () => {
    const page = document.createElement("section");
    page.innerHTML = "<p>同一引用。</p><p>同一引用。</p>";

    expect(highlightUniqueDocxQuote(page, "同一引用。"))
      .toBe("ambiguous");
    expect(page.querySelector("mark[data-cited-evidence]"))
      .toBeNull();
  });

  it("does not highlight a quote that is absent from the cited page", () => {
    const page = document.createElement("section");
    page.innerHTML = "<p>另一页的内容</p>";

    expect(highlightUniqueDocxQuote(page, "未找到的引用"))
      .toBe("not_found");
    expect(page.querySelector("mark[data-cited-evidence]"))
      .toBeNull();
  });
});
