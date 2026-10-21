# 规则 Schema

> 版本：v1.1 候选｜状态：按新 MVP 知识分层修订中
> 负责人：内部规则负责人 + 技术负责人
> 批准人：内部项目发起人
> 适用阶段：M0～M4
> 上游依据：规则与槽位规范 v1.0、事实与证据模型 v1.0  
> 序列化格式：YAML；发布时生成规范化 JSON 和内容哈希

## 1. RuleDefinition

```yaml
rule_id: legal.liability.cap
version: 1.0.0
domain: legal
knowledge_domain: legal # legal / enterprise / cross_domain
basis_kind: legal_validity_question # 示例；实际值由受控枚举确定
title: 责任限制检查
status: draft
applicability:
  contract_types: [service, software_purchase]
  party_roles: [customer]
  jurisdictions: [CN-mainland]
  expression: null
dependencies:
  facts: [contract.contract_type, contract.party_role, contract.jurisdiction, contract.total_amount]
  checks: []
normative_refs: []
enterprise_position_refs: []
applicable_time_basis: signing_date
review_owner_role: legal_reviewer
slots: []
execution:
  deterministic_steps: []
  semantic_tasks: []
  tool_allowlist: []
  max_rounds: 3
  timeout_seconds: 120
  token_budget: 12000
risk_policy:
  minimum_judgment_rules: []
  human_review_when: []
evidence_policy: {}
invalidation_dependencies: []
eval_suite_refs: []
```

`contract_types`、`party_roles`、`jurisdictions` 分别绑定 `contract.contract_type`、`contract.party_role`、`contract.jurisdiction`，非空维度对应的事实必须在 `dependencies.facts` 中声明。适用性要求表达式与全部非空维度同时成立；已知值不在允许列表内为 `not_applicable`，缺少维度事实为 `undetermined`。空允许列表不解释为“无限制”，而使计划保持 `undetermined` 并产生 `applicability_dimension_empty:<fact>` 发布阻断。

`eval_suite_refs` 使用稳定评测套件标识 `suite_id@version`，不使用仓库文件路径。评测器除核对套件的 `rule_ref` 外，还必须确认该套件标识由编译规则显式引用；文件移动不改变引用身份，未声明或版本不匹配的套件不得用于该规则的评分。

开发评测器将 suite YAML 限制为 2 MiB、单个规范化 fixture 限制为 1 MiB、单 suite 全部 fixture 合计限制为 16 MiB、最多 10,000 个 case；observations JSONL 与评测报告各限 8 MiB，JSONL 单行限 1 MiB。文件上限在读取时执行，最多只读上限加 1 字节后拒绝；JSONL 逐行解析。

期望项 `evidence_missing: true|false` 可明确校验 observation 是否显式提供空/非空 `evidence` 列表；未提供该列表时计为未观测，而非把缺省字段误当成“无引用”。该项只核对观察记录中的引文数量，不验证引文对判断是否构成充分或直接支持。

`max_rounds`、`timeout_seconds` 与 `token_budget` 必须是有界正整数；当前编译器分别限制为 1～10、1～300 秒、1～50000。Worker 将按冻结规则执行最大 Agent 轮数，并取规则超时与 Worker 本地超时上限的较小值。累计 token 预算尚未接入 AgentScope 用量核算，因此会保留 `execution_budget_unimplemented:token_budget` 发布阻断，不得把该字段当成已生效的费用上限。

## 2. SlotDefinition

```yaml
slot_id: cap_basis
title: 责任上限基数
slot_type: fact
value_schema:
  type: object
required: true
required_when: clause_present == true
critical: true
dependencies: [clause_present]
accepted_fact_types: [contract.liability_cap_basis]
evidence_policy:
  kinds: [document_span]
  quote_required: true
validation_rules: []
allowed_tools: [document.locate_clause]
allowed_terminal_states: [filled, needs_material, conflicted, undetermined, human_review]
invalidation_dependencies: [document_version, party_role]
```

## 3. 发布校验

