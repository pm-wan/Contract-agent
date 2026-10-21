import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CaseDocument } from "./api";
import { DocumentParseStatus } from "./DocumentParseStatus";

describe("DocumentParseStatus", () => {
  it("explains a safe parse retry without exposing internal diagnostics", () => {
    render(<DocumentParseStatus status="queued" errorCode="docx_renderer_timeout" />);

    expect(screen.getByText("等待解析")).toBeInTheDocument();
    expect(screen.getByText(/上次尝试未完成，等待再次处理。.*DOCX 页面转换超时/)).toBeInTheDocument();
    expect(screen.queryByText(/private\/path|renderer stack/i)).not.toBeInTheDocument();
  });

  it("does not render an error code outside the API allowlist", () => {
    const untrustedCode = "/private/path/secret.docx: raw converter diagnostic" as CaseDocument["parse_error_code"];
    render(<DocumentParseStatus status="queued" errorCode={untrustedCode} />);

    expect(screen.getByText("等待解析")).toBeInTheDocument();
    expect(screen.queryByText(/private\/path|raw converter diagnostic/i)).not.toBeInTheDocument();
  });
});
