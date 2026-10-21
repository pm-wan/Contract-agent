import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Layout,
  Menu,
  Pagination,
  Result,
  Table,
  Tag,
  Typography,
} from "antd";
import AuditOutlined from "@ant-design/icons/es/icons/AuditOutlined";
import BankOutlined from "@ant-design/icons/es/icons/BankOutlined";
import FileSearchOutlined from "@ant-design/icons/es/icons/FileSearchOutlined";
import FolderOpenOutlined from "@ant-design/icons/es/icons/FolderOpenOutlined";
import HomeOutlined from "@ant-design/icons/es/icons/HomeOutlined";
import ProfileOutlined from "@ant-design/icons/es/icons/ProfileOutlined";
import RightOutlined from "@ant-design/icons/es/icons/RightOutlined";
import SearchOutlined from "@ant-design/icons/es/icons/SearchOutlined";
import LogoutOutlined from "@ant-design/icons/es/icons/LogoutOutlined";

import { Link, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { AsyncModuleBoundary } from "./AsyncModuleBoundary";
import { AuthProvider, useAuth } from "./AuthContext";
import { BrandMark } from "./BrandMark";
import { ApiError, listCases, type CaseRecord } from "./api";
import { caseStatusLabel, formatDate } from "./case-format";
import { ChangePasswordPage } from "./ChangePasswordPage";
import { LoginPage } from "./LoginPage";
import { RouteLoading, SectionHeading } from "./shared-ui";

const CaseDetailPage = lazy(() =>
  import("./CaseDetailPage").then(({ CaseDetailPage: Page }) => ({ default: Page })),
);
const RunResultsPage = lazy(() =>
  import("./RunResultsPage").then(({ RunResultsPage: Page }) => ({ default: Page })),
);
const UpstreamComparisonResultsPage = lazy(() =>
  import("./UpstreamComparisonResultsPage").then(({ UpstreamComparisonResultsPage: Page }) => ({ default: Page })),
);
const HumanTasksPage = lazy(() =>
  import("./HumanTasksPage").then(({ HumanTasksPage: Page }) => ({ default: Page })),
);
const EntityCorrectionQueuePage = lazy(() =>
  import("./EntityCorrectionPages").then(({ EntityCorrectionQueuePage: Page }) => ({ default: Page })),
);
const EntityCorrectionPage = lazy(() =>
  import("./EntityCorrectionPages").then(({ EntityCorrectionPage: Page }) => ({ default: Page })),
);
const WorkbenchPage = lazy(() =>
  import("./PrototypePages").then(({ WorkbenchPage: Page }) => ({ default: Page })),
);
const ResultsPage = lazy(() =>
  import("./PrototypePages").then(({ ResultsPage: Page }) => ({ default: Page })),
);
const CreateCaseDialog = lazy(() =>
  import("./CreateCaseDialog").then(({ CreateCaseDialog: Page }) => ({ default: Page })),
);
const AdminAccessPage = lazy(() =>
  import("./AdminAccessPage").then(({ AdminAccessPage: Page }) => ({ default: Page })),
);
const EntityManagementPage = lazy(() =>
  import("./EntityManagementPage").then(({ EntityManagementPage: Page }) => ({ default: Page })),
);
const CompanyRulesPage = lazy(() =>
  import("./CompanyRulesPage").then(({ CompanyRulesPage: Page }) => ({ default: Page })),
);
const { Header, Sider, Content } = Layout;
const { Text } = Typography;

function Shell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const contentRef = useRef<HTMLDivElement>(null);
  const activeKey = location.pathname === "/" ? "home" : location.pathname.startsWith("/tasks") ? "tasks"
    : location.pathname.startsWith("/company-rules") ? "company-rules"
    : location.pathname.startsWith("/entities") ? "entities"
    : location.pathname.startsWith("/entity-corrections") ? "entity-corrections"
    : location.pathname.startsWith("/admin/access") ? "admin-access" : "cases";
  const hiddenRoute = location.pathname.startsWith("/tasks") || location.pathname.startsWith("/admin/access");
  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = 0;
      contentRef.current.scrollLeft = 0;
    }
  }, [location.pathname]);
  return (
    <Layout className="app-shell">
      <Sider breakpoint="lg" collapsedWidth="0" width={224} className="app-sider">
        <div className="brand"><BrandMark /><span><strong>合同审查</strong><small>证据工作台</small></span></div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[activeKey]}
          onClick={({ key }) => navigate(key === "home" ? "/" : key === "cases" ? "/cases" : key === "entities" ? "/entities" : key === "tasks" ? "/tasks" : key === "admin-access" ? "/admin/access" : key === "company-rules" ? "/company-rules" : "/entity-corrections")}
          items={[
            { key: "home", icon: <HomeOutlined />, label: "首页" },
            { key: "cases", icon: <FolderOpenOutlined />, label: "案件列表" },
            { key: "entity-corrections", icon: <AuditOutlined />, label: "主体纠错" },
            
            { key: "entities", icon: <BankOutlined />, label: "主体管理" },
            
            { key: "company-rules", icon: <ProfileOutlined />, label: "公司知识库" },
          ]}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <span className="app-header-title">合同审查与证据追溯</span>
          <div className="app-header-account">
            <span className="app-header-profile" aria-label="当前账号">
              <strong>{auth.user?.display_name || auth.user?.username || "当前用户"}</strong>
              {auth.user?.organization_name && <small>{auth.user.organization_name}</small>}
            </span>
            <Button className="app-header-signout" type="text" icon={<LogoutOutlined />} onClick={() => { void auth.signOut(); }}>退出登录</Button>
          </div>
        </Header>
        <Content ref={contentRef} className="app-content">
          <div className="content-inner">{hiddenRoute ? <Navigate to="/cases" replace /> : children}</div>
        </Content>
      </Layout>
    </Layout>
  );
}

