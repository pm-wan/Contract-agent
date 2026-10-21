export function contractFileError(file: File): string | undefined {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (file.size === 0) return "文件为空，请选择其他文件。";
  if (file.size > 30 * 1024 * 1024) return "文件超过 30 MiB，请压缩或拆分后重试。";
  if (!["pdf", "docx"].includes(extension)) return "仅支持 PDF 或 DOCX 文件。";
  return undefined;
}