- rule_id 稳定且全局唯一；
- SemVer 版本合法；
- 所有引用可解析；
- 关键槽位不得允许无证据 `filled`；
- 规则不能引用未授权工具；
- `not_found` 必须声明 absence_evidence 策略；
- 具有强制最低结论的条件必须可确定执行；
- 评测集通过且审批记录存在。
- `knowledge_domain`、`basis_kind`、依据版本/适用时点和审查责任人明确；法律原文、企业制度及交易背景不能互相冒充；
- 运行用 MD Skill 的内容哈希、规则包与知识引用可回取；开发用 `skills/` 文档不因存在于仓库即视为发布。

## 4. 版本语义

- PATCH：文字说明或不影响结论的修正；
- MINOR：新增兼容槽位、场景或评测；
- MAJOR：适用性、判断、风险或证据要求发生不兼容变化。

是否需要重审由 invalidation policy 决定，不仅看 SemVer。当前规则/槽位失效依赖会原样保留在编译对象中，但尚无依赖 ID 到来源变更事件及重审动作的执行映射；每项声明会生成 `invalidation_dependency_unimplemented:<id>` 或 `slot_invalidation_dependency_unimplemented:<slot>:<id>` 发布阻断。不得把这些声明当成已自动触发重审。

## 5. 顶层必填与未知字段

旧 v1.0 发布对象使用旧示例顶层字段；本轮 v1.1 候选增加知识域、依据性质、适用时间和责任人字段，受控枚举与兼容迁移在规则包实现前冻结。编译器对顶层及 `applicability`、`dependencies`、`execution`、`risk_policy`、`evidence_policy` 和槽位证据策略实行闭合字段校验，并验证字段类型、可执行步骤/任务和工具 allowlist；未知键、非法类型或未实现执行项均拒绝。字符串 ID 使用小写点分命名。表达式不得使用任意代码，首版只允许由应用实现的受控操作符：`all`、`any`、`eq`、`in`、`exists`、`gte`、`lte`。编译时还会递归检查表达式字段引用：适用性条件只能引用已声明事实；槽位 `required_when` 只能引用已声明事实或已定义槽位；最低判断规则 `when` 只能引用已声明事实或已定义槽位。未声明引用拒绝编译，避免拼写错误或孤儿依赖静默进入运行计划。

`knowledge_domain`、`basis_kind`、`applicable_time_basis`、`review_owner_role` 若提供必须为非空字符串；在受控枚举/目录尚未批准前只保留原值，不由编译器推定枚举语义。字段缺失会保留对应发布阻断。四项元数据进入编译规则、开发态 Skill 的规范化 JSON 和运行定义快照；读取快照时会重新从冻结规则源编译并逐项核对，避免只在发布检查中读取、却未绑定模型上下文或运行身份。

规则计划的摘要绑定完整规范化事实集与已完成检查；`RulePlan` 仅携带事实集摘要，不携带事实明文。事实快照遵循 JSON 对象键规则，只接受字符串键，并拒绝浮点数，避免键名规范化碰撞及浮点数舍入歧义。执行最低判断时，`RuleRun` 必须收到摘要匹配的事实集，并与已验证槽位值共同用于表达式求值；不匹配时拒绝执行。事实 ID 与槽位 ID 不得重名，避免合并执行上下文时发生覆盖。

`dependencies.checks` 当前声明稳定检查 ID。当前开发期推荐将它映射为同一合同输入上先行执行的 RuleDefinition ID：方案目录将 `legal.price.definition` 列为一项独立规则；知识来源引用与审批仍通过 `normative_refs`、`enterprise_position_refs` 单独管理。该解释尚待领域负责人确认，获批知识本身不能代替依赖规则结果。声明字符串或调用方传入同名 `completed_checks` 不构成该检查已执行的可复核证据。M2 尚无跨规则执行编排、持久检查结果或完成凭据；因此每个依赖检查都作为 `dependent_check_completion_proof_unimplemented:<check_id>` 发布阻断。当前 `RulePlan` 仍可用于诊断性拓扑计划，但 `RuleRun` 会拒绝任何非空 `completed_checks`，以免受信应用代码把字符串 ID 当作完成凭据。将来完成凭据至少须绑定依赖规则定义/知识版本、同一输入快照、实际执行结果及其证据，并由运行编排从持久结果推导，不能由普通 reviewer 事实确认或 Worker 任意字符串集合代填。

