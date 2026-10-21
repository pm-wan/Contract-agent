import React from "react";
import ReactDOM from "react-dom/client";
import { Button, ConfigProvider, Result, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "../src/AuthContext";
import { ChangePasswordPage } from "../src/ChangePasswordPage";
import { LoginPage } from "../src/LoginPage";
import { antTheme, applyCssTokens } from "../src/design/tokens";
import { AlternateWorkbench } from "./Workbench";
import "../src/styles.css";
import "./style.css";

applyCssTokens();

function AlternateAuthenticationGate() {
  const auth = useAuth();
  const location = useLocation();

  if (auth.loading) {
    return <main className="auth-service-error"><Spin size="large" tip="正在验证登录状态…" /></main>;
  }
  if (auth.error) {
    return <main className="auth-service-error"><Result
      status="error"
      title="认证服务暂不可用"
      subTitle={auth.error}
      extra={<Button type="primary" onClick={() => { void auth.refresh(); }}>重试</Button>}
    /></main>;
  }
  if (!auth.user) return <LoginPage onAuthenticated={auth.acceptLogin} />;
  if (auth.user.must_change_password) {
    return <ChangePasswordPage onAuthenticated={auth.acceptLogin} />;
  }
  if (location.pathname === "/login" || location.pathname === "/change-password") {
    return <Navigate to="/" replace />;
  }
  return <AlternateWorkbench user={auth.user} onSignOut={() => { void auth.signOut(); }} />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ ...antTheme, token: { ...antTheme.token, motion: !window.matchMedia("(prefers-reduced-motion: reduce)").matches } }}>
      <BrowserRouter basename="/alternate">
        <AuthProvider>
          <AlternateAuthenticationGate />
        </AuthProvider>
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
