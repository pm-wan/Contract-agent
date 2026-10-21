import createClient from "openapi-fetch";
import type { components, paths } from "./api-schema";

export type ApiPaths = paths;
export type CaseRecord = components["schemas"]["CaseResponse"] & { legal_entity_name?: string | null };
export type CasePage = components["schemas"]["CaseListResponse"];
export type EntityCorrectionQueue = components["schemas"]["EntityCorrectionQueueResponse"];
export type EntityCorrectionContext = components["schemas"]["EntityCorrectionContextResponse"];
export type CaseDocument = components["schemas"]["CaseDocumentResponse"];
export type DocumentAccessEvent = components["schemas"]["DocumentAccessEventResponse"];
export type DocumentAccessEventPage = components["schemas"]["DocumentAccessEventPageResponse"];
export type CaseLegalEntityHistoryEvent = components["schemas"]["CaseLegalEntityHistoryEventResponse"];
export type CaseLegalEntityHistoryPage = components["schemas"]["CaseLegalEntityHistoryResponse"];
export type UploadedDocument = components["schemas"]["UploadedDocumentResponse"];
export type SelectedDocument = components["schemas"]["SelectedDocumentResponse"];
export type RevokedDocumentSelection = components["schemas"]["RevokedDocumentSelectionResponse"];
export type RemovedDocument = components["schemas"]["RemovedDocumentResponse"];
export type SourcePreviewStatus = components["schemas"]["SourceItemResponse"]["status"];
export type SourcePreviewItem = components["schemas"]["SourceItemResponse"];
export type SourcePreview = components["schemas"]["PreviewResponse"];
export type FrozenInput = components["schemas"]["FrozenInputResponse"];
export type AuditRun = Omit<components["schemas"]["RunResponse"], "task_type"> & {
  task_type?: "contract_review" | "upstream_comparison" | null;
};
export type RunPage = Omit<components["schemas"]["RunListResponse"], "items"> & { items: AuditRun[] };
export type RunEvidence = components["schemas"]["EvidenceResponse"];
export type CheckResult = components["schemas"]["CheckResultResponse"];
export type RunResults = Omit<components["schemas"]["RunResultsResponse"], "run"> & { run: AuditRun };
export type UpstreamComparisonResults = Omit<components["schemas"]["UpstreamComparisonResponse"], "run"> & { run: AuditRun };
export type FactSetSnapshot = components["schemas"]["FactSetSnapshotResponse"];
export type FactConfirmationItem = components["schemas"]["FactConfirmationItem"];
export type HumanTask = components["schemas"]["HumanTaskResponse"];
export type HumanTaskPage = components["schemas"]["HumanTaskListResponse"];
export type ParsedDocumentArtifact = Pick<
  components["schemas"]["ParsedDocumentArtifactResponse"],
  "source_sha256" | "media_type" | "parser_version" | "index_source_sha256" | "renderer_identity" | "pages"
>;

class ApiRequest extends Request {
  readonly cancellationSignal: AbortSignal | null;

  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const { signal, ...requestInit } = init ?? {};
    super(input, requestInit);
    this.cancellationSignal = signal ?? null;
  }

  override toString() {
    return this.url;
  }
}

const client = createClient<ApiPaths>({
  baseUrl: typeof window === "undefined" ? "" : window.location.origin,
  fetch: async (input) => {
    const signal = input instanceof ApiRequest ? input.cancellationSignal : undefined;
    const response = await globalThis.fetch(input, {
      credentials: "same-origin",
      ...(signal ? { signal } : {}),
    });
    // Keep the transport compatible with lightweight Response adapters used by
    // test doubles; browsers always take the native Response branch.
    if (typeof response.headers?.get === "function") return response;
    const body = await response.json();
    return new Response(JSON.stringify(body), {
      status: response.status || 200,
      headers: { "Content-Type": "application/json" },
    });
  },
  Request: ApiRequest,
  headers: { Accept: "application/json" },
});

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
    this.name = "ApiError";
  }
}

