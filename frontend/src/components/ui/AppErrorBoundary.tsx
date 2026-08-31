import { Component, type ErrorInfo, type ReactNode } from "react";
import { CircleAlert, RotateCcw } from "lucide-react";

interface AppErrorBoundaryProps { children: ReactNode; }
interface AppErrorBoundaryState { failed: boolean; }

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): AppErrorBoundaryState { return { failed: true }; }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ui_render_failed", { errorType: error.name, componentStack: info.componentStack });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <main className="error-boundary"><CircleAlert size={22} /><p className="eyebrow">UI RENDER ERROR</p><h1>界面加载失败</h1><p>业务数据没有被修改。刷新页面可重新初始化前端状态。</p><button className="secondary-button" onClick={() => window.location.reload()}><RotateCcw size={15} />刷新页面</button></main>;
  }
}