槽位 `required_when` 可以引用已声明槽位，也可以引用 `dependencies.facts` 中声明的合同事实。`eq` / `in` 按 JSON 值类型比较；例如布尔值 `true` 不等同于数字 `1`。比较值类型不兼容时返回 `unknown`，而不是 Python 式隐式相等；对非空 `in` 候选列表，如果没有同类型候选项，也返回 `unknown`。`gte` / `lte` 的规则常量必须可解析为有限、至多 128 字符的整数/十进制文本；非法常量在编译时拒绝，运行期事实若不是有效数值则返回 `unknown`。因此错误类型不会把必需槽位变成可跳过，也不会将适用性错误地确定为不适用；`unknown` 继续按各执行位置的保守规则处理。

> **待闭合的表达式语义：** 执行器现在可沿 Rule Schema 明确声明的 object properties 读取嵌套字段；映射路径缺失时比较返回 unknown，`exists` 对缺失/null 返回 false。付款开发规则的 `payment_triggers.acceptance_required` 位于对象数组中，当前不会遍历数组并返回 unknown；编译器因此保留 `expression_path_semantics_unresolved` 发布阻断。发布或用该路径作判断前，仍须由领域负责人确认数组量化方式（例如任一阶段、所有阶段或显式聚合）、缺失/null 的三值传播和空数组行为；不得由实现者默认选择 any/all，也不得把未解析条件当作未触发风险。对象路径支持不清除此规则的数组阻断或其他发布阻断。
> **未实现证据适配器：** 当前 `RuleRun` 尚不接受 `human_confirmation` 证据，使用时明确拒绝。声明该证据类型的规则会被标记 `human_confirmation_evidence_unimplemented` 发布阻断；此项须在持久化人工决定契约和权限校验接通并通过测试后移除。
> **人工复核触发声明：** `risk_policy.human_review_when` 中的字符串 ID 会原样保留在编译规则中；由于每个 ID 到事实、候选状态和人工复核流程的执行映射尚未定义并接通，编译器为每项声明附加 `human_review_condition_unimplemented:<id>` 发布阻断。不得仅凭 ID 名称推断触发条件或把它解释为已执行的人工复核策略。
> **最低判断政策的开发态执行边界：** `RuleRun.assess()` 在必需槽位齐全后，以已校验证据的填槽值执行 `minimum_judgment_rules`，仅返回命中规则及未解析条件索引；条件为 unknown 时不当作未命中，而转人工复核。该执行不把 `issue_found` 等候选映射成“通过/无风险”，也不聚合多个规则为最终法律/企业判断。对象数组路径仍是 unknown 并继续由 `expression_path_semantics_unresolved` 阻断。当前候选规则没有批准评测、法务/企业知识正文及发布审批，因此策略求值实现已接通不代表规则批准或 M2 发布门通过。
> **计划执行覆盖与发布资格是两条不同门槛：** `verify_input_snapshot`、`locate_payment_candidates`、`calculate_payment_totals`、`apply_minimum_policy`、`validate_evidence` 及 `document.locate_clause` 已有确定性/工具执行边界；`extract_payment_schedule` 与 `classify_payment_triggers` 已由冻结的结构化模型输出契约接入 Worker，编译器强制每项任务与对应 `payment_schedule` / `payment_triggers` 槽位同时声明，候选槽位及引用经 Schema 和来源校验后才进入只读结果。付款汇总严格分开原始百分比数值和不同币种固定金额，Residual 只计数，不归一化百分比尺度、不做币种换算或风险推断；输入仍是待人工核验的抽取候选。付款阶段数组最多 500 项，数字字符串最多 128 字符，以限制计算资源。`calculation.sum_decimal` 已由闭合参数契约调用精确十进制执行器，并在 `calculate_payment_totals` 中使用；`calculation.payment_ratio` 尚未接入（比例分母须来自明确、授权且冻结的合同总价定义），当前只对此具体工具保留 `rule_tool_unimplemented:calculation.payment_ratio` 阻断。语义/计算工程覆盖不代表模型质量或规则批准；批准评测、规则/知识审核和发布门槛仍有效。
> **槽位 validation_rules：** validation rule ID 必须属于闭合清单；比例字符串格式由 JSON Schema 检查，“固定金额必须有币种”由运行器检查。`derive_with_decimal_executor_only` 与 `judgment_must_follow_minimum_policy` 仍附加逐项发布阻断；没有可信计算执行器/政策约束执行时，即使调用方提交 `calculation` 证据哈希，也拒绝对应填槽，不把声明当成已执行。
> **accepted_fact_types：** 对 `slot_type: fact`，模型候选必须显式返回 `fact_type`；冻结系统提示词给出每个槽位的类型及允许 ID，`RuleRun` 要求填充值的 fact type 精确匹配声明列表，并拒绝非 fact/非 filled 候选携带类型。该类型 ID 随只读结果保存。fact 槽位没有声明类型时产生 `slot_fact_type_missing:<slot>` 发布阻断；将 `accepted_fact_types` 配置在非 fact 槽位会拒绝规则编译。这个检查只验证类型绑定及槽位自身 JSON Schema，不证明事实语义正确，也不形成批准的业务结论。
> **slot_type 与 quote_required：** `fact` 类型现有显式 fact-type 分派；`evidence` 与 `judgment` 尚未完成各自语义适配，每项仍附加 `slot_type_unimplemented:<slot>:<type>` 发布阻断。模型与运行提交目前总是要求引文；配置 `quote_required: false` 会附加 `slot_quote_requirement_unimplemented:<slot>` 阻断，不能声明可无引文运行。
> **全局 evidence_policy：** `direct_evidence_required`、`quote_hash_required` 与 absence scope 字段由编译规则保留并供验证器使用；absence evidence 必须要求搜索范围、完整解析覆盖和查询词。当前模型/`RuleEvidence` 没有可信 support relation 字段，故声明 `direct_evidence_required: true` 会生成 `direct_evidence_support_unimplemented` 阻断，不能把模型引文或精确命中直接认定为 direct support。引用原文与哈希一致性校验仍按配置执行。

