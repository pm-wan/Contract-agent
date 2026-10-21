---
version: alpha
name: "案牍 / 苔墨校核台"
description: "现有合同审查工作台的并行视觉方案；不覆盖主站设计系统。"
colors:
  primary: "#24483f"
  primary-deep: "#183b33"
  accent: "#cb573d"
  secondary-accent: "#b98e42"
  canvas: "#f3f2e9"
  surface: "#fbfaf4"
  text: "#1f2d27"
  muted: "#78847d"
  border: "#d9ded3"
  focus: "#cb573d"
  success: "#35755f"
  warning: "#94651e"
  danger: "#ac4437"
typography:
  sans:
    fontFamily: '"DM Sans", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
  serif:
    fontFamily: '"Noto Serif SC", "Songti SC", "STSong", Georgia, serif'
  mono:
    fontFamily: '"DM Mono", "SFMono-Regular", Consolas, monospace'
rounded:
  DEFAULT: "7px"
  sm: "3px"
  md: "5px"
  lg: "7px"
spacing:
  page-max: "1500px"
  rail-width: "82px"
components:
  navigation-rail: { background: "#183b33", active: "#31574d", marker: "#dc7658" }
  case-stamp: { size: "36px", border: "#e7b3a2", color: "#cb573d" }
  table: { min-width: "780px", row-height: "76px", header: "#f4f3eb" }
  evidence-state: { status-requires-label: true, upload-is-not-authorization: true }
---

# 案牍 / 苔墨校核台

## Overview

这是主工作台的并行方案，产品 register 仍为面向中国企业审阅人员的内部工具，简体中文、桌面优先并适配小屏。参考对象不是另一张通用 dashboard，而是现代档案处的案卷交接桌：每一份材料有明确归属，先核对主体，再谈范围和授权。

视觉决策以青苔墨绿替代主站海军蓝，以朱砂橘替代铜色。朱砂只标出需要人工判断、当前焦点与档案索引；成功、警告和错误仍用独立语义色与文字。浅麦纸色负责承载长时间阅读，绿色侧轨提供稳定定位。唯一签名元素是“案”字档签与短朱砂定位线，它们对应真实案件归属，不是装饰性数据图形。

不仿法律印章，不模拟真实公文或政府背书，不用色彩代替权限，不展示虚构统计或模型结论。字体优先采用本机中文系统字库，不依赖远程字体加载。高风险主体确认须显示锁定后果和核验依据。

## Colors

`#24483f` 是品牌/主操作绿；`#183b33` 是导航深色；`#cb573d` 是焦点与人工注意提示；`#b98e42` 是待确认提示；`#f3f2e9` 与 `#fbfaf4` 构成桌面底色和内容纸面。语义成功 `#35755f`、警告 `#94651e`、危险 `#ac4437` 必须搭配文字。焦点轮廓使用朱砂并保持可见。

运行时 token 只定义于 `style.css` 的 `:root`，与主站 `web/src/design/tokens.ts` 并行；本目录是该备选入口唯一的样式真源，不回写主站 token。全局滚动条使用标准属性和 WebKit 回退；强制颜色模式恢复系统控制。

## Typography

标题使用本机可用的中文宋体/衬线回退，突出案卷名称；正文使用系统中文黑体，确保表格和表单易读；编号使用等宽回退。正文基线 12–14px 用于密集桌面工作流，重要标题和操作不小于 12px；窄屏放大主标题并允许中文自然换行。没有运行时远程字体依赖。

## Layout

82px 侧轨承载主导航标识，顶栏只放环境和身份上下文；内容区域最大宽 1500px。案件表格优先保留列语义，窄屏在表格内部横向滚动，不锁定页面高度。详情使用主材料区 + 辅助案卷信息区，窄屏切换为自然纵向文档流。

## Elevation & Depth

层级由纸面色块、边线、档签和留白表达。静态卡片不堆叠阴影；原生对话框用遮罩表达阻断状态，正文可在短视口内滚动。

## Shapes

使用 3–7px 小圆角和直边档案标签。圆形只用于状态点和身份提示。状态标签总带文字；主体待确认用金色边线与文字，不把风险仅编码为颜色。

## Components

侧轨当前项有左侧朱砂短线。案件条目使用带“案”字的方档签，并保留可读案件标题、版本与 ID 前缀。上传状态、解析状态和授权状态分开呈现；上传成功不代表授权。实体确认对话框说明确认会锁定主体，要求 UUID 与人工核验依据。按钮保持固定高度，忙碌状态不改变尺寸。

## Do's and Don'ts

- 用真实服务端记录支撑案件、状态和数量；无数据时给出下一步操作。
- 将上传、解析、授权与输入冻结表达为独立阶段。
- 不把本地选择或视觉状态当作权限事实。
- 不使用远程字体请求、虚构 KPI、装饰性图表或伪造审查结论。
