export type SourceState = "ready" | "partial" | "unauthorized" | "empty" | "failed";

export interface Source {
  id: string;
  name: string;
  kind: string;
  scope: string;
  queriedAt: string;
  completeness: string;
  state: SourceState;
  purpose: string;
  detail: string;
}

export const sources: Source[] = [
  {
    id: "main",
    name: "待审合同 v3",
    kind: "上传文件 · PDF",
    scope: "本案主合同 · 18 页",
    queriedAt: "2026-09-24 16:30",
    completeness: "18 / 18 页已解析",
    state: "ready",
    purpose: "本次审查对象",
    detail: "签署页和付款条款均可定位。此处展示的是合成样本，不代表真实解析质量。",
  },
  {
    id: "history",
    name: "历史合作合同 v2",
    kind: "模拟合同库",
    scope: "已确认主体 · 最近 12 个月 · 3 条候选",
    queriedAt: "2026-09-24 16:31",
    completeness: "3 / 3 条候选",
    state: "ready",
    purpose: "背景参考 · 需显式选择",
    detail: "候选摘要不展示金额。本原型只呈现选择意图，后端用途授权尚未接入。",
  },
  {
    id: "customer",
    name: "项目客户合同",
    kind: "模拟合同库",
    scope: "项目 PRJ-024 · 1 条关联候选",
    queriedAt: "2026-09-24 16:32",
    completeness: "关联版本待核验",
    state: "partial",
    purpose: "上下游义务比对 · 需显式选择",
    detail: "关联关系和合同版本尚未完成校验。不得据此形成确定的上下游比对结论。",
  },
  {
    id: "finance",
    name: "财务往来",
    kind: "未接入",
    scope: "主体往来与应收应付",
    queriedAt: "未查询",
    completeness: "未知",
    state: "unauthorized",
    purpose: "经营背景",
    detail: "真实财务查询在 M4 设计和接入；当前不提供金额、余额或逾期判断。",
  },
];

export const findings = [
  {
    id: "F-01",
    title: "付款节点表述需要复核",
    status: "待人工复核",
    clause: "第 6.2 条",
    excerpt: "乙方提交阶段成果并经甲方书面确认后，甲方于约定期限内支付对应款项。",
    page: 6,
    sourceId: "main",
    note: "示例发现，仅用于验证引用回跳。未执行规则、法律或财务判断。",
  },
  {
    id: "F-02",
    title: "附件交付清单需核对",
    status: "材料待补充",
    clause: "第 9.1 条",
    excerpt: "具体交付清单及验收标准以双方确认的附件为准。",
    page: 11,
    sourceId: "main",
    note: "示例发现，仅用于验证引用回跳。附件缺失时不能推断为无风险。",
  },
];