function errorFromProblem(status: number, problem: unknown): ApiError {
  let body = problem;
  if (typeof body === "string") {
    try {
      body = JSON.parse(body) as unknown;
    } catch {
      // Keep the generic safe error code for non-JSON responses.
    }
  }
  const code = body && typeof body === "object" && "code" in body && typeof body.code === "string"
    ? body.code
    : "request_failed";
  return new ApiError(status, code);
}

export const AUTH_SESSION_EXPIRED_EVENT = "contract-audit:auth-expired";

function notifyAuthenticationExpired() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT));
  }
}

async function readApiError(response: Response): Promise<ApiError> {
  try {
    return errorFromProblem(response.status, await response.json());
  } catch {
    // Keep server response bodies out of UI copy and logs.
    return new ApiError(response.status, "request_failed");
  }
}

type OpenApiResult<T> = { data?: T; error?: unknown; response: Response };

async function unwrapOpenApi<T>(request: Promise<OpenApiResult<T>>): Promise<T> {
  try {
    const result = await request;
    if (!result.response.ok) {
      if (result.response.status === 401) notifyAuthenticationExpired();
      throw errorFromProblem(result.response.status, result.error);
    }
    if (result.error !== undefined || result.data === undefined) {
      throw new ApiError(0, "invalid_server_response");
    }
    return result.data;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, "network_error");
  }
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    response = await globalThis.fetch(path, {
      credentials: "same-origin",
      ...init,
      headers,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error");
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/v1/auth/")) {
      notifyAuthenticationExpired();
    }
    throw await readApiError(response);
  }
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(0, "invalid_server_response");
  }
}

export interface AuthProfile {
  actor_id: string;
  username: string;
  display_name: string;
  organization_id: string;
  organization_name: string;
  roles: string[];
  permissions: string[];
  must_change_password: boolean;
}

export interface OrganizationUnit {
  id: string;
  parent_id: string | null;
  code: string;
  name: string;
  is_active: boolean;
  created_at: string;
}

export interface PermissionDefinition {
  key: string;
  name: string;
  description: string;
}

export interface AppRole {
  id: string;
  role_key: string;
  name: string;
  description: string;
  is_system: boolean;
  is_active: boolean;
  permissions: string[];
}

export interface AdminUserRole {
  id: string;
  role_key: string;
  name: string;
}

export interface AdminUser {
  actor_id: string;
  username: string;
  display_name: string;
  organization_id: string;
  organization_name: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  roles: AdminUserRole[];
}

