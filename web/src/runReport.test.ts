import { describe, expect, it } from "vitest";
import type { RunResults } from "./api";
import { buildRunReportMarkdown, MAX_RUN_REPORT_BYTES, RunReportTooLarge } from "./runReport";

const baseRun: RunResults = {
  run: {
    run_id: "11111111-1111-4111-8111-111111111111",
    case_id: "22222222-2222-4222-8222-222222222222",
    input_snapshot_id: "33333333-3333-4333-8333-333333333333",
    status: "completed",
    version: 1,
    created_at: "2026-09-29T01:00:00Z",
  },
  results: [],
  can_confirm_facts: false,
};

describe("buildRunReportMarkdown", () => {
  it("states the non-final boundary and does not equate no findings with no risk", () => {
    const report = buildRunReportMarkdown(baseRun, "2026-09-29T02:00:00Z");

    expect(report).toContain("不是正式法律意见或签约结论");
    expect(report).toContain("没有问题发现不代表合同无风险");
    expect(report).toContain("没有返回 `issue_found` 状态");
    expect(report).toContain("未返回候选不证明合同中不存在相应约定");
  });

  it("keeps all three result layers separate and labels extracted data as candidates", () => {
    const report = buildRunReportMarkdown({
      ...baseRun,
      results: [{
        result_id: "44444444-4444-4444-8444-444444444444",
        task_result_sha256: "e".repeat(64),
        rule_id: "legal.payment_conditions",
        rule_version: "1.0.0",
        judgment: "issue_found",
        severity: "high",
        summary: "付款条款有待核验",
        evidence: [{
          evidence_id: "55555555-5555-4555-8555-555555555555",
          kind: "document_span",
          support: "direct",
          source_version: "a".repeat(64),
          document_version_id: "66666666-6666-4666-8666-666666666666",
          page: 2,
          start_offset: 0,
          end_offset: 8,
          quote: "付款比例 100%",
          text_hash: "b".repeat(64),
        }],
        slot_candidates: [{
          slot_id: "payment_schedule",
          state: "filled",
          value: [{ trigger: "验收", percentage: "100" }],
          fact_type: "contract.payment_schedule",
          evidence_ids: ["55555555-5555-4555-8555-555555555555"],
        }, {
          slot_id: "price_basis",
          state: "filled",
          value: {
            tax_inclusive_total: "1000",
            tax_exclusive_total: "900",
            tax_amount: "90",
            currency: "CNY",
          },
          fact_type: "contract.price_basis",
          evidence_ids: ["55555555-5555-4555-8555-555555555555"],
        }],
        fact_candidates: [{
          predicate: "contract.total_amount",
          state: "candidate",
          value: "CNY 1000",
          evidence_ids: ["55555555-5555-4555-8555-555555555555"],
        }],
        limitations: ["legal.price.definition 未完成", "价款来源不一致，需人工核对"],
        dependency_result_references: [{
          kind: "dependency_result_reference",
          check_id: "legal.price.definition",
          dependency_rule_id: "legal.price.definition",
          dependency_rule_version: "1.0.0",
          dependency_rule_sha256: "a".repeat(64),
          definition_manifest_sha256: "b".repeat(64),
          case_id: "22222222-2222-4222-8222-222222222222",
          input_snapshot_id: "33333333-3333-4333-8333-333333333333",
          fact_set_snapshot_id: null,
          source_run_id: "11111111-1111-4111-8111-111111111111",
          result_id: "44444444-4444-4444-8444-444444444444",
          task_result_sha256: "c".repeat(64),
          assessment_sha256: "d".repeat(64),
          evidence_manifest_sha256: "e".repeat(64),
          observed_judgment: "undetermined",
          assessment_status: "incomplete",
          is_completion_proof: false,
          reference_sha256: "f".repeat(64),
        }],
        created_at: "2026-09-29T01:01:00Z",
        fact_set_snapshot_id: null,
      }],
    }, "2026-09-29T02:00:00Z");

    expect(report).toContain("## 合同审核发现");
    expect(report).toContain("## 签约决策参考");
    expect(report).toContain("## 审核范围与数据提示");
    expect(report).toContain("### 检查状态");
    expect(report).toContain("状态：issue\\_found");
    expect(report).toContain("### 数据与范围限制");
    expect(report).toContain("## 待核验候选");
    expect(report).toContain("CNY 1000（候选，待人工核验）");
    expect(report).toMatch(/price_basis/);
    expect(report).toContain("tax\\_inclusive\\_total");
    expect(report).toContain("价款来源不一致，需人工核对");
    expect(report).toContain("前置结果引用 `legal.price.definition`");
    expect(report).toContain("来源运行 `11111111-1111-4111-8111-111111111111`");
    expect(report).toContain("付款比例 100%");
    expect(report).toContain("66666666-6666-4666-8666-666666666666");
    expect(report).toContain("持久结果 SHA-256");
    expect(report).toContain("e".repeat(64));
    expect(report).toContain("legal\\.price\\.definition");
    expect(report).toContain("字符区间 [0, 8)");
    expect(report).toContain("引用 SHA-256");
  });

  it("renders price candidates as readable fields and localized review reasons", () => {
    const report = buildRunReportMarkdown({
      ...baseRun,
      results: [{
        result_id: "44444444-4444-4444-8444-444444444444",
        task_result_sha256: "e".repeat(64),
        rule_id: "legal.price.definition",
        rule_version: "1.0.0",
        judgment: "undetermined",
        severity: "undetermined",
        evidence: [],
        slot_candidates: [
          {
            slot_id: "price_basis",
            state: "human_review",
            value: {
              tax_inclusive_total: "1000",
              tax_exclusive_total: "900",
              tax_amount: "100",
              tax_rate: null,
              currency: "CNY",
            },
            evidence_ids: [],
          },
          {
            slot_id: "price_sources",
            state: "conflicted",
            value: [{ source_type: "attachment", amount: "1200", currency: "CNY", priority: "secondary" }],
            evidence_ids: [],
          },
          {
            slot_id: "payment_basis",
            state: "human_review",
            value: [{ stage: "验收", denominator: "unspecified", percentage: "100", fixed_amount: null }],
            evidence_ids: [],
          },
        ],
        fact_candidates: [],
        limitations: ["价款来源不一致，需人工核对。"],
        rule_plan_execution: {
          kind: "rule_plan_assessment",
          input_snapshot_id: baseRun.run.input_snapshot_id!,
          fact_set_snapshot_id: null,
          rule_id: "legal.price.definition",
          rule_version: "1.0.0",
          rule_source_sha256: "a".repeat(64),
          plan_sha256: "b".repeat(64),
          facts_sha256: "c".repeat(64),
          assessment_status: "blocked",
          blocked_by_checks: ["legal.price.definition"],
          missing_slots: [],
          unresolved_minimum_judgment_indexes: [],
          is_dependency_completion_proof: false,
          assessment_sha256: "d".repeat(64),
        },
        created_at: "2026-09-29T01:01:00Z",
      }],
    });

    expect(report).toContain("含税价 1000 CNY；未税价 900 CNY；税额 100 CNY");
    expect(report).toContain("附件：1200 CNY（其他来源候选）");
    expect(report).toContain("付款分母与阶段金额候选（需要人工复核）");
    expect(report).toContain("分母 未明确");
    expect(report).toContain("该记录不是依赖完成凭据");
  });

  it("includes frozen definition, source, knowledge, and dependency provenance", () => {
    const manifest = {
      kind: "development_execution_definitions" as const,
      rule: {
        source_path: "docs/development/06-rules/rules/payment.yaml",
        source_sha256: "a".repeat(64),
        rule_id: "legal.payment_conditions",
        version: "1.0.0",
      },
      skill: {
        source_path: "skills/generated/SKILL.md",
        source_sha256: "b".repeat(64),
        generator_version: "renderer-v1",
        status: "development_candidate" as const,
        rule_sha256: "a".repeat(64),
        tool_contract_sha256: "c".repeat(64),
        output_schema_sha256: "d".repeat(64),
        publication_blockers: ["knowledge_unapproved"],
        generated_at: "2026-09-29T01:00:00+00:00",
      },
      user_prompt: { source_path: "prompts/user.md", source_sha256: "e".repeat(64) },
      output_contract: { source_path: "schemas/output.json", source_sha256: "f".repeat(64) },
      implementation_bundle: {
        source_tree_sha256: "1".repeat(64),
        source_file_count: 42,
        dependency_lock_sha256: "2".repeat(64),
        project_config_sha256: "3".repeat(64),
        python_version: "3.13",
      },
      knowledge_sources: {
        legal_normative_refs: ["law-source-v1"],
        enterprise_position_refs: ["payment-policy-v1"],
        content_binding: "unresolved_development_references" as const,
      },
      dependency_graph: {
        kind: "development_rule_dependency_graph" as const,
        status: "blocked" as const,
        definitions: [],
        execution_order: [],
        blockers: ["dependent_check_definition_missing:legal.price.definition"],
        graph_sha256: "4".repeat(64),
        is_execution_proof: false as const,
        is_approval_record: false as const,
      },
      manifest_sha256: "5".repeat(64),
    };
    const report = buildRunReportMarkdown({
      ...baseRun,
      results: [{
        result_id: "44444444-4444-4444-8444-444444444444",
        task_result_sha256: "e".repeat(64),
        rule_id: "legal.payment_conditions",
        rule_version: "1.0.0",
        judgment: "undetermined",
        severity: "undetermined",
        evidence: [],
        slot_candidates: [],
        fact_candidates: [],
        limitations: [],
        created_at: "2026-09-29T01:01:00Z",
        definition_manifest: manifest,
      }],
    });

    expect(report).toContain("## 来源与定义覆盖");
    expect(report).toContain("payment.yaml");
    expect(report).toContain("law-source-v1");
    expect(report).toContain("payment-policy-v1");
    expect(report).toContain("dependent_check_definition_missing:legal.price.definition");
    expect(report).toContain("不是规则执行证明，也不是审批记录");
  });

  it("does not misclassify a structured external source as a contract finding", () => {
    const report = buildRunReportMarkdown({
      ...baseRun,
      results: [{
        result_id: "44444444-4444-4444-8444-444444444444",
        task_result_sha256: "e".repeat(64),
        rule_id: "legal.payment_conditions",
        rule_version: "1.0.0",
        judgment: "undetermined",
        severity: "undetermined",
        evidence: [{
          evidence_id: "55555555-5555-4555-8555-555555555555",
          kind: "source_record",
          support: "context",
          source_version: "source-v1",
          document_version_id: null,
          page: null,
          start_offset: null,
          end_offset: null,
          quote: null,
          text_hash: null,
          source_record_ref: "finance:account-42",
        }],
        slot_candidates: [],
        fact_candidates: [],
        limitations: [],
        created_at: "2026-09-29T01:01:00Z",
        fact_set_snapshot_id: null,
      }],
    }, "2026-09-29T02:00:00Z");

    expect(report).toContain("签约决策背景参考");
    expect(report).not.toContain("### `legal.payment_conditions` v1.0.0");
  });

  it("reports non-finding check states so needs-material and not-applicable results remain visible", () => {
    const report = buildRunReportMarkdown({
      ...baseRun,
      results: [{
        result_id: "44444444-4444-4444-8444-444444444444",
        task_result_sha256: "e".repeat(64),
        rule_id: "legal.payment_conditions",
        rule_version: "1.0.0",
        judgment: "needs_material",
        severity: "undetermined",
        summary: "解析覆盖不完整",
        evidence: [],
        slot_candidates: [],
        fact_candidates: [],
        limitations: ["缺少可核验的完整页码"],
        created_at: "2026-09-29T01:01:00Z",
        rule_plan_execution: {
          kind: "rule_plan_assessment",
          input_snapshot_id: baseRun.run.input_snapshot_id!,
          fact_set_snapshot_id: null,
          rule_id: "legal.payment_conditions",
          rule_version: "1.0.0",
          rule_source_sha256: "a".repeat(64),
          plan_sha256: "b".repeat(64),
          facts_sha256: "c".repeat(64),
          assessment_status: "needs_material",
          blocked_by_checks: ["legal.price.definition"],
          missing_slots: ["payment_schedule"],
          unresolved_minimum_judgment_indexes: [],
          is_dependency_completion_proof: false,
          assessment_sha256: "d".repeat(64),
        },
      }],
    });

    expect(report).toContain("状态 needs\\_material");
    expect(report).toContain("解析覆盖不完整");
    expect(report).toContain("缺少可核验的完整页码");
    expect(report).toContain(String.raw`RulePlan 评估 needs\_material`);
    expect(report).toContain("该记录不是依赖完成凭据");
  });

  it("refuses to generate a report larger than the download bound", () => {
    const oversized: RunResults = {
      ...baseRun,
      results: Array.from({ length: 500 }, (_, index) => ({
        result_id: `44444444-4444-4444-8444-${String(index).padStart(12, "0")}`,
        task_result_sha256: "e".repeat(64),
        rule_id: "legal.payment_conditions",
        rule_version: "1.0.0",
        judgment: "undetermined" as const,
        severity: "undetermined" as const,
        evidence: [],
        slot_candidates: [],
        fact_candidates: [],
        limitations: Array.from({ length: 20 }, () => "限制".repeat(250)),
        created_at: "2026-09-29T01:01:00Z",
      })),
    };

    expect(MAX_RUN_REPORT_BYTES).toBe(4 * 1024 * 1024);
    expect(() => buildRunReportMarkdown(oversized)).toThrow(RunReportTooLarge);
  });
});
