import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { CreateCaseDialog } from "./CreateCaseDialog";

vi.mock("./api", async (original) => ({ ...await original<typeof api>(), createCase: vi.fn(), uploadCaseDocument: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("上传主合同入口", () => {
  it("从文件名预填案件名称，上传完成后进入案件；上传重试不重复建案", async () => {
    vi.mocked(api.createCase).mockResolvedValue({ case_id: "case" } as api.CaseRecord);
    vi.mocked(api.uploadCaseDocument).mockRejectedValueOnce(new api.ApiError(0, "network_error")).mockResolvedValue({} as api.UploadedDocument);
    const onCreated = vi.fn();
    render(<ConfigProvider><CreateCaseDialog open uploadFirst onClose={vi.fn()} onCreated={onCreated} /></ConfigProvider>);
    const file = new File(["synthetic contract"], "服务采购合同.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("待审主合同"), { target: { files: [file] } });
    expect(screen.getByLabelText("案件名称")).toHaveValue("服务采购合同");
    fireEvent.click(screen.getByRole("button", { name: "创建案件并上传主合同" }));
    expect(await screen.findByText(/主合同上传未完成，案件已保留/)).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
    const first = vi.mocked(api.uploadCaseDocument).mock.calls[0];
    fireEvent.click(screen.getByRole("button", { name: "使用同一请求重试" }));
    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith("case"));
    expect(api.createCase).toHaveBeenCalledTimes(1);
    expect(api.uploadCaseDocument).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.uploadCaseDocument).mock.calls[1].slice(0, 4)).toEqual(first.slice(0, 4));
  });

  it("未选择文件或文件类型不支持时不创建案件", async () => {
    render(<ConfigProvider><CreateCaseDialog open uploadFirst onClose={vi.fn()} onCreated={vi.fn()} /></ConfigProvider>);
    fireEvent.change(screen.getByLabelText("案件名称"), { target: { value: "主合同" } });
    fireEvent.click(screen.getByRole("button", { name: "创建案件并上传主合同" }));
    expect(await screen.findByText("请先选择一份主合同。")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("待审主合同"), { target: { files: [new File(["x"], "合同.txt")] } });
    expect(screen.getByText("仅支持 PDF 或 DOCX 文件。")).toBeInTheDocument();
    expect(api.createCase).not.toHaveBeenCalled();
  });
});