export interface LegalEntity {
  legal_entity_id: string;
  legal_name: string;
  unified_social_credit_code: string | null;
  verification_basis: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LegalEntityPage {
  items: LegalEntity[];
  total: number;
  page: number;
  page_size: number;
}

export interface LegalEntityInput {
  legal_name: string;
  unified_social_credit_code: string | null;
  verification_basis: string;
  is_active: boolean;
}

export function getCurrentUser() {
  return requestJson<AuthProfile>("/api/v1/auth/me");
}

export function listLegalEntities(input: {
  query?: string;
  page?: number;
  page_size?: number;
  include_inactive?: boolean;
} = {}, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (input.query) params.set("query", input.query);
  if (input.page) params.set("page", String(input.page));
  if (input.page_size) params.set("page_size", String(input.page_size));
  if (input.include_inactive) params.set("include_inactive", "true");
  const search = params.toString();
  const suffix = search ? `?${search}` : "";
  return requestJson<LegalEntityPage>(`/api/v1/entities${suffix}`, signal ? { signal } : {});
}

export function createLegalEntity(input: LegalEntityInput) {
  return requestJson<LegalEntity>("/api/v1/entities", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateLegalEntity(legalEntityId: string, input: LegalEntityInput) {
  return requestJson<LegalEntity>(`/api/v1/entities/${encodeURIComponent(legalEntityId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function login(username: string, password: string) {
  return requestJson<AuthProfile>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function logout() {
  return requestJson<void>("/api/v1/auth/logout", { method: "POST" });
}

export function changePassword(current_password: string, new_password: string) {
  return requestJson<AuthProfile>("/api/v1/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
}

export async function listAccessAdminData() {
  const [organizations, users, roleData] = await Promise.all([
    requestJson<{ items: OrganizationUnit[] }>("/api/v1/admin/org-units"),
    requestJson<{ items: AdminUser[] }>("/api/v1/admin/users"),
    requestJson<{ roles: AppRole[]; permissions: PermissionDefinition[] }>("/api/v1/admin/roles"),
  ]);
  return { organizations: organizations.items, users: users.items, ...roleData };
}

export function createOrganizationUnit(input: {
  parent_id: string | null;
  code: string;
  name: string;
}) {
  return requestJson<OrganizationUnit>("/api/v1/admin/org-units", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateOrganizationUnit(
  organizationId: string,
  input: { parent_id: string | null; code: string; name: string },
) {
  return requestJson<OrganizationUnit>(`/api/v1/admin/org-units/${encodeURIComponent(organizationId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteOrganizationUnit(organizationId: string) {
  return requestJson<void>(`/api/v1/admin/org-units/${encodeURIComponent(organizationId)}`, {
    method: "DELETE",
  });
}

export function createAccessRole(input: {
  name: string;
  description: string;
  permissions: string[];
}) {
  return requestJson<AppRole>("/api/v1/admin/roles", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateAccessRole(
  roleId: string,
  input: { name: string; description: string; permissions: string[] },
) {
  return requestJson<AppRole>(`/api/v1/admin/roles/${encodeURIComponent(roleId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deactivateAccessRole(roleId: string) {
  return requestJson<void>(`/api/v1/admin/roles/${encodeURIComponent(roleId)}`, {
    method: "DELETE",
  });
}

export function createAccessUser(input: {
  username: string;
  display_name: string;
  organization_id: string;
  role_ids: string[];
  initial_password: string;
}) {
  return requestJson<AdminUser>("/api/v1/admin/users", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateAccessUser(
  actorId: string,
  input: {
    display_name: string;
    organization_id: string;
    role_ids: string[];
    is_active: boolean;
    new_password?: string;
  },
) {
  return requestJson<AdminUser>(`/api/v1/admin/users/${encodeURIComponent(actorId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function listCases(
  options: { page: number; pageSize: number; query: string },
  signal?: AbortSignal,
) {
  return unwrapOpenApi(
    client.GET("/api/v1/cases", {
      params: {
        query: {
          page: options.page,
          page_size: options.pageSize,
          query: options.query || undefined,
        },
      },
      signal,
    }),
  );
}

export function getCase(caseId: string, signal?: AbortSignal) {
  return unwrapOpenApi(
    client.GET("/api/v1/cases/{case_id}", { params: { path: { case_id: caseId } }, signal }),
  );
}

export function listEntityCorrectionCases(
  options: { page: number; pageSize: number; query: string },
  signal?: AbortSignal,
) {
  return unwrapOpenApi(client.GET("/api/v1/cases/legal-entity-correction-cases", {
    params: { query: { page: options.page, page_size: options.pageSize, query: options.query || undefined } },
    signal,
  }));
}

export function getEntityCorrectionContext(caseId: string, signal?: AbortSignal) {
  return unwrapOpenApi(client.GET("/api/v1/cases/{case_id}/legal-entity-correction-context", {
    params: { path: { case_id: caseId } }, signal,
  }));
}

export function createCase(title: string, idempotencyKey: string) {
  return unwrapOpenApi(
    client.POST("/api/v1/cases", {
      params: { header: { "Idempotency-Key": idempotencyKey } },
      body: { title },
    }),
  );
}

export function confirmCaseLegalEntity(
  caseId: string,
  body: { legal_entity_id: string; expected_case_version: number; verification_note: string },
  idempotencyKey: string,
) {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/legal-entity-confirmations", {
      params: { path: { case_id: caseId }, header: { "Idempotency-Key": idempotencyKey } },
      body,
    }),
  );
}

export function correctCaseLegalEntity(
  caseId: string,
  body: { legal_entity_id: string; expected_case_version: number; correction_note: string },
  idempotencyKey: string,
) {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/legal-entity-corrections", {
      params: { path: { case_id: caseId }, header: { "Idempotency-Key": idempotencyKey } },
      body,
    }),
  );
}

export function listCaseLegalEntityHistory(
  caseId: string,
  options: { cursor?: string; limit?: number } = {},
  signal?: AbortSignal,
) {
  return unwrapOpenApi(
    client.GET("/api/v1/cases/{case_id}/legal-entity-history", {
      params: {
        path: { case_id: caseId },
        query: { limit: options.limit ?? 20, cursor: options.cursor },
      },
      signal,
      cache: "no-store",
    }),
  );
}

export function getAuditRun(runId: string, signal?: AbortSignal): Promise<AuditRun> {
  return unwrapOpenApi(
    client.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: runId } }, signal }),
  ) as Promise<AuditRun>;
}

export function listCaseRuns(caseId: string, options: { cursor?: string; limit?: number } = {}, signal?: AbortSignal): Promise<RunPage> {
  return unwrapOpenApi(
    client.GET("/api/v1/cases/{case_id}/runs", {
      params: {
        path: { case_id: caseId },
        query: { limit: options.limit ?? 20, cursor: options.cursor },
      },
      signal,
    }),
  ) as Promise<RunPage>;
}

export function getRunResults(runId: string, signal?: AbortSignal): Promise<RunResults> {
  return unwrapOpenApi(
    client.GET("/api/v1/runs/{run_id}/results", {
      params: { path: { run_id: runId } },
      signal,
      cache: "no-store",
    }),
  ) as Promise<RunResults>;
}

export function confirmRunFacts(
  runId: string,
  body: { confirmations: FactConfirmationItem[]; reason: string },
  idempotencyKey: string,
) {
  return unwrapOpenApi(
    client.POST("/api/v1/runs/{run_id}/fact-set-snapshots", {
      params: {
        path: { run_id: runId },
        header: { "Idempotency-Key": idempotencyKey },
      },
      body,
    }),
  );
}

export function startAuditRun(
  caseId: string,
  inputSnapshotId: string,
  idempotencyKey: string,
): Promise<AuditRun> {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/runs", {
      params: {
        path: { case_id: caseId },
        header: { "Idempotency-Key": idempotencyKey },
      },
      body: { input_snapshot_id: inputSnapshotId },
    }),
  ) as Promise<AuditRun>;
}

