import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocxOriginalPreview } from "./DocxOriginalPreview";
import { blobSha256 } from "./blobSha256";
import { renderAsync } from "docx-preview";

vi.mock("docx-preview", () => ({
  renderAsync: vi.fn(async (_file: Blob, body: HTMLElement) => {
    const page = body.ownerDocument.createElement("section");
    page.className = "docx";
    const paragraph = body.ownerDocument.createElement("p");
    paragraph.append("模拟 Word ");
    const bold = body.ownerDocument.createElement("strong");
    bold.textContent = "原文内容";
    paragraph.append(bold);
    page.append(paragraph);
    const link = body.ownerDocument.createElement("a");
    link.href = "javascript:alert(1)";
    link.textContent = "外部链接";
    page.append(link);
    body.append(page);
  }),
}));

afterEach(() => {
  vi.clearAllMocks();
});

describe("DocxOriginalPreview", () => {
  it("verifies the original and highlights a unique exact quote in its cited page", async () => {
    const file = new Blob(["authorized-docx"], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    const sourceSha256 = await blobSha256(file);
    render(<ConfigProvider><DocxOriginalPreview
      file={file}
      citedPage={1}
      quote="模拟 Word 原文内容"
      sourceSha256={sourceSha256}
    /></ConfigProvider>);
    const frame = screen.getByTitle("DOCX 原件预览，引用第 1 页") as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin");
    frame.contentDocument!.body.innerHTML = '<div id="docx-style"></div><div id="docx-content"></div>';
    fireEvent.load(frame);

    expect(await screen.findByText(/已定位到原件第 1 页，并标记唯一匹配的引用/)).toBeInTheDocument();
    await waitFor(() => expect(renderAsync).toHaveBeenCalledTimes(1));
    const [, body, styles, options] = vi.mocked(renderAsync).mock.calls[0];
    expect(body.id).toBe("docx-content");
    expect(styles?.id).toBe("docx-style");
    expect(options).toMatchObject({ renderAltChunks: false, renderComments: false, renderChanges: false, useBase64URL: true });
    const renderedLink = frame.contentDocument?.querySelector("a");
    expect(renderedLink?.hasAttribute("href")).toBe(false);
    const highlight = frame.contentDocument?.querySelector('mark[data-cited-evidence="true"]');
    expect(highlight?.textContent).toBe("模拟 Word 原文内容");
    expect(frame.contentDocument?.querySelector("section.docx")?.textContent).toContain("模拟 Word 原文内容");
  });

  it("does not render an original whose bytes do not match the cited source hash", async () => {
    const file = new Blob(["tampered-docx"], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    render(<ConfigProvider><DocxOriginalPreview
      file={file}
      citedPage={1}
      quote="不可信引用"
      sourceSha256={"0".repeat(64)}
    /></ConfigProvider>);
    const frame = screen.getByTitle("DOCX 原件预览，引用第 1 页") as HTMLIFrameElement;
    frame.contentDocument!.body.innerHTML = '<div id="docx-style"></div><div id="docx-content"></div>';
    fireEvent.load(frame);

    expect(await screen.findByText(/原件哈希与已校验的材料版本不一致/)).toBeInTheDocument();
    expect(renderAsync).not.toHaveBeenCalled();
  });
});
