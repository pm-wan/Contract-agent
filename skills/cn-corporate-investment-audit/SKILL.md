---
name: cn-corporate-investment-audit
description: 审查中国大陆公司投资、增资、股权转让、股东协议、公司章程、可转股安排及公司治理文件，建立资本、股权、控制、决策与退出权利图谱。适用于企业内部证据化审计；不替代证券发行、并购申报、税务或诉讼专项意见。
metadata:
  version: "1.0.0"
  status: "development-only"
---

# 中国投资股权与公司治理审计

以交易前后资本结构、股东权利、控制机制和公司程序为主线，联合审查交易协议、章程、决议和登记材料。单看股东协议不能证明公司层面权利已经有效落实。

## 适用文件

增资协议、股权转让、投资协议、股东协议、公司章程、表决权委托、一致行动、可转股安排、回购/退出、董事会和股东会决议、交割文件及治理附件。

## 模式

`full_review`、`cap_table_review`、`investment_terms_review`、`share_transfer_review`、`governance_review`、`approval_review`、`closing_review`、`version_compare`、`amendment_trace`、`redline_draft`。执行时读取 [规则目录](references/rule-catalog.md) 和 [输出契约](references/output-contract.md)。

## 输入

固定交易文件、现行章程、股东名册、出资/实缴与登记记录、历史融资和股权变更、质押/冻结、决议、授权、财务和税务确认、企业投资立场及已核验规范依据。

## 工作流

1. 固定目标公司、各方身份、交易类型、签署/交割时点和审查立场。
2. 建立交易前与交易后 cap table，所有比例、稀释、对价和阈值使用确定性计算。
3. 建立经济权利、表决权、董事席位、否决事项、信息权、转让权和退出权图谱。
4. 执行 [规则目录](references/rule-catalog.md)，并对协议、章程、决议、名册和登记的一致性作交叉验证。
5. 区分签署条件、交割条件、交割动作和交割后义务，缺一不得描述为“交易完成”。
6. 涉及税务、国资、外资、反垄断、证券监管或行业许可时，只在事实触发后路由专项复核。
7. 不自行认定回购、对赌、优先权或治理条款必然有效；以版本化规范和专业复核为准。

## 关键约束

- 公司合同权利、章程权利、登记状态和实际执行分别建模。
- 不用百分比文本替代完全稀释口径 cap table；期权、可转股和未实缴安排必须显示假设。
- 董事会/股东会权限、表决门槛、回避和法定程序不得仅凭商业协议推断。
- 历史同意、豁免、优先权和转让限制需要完整文件链。
- 不自动签署、提交工商登记、发出股东通知、划款或更新股东名册。

## 证据与输出

合同、章程和决议使用固定文档证据；登记、股东名册、出资和质押记录使用 `source_record`；资本计算使用 `calculation`；批准使用 `human_confirmation`。输出遵循 [输出契约](references/output-contract.md) 与 [JSON Schema](references/audit-output.schema.json)。资本结构、权限或交割条件存在冲突时不得自动通过。