export function startUpstreamComparison(
  caseId: string,
  inputSnapshotId: string,
  idempotencyKey: string,
): Promise<AuditRun> {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/upstream-comparisons", {
      params: {
        path: { case_id: caseId },
        header: { "Idempotency-Key": idempotencyKey },
      },
      body: { input_snapshot_id: inputSnapshotId },
    }),
  ) as Promise<AuditRun>;
}

export function getUpstreamComparisonResults(runId: string, signal?: AbortSignal): Promise<UpstreamComparisonResults> {
  return unwrapOpenApi(
    client.GET("/api/v1/runs/{run_id}/upstream-comparison-results", {
      params: { path: { run_id: runId } },
      signal,
      cache: "no-store",
    }),
  ) as Promise<UpstreamComparisonResults>;
}

export function listHumanTasks(options: { status?: HumanTask["status"]; caseId?: string; runId?: string; cursor?: string; limit?: number }, signal?: AbortSignal) {
  return unwrapOpenApi(
    client.GET("/api/v1/human-tasks", {
      params: {
        query: {
          limit: options.limit ?? 20,
          case_id: options.caseId,
          run_id: options.runId,
          status: options.status,
          cursor: options.cursor,
        },
      },
      signal,
    }),
  );
}

export function decideHumanTask(
  taskId: string,
  body: {
    version: number;
    decision: NonNullable<HumanTask["decision"]>;
    reason: string;
  },
  idempotencyKey: string,
) {
  return unwrapOpenApi(
    client.POST("/api/v1/human-tasks/{task_id}/decisions", {
      params: {
        path: { task_id: taskId },
        header: { "Idempotency-Key": idempotencyKey },
      },
      body,
    }),
  );
}

