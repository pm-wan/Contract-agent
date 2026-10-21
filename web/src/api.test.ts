import { afterEach, describe, expect, it, vi } from "vitest";
import { freezeCaseSources, getCanonicalRenderedPdf, getOriginalDocument, listCaseDocumentAccessEvents, listCaseRuns, listHumanTasks, previewCaseSources, uploadCaseDocument } from "./api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL) {
  const raw = input instanceof Request ? input.url : String(input);
  const url = new URL(raw, window.location.origin);
  return `${url.pathname}${url.search}`;
}

afterEach(() => vi.unstubAllGlobals());

describe("canonical DOCX rendering API", () => {
  it("fetches the canonical PDF without caching and validates its media type", async () => {
    const pdf = new TextEncoder().encode("%PDF-1.7\n");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(requestUrl(input)).toBe(
        "/api/v1/cases/case%2F1/documents/doc%2F1/content?kind=rendered",
      );
      expect(init?.cache).toBe("no-store");
      expect(new Headers(init?.headers).get("Accept")).toBe("application/pdf");
      return new Response(pdf, { headers: { "Content-Type": "application/pdf" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    const rendered = await getCanonicalRenderedPdf("case/1", "doc/1");

    expect(rendered.type).toBe("application/pdf");
    expect(rendered.size).toBe(pdf.byteLength);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("rejects a non-PDF response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not pdf", {
      headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
    })));

    await expect(getCanonicalRenderedPdf("case-1", "doc-1")).rejects.toMatchObject({
      status: 415,
      code: "canonical_render_unsupported",
    });
  });
});

describe("uploadCaseDocument", () => {
  it("sends the selected role with an idempotency key and reports progress", async () => {
    const response = {
      document_version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      file_name: "采购合同.pdf",
      role: "primary_contract",
      media_type: "application/pdf",
      size_bytes: 12,
      content_hash: "a".repeat(64),
      parse_status: "completed",
      warnings: [],
      case_version: 2,
      document_status: "staged",
      authorization_status: "not_granted_by_upload",
    };
    const progress = vi.fn();
    const requestRecord: { url?: string; headers: Record<string, string>; body?: FormData } = {
      headers: {},
    };

    class FakeXMLHttpRequest {
      upload = {
        onprogress: null as ((event: ProgressEvent) => void) | null,
      };
      status = 201;
      responseText = JSON.stringify(response);
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;

      open(method: string, url: string) {
        expect(method).toBe("POST");
        requestRecord.url = url;
      }

      setRequestHeader(name: string, value: string) {
        requestRecord.headers[name] = value;
      }

      send(body: FormData) {
        requestRecord.body = body;
        this.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: 4 } as ProgressEvent);
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);

    const file = new File(["contract"], "采购合同.pdf", { type: "application/pdf" });
    const uploaded = await uploadCaseDocument(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      file,
      "primary_contract",
      "upload-key-1",
      progress,
    );

    expect(requestRecord.url).toBe("/api/v1/cases/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/documents");
    expect(requestRecord.headers).toEqual({
      Accept: "application/json",
      "Idempotency-Key": "upload-key-1",
    });
    const sentFile = requestRecord.body?.get("file") as File;
    expect(sentFile.name).toBe(file.name);
    expect(sentFile.size).toBe(file.size);
    expect(sentFile.type).toBe(file.type);
    expect(requestRecord.body?.get("role")).toBe("primary_contract");
    expect(progress).toHaveBeenCalledWith(75);
    expect(uploaded).toEqual(response);
  });
});

