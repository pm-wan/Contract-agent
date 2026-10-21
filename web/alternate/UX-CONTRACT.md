# 案牍备选工作台 UX 契约

本契约只适用于 `alternate/index.html` 并行入口。产品事实来自 [PRD](../../docs/development/01-product/PRD.md)、[权限模型](../../docs/development/02-domain/权限模型.md)、[OpenAPI](../../docs/development/04-interfaces/OpenAPI.yaml) 和主站 [UX 契约](../UX-CONTRACT.md)。视觉 token 见本目录 `DESIGN.md`；共享 API 客户端和服务器授权仍由 `web/src/api.ts` 与后端拥有。

## 范围

- 目标用户为中国企业合同审阅人员；界面语言为简体中文，API 时间显示使用 `Asia/Shanghai`。
- 当前实现提供真实案件台账、案件创建、案件详情、主体确认与文档上传；其它人工任务和结果浏览仍由主站入口负责。
- `/alternate/` 是平行入口，不替换 `/`，不改变现有页面和 API 行为。
- 本入口不直接创建运行；“继续审核此主合同”直达主入口 `/cases/{caseId}`，由主入口完成必要确认并发起显式开启的开发审核。服务端响应是身份、权限、状态和内容唯一真源。

## 行为契约

| 操作 | 成功 | 失败/恢复 | 来源 |
|---|---|---|---|
| 打开案件台账 | 服务端分页，URL 保留 query/page/page_size | 首屏错误保留条件并可重试；401/403/404 不显示受限案卷 | OpenAPI `listCases` |
| 搜索案件 | 300ms 防抖，IME composition 结束后提交；清空按钮立即清空并聚焦输入框 | abort 旧请求，旧响应不得覆盖新结果；页码复位 | 主站 UX 契约 + `listCases` |
| 建立案件 | 按 `createCase` 幂等键创建，成功进入新案卷 | 结果不确定时保留相同 key 与原请求值，提供回台账核对/同请求重试 | PRD §2；OpenAPI `createCase` |
| 查看详情 | 并行读取案件和文档目录，展示后端返回字段 | 错误不泄露后端诊断，提供重试；授权丢失时隐藏内容 | OpenAPI `getCase`、`listCaseDocuments` |
| 确认签约主体 | 工作人员输入已核对 UUID 与核验依据，提交当前案件版本和幂等 key | 明确提醒主体确认后锁定；冲突要求刷新核对，不推断主体 | PRD §2；权限模型；`confirmCaseLegalEntity` |
| 上传材料 | 未确认主体时只允许主合同；已确认后可选择合法角色；每文件独立状态 | 仅 PDF/DOCX，30 MiB；不确定时先刷新目录，再以同一文件、角色和幂等 key 重试 | PRD §2；OpenAPI upload |

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Case table/pagination | 本目录语义案件表与 URL 分页状态 | 本契约 + OpenAPI | 桌面表格；窄屏仅表格区横向滚动 | `Workbench.test.tsx` + 浏览器 |
| Form | 本目录表单与字段错误 | 本契约 + OpenAPI | 案件新建；主体核验 | 表单/API 组件测试 |
| Dialog | 原生 `<dialog>.showModal()` | 本契约 + HTML 对话框行为 | 新建案件；主体锁定确认 | 键盘/Escape/焦点验证 |
| File upload | 本目录单文件上传控件 + 共享 `uploadCaseDocument` API | PRD + OpenAPI + 主站 UX 契约 | PDF/DOCX；主体确认前仅主合同 | 上传状态/幂等重试测试 |
| Scrollbar | 本目录 `style.css` 全局滚动条规则 | `DESIGN.md` | 强制颜色使用系统色 | computed style + 浏览器 |
| CRUD | 本目录 URL 案件状态 + 共享 API client | 本契约 + OpenAPI | 列表、创建、详情；旧工作台保留 | 端到端路线测试 |

## 重要边界

- 上传不是审阅授权；界面明确显示“未授权读取”。
- 主体未确认时不显示附件/项目客户合同上传选项，不能用前端绕过服务器规则。
- 不提供跨案件材料选择、历史合同搜索、财务数据或运行创建。
- 无结果不是条款缺失；解析状态不得伪装成授权状态。

## 可访问性与响应式

- 目标 WCAG 2.2 AA；原生按钮/链接/输入控件，所有操作可键盘访问且可见焦点。
- 对话框使用原生 `<dialog>.showModal()`，含可访问标题、Escape/返回操作和受限背景；表单设置 `noValidate`，错误与字段关联。
- 搜索明确提供清除按钮；表格为语义 `<table>`，小屏仅表格容器横向滚动。
- 全局滚动条可见；支持 `prefers-reduced-motion` 与 `forced-colors`。
