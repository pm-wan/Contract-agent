export interface KnowledgeDocument {
  id: string; source_key: string; version: number; kind: "legal" | "company";
  title: string; file_name: string; source_url: string; issuer: string; scope: string;
  effective_from: string | null; effective_until: string | null;
  status: "draft" | "published" | "withdrawn"; content_sha256: string;
  warnings: string[]; passage_count: number; segments?: Array<{ locator: string; text: string }>;
}
export interface KnowledgeCatalog {
  items: KnowledgeDocument[];
  online_legal_search: { provider: string; configured: boolean; allowed_domains: string[] };
}
export interface ReviewSamples {
  sources: Array<{ key: string; kind: "legal" | "company"; title: string; issuer: string; scope: string;
    effective_from?: string; effective_until?: string; source_url: string }>;
  contracts: Array<{ scenario_id: string; title: string; category: string; description: string }>;
}
export async function knowledgeRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...init });
  if (!response.ok) throw new Error(response.status === 403 ? "当前账号没有资料管理权限。" :
    response.status === 422 ? "文件解析失败或元数据不完整，请核对文件格式、日期及文本内容。" :
    response.status === 409 ? "资料状态已变化，请刷新后重试。" : "资料服务暂不可用，请稍后重试。");
  return response.json() as Promise<T>;
}
export const getKnowledgeCatalog = (signal?: AbortSignal) => knowledgeRequest<KnowledgeCatalog>("/api/v1/knowledge-documents", { signal });
export const getReviewSamples = () => knowledgeRequest<ReviewSamples>("/api/v1/review-samples");
export const getKnowledgeDocument = (id: string) => knowledgeRequest<KnowledgeDocument>(`/api/v1/knowledge-documents/${id}`);
export const uploadKnowledgeDocument = (data: FormData) => knowledgeRequest<KnowledgeDocument>("/api/v1/knowledge-documents", { method: "POST", body: data });
export const setKnowledgeStatus = (id: string, action: "publish" | "withdraw") => knowledgeRequest<KnowledgeDocument>(`/api/v1/knowledge-documents/${id}/${action}`, { method: "POST" });
export async function sampleFile(path: string, name: string): Promise<File> {
  const response = await fetch(`/api/v1/review-samples/${path}`, { credentials: "same-origin" });
  if (!response.ok) throw new Error("测试材料读取失败。");
  return new File([await response.blob()], name, { type: response.headers.get("Content-Type") ?? "text/plain" });
}