describe("case source snapshot API", () => {
  it("previews the contract-review amount scope and freezes the exact preview", async () => {
    const preview = {
      preview_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      case_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      purpose: "contract_review" as const,
      case_version: 4,
      manifest_sha256: "c".repeat(64),
      items: [{
        selection_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        relation: "main",
        status: "ready" as const,
        document_version_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        source_sha256: "f".repeat(64),
      }],
    };
    const frozen = {
      input_snapshot_id: "11111111-1111-4111-8111-111111111111",
      case_id: preview.case_id,
      purpose: "contract_review" as const,
      case_version: 5,
      manifest_sha256: preview.manifest_sha256,
      document_version_ids: [preview.items[0].document_version_id],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(preview, 201))
      .mockResolvedValueOnce(jsonResponse(frozen));
    vi.stubGlobal("fetch", fetchMock);

    const result = await previewCaseSources(preview.case_id);
    const snapshot = await freezeCaseSources(preview.case_id, result);

    expect(requestUrl(fetchMock.mock.calls[0][0] as RequestInfo)).toBe(`/api/v1/cases/${preview.case_id}/source-previews`);
    expect(await (fetchMock.mock.calls[0][0] as Request).clone().json()).toEqual({
      purpose: "contract_review",
      task_requires_amount: true,
    });
    expect(requestUrl(fetchMock.mock.calls[1][0] as RequestInfo)).toBe(`/api/v1/cases/${preview.case_id}/input-snapshots`);
    expect(await (fetchMock.mock.calls[1][0] as Request).clone().json()).toEqual({
      purpose: "contract_review",
      task_requires_amount: true,
      preview_id: preview.preview_id,
      expected_case_version: 4,
      manifest_sha256: "c".repeat(64),
    });
    expect(snapshot).toEqual(frozen);
  });
});

describe("human task queue API", () => {
  it("requests role-filtered open tasks using the server cursor", async () => {
    const responseBody = { items: [], next_cursor: "opaque-cursor" };
    const controller = new AbortController();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(requestUrl(input)).toContain("/api/v1/human-tasks?");
      expect(init?.signal).toBe(controller.signal);
      return jsonResponse(responseBody);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listHumanTasks(
      { status: "open", limit: 20, cursor: "opaque cursor/next" },
      controller.signal,
    );

    expect(requestUrl(fetchMock.mock.calls[0][0] as RequestInfo)).toBe(
      "/api/v1/human-tasks?limit=20&status=open&cursor=opaque%20cursor%2Fnext",
    );
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result).toEqual(responseBody);
  });
});

describe("case run history API", () => {
  it("uses the case-scoped list route and opaque cursor", async () => {
    const body = { items: [], next_cursor: null };
    const controller = new AbortController();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(requestUrl(input)).toContain("/api/v1/cases/");
      expect(init?.signal).toBe(controller.signal);
      return jsonResponse(body);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(listCaseRuns("case/id", { cursor: "opaque cursor" }, controller.signal)).resolves.toEqual(body);
    expect(requestUrl(fetchMock.mock.calls[0][0] as RequestInfo)).toBe(
      "/api/v1/cases/case%2Fid/runs?limit=20&cursor=opaque%20cursor",
    );
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("document access audit API", () => {
  it("uses the case-scoped audit route and URL-encodes opaque cursors", async () => {
    const body = { items: [], next_cursor: null };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(requestUrl(input)).toContain("/api/v1/cases/");
      return jsonResponse(body);
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(listCaseDocumentAccessEvents("case/id", { limit: 20, cursor: "audit cursor/2" })).resolves.toEqual(body);
    expect(requestUrl(fetchMock.mock.calls[0][0] as RequestInfo)).toBe(
      "/api/v1/cases/case%2Fid/document-access-events?limit=20&cursor=audit%20cursor%2F2",
    );
  });
});

describe("original document API", () => {
  it("fetches the case-scoped PDF original without caching", async () => {
    const pdf = new Blob(["%PDF-1.7"], { type: "application/pdf" });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/cases/case-id/documents/doc-id/content?kind=original");
      expect(init?.cache).toBe("no-store");
      expect(new Headers(init?.headers).get("Accept")).toContain("application/pdf");
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "Content-Type": "application/pdf", "Content-Length": String(pdf.size) }),
        blob: async () => pdf,
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(getOriginalDocument("case-id", "doc-id")).resolves.toBe(pdf);

    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }),
      blob: async () => new Blob(["docx"]),
    }) as Response));
    await expect(getOriginalDocument("case-id", "doc-id")).resolves.toBeInstanceOf(Blob);

    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "text/html" }),
      blob: async () => new Blob(["html"]),
    }) as Response));
    await expect(getOriginalDocument("case-id", "doc-id")).rejects.toMatchObject({ status: 415, code: "original_preview_unsupported" });
  });
});
