import { Component, type ReactNode } from "react";
import { Button, Result } from "antd";

type AsyncModuleBoundaryProps = {
  children: ReactNode;
  pageName: string;
  onRetry?: () => void;
};

type AsyncModuleBoundaryState = {
  failed: boolean;
};

export class AsyncModuleBoundary extends Component<
  AsyncModuleBoundaryProps,
  AsyncModuleBoundaryState
> {
  state: AsyncModuleBoundaryState = { failed: false };

  static getDerivedStateFromError(): AsyncModuleBoundaryState {
    return { failed: true };
  }

  componentDidCatch() {
    document.title = "页面加载失败 — 合同审查工作台";
  }

  private retry = () => {
    if (this.props.onRetry) this.props.onRetry();
    else window.location.reload();
  };

  render() {
    if (this.state.failed) {
      return <Result
        status="error"
        title={`${this.props.pageName}暂时无法载入`}
        subTitle="页面资源没有完整加载。重新载入页面后可再次尝试；已提交的数据以服务端状态为准。"
        extra={<Button onClick={this.retry}>重新载入页面</Button>}
      />;
    }
    return this.props.children;
  }
}
