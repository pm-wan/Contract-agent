# 06 规则与知识工程

- [销售合同双维审查试验台说明](销售合同双维审查试验台说明.md)
- [首批规则目录](首批规则目录.md)
- [规则 Schema](规则Schema.md)
- [Skill 生成规范](Skill生成规范.md)
- [规则发布流程](规则发布流程.md)
- [开发态 Rule/Skill/评测产物审计脚本](../../../scripts/audit_rule_release.py)
- [开发规则依赖图只读审计脚本](../../../scripts/audit_rule_dependencies.py)
- [采购合同开发用 Skill](../../../skills/cn-procurement-contract-audit/SKILL.md)
- [销售合同开发用 Skill](../../../skills/cn-sales-contract-audit/SKILL.md)
- [劳动人事合同审计 Skill](../../../skills/cn-employment-contract-audit/SKILL.md)
- [知识产权与技术合同审计 Skill](../../../skills/cn-ip-technology-contract-audit/SKILL.md)
- [数据与隐私合同审计 Skill](../../../skills/cn-data-privacy-contract-audit/SKILL.md)
- [投资股权与公司治理审计 Skill](../../../skills/cn-corporate-investment-audit/SKILL.md)
- [融资担保合同审计 Skill](../../../skills/cn-financing-guarantee-audit/SKILL.md)
- [外来与旧版 Skill 参考资料](../../../skills/refs/README.md)
- [价款口径规则：合同价款口径检查](rules/legal.price.definition.yaml)
- [首条纵向规则：付款条件](rules/legal.payment_conditions.v1.yaml)
- [付款条件评测种子](eval/legal.payment_conditions.cases.yaml)
- [价款口径开发评测套件](eval/legal.price.definition.dev.yaml)
- [价款口径开发评测 observations](eval/legal.price.definition.dev.observations.jsonl)
- [价款口径开发评测报告](eval/reports/legal.price.definition.dev.report.json)
- [价款口径开发评测报告（中文可读版）](eval/reports/legal.price.definition.dev.report-中文可读版.md)
- [价款口径开发评测 observations（中文可读版）](eval/legal.price.definition.dev.observations-中文可读版.md)
- [付款口径决策表（开发草案）](付款口径决策表-开发草案.md)
- [法律知识库字段与关联设计（开发草案）](法律知识库字段与关联设计-开发草案.md)
- [国有企业付款立场模板（开发版）](国有企业付款立场模板-开发版.md)
- [付款规则证据类型与人工处理说明（开发草案）](付款规则证据类型与人工处理说明-开发草案.md)
- [付款规则反例案例草案（待核对）](付款规则反例案例草案-待核对.md)
- [评测运行与指标规范](../07-quality/评测运行与指标规范.md)
- [规则生产升级前置清单](生产升级前置清单.md)
- [评测报告校验脚本](../../../scripts/verify_evaluation_report.py)

结构化规则是执行定义的唯一作者源；规范原文是依据源；Skill 是派生执行说明。

仓库中的合同审计 MD Skill 用于开发推演、规则提取和模型验证，不是平台正式发布状态。平台运行用 Skill 仍采用 MD，须绑定已发布 RuleDefinition/SlotDefinition、知识版本、评测和内容哈希；未来可由数据库管理版本与发布元数据。项目计划决定实际运行的规则与覆盖范围。

目前 `legal.price.definition@1.0.0` 和 `legal.payment_conditions@1.0.0` 均在开发态规则目录；付款规则依赖价款规则。价款规则负责提取价款、金额来源、税额、币种和付款分母候选，并对明确的金额关系作精确十进制核算。两者仍属于开发态，不代表生产发布。

`contract_app.rules.compiler` / `runtime` 已有开发态执行基础：闭合规则 Schema、必需槽位与证据验证、确定性最低判断条件匹配、槽位校验及精确金额核算。`contract_review` Worker 将价款与付款候选写入同一运行结果，模型引文必须来自授权定位工具实际暴露的文本，且逐条通过解析版本、页码、偏移、原文及哈希验证。价款计算保留含税/未税/税额、币种、来源、固定金额、比例和分母；只执行规则明示的精确十进制校验，不换汇、不估 residual、不推导验收前比例，也不产生“通过”或风险结论。付款汇总只分开汇总原始百分比数值和同币种固定金额；付款比例及判断—政策一致性槽位校验仍 fail closed，结果继续保持 `undetermined`，由 blocker 阻止将开发候选解释为正式结论。

依赖图编译器 `contract_app.rules.dependency_graph` 提供确定性排序、缺项/环路检查和图哈希；目录审计脚本输出图元数据及依赖缺口。运行入队时，`FrozenDefinitionSnapshot` 冻结规则源码及图清单；Worker 恢复快照时重新编译并核对 check ID、规则 ID/版本/源哈希和 dependencies。Goal 5 的跨运行引用从同案、同一输入及事实快照的持久价款结果构建，绑定规则、结果和证据哈希，并在入队及 Worker 执行时重新验证；它不是 `DependencyCompletionProof`，不能清除 `blocked_by_checks` 或作为完成凭据。价款/付款运行和人工作业 API 的集成测试覆盖引用和 fail-closed 行为；当前状态仍不代表领域批准或发布许可。

`contract_app.rules.evaluation` 和 `scripts/evaluate_rules.py` 对外部观察记录作确定性评分并固定套件/输入指纹，可生成哈希绑定的 JSON 报告和中文 Markdown 摘要；没有获批阈值时始终阻断发布。当前价款 observations 是人工编制的合成开发回归值，不是实时 Worker/模型输出，也不是经业务/法务批准的金标；评分器测试不能替代合同判断质量验收。

评测 fixture 支持 `normalized_text_v1`（单逻辑页 UTF-8 文本）与 `normalized_pages_v1`（UTF-8 JSON，根对象仅含 `pages` 字符串数组）。多页格式保留空白页序号；fixture 文件 SHA-256 绑定全部页，证据页码从 1 开始，偏移按对应页的 Unicode code point 计算，并逐条校验页内原文及 quote SHA-256。资源上限为 suite YAML 2 MiB、单 fixture 1 MiB / 10,000 页、单 suite 全部 fixture 合计 16 MiB、suite 最多 10,000 cases；observations JSONL 和报告文件各 8 MiB，JSONL 单行 1 MiB。读取器按上限有界读取，JSONL 按行解析。重复 JSON 键、CRLF、非法 Unicode、超界页码或错误坐标均拒绝。它只为工程回归提供多页坐标样本，不代表原始 PDF/DOCX 解析、OCR 或真实页码质量。完整实现证据及未完成项见 [M2 进展记录](../00-project/M2进展记录.md)。
