import { Tag } from "antd";
import type { CaseDocument } from "./api";

type ParseStatus = CaseDocument["parse_status"];
type ParseErrorCode = NonNullable<CaseDocument["parse_error_code"]>;

const descriptions: Record<ParseErrorCode, string> = {
  document_size_invalid: "文件大小无效，请检查文件后重新上传。",
  pdf_signature_invalid: "文件内容不是有效 PDF，请从原文件重新导出后上传。",
  docx_signature_invalid: "文件内容不是有效 DOCX，请从原文件重新保存后上传。",
  docx_structure_invalid: "DOCX 文件结构不完整，请重新保存后上传。",
  docx_expanded_size_invalid: "文件内部内容超出处理范围，请检查或压缩文件。",
  docx_macro_forbidden: "此 DOCX 含有不支持的宏内容，请移除宏后另存并上传。",
  docx_archive_invalid: "DOCX 文件包损坏或不完整，请重新保存后上传。",
  media_type_unsupported: "当前仅支持 PDF 或 DOCX，请上传支持的文件格式。",
  docx_render_source_hash_mismatch: "处理期间源文件校验不一致，请重新上传文件。",
  pdf_encrypted: "PDF 受密码保护，请上传无需密码即可读取的版本。",
  pdf_parse_failed: "PDF 内容无法读取，请重新导出或检查文件后重试。",
  pdf_has_no_pages: "PDF 没有可读取的页面，请检查文件后重试。",
  ocr_page_too_large: "扫描页超出处理范围，请拆分或调整文件后重试。",
  ocr_coordinates_invalid: "扫描件识别结果无法校验，请联系管理员处理。",
  ocr_image_invalid: "扫描图像无法读取，请检查文件质量后重试。",
  ocr_unavailable: "扫描件文字识别服务暂不可用，请稍后查看或联系管理员。",
  ocr_timeout: "扫描件文字识别超时，请稍后查看；若持续发生，请联系管理员。",
  ocr_failed: "扫描件文字识别未完成，请稍后查看或联系管理员。",
  ocr_output_too_large: "扫描件识别内容超出处理范围，请拆分文件后重试。",
  ocr_output_invalid: "扫描件识别结果无法使用，请检查文件或联系管理员。",
  docx_parse_failed: "DOCX 内容无法读取，请重新保存文件后重试。",
  docx_renderer_unavailable: "DOCX 页面转换服务暂不可用，请联系管理员处理。",
  docx_renderer_version_unavailable: "DOCX 页面转换服务状态无法核验，请联系管理员处理。",
  docx_renderer_version_unexpected: "DOCX 页面转换版本与配置不匹配，请联系管理员处理。",
  docx_renderer_environment_invalid: "DOCX 页面转换配置无效，请联系管理员处理。",
  docx_renderer_timeout: "DOCX 页面转换超时；如多次出现，请联系管理员。",
  docx_renderer_failed: "DOCX 页面转换未完成，请稍后查看；若持续发生，请联系管理员。",
  docx_renderer_output_missing: "DOCX 页面转换结果未生成，请联系管理员检查处理服务。",
  docx_renderer_output_invalid: "DOCX 页面转换结果无法校验，请重新上传或联系管理员。",
  parse_storage_mismatch: "材料存储状态无法核验，请联系管理员处理。",
  parse_processing_error: "材料处理暂未完成，请稍后查看；如持续出现，请联系管理员。",
};

const statusLabels: Record<ParseStatus, string> = {
  queued: "等待解析",
  processing: "解析中",
  completed: "解析完成",
  needs_material: "需要补充材料",
  failed: "解析失败",
};

function retryNote(status: ParseStatus, hasError: boolean): string | null {
  if (status === "failed") return "解析未完成。";
  if (status === "processing" && hasError) return "上次尝试未完成，正在再次处理。";
  if (status === "queued" && hasError) return "上次尝试未完成，等待再次处理。";
  return null;
}

export function DocumentParseStatus({
  status,
  errorCode,
}: {
  status: ParseStatus;
  errorCode: CaseDocument["parse_error_code"];
}) {
  const detail = errorCode ? descriptions[errorCode] : undefined;
  const color = status === "completed"
    ? "success"
    : status === "needs_material"
      ? "warning"
      : status === "failed"
        ? "error"
        : "processing";
  const note = retryNote(status, Boolean(detail));
  const progressLabel = status === "queued"
    ? "待开始"
    : status === "processing"
      ? "处理中"
      : status === "completed"
        ? "100%"
        : status === "needs_material"
          ? "100%"
          : "未完成";
  const progressValue = status === "queued"
    ? 0
    : status === "completed" || status === "needs_material"
      ? 100
      : undefined;

  return <div className="authorization-state document-parse-status">
    <Tag color={color}>{statusLabels[status]}</Tag>
    <div className={`parse-progress parse-progress-${status}`}>
      <div
        className="parse-progress-track"
        role="progressbar"
        aria-label="解析进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progressValue}
        aria-valuetext={progressLabel}
      >
        <span className="parse-progress-fill" />
      </div>
      <span className="parse-progress-label" aria-hidden="true">{progressLabel}</span>
    </div>
    {detail && <small>{note} {detail}</small>}
  </div>;
}