`normative_refs` 和 `enterprise_position_refs` 分别保存法律规范与企业立场引用，不得混填；编译器要求前者使用 `legal.` 命名空间、后者使用 `enterprise.` 命名空间。引用须为唯一、带数字版本后缀的稳定字符串（当前规范形式至少包含 `/版本`）。运行定义快照会分别冻结两类引用 ID，但当前开发规则不解析知识正文，快照必须标记为 `unresolved_development_references`，不得据此宣称法律/企业知识已经绑定或批准。缺少已批准企业阈值时可以提取事实并转人工，但不得自行创造比例、金额或期限标准。

## 6. 变更记录

| 版本 | 日期 | 变更 | 作者 |
|---|---|---|---|
| v0.1 | 2026-09-17 | 建立结构草案 | 项目组 |
| v1.0 | 2026-09-17 | 冻结顶层结构、发布校验和表达式边界 | 项目组 |
| v1.1 候选 | 2026-09-24 | 增加知识域、依据性质和运行 MD 绑定要求 | 待评审 |
| v1.1 候选 | 2026-09-26 | 明确已接入语义候选任务与十进制求和的工程覆盖，发布质量/审批门槛不变 | 技术负责人 |
| v1.1 候选 | 2026-09-26 | 记录 Agent 轮数/超时执行边界与未接通 token 预算的发布阻断 | 技术负责人 |
| v1.1 候选 | 2026-09-26 | 明确 `eq` / `in` 严格 JSON 类型比较，并允许 `required_when` 使用已声明合同事实 | 技术负责人 |
| v1.1 候选 | 2026-09-26 | 规范化事实快照仅允许 JSON 字符串对象键，拒绝非字符串键 | 技术负责人 |
