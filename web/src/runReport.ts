import type { CheckResult, RunEvidence, RunResults } from "./api";
import type { ResearchReview } from "./ResearchResults";

export const MAX_RUN_REPORT_BYTES = 4 * 1024 * 1024;

export class RunReportTooLarge extends Error {
  constructor() {
    super("run_report_too_large");
    this.name = "RunReportTooLarge";
  }
}

const candidateStateLabels: Record<string, string> = {
  filled: "已抽取，待核对",
  needs_material: "需要补充材料",
  conflicted: "候选存在冲突",
  undetermined: "尚无法判定",
  human_review: "需要人工复核",
};
const priceSourceLabels: Record<string, string> = {
  contract_body: "合同正文",
  price_table: "价款表",
  attachment: "附件",
  supplementary_agreement: "补充协议",
};
const denominatorLabels: Record<string, string> = {
  contract_total: "合同总价",
  tax_exclusive_total: "未税价",
  stage_amount: "阶段金额",
  unspecified: "未明确",
};
const priceOutcomeLabels: Record<string, string> = {
  conform: "候选一致",
  deviation: "候选存在偏差",
  undetermined: "无法判定",
  human_review: "待人工核对",
};

function markdownText(value: string): string {
  return value
    .replace(/[\r\n]+/g, " ")
    .replace(/\\/g, "\\\\")
    .replace(/([`*_{}\x5B\x5D<>#+.!|()-])/g, "\\$1");
}

function markdownCandidateText(value: unknown): string {
  if (typeof value === "string") return markdownText(value);
  if (typeof value === "number" || typeof value === "boolean") return markdownText(String(value));
  return "未形成候选";
}

function markdownCode(value: string): string {
  return `\`${value.replace(/[\r\n]+/g, " ").replaceAll("`", "\\`")}\``;
}

function evidenceDescription(evidence: RunEvidence, indent = ""): string {
  const location = evidence.page ? `第 ${evidence.page} 页` : "页码未记录";
  const source = evidence.document_version_id
    ? `文档 ${markdownCode(evidence.document_version_id)}`
    : evidence.kind === "source_record" && evidence.source_record_ref
      ? `来源记录 ${markdownCode(evidence.source_record_ref)}`
      : `依据版本 ${markdownCode(evidence.source_version)}`;
  const quote = evidence.quote
    ? `\n${indent}  > ${evidence.quote.split(/\r?\n/).map(markdownText).join(`\n${indent}  > `)}`
    : "";
  const range = evidence.start_offset !== null && evidence.start_offset !== undefined
    && evidence.end_offset !== null && evidence.end_offset !== undefined
    ? `，字符区间 [${evidence.start_offset}, ${evidence.end_offset})`
    : "，字符区间未记录";
  const hash = evidence.text_hash ? `，引用 SHA-256 ${markdownCode(evidence.text_hash)}` : "";
  return `${indent}- ${evidence.kind} / ${evidence.support}；${source}；来源版本 ${markdownCode(evidence.source_version)}，${location}${range}${hash}；证据编号 ${markdownCode(evidence.evidence_id)}${quote}`;
}

function resultHeading(result: CheckResult): string {
  return `${markdownCode(result.rule_id)} v${markdownText(result.rule_version)}`;
}

function findingSection(results: CheckResult[]): string[] {
  const research = results.flatMap((result) => result.research_review ? [result.research_review as unknown as ResearchReview] : []);
  if (research.length) return research.flatMap((review) => [
    markdownText(review.summary), "", "### 合同理解", "", markdownText(review.understood_contract), "",
    ...review.findings.flatMap((finding) => [
      `### ${finding.domain === "legal" ? "法律" : "公司制度"} · ${markdownText(finding.title)}`,
      `状态：${markdownText(finding.status)}`, "", markdownText(finding.reasoning),
      `适用性：${markdownText(finding.applicability)}`, "",
      ...finding.contract_evidence.map((quote) => `合同第 ${quote.page} 页：${markdownText(quote.quote)}`),
      ...finding.source_evidence.flatMap((quote) => { const source = review.sources.find((item) => item.source_id === quote.source_id); return [
        `依据：${markdownText(source?.title ?? "资料")} · ${markdownText(source?.locator ?? "")} · ${markdownText(source?.url ?? "")}`,
        `原文：${markdownText(quote.quote)}`, `来源 SHA-256：${markdownCode(source?.content_sha256 ?? "")}`,
      ]; }), `建议：${markdownText(finding.next_action)}`, "",
    ]), "### 检索过程", "", ...review.searches.map((search) => `- ${markdownText(search.channel)}：${markdownText(search.query)}；命中 ${search.source_ids.length} 个段落${search.warning ? `；${markdownText(search.warning)}` : ""}`),
  ]);
  const findings = results.filter((result) => result.judgment === "issue_found");
  if (findings.length === 0) {
    return [
      "本次没有返回 `issue_found` 状态的结果。当前结果属于开发态候选处理；没有列出问题不代表合同无风险，也不代表审查范围完整。",
    ];
  }
  return findings.flatMap((result) => [
    `### ${resultHeading(result)}`,
    `状态：${markdownText(result.judgment)}；严重程度：${markdownText(result.severity)}。`,
    result.summary ? markdownText(result.summary) : "结果未提供摘要。",
    ...(result.evidence.length > 0
      ? result.evidence.map((evidence) => evidenceDescription(evidence))
      : ["没有绑定可展示的证据。"]),
  ]);
}

function decisionSection(results: CheckResult[]): string[] {
  const references = results.flatMap((result) =>
    result.evidence
      .filter((evidence) => evidence.kind === "source_record")
      .map((evidence) => ({ result, evidence })),
  );
  if (references.length === 0) {
    return ["本次授权结果中没有可展示的结构化来源记录；不能据此推断外部信息已取得、未取得或核验通过。"];
  }
  return references.flatMap(({ result, evidence }) => [
    `### ${resultHeading(result)}`,
    `来源记录：${evidence.source_record_ref ? markdownCode(evidence.source_record_ref) : "来源编号未返回"}；来源版本 ${markdownCode(evidence.source_version)}。此项仅作签约决策背景参考，不自动构成合同问题。`,
  ]);
}

function limitationsSection(results: CheckResult[]): string[] {
  const outcomes = results.length > 0
    ? results.map((result) => {
        const dependencyReferences = (result.dependency_result_references ?? []).map((reference) =>
          `；前置结果引用 ${markdownCode(reference.check_id)} → ${markdownCode(`${reference.dependency_rule_id}@${reference.dependency_rule_version}`)}；来源运行 ${markdownCode(reference.source_run_id)} / 结果 ${markdownCode(reference.result_id)}；状态 ${markdownText(reference.assessment_status)}；引用 SHA-256 ${markdownCode(reference.reference_sha256)}；不是完成凭据`,
        ).join("");
        const consumedReferences = (result.consumed_dependency_result_references ?? []).map((reference) =>
          `；已传入前置结果 ${markdownCode(reference.check_id)} → ${markdownCode(`${reference.dependency_rule_id}@${reference.dependency_rule_version}`)}；来源运行 ${markdownCode(reference.source_run_id)} / 结果 ${markdownCode(reference.result_id)}；状态 ${markdownText(reference.assessment_status)}；引用 SHA-256 ${markdownCode(reference.reference_sha256)}；不是完成凭据`,
        ).join("");
        return `- ${resultHeading(result)}：状态 ${markdownText(result.judgment)}；严重程度 ${markdownText(result.severity)}；${result.summary ? markdownText(result.summary) : "没有摘要"}${result.rule_plan_execution ? `；RulePlan 评估 ${markdownText(result.rule_plan_execution.assessment_status)}（${markdownCode(result.rule_plan_execution.assessment_sha256)}）；阻断依赖 ${result.rule_plan_execution.blocked_by_checks.map(markdownCode).join("、") || "无"}；该记录不是依赖完成凭据` : "；未返回 RulePlan 评估快照"}${dependencyReferences}${consumedReferences}`;
      })
    : ["没有可展示的检查结果。"];
  const limitations = results.flatMap((result) =>
    result.limitations.map((limitation) => ({ result, limitation })),
  );
  if (limitations.length === 0) {
    return [
      "### 检查状态",
      ...outcomes,
      "",
      "### 数据与范围限制",
      "服务端没有返回额外限制条目。请结合冻结输入、来源权限和本次检查范围阅读；该状态不表示所有检查均已覆盖。",
    ];
  }
  return [
    "### 检查状态",
    ...outcomes,
    "",
    "### 数据与范围限制",
    ...limitations.map(
      ({ result, limitation }) => `- ${resultHeading(result)}：${markdownText(limitation)}`,
    ),
  ];
}

function candidateSection(results: CheckResult[]): string[] {
  const candidates: string[] = [];
  for (const result of results) {
    const addCandidateEvidence = (evidenceIds: string[]) => {
      if (evidenceIds.length === 0) {
        candidates.push("  - 当前没有绑定可回跳的证据。");
        return;
      }
      for (const evidenceId of evidenceIds) {
        const evidence = result.evidence.find((item) => item.evidence_id === evidenceId);
        candidates.push(evidence
          ? evidenceDescription(evidence, "  ")
          : `  - 证据编号 ${markdownCode(evidenceId)} 不在当前结果证据列表中。`);
      }
    };
    for (const candidate of result.fact_candidates ?? []) {
      const value = candidate.value === null ? "未形成候选值" : markdownText(candidate.value);
      candidates.push(
        `- ${markdownCode(candidate.predicate)}：${value}（${candidate.state === "candidate" ? "候选，待人工核验" : "尚未识别"}）`,
      );
      addCandidateEvidence(candidate.evidence_ids);
    }
    for (const candidate of result.slot_candidates ?? []) {
      if (result.rule_id === "legal.price.definition" && candidate.slot_id === "price_basis") {
        const value = candidate.value && typeof candidate.value === "object" && !Array.isArray(candidate.value)
          ? candidate.value as Record<string, unknown>
          : {};
        const currency = typeof value.currency === "string" ? ` ${markdownText(value.currency)}` : "";
        candidates.push(
          `- 合同价款口径（${candidateStateLabels[candidate.state] ?? "待核验"}）：含税价 ${markdownCandidateText(value.tax_inclusive_total)}${currency}；未税价 ${markdownCandidateText(value.tax_exclusive_total)}${currency}；税额 ${markdownCandidateText(value.tax_amount)}${currency}；税率 ${markdownCandidateText(value.tax_rate)}`,
        );
      } else if (result.rule_id === "legal.price.definition" && candidate.slot_id === "price_sources") {
        const sources = Array.isArray(candidate.value) ? candidate.value : [];
        candidates.push(`- 价款来源候选（${candidateStateLabels[candidate.state] ?? "待核验"}）：`);
        if (sources.length === 0) candidates.push("  - 未形成来源候选。");
        for (const [index, source] of sources.entries()) {
          if (!source || typeof source !== "object" || Array.isArray(source)) {
            candidates.push(`  - 第 ${index + 1} 项格式无法展示。`);
            continue;
          }
          const item = source as Record<string, unknown>;
          candidates.push(
            `  - ${priceSourceLabels[String(item.source_type)] ?? "其他来源"}：${markdownCandidateText(item.amount)}${typeof item.currency === "string" ? ` ${markdownText(item.currency)}` : ""}（${item.priority === "primary" ? "主要来源候选" : "其他来源候选"}）`,
          );
        }
      } else if (result.rule_id === "legal.price.definition" && candidate.slot_id === "payment_basis") {
        const stages = Array.isArray(candidate.value) ? candidate.value : [];
        candidates.push(`- 付款分母与阶段金额候选（${candidateStateLabels[candidate.state] ?? "待核验"}）：`);
        if (stages.length === 0) candidates.push("  - 未形成阶段候选。");
        for (const [index, stage] of stages.entries()) {
          if (!stage || typeof stage !== "object" || Array.isArray(stage)) {
            candidates.push(`  - 第 ${index + 1} 项格式无法展示。`);
            continue;
          }
          const item = stage as Record<string, unknown>;
          const amountParts = [
            typeof item.percentage === "string" ? `比例 ${markdownText(item.percentage)}%` : null,
            typeof item.fixed_amount === "string" ? `固定金额 ${markdownText(item.fixed_amount)}${typeof item.currency === "string" ? ` ${markdownText(item.currency)}` : ""}` : null,
            item.residual === true ? "剩余款项" : null,
          ].filter((part): part is string => part !== null);
          candidates.push(
            `  - ${typeof item.stage === "string" ? markdownText(item.stage) : `付款阶段 ${index + 1}`}：${amountParts.join("；") || "金额形式未明确"}；分母 ${denominatorLabels[String(item.denominator)] ?? "未识别"}`,
          );
        }
      } else if (result.rule_id === "legal.price.definition" && candidate.slot_id === "price_basis_judgment") {
        const value = candidate.value && typeof candidate.value === "object" && !Array.isArray(candidate.value)
          ? candidate.value as Record<string, unknown>
          : {};
        const reasons = Array.isArray(value.reasons)
          ? value.reasons.filter((reason): reason is string => typeof reason === "string")
          : [];
        candidates.push(`- 价款口径核对状态（${candidateStateLabels[candidate.state] ?? "待核验"}）：${priceOutcomeLabels[String(value.outcome)] ?? "待人工核对"}`);
        for (const reason of reasons) candidates.push(`  - ${markdownText(reason)}`);
      } else {
        const value = candidate.value === null
          ? "未形成候选值"
          : markdownText(JSON.stringify(candidate.value));
        candidates.push(
          `- ${markdownCode(candidate.slot_id)}：${value}（状态：${markdownText(candidate.state)}；fact type：${candidate.fact_type ? markdownCode(candidate.fact_type) : "未声明"}）`,
        );
      }
      addCandidateEvidence(candidate.evidence_ids);
    }
  }
  return candidates.length > 0
    ? candidates
    : ["本次结果未返回事实或槽位候选。未返回候选不证明合同中不存在相应约定。"];
}

function definitionSection(results: CheckResult[]): string[] {
  const manifests = new Map<string, NonNullable<CheckResult["definition_manifest"]>>();
  for (const result of results) {
    const manifest = result.definition_manifest;
    if (manifest) manifests.set(manifest.manifest_sha256, manifest);
  }
  if (manifests.size === 0) {
    return ["服务端未返回冻结定义清单；本摘要无法证明规则、Skill、知识引用或依赖拓扑的具体版本。"];
  }
  const lines: string[] = [];
  for (const manifest of manifests.values()) {
    lines.push(
      `### 定义清单 ${markdownCode(manifest.manifest_sha256)}`,
      `- 规则：${markdownCode(`${manifest.rule.rule_id}@${manifest.rule.version}`)}；源文件 ${markdownCode(manifest.rule.source_path)}；SHA-256 ${markdownCode(manifest.rule.source_sha256)}`,
      `- Skill：${markdownCode(manifest.skill.source_path)}；SHA-256 ${markdownCode(manifest.skill.source_sha256)}；状态 ${markdownText(manifest.skill.status)}`,
      `- Prompt：${manifest.prompt ? `${markdownCode(manifest.prompt.source_path)} / ${markdownCode(manifest.prompt.source_sha256)}` : "未单独绑定"}`,
      `- 用户提示：${markdownCode(manifest.user_prompt.source_path)} / ${markdownCode(manifest.user_prompt.source_sha256)}`,
      `- 工具契约：${manifest.tool_contract ? `${markdownCode(manifest.tool_contract.source_path)} / ${markdownCode(manifest.tool_contract.source_sha256)}` : "未单独绑定"}`,
      `- 输出契约：${markdownCode(manifest.output_contract.source_path)} / ${markdownCode(manifest.output_contract.source_sha256)}`,
      `- 实现包：源树 ${markdownCode(manifest.implementation_bundle.source_tree_sha256)}；依赖锁 ${markdownCode(manifest.implementation_bundle.dependency_lock_sha256)}；项目配置 ${markdownCode(manifest.implementation_bundle.project_config_sha256)}`,
      `- 知识引用：法律 ${manifest.knowledge_sources.legal_normative_refs.map(markdownCode).join("、") || "无"}；企业 ${manifest.knowledge_sources.enterprise_position_refs.map(markdownCode).join("、") || "无"}；内容绑定 ${markdownText(manifest.knowledge_sources.content_binding)}`,
      `- 依赖图：${markdownText(manifest.dependency_graph.status)}；SHA-256 ${markdownCode(manifest.dependency_graph.graph_sha256)}；阻断项 ${manifest.dependency_graph.blockers.map(markdownCode).join("、") || "无"}；执行顺序 ${manifest.dependency_graph.execution_order.map(markdownCode).join(" → ") || "空"}`,
      "- 依赖图仅是冻结拓扑；不是规则执行证明，也不是审批记录。",
      "",
    );
  }
  return lines;
}

export function buildRunReportMarkdown(
  data: RunResults,
  generatedAt = new Date().toISOString(),
): string {
  const researchResults = data.results.filter((result) => result.research_review);
  if (researchResults.length) {
    const lines = ["# 合同法律与公司制度审核", "", `运行编号：${markdownCode(data.run.run_id)}`,
      `生成时间：${markdownText(generatedAt)}`, "",
      ...researchResults.flatMap((result) => { const review = result.research_review as unknown as ResearchReview; return [
        `资料快照 SHA-256：${markdownCode(review.sources_sha256)}`,
        `审核定义 SHA-256：${markdownCode(review.snapshot_sha256)}`,
        `持久结果 SHA-256：${markdownCode(result.task_result_sha256)}`,
        `本次审查主题：${review.reviewed_topics.map(markdownText).join("、")}`, "",
      ]; }), ...findingSection(researchResults), "", "## 待核实事项与覆盖限制", "",
      ...researchResults.flatMap((result) => result.limitations.map((item) => `- ${markdownText(item)}`)), "",
    ];
    const markdown = lines.join("\n");
    if (new TextEncoder().encode(markdown).byteLength > MAX_RUN_REPORT_BYTES) throw new RunReportTooLarge();
    return markdown;
  }
  const manifest = data.results
    .map((result) => result.definition_manifest)
    .find((value) => value !== undefined);
  const knowledgeBinding = manifest?.knowledge_sources.content_binding ?? "未提供";
  const skillState = manifest?.skill.status ?? "未提供";
  const runStatus = data.run.status;
  const rules = [...new Set(data.results.map((result) => `${result.rule_id}@${result.rule_version}`))];
  const factSnapshotIds = [...new Set(
    data.results.flatMap((result) => result.fact_set_snapshot_id ? [result.fact_set_snapshot_id] : []),
  )];
  const persistedResultHashes = [...new Set(data.results.map((result) => result.task_result_sha256))];
  const lines = [
    "# 合同审查运行摘要",
    "",
    "> **开发态文件，不是正式法律意见或签约结论。** 内容仅整理当前身份在生成时通过服务端授权读取的运行结果。此文件没有签名或审批效力；原文权限、冻结输入或结果变化后，应重新读取服务端结果。没有问题发现不代表合同无风险。",
    "",
    "## 运行信息",
    "",
    `- 运行编号：${markdownCode(data.run.run_id)}`,
    `- 运行状态：${markdownText(runStatus)}`,
    `- 冻结输入快照：${data.run.input_snapshot_id ? markdownCode(data.run.input_snapshot_id) : "未绑定"}`,
    `- 运行创建时间：${markdownText(data.run.created_at)}`,
    `- 文件生成时间：${markdownText(generatedAt)}`,
    `- 持久结果 SHA-256：${persistedResultHashes.map(markdownCode).join("、") || "无结果哈希"}`,
    `- 规则版本：${rules.map(markdownCode).join("、") || "无结果"}`,
    `- 事实集快照：${factSnapshotIds.map(markdownCode).join("、") || "本次结果未绑定"}`,
    `- 知识绑定状态：${markdownText(knowledgeBinding)}`,
    `- Skill 状态：${markdownText(skillState)}`,
    "",
    "## 合同审核发现",
    "",
    ...findingSection(data.results),
    "",
    "## 签约决策参考",
    "",
    ...decisionSection(data.results),
    "",
    "## 审核范围与数据提示",
    "",
    ...limitationsSection(data.results),
    "",
    "## 来源与定义覆盖",
    "",
    ...definitionSection(data.results),
    "",
    "## 待核验候选",
    "",
    "> 下列值是抽取或人工确认流程中的候选信息，不会因出现在此报告而变成规则判断。",
    "",
    ...candidateSection(data.results),
    "",
  ];
  const markdown = lines.join("\n");
  if (new TextEncoder().encode(markdown).byteLength > MAX_RUN_REPORT_BYTES) {
    throw new RunReportTooLarge();
  }
  return markdown;
}
