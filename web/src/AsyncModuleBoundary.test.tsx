import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AsyncModuleBoundary } from "./AsyncModuleBoundary";

function BrokenModule(): never {
  throw new Error("chunk request failed");
}

describe("AsyncModuleBoundary", () => {
  it("keeps normal route content unchanged", () => {
    render(<AsyncModuleBoundary pageName="审核详情"><div>已载入</div></AsyncModuleBoundary>);
    expect(screen.getByText("已载入")).toBeInTheDocument();
  });

  it("shows a safe recovery path when a lazy module fails", () => {
    const retry = vi.fn();
    render(
      <AsyncModuleBoundary pageName="审核详情" onRetry={retry}>
        <BrokenModule />
      </AsyncModuleBoundary>,
    );

    expect(screen.getByText("审核详情暂时无法载入")).toBeInTheDocument();
    expect(screen.getByText(/已提交的数据以服务端状态为准/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新载入页面" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