export function getParsedDocumentArtifact(caseId: string, documentVersionId: string, signal?: AbortSignal) {
  const request = client.GET(
    "/api/v1/cases/{case_id}/documents/{document_version_id}/content",
    {
      params: {
        path: { case_id: caseId, document_version_id: documentVersionId },
        query: { kind: "parsed" },
      },
      signal,
      cache: "no-store",
    },
  );
  return request.then(async (result) => {
    if (!result.response.ok) throw await readApiError(result.response);
    if (
      typeof result.data !== "object" ||
      result.data === null ||
      !("source_sha256" in result.data) ||
      typeof result.data.source_sha256 !== "string" ||
      !("index_source_sha256" in result.data) ||
      typeof result.data.index_source_sha256 !== "string" ||
      !("media_type" in result.data) ||
      typeof result.data.media_type !== "string" ||
      !("renderer_identity" in result.data) ||
      (result.data.renderer_identity !== null && typeof result.data.renderer_identity !== "string") ||
      !("pages" in result.data) ||
      !Array.isArray(result.data.pages) ||
      !result.data.pages.every((page) => typeof page === "string")
    ) {
      throw new ApiError(0, "invalid_server_response");
    }
    return result.data;
  }).catch((error: unknown) => {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, "network_error");
  });
}

export async function getOriginalDocument(caseId: string, documentVersionId: string, signal?: AbortSignal) {
  const url = `/api/v1/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(documentVersionId)}/content?kind=original`;
  let response: Response;
  try {
    response = await fetch(url, {
      signal,
      cache: "no-store",
      headers: { Accept: "application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error");
  }
  if (!response.ok) {
    let code = "request_failed";
    try {
      const problem = (await response.json()) as { code?: unknown };
      if (typeof problem.code === "string") code = problem.code;
    } catch {
      // Do not expose raw server content in the viewer.
    }
    throw new ApiError(response.status, code);
  }
  const mediaType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== "application/pdf"
    && mediaType !== "application/vnd.openxmlformats-officedocument.wordprocessingml.document") {
    throw new ApiError(415, "original_preview_unsupported");
  }
  const declaredSize = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredSize) && declaredSize > 30 * 1024 * 1024) {
    throw new ApiError(413, "payload_too_large");
  }
  const blob = await response.blob();
  if (blob.size > 30 * 1024 * 1024) throw new ApiError(413, "payload_too_large");
  return blob;
}

export async function getCanonicalRenderedPdf(
  caseId: string,
  documentVersionId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const url = `/api/v1/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(documentVersionId)}/content?kind=rendered`;
  let response: Response;
  try {
    response = await fetch(url, {
      signal,
      cache: "no-store",
      headers: { Accept: "application/pdf" },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error");
  }
  if (!response.ok) {
    let code = "request_failed";
    try {
      const problem = (await response.json()) as { code?: unknown };
      if (typeof problem.code === "string") code = problem.code;
    } catch {
      // Do not expose raw server content in the viewer.
    }
    throw new ApiError(response.status, code);
  }
  const mediaType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== "application/pdf") throw new ApiError(415, "canonical_render_unsupported");
  const maximumBytes = 120 * 1024 * 1024;
  const declaredSize = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredSize) && declaredSize > maximumBytes) {
    throw new ApiError(413, "payload_too_large");
  }
  const blob = await response.blob();
  if (blob.size > maximumBytes) throw new ApiError(413, "payload_too_large");
  return blob;
}

export function listCaseDocuments(caseId: string, signal?: AbortSignal) {
  return unwrapOpenApi(
    client.GET("/api/v1/cases/{case_id}/documents", {
      params: { path: { case_id: caseId } },
      signal,
    }),
  );
}

export function listCaseDocumentAccessEvents(
  caseId: string,
  options: { cursor?: string; limit?: number } = {},
  signal?: AbortSignal,
) {
  return unwrapOpenApi(
    client.GET("/api/v1/cases/{case_id}/document-access-events", {
      params: {
        path: { case_id: caseId },
        query: { limit: options.limit, cursor: options.cursor },
      },
      signal,
    }),
  );
}