function HomePage() {
  const navigate = useNavigate();
  useEffect(() => { document.title = "首页 — 合同审查工作台"; }, []);
  return <main className="home-page">
    <span className="eyebrow">合同审查工作台</span>
    <Typography.Title level={1}>从一份主合同开始</Typography.Title>
    <Typography.Paragraph className="home-intro" type="secondary">按当前准备程度选择审核方式。案件创建后，材料、主体与审核记录都在同一处管理。</Typography.Paragraph>
    <div className="home-entry-grid">
      <Card className="home-entry-card home-entry-quick">
        <FileSearchOutlined className="home-entry-icon" />
        <Typography.Title level={3}>快速审核</Typography.Title>
        <Typography.Paragraph>上传一份主合同，自动创建案件并进入审核。先看这份合同本身，不要求预先整理关联合同和背景材料。</Typography.Paragraph>
        <Typography.Paragraph className="home-entry-footnote" type="secondary">模型理解合同，检索法律与公司制度，逐项展示原文依据和比对结果。</Typography.Paragraph>
        <Button type="primary" size="large" onClick={() => navigate("/cases?start=quick")}>上传主合同</Button>
      </Card>
      <Card className="home-entry-card">
        <FolderOpenOutlined className="home-entry-icon" />
        <Typography.Title level={3}>准备完整审核</Typography.Title>
        <Typography.Paragraph>先建立案件，再梳理签约主体、背景信息、附件和关联合同，准备好后再发起审核。</Typography.Paragraph>
        <Typography.Paragraph className="home-entry-footnote" type="secondary">背景材料与关联合同需由审核人逐项选择和授权。</Typography.Paragraph>
        <Button size="large" onClick={() => navigate("/cases?start=prepare")}>创建并整理案件</Button>
      </Card>
    </div>
    <div className="home-case-link"><Text type="secondary">已有进行中的工作？</Text> <Link to="/cases">查看案件列表</Link></div>
  </main>;
}

function CasesPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const searchState = params.toString();
  const [inputQuery, setInputQuery] = useState(params.get("query") ?? "");
  const [isComposing, setIsComposing] = useState(false);
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const pageSize = Math.min(100, Math.max(1, Number(params.get("page_size") ?? 20) || 20));
  const query = params.get("query") ?? "";
  const [result, setResult] = useState<{ items: CaseRecord[]; total: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [createDialogMounted, setCreateDialogMounted] = useState(false);
  const [uploadFirst, setUploadFirst] = useState(false);

  useEffect(() => setInputQuery(query), [query]);
  useEffect(() => {
    const next = new URLSearchParams(searchState);
    const start = next.get("start");
    if (start !== "quick" && start !== "prepare") return;
    setUploadFirst(start === "quick");
    setCreateDialogMounted(true);
    setCreateOpen(true);
    next.delete("start");
    setParams(next, { replace: true });
  }, [searchState, setParams]);
  useEffect(() => {
    if (isComposing || inputQuery.trim() === query) return;
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(searchState);
      const normalized = inputQuery.trim();
      if (normalized) next.set("query", normalized);
      else next.delete("query");
      next.set("page", "1");
      setParams(next, { replace: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [inputQuery, isComposing, query, searchState, setParams]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    listCases({ page, pageSize, query }, controller.signal)
      .then((response) => {
        setResult({ items: response.items, total: response.total });
        if (response.page !== page) {
          const next = new URLSearchParams(searchState);
          next.set("page", String(response.page));
          setParams(next, { replace: true });
        }
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [page, pageSize, query, retryCount, searchState, setParams]);

  const updatePage = (nextPage: number, nextSize: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(nextPage));
    next.set("page_size", String(nextSize));
    setParams(next);
  };

  useEffect(() => {
    document.title = "案件列表 — 合同审查工作台";
  }, []);
  useEffect(() => {
    const heading = document.querySelector<HTMLElement>(".content-inner h2");
    heading?.setAttribute("tabindex", "-1");
    heading?.focus({ preventScroll: true });
  }, []);

  const errorMessage = error?.status === 401
    ? { title: "登录状态已失效", description: "请返回登录页面重新登录后继续。" }
    : error?.status === 403
      ? { title: "无权查看案件", description: "当前身份没有可查看的案件权限。不会展示受限案件信息。" }
      : { title: "案件列表暂时无法读取", description: "检查服务连接后重试；当前搜索条件会保留。" };

  return <>
    <SectionHeading eyebrow="合同 / 审核记录" title="案件列表" extra={<div className="review-entry-actions"><Button onClick={() => { setUploadFirst(false); setCreateDialogMounted(true); setCreateOpen(true); }}>新建案件</Button><Button type="primary" onClick={() => { setUploadFirst(true); setCreateDialogMounted(true); setCreateOpen(true); }}>上传合同审核</Button></div>} />
    <Text type="secondary">上传一份主合同，发起审核后查看结果与原文证据。</Text>
    <div className="toolbar">
      <Input
        prefix={<SearchOutlined />}
        placeholder="搜索案件名称"
        aria-label="搜索案件名称"
        value={inputQuery}
        onChange={(event) => setInputQuery(event.target.value)}
        onCompositionStart={() => setIsComposing(true)}
        onCompositionEnd={(event) => { setIsComposing(false); setInputQuery(event.currentTarget.value); }}
        allowClear
        className="search-field"
      />
      <Text type="secondary" aria-live="polite">{loading ? "正在读取案件…" : `共 ${result?.total ?? 0} 个案件`}</Text>
    </div>
    {error && <Alert className="page-error" type={error.status === 403 ? "warning" : "error"} showIcon title={errorMessage.title} description={errorMessage.description} action={error.status !== 403 && <Button onClick={() => setRetryCount((count) => count + 1)}>重试</Button>} />}
    {!error && result?.total === 0 && !loading ? <Empty className="case-empty" description={query ? "没有匹配案件" : "当前没有可查看的案件"}>
      {query ? <Button onClick={() => { setInputQuery(""); setParams({}); }}>清除搜索</Button> : <div className="review-entry-actions"><Button type="primary" onClick={() => { setUploadFirst(true); setCreateDialogMounted(true); setCreateOpen(true); }}>上传第一份主合同</Button><Button onClick={() => { setUploadFirst(false); setCreateDialogMounted(true); setCreateOpen(true); }}>创建第一个案件</Button></div>}
    </Empty> : <>
      <Table<CaseRecord>
        aria-label="案件列表"
        rowKey="case_id"
        loading={loading}
        pagination={false}
        dataSource={result?.items ?? []}
        columns={[
          { title: "案件名称", dataIndex: "title", render: (value: string, record) => <Link className="table-link" to={`/cases/${record.case_id}`}>{value}<RightOutlined /></Link> },
          { title: "签约主体", dataIndex: "legal_entity_name", render: (value: string | null | undefined, record) => value ? <span>{value}</span> : record.legal_entity_id ? <Text type="secondary">主体未登记</Text> : <Text type="secondary">待确认</Text> },
          { title: "案件编号", dataIndex: "case_id", render: (value: string) => <Text code className="case-id">{value}</Text> },
          { title: "案件状态", dataIndex: "status", render: (value: string) => <Tag color="processing">{caseStatusLabel(value)}</Tag> },
          { title: "版本", dataIndex: "version", render: (value: number) => `v${value}` },
          { title: "创建时间", dataIndex: "created_at", render: (value: string) => formatDate(value) },
        ]}
        locale={{ emptyText: <Empty description="没有当前页案件" /> }}
        scroll={{ x: 920 }}
      />
      {(result?.total ?? 0) > 0 && <Pagination
        className="case-pagination"
        aria-label="案件列表分页"
        current={page}
        pageSize={pageSize}
        pageSizeOptions={[10, 20, 50, 100]}
        total={result?.total ?? 0}
        showSizeChanger
        showTotal={(total, range) => `第 ${range[0]}–${range[1]} 项，共 ${total} 个案件`}
        onChange={updatePage}
      />}
    </>}
    {createDialogMounted && <AsyncModuleBoundary pageName="新建案件"><Suspense fallback={<RouteLoading title="新建案件" />}>
      <CreateCaseDialog
        key={String(uploadFirst)}
        uploadFirst={uploadFirst}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(caseId) => { setCreateOpen(false); navigate(`/cases/${caseId}?view=background`); }}
      />
    </Suspense></AsyncModuleBoundary>}
  </>;
}

function NotFoundPage() {
  useEffect(() => {
    document.title = "页面不存在 — 合同审查工作台";
  }, []);
  return <Result status="404" title="页面不存在" extra={<Link to="/cases">返回案件列表</Link>} />;
}

function WorkbenchRoutes() {
  return <Shell><Routes>
    <Route path="/" element={<HomePage />} />
    <Route path="/cases" element={<CasesPage />} />
    <Route path="/entities" element={<AsyncModuleBoundary pageName="主体台账"><Suspense fallback={<RouteLoading title="主体台账" />}><EntityManagementPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/entity-corrections" element={<AsyncModuleBoundary pageName="主体纠错队列"><Suspense fallback={<RouteLoading title="主体纠错队列" />}><EntityCorrectionQueuePage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/entity-corrections/:caseId" element={<AsyncModuleBoundary pageName="主体纠错"><Suspense fallback={<RouteLoading title="主体纠错" />}><EntityCorrectionPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/cases/demo" element={<AsyncModuleBoundary pageName="案件工作台"><Suspense fallback={<RouteLoading title="案件工作台" />}><WorkbenchPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/cases/demo/results" element={<AsyncModuleBoundary pageName="结果交互原型"><Suspense fallback={<RouteLoading title="结果交互原型" />}><ResultsPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/runs/:runId" element={<AsyncModuleBoundary pageName="审核详情"><Suspense fallback={<RouteLoading title="审核详情" />}><RunResultsPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/comparisons/:runId" element={<AsyncModuleBoundary pageName="上下游比对详情"><Suspense fallback={<RouteLoading title="上下游比对详情" />}><UpstreamComparisonResultsPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/cases/:caseId" element={<AsyncModuleBoundary pageName="案件详情"><Suspense fallback={<RouteLoading title="案件详情" />}><CaseDetailPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/tasks" element={<AsyncModuleBoundary pageName="人工任务"><Suspense fallback={<RouteLoading title="人工任务" />}><HumanTasksPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/admin/access" element={<AsyncModuleBoundary pageName="组织与权限"><Suspense fallback={<RouteLoading title="组织与权限" />}><AdminAccessPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="/sales-contract-review" element={<Navigate to="/cases" replace />} />
    <Route path="/company-rules" element={<AsyncModuleBoundary pageName="公司知识库"><Suspense fallback={<RouteLoading title="公司知识库" />}><CompanyRulesPage /></Suspense></AsyncModuleBoundary>} />
    <Route path="*" element={<NotFoundPage />} />
  </Routes></Shell>;
}

export function App() {
  return <AuthProvider><AuthenticationGate /></AuthProvider>;
}

function AuthenticationGate() {
  const auth = useAuth();
  const location = useLocation();
  if (auth.loading) return <RouteLoading title="验证登录" />;
  if (auth.error) return <main className="auth-service-error"><Result status="error" title="认证服务暂不可用" subTitle={auth.error} extra={<Button type="primary" onClick={() => { void auth.refresh(); }}>重试</Button>} /></main>;
  if (!auth.user) {
    return <Routes>
      <Route path="/login" element={<LoginPage onAuthenticated={auth.acceptLogin} />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>;
  }
  if (location.pathname === "/login") return <Navigate to="/" replace />;
  if (auth.user.must_change_password) {
    return <Routes>
      <Route path="/change-password" element={<ChangePasswordPage onAuthenticated={auth.acceptLogin} />} />
      <Route path="*" element={<Navigate to="/change-password" replace />} />
    </Routes>;
  }
  if (location.pathname === "/change-password") return <Navigate to="/" replace />;
  return <WorkbenchRoutes />;
}
