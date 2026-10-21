---
version: alpha
name: "合同审查证据工作台"
description: "面向中国企业合同审查人员的克制、可追溯、高信息密度工作台。"
colors:
  primary: "#17324d"
  primary-deep: "#10283f"
  accent: "#9d7343"
  canvas: "#f4f6f7"
  surface: "#ffffff"
  text: "#182b3a"
  muted: "#5c6b76"
  border: "#dbe1e5"
  focus: "#195d9b"
  success: "#276d54"
  warning: "#9a651d"
  danger: "#b33a3a"
typography:
  sans:
    fontFamily: '"Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
rounded:
  DEFAULT: "8px"
  sm: "4px"
  md: "8px"
  lg: "12px"
spacing:
  section-gap: "42px"
  page-max: "1510px"
components:
  button: {}
  card: {}
  dialog: {}
  table: {}
  source-card: {}
  evidence-rail: {}
---

# 合同审查证据工作台设计系统

## Overview

### Creative North Star

参考案卷索引与批注边栏：信息可扫描、证据能回到原文，重要限制比装饰更显眼。

### Product context and register

- **受众和任务：** 中国企业的合同审查、法务和业务复核人员，在桌面浏览器中确认来源范围、阅读发现并回跳原文。
- **市场与语言：** 当前产品方案面向中文企业合同流程；M0 界面为简体中文。未验证其他市场或语言，不自动推断跨境适用性。
- **使用场景：** 工作日高密度审查与复核；桌面优先，小屏保持阅读和基本操作能力。
- **类别：** 内部产品工具，不采用营销页视觉。
- **标志性元素：** PDF 页码边栏和发现的来源定位并列呈现。
- **克制：** 案件、权限、金额和审批状态使用熟悉的企业产品构件，不做视觉隐喻。
- **反例：** 营销大图、假仪表盘统计、仅用颜色表达风险、无法解释的“完成”动画。
- **运行时所有权：** `web/src/design/tokens.ts` 是颜色、字体和圆角的代码真源；同文件映射到 Ant Design ConfigProvider 和 CSS 自定义属性。本文件镜像意图与当前值；变更须同步并用审核检查差异。版面局部尺寸仍在 `web/src/styles.css`，待后续共享布局组件收敛。

## Colors

深海军蓝 `#17324d` 只用于导航、主要操作和层级标题；古铜 `#9d7343` 指向案卷页码、来源边界。白色内容层置于灰白底。状态必须同时带文字：成功、警告、失败分别由 `success`、`warning`、`danger` 支持。焦点使用 `#195d9b` 的可见轮廓。M0 只支持浅色模式，深色与高对比模式须在正式发布前单独验证。

## Typography

中文优先采用 Noto Sans SC / 平台中文字体；英文与数字沿用系统回退。正文约 14–15px、行高至少 1.5，文档节选使用 1.9。编号和页码可使用等宽/数字对齐，不能替代中文标签。不使用全大写英文承担关键业务信息。

## Layout

桌面左侧 224px 导航，内容最大 1510px。来源卡片双列，720px 以下单列。文档页码边栏保持独立，不让其遮挡原文；表格拥有自己的横向滚动。页面随内容自然增长，不锁住视口滚动。固定宽度字段需适配中文长文本和未知数据。

## Elevation & Depth

主要靠边框、色面和留白建立层级；仅文档纸页有轻微阴影。业务卡片不堆叠重阴影，抽屉由 Ant Design 管理叠层和焦点。

## Shapes

控件与容器以 8px 圆角为主。来源卡片选中时增加轮廓和文案，警示区域使用左边框但不能只靠颜色识别。

## Components

### Foundational visual states

每个来源显式显示可预览、部分返回、未启用、空结果或失败。M0 合成内容始终展示“交互原型”提示。加载、失败、空、无权限和过期版本按页面职责呈现；正式服务端接入后必须按真实响应驱动。默认加载用 Ant Design Spin 的固定区域，不以动画冒充业务进度。无权限不显示受限内容摘要。

### Buttons and actions

主要操作使用实心海军蓝；次要操作使用边框。默认主线是上传主合同、发起审核、查看结果。案件详情使用“背景与材料 / 审核过程 / 审核结果”三个 URL 导航分区。背景区管理主体和文件；审核过程展示主合同审核操作与真实执行记录；结果区展示已产出批次并链接到结果与原文证据。读取授权、手动用途预览与冻结、操作记录分别置于审核过程的独立披露区。首次审核在一个确认弹窗内完成必要的主体核验与明确读取授权，再串联服务端预览、冻结和运行创建。材料未就绪时解释原因并禁用审核；审批等未接入能力不得伪装为可用。不得把本地选择表现为授权生效。

### Navigation and data display

Ant Design Menu + Breadcrumb 负责层级，Table 显示案件。分页将由 M1 服务端契约决定；M0 仅一条合成记录，不假装已处理大数据。结果以发现、决策参考、范围提示分组。案件分区使用 React Router 链接，`view=background|review|results` 保存当前位置；切换保留当前案件的表单草稿。案件编号与版本收进档案披露区，主要内容保持自然页面滚动。

### Forms and overlays

输入由 Ant Design 维护；搜索可清空且不请求服务端。Modal 展示来源说明，Drawer 展示证据定位。正式表单使用 `noValidate` 与共享错误提示，不能依赖浏览器原生验证。

### Iconography

使用 `@ant-design/icons`，图标只辅助导航和操作，关键状态与动作始终有文字。常规图标为 16px，跟随文本色。

### Motion

只在状态变化时使用 200ms 左右的轻过渡；遵守 `prefers-reduced-motion`，不做连续装饰动画。主入口启动时由 `web/src/main.tsx` 将减少动效偏好映射到 Ant Design 的 `motion` 开关，避免仅缩短 CSS 时长后弹窗退出状态滞留。高风险操作不以动效替代确认。

### Content and data visualization

文案明确区分“合成示例”“未接入”“待核验”“已冻结”。金额、来源完整性与法律依据缺失时不输出确定结论。M0 不展示虚构的统计图。

## Do's and Don'ts

- **Do:** 每个发现保留可解释的来源与页码入口。
- **Do:** 将无权限、部分、过期和未接入状态与结果并列展示。
- **Don't:** 把前端本地勾选解释成用途授权或冻结。
- **Don't:** 只用颜色显示风险，或把演示数据写成真实审查结果。