export function selectCaseDocument(
  caseId: string,
  selection: {
    document_version_id: string;
    relation: "main" | "attachment" | "upstream";
    purpose: "contract_review" | "upstream_comparison";
    confirm_project_relation: boolean;
    allow_amount: boolean;
    valid_until: string;
  },
) {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/document-selections", {
      params: { path: { case_id: caseId } },
      body: selection,
    }),
  );
}

export function revokeCaseDocumentSelection(
  caseId: string,
  selectionId: string,
  request: { expected_case_version: number; reason: string },
  idempotencyKey: string,
) {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/document-selections/{selection_id}/revoke", {
      params: {
        path: { case_id: caseId, selection_id: selectionId },
        header: { "Idempotency-Key": idempotencyKey },
      },
      body: request,
    }),
  );
}

export function removeCaseDocument(
  caseId: string,
  documentVersionId: string,
  request: { expected_case_version: number; reason: string },
  idempotencyKey: string,
) {
  return unwrapOpenApi(
    client.DELETE("/api/v1/cases/{case_id}/documents/{document_version_id}", {
      params: {
        path: { case_id: caseId, document_version_id: documentVersionId },
        header: { "Idempotency-Key": idempotencyKey },
      },
      body: request,
    }),
  );
}

export function previewCaseSources(
  caseId: string,
  purpose: SourcePreview["purpose"] = "contract_review",
  signal?: AbortSignal,
) {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/source-previews", {
      params: { path: { case_id: caseId } },
      body: { purpose, task_requires_amount: true },
      signal,
    }),
  );
}

export function freezeCaseSources(caseId: string, preview: SourcePreview) {
  return unwrapOpenApi(
    client.POST("/api/v1/cases/{case_id}/input-snapshots", {
      params: { path: { case_id: caseId } },
      body: {
        purpose: preview.purpose,
        task_requires_amount: true,
        preview_id: preview.preview_id,
        expected_case_version: preview.case_version,
        manifest_sha256: preview.manifest_sha256,
      },
    }),
  );
}

