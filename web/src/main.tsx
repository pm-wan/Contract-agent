import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { antTheme, applyCssTokens } from "./design/tokens";
import "./styles.css";

applyCssTokens();
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ ...antTheme, token: { ...antTheme.token, motion: !window.matchMedia("(prefers-reduced-motion: reduce)").matches } }}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
