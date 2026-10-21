import { useEffect, useRef, useState } from "react";
import { Alert, Spin } from "antd";
import { highlightUniqueDocxQuote } from "./highlightDocxEvidence";
import { blobSha256 } from "./blobSha256";

const DOCX_FRAME = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
html,body{margin:0;min-height:100%;background:#f4f6f7;color:#182b3a;font:14px/1.6 "Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif}
body{overflow:auto}.docx-wrapper{padding:14px 8px!important;background:transparent!important;align-items:center}
section.docx{max-width:100%!important;margin:0 auto 14px!important;box-shadow:0 1px 5px rgba(16,40,63,.18)!important}
mark[data-cited-evidence="true"]{background:color-mix(in srgb,var(--color-bronze) 24%,white);color:inherit;box-shadow:0 0 0 1px var(--color-bronze);border-radius:2px}
a{pointer-events:none!important;text-decoration:none!important;color:inherit!important}
</style></head><body><div id="docx-style"></div><div id="docx-content"></div></body></html>`;

export function DocxOriginalPreview({ file, citedPage, quote, sourceSha256 }: {
  file: Blob;
  citedPage: number;
  quote: string;
  sourceSha256: string;
}) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const renderIdRef = useRef(0);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState(false);
  const [failureMessage, setFailureMessage] = useState<string | null>(null);
  const [pageStatus, setPageStatus] = useState<string | null>(null);

  useEffect(() => () => { renderIdRef.current += 1; }, []);

  const render = async () => {
    const renderId = ++renderIdRef.current;
    const frameDocument = frameRef.current?.contentDocument;
    const content = frameDocument?.getElementById("docx-content");
    const styles = frameDocument?.getElementById("docx-style");
    if (!frameDocument || !content || !styles) {
      setFailure(true);
      setLoading(false);
      return;
    }
    setLoading(true);
    setFailure(false);
    setFailureMessage(null);
    setPageStatus(null);
    try {
      if (!globalThis.crypto?.subtle || !/^[a-f0-9]{64}$/.test(sourceSha256)) {
        throw new Error("original_integrity_unavailable");
      }
      const actualHash = await blobSha256(file);
      if (actualHash !== sourceSha256) throw new Error("original_hash_mismatch");
      const { renderAsync } = await import("docx-preview");
      if (renderId !== renderIdRef.current) return;
      await renderAsync(file, content, styles, {
        breakPages: true,
        ignoreLastRenderedPageBreak: false,
        renderAltChunks: false,
        renderComments: false,
        renderChanges: false,
        useBase64URL: true,
      });
      if (renderId !== renderIdRef.current) return;
      content.querySelectorAll("a").forEach((link) => {
        link.removeAttribute("href");
        link.removeAttribute("target");
      });
      content.querySelectorAll("*").forEach((element) => {
        Array.from(element.attributes).forEach((attribute) => {
          if (attribute.name.toLowerCase().startsWith("on")) element.removeAttribute(attribute.name);
        });
      });
      const pages = Array.from(content.querySelectorAll<HTMLElement>("section.docx"));
      const cited = pages[citedPage - 1];
      if (cited) {
        cited.dataset.citedSourcePage = String(citedPage);
        const bronze = getComputedStyle(document.documentElement).getPropertyValue("--color-bronze").trim();
        if (bronze) frameDocument.documentElement.style.setProperty("--color-bronze", bronze);
        const highlight = highlightUniqueDocxQuote(cited, quote);
        const status = highlight === "highlighted"
          ? `已定位到原件第 ${citedPage} 页，并标记唯一匹配的引用。`
          : highlight === "ambiguous"
            ? `已定位到原件第 ${citedPage} 页，但存在多处相同文本，未绘制高亮。`
            : `已定位到原件第 ${citedPage} 页，但该分页片段中未找到逐字匹配，未绘制高亮。`;
        setPageStatus(status);
        const target = cited.querySelector<HTMLElement>('mark[data-cited-evidence="true"]') ?? cited;
        if (typeof target.scrollIntoView === "function") {
          requestAnimationFrame(() => target.scrollIntoView({ block: "center" }));
        }
      } else {
        setPageStatus(`原件已打开，但未找到对应的第 ${citedPage} 个分页片段（共 ${pages.length} 段）；未绘制高亮。`);
      }
    } catch (error) {
      if (renderId === renderIdRef.current) {
        setFailure(true);
        setFailureMessage(error instanceof Error && error.message === "original_hash_mismatch"
          ? "原件哈希与已校验的材料版本不一致，原件已隐藏。请刷新结果后重试。"
          : error instanceof Error && error.message === "original_integrity_unavailable"
            ? "当前浏览器无法校验原件版本，因此未展示原件。"
            : "文件内容可能不受当前预览器支持。已校验的解析文本仍可供对照；请通过材料目录获取原件。");
      }
    } finally {
      if (renderId === renderIdRef.current) setLoading(false);
    }
  };

  return <div className="docx-original-preview" aria-busy={loading}>
    {loading && <div className="docx-preview-loading" role="status"><Spin size="small" description="正在安全渲染 DOCX 原件…" /></div>}
    {failure && <Alert type="warning" showIcon title="DOCX 原件暂时无法预览" description={failureMessage} />}
    {pageStatus && !failure && <Alert type="info" showIcon title={pageStatus} description="预览前已核对原件 SHA-256；仅在对应分页片段中存在唯一逐字匹配时绘制高亮。DOCX 分页由文件中的分页标记推算，可能与桌面 Word 略有差异，不代表分页定位质量已通过实测。" />}
    <iframe
      ref={frameRef}
      className="original-docx-frame"
      title={`DOCX 原件预览，引用第 ${citedPage} 页`}
      sandbox="allow-same-origin"
      srcDoc={DOCX_FRAME}
      onLoad={() => void render()}
      hidden={failure}
    />
  </div>;
}