export function uploadCaseDocument(
  caseId: string,
  file: File,
  role: "primary_contract" | "attachment" | "customer_contract",
  idempotencyKey: string,
  onProgress: (percent: number) => void,
) {
  return new Promise<UploadedDocument>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/v1/cases/${encodeURIComponent(caseId)}/documents`);
    request.withCredentials = true;
    request.setRequestHeader("Accept", "application/json");
    request.setRequestHeader("Idempotency-Key", idempotencyKey);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () => reject(new ApiError(0, "network_error"));
    request.onload = () => {
      if (request.status < 200 || request.status >= 300) {
        if (request.status === 401) notifyAuthenticationExpired();
        let code = "request_failed";
        try {
          const problem = JSON.parse(request.responseText) as { code?: unknown };
          if (typeof problem.code === "string") code = problem.code;
        } catch {
          // Keep server response bodies out of UI copy and logs.
        }
        reject(new ApiError(request.status, code));
        return;
      }
      try {
        resolve(JSON.parse(request.responseText) as UploadedDocument);
      } catch {
        reject(new ApiError(0, "invalid_server_response"));
      }
    };
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("role", role);
    request.send(form);
  });
}

export interface SalesReviewScenario {
  scenario_id: string;
  title: string;
  category: string;
  description: string;
  contract_text: string;
  facts: Record<string, unknown>;
  expected_statuses: Record<string, string>;
}

export interface SalesReviewCheck {
  domain: "legal" | "company_policy";
  rule_key: string;
  title: string;
  status: string;
  severity: string;
  summary: string;
  next_action: string;
  evidence: Array<{ quote: string; source: string }>;
  basis: {
    title: string;
    summary: string;
    source_url?: string;
    version: string;
    content_sha256?: string | null;
    demo_data?: boolean;
  };
}

export interface SalesReviewResult {
  evaluation_id: string;
  scenario_id: string;
  evaluated_at: string;
  input_sha256: string;
  facts: Record<string, unknown>;
  contract_text: string;
  legal_checks: SalesReviewCheck[];
  company_checks: SalesReviewCheck[];
  summary: {
    legal_risk_count: number;
    company_deviation_count: number;
    needs_material_count: number;
    no_issue_found_count: number;
    test_data: boolean;
  };
  company_rule_versions: Array<{
    rule_key: string;
    version: number;
    is_demo: boolean;
    content_sha256?: string | null;
  }>;
  limitations: string[];
  expected_statuses: Record<string, string>;
  scenario_verification: {
    matches_expected: boolean;
    mismatches: Array<{ rule_key: string; expected: string; actual: string | null }>;
  };
}

export type CompanyRuleStatus = "draft" | "review" | "published" | "withdrawn";
export type CompanyRuleType = "discount_limit" | "credit_days_limit" | "approval_amount" | "template_deviation_approval";

export interface CompanyRule {
  id: string;
  rule_key: string;
  version: number;
  contract_type: "sales_contract";
  title: string;
  source_name: string;
  source_clause: string;
  rule_type: CompanyRuleType;
  threshold: string | null;
  role_scope: "seller" | "buyer" | "either";
  effective_from: string;
  effective_until: string | null;
  status: CompanyRuleStatus;
  content_sha256: string | null;
  created_by?: string;
  reviewed_by?: string | null;
  published_by?: string | null;
  created_at?: string;
  updated_at?: string;
  published_at?: string | null;
  withdrawn_at?: string | null;
  is_demo?: boolean;
}

export interface CompanyRuleInput {
  rule_key: string;
  title: string;
  source_name: string;
  source_clause: string;
  rule_type: CompanyRuleType;
  threshold: number | null;
  role_scope: "seller" | "buyer" | "either";
  effective_from: string;
  effective_until: string | null;
}

export interface CompanyRuleCatalog {
  items: CompanyRule[];
  legal_rules: Array<{
    rule_key: string;
    title: string;
    article: string;
    summary: string;
    source_url: string;
    version: string;
  }>;
  demo_notice: string;
}

export function listSalesReviewScenarios(signal?: AbortSignal) {
  return requestJson<SalesReviewScenario[]>("/api/v1/sales-contract-review/scenarios", signal ? { signal } : {});
}

export function evaluateSalesReviewScenario(scenarioId: string) {
  return requestJson<SalesReviewResult>("/api/v1/sales-contract-review/evaluate", {
    method: "POST",
    body: JSON.stringify({ scenario_id: scenarioId }),
  });
}

export function getSalesReviewCatalog(signal?: AbortSignal) {
  return requestJson<{
    contract_type: string;
    contract_type_label: string;
    legal_rules: CompanyRuleCatalog["legal_rules"];
    demo_company_rules: CompanyRule[];
    result_statuses: string[];
    test_data: boolean;
  }>("/api/v1/sales-contract-review/catalog", signal ? { signal } : {});
}

export function listCompanyRules(signal?: AbortSignal) {
  return requestJson<CompanyRuleCatalog>("/api/v1/company-rules", signal ? { signal } : {});
}

export function createCompanyRule(input: CompanyRuleInput) {
  return requestJson<CompanyRule>("/api/v1/company-rules", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateCompanyRule(ruleId: string, input: CompanyRuleInput) {
  return requestJson<CompanyRule>(`/api/v1/company-rules/${encodeURIComponent(ruleId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function runCompanyRuleAction(ruleId: string, action: "submit" | "approve" | "reject" | "withdraw" | "new-version", reason: string) {
  return requestJson<CompanyRule>(`/api/v1/company-rules/${encodeURIComponent(ruleId)}/${action}`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function listCompanyRuleEvents(ruleId: string) {
  return requestJson<{ items: Array<{ id: string; actor_id: string; event_type: string; note: string; snapshot: Record<string, unknown>; created_at: string }> }>(
    `/api/v1/company-rules/${encodeURIComponent(ruleId)}/events`,
  );
}
