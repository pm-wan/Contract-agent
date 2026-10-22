import { useEffect, useState } from "react";
import { Alert, Breadcrumb, Button, Card, Collapse, Descriptions, Empty, Result, Space, Spin, Tag, Typography } from "antd";
import { Link, useParams } from "react-router-dom";
import { ApiError, getUpstreamComparisonResults, type UpstreamComparisonResults } from "./api";

const { Title, Text, Paragraph } = Typography;
type ComparisonResult = NonNullable<UpstreamComparisonResults["result"]>;
type ComparisonEvidence = ComparisonResult["pairs"][number]["categories"][number]["evidence"][number];
const markdownSpecialCharacters = new Set(["\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", ".", "!", "|", ">"]);

export const categoryTitles: Record<string, string> = {
  work_scope_deliverables: "工作范围与交付物",
  delivery_schedule: "交付计划",
  acceptance_remediation: "验收与整改",
  quality_service_assurance: "质量、服务与保障",
  warranty_defect_handling: "质保与缺陷处理",
  compliance_qualification_site_requirements: "合规、资质与现场要求",
  data_confidentiality_security: "数据、保密与安全",
  intellectual_property_third_party_rights: "知识产权与第三方权利",
  changes_project_coordination: "变更与项目配合",
  payment_commercial_terms: "付款与商业条件",
  liability_risk_allocation: "违约责任与风险分配",
  subcontracting_personnel: "分包与人员安排",
  suspension_termination_exit: "暂停、解除与退出",
  documents_reports_audit: "文件、报告与审计",
};

export const coverageLabels: Record<string, string> = {
  covered: "已找到对应约定",
  partial: "部分对应",
  missing: "候选未覆盖",
  inconsistent: "候选不一致",
  not_applicable: "候选不适用",
};

export const stateLabels: Record<string, string> = {
  candidate: "候选已形成，待复核",
  needs_material: "需要补充材料",
  undetermined: "暂无法判定",
  human_review: "需要人工复核",
};

const statusLabels: Record<UpstreamComparisonResults["run"]["status"], string> = {
  queued: "等待处理",
  running: "正在比对",
  waiting: "待人工复核",
  completed: "候选结果已生成",
  failed: "处理失败",
  cancelled: "已取消",
  superseded: "输入已更新",
};

function markdownSafeInline(value: string): string {
  return [...value.replace(/\r?\n/g, " ")]
    .map((character) => markdownSpecialCharacters.has(character) ? `\\${character}` : character)
    .join("");
}

export function reportMarkdown(response: UpstreamComparisonResults): string {
  const result = response.result;
  if (!result) return "";
  const lines = [
    "# 上下游合同比对候选报告",
    "",
    `- 运行编号：${response.run.run_id}`,
    `- 冻结快照：${result.input_snapshot_id}`,
    `- 比对版本：${result.comparison_version}`,
    `- 模型配置摘要：${result.definition.model_profile_sha256 ?? "未记录"}`,
    `- 执行轨迹 SHA-256：${result.execution_trace.trace_sha256}`,
    `- 模型用量：${result.execution_trace.model_usage?.input_tokens ?? "未报告"} 输入 tokens / ${result.execution_trace.model_usage?.output_tokens ?? "未报告"} 输出 tokens`,
    "- 结果性质：机器定位候选，需审核人结合完整合同和项目背景复核，不构成法律意见或最终结论。",
    "",
  ];
  for (const pair of result.pairs) {
    lines.push(
      `## 项目客户合同：${markdownSafeInline(pair.upstream_file_name)}`,
      "",
      `- 项目客户合同版本：${pair.upstream_document_version_id}`,
      `- 采购合同：${markdownSafeInline(pair.procurement_file_name)}`,
      `- 采购合同版本：${pair.procurement_document_version_id}`,
      "",
    );
    for (const category of pair.categories) {
      lines.push(
        `### ${categoryTitles[category.category] ?? category.category}`,
        "",
        `- 状态：${stateLabels[category.state] ?? category.state}`,
        `- 覆盖候选：${category.coverage ? coverageLabels[category.coverage] ?? category.coverage : "未形成"}`,
        `- 适用性：${category.applicability ?? "未形成"}`,
      );
      for (const side of ["upstream", "procurement"] as const) {
        const evidence = category.evidence.filter((item) => item.side === side);
        lines.push(`- ${side === "upstream" ? "项目客户合同依据" : "采购合同依据"}：`);
        if (evidence.length === 0) lines.push("  - 未提供可核验引用");
        for (const item of evidence) {
          if (item.kind === "document_span") {
            lines.push(
              `  - 第 ${item.page ?? "?"} 页原文：`,
              ...(item.quote ?? "（无引文）").split(/\r?\n/).map((line) => `    ${line}`),
              `    - 文档版本：${item.document_version_id}；原件 SHA-256：${item.source_sha256}；解析器：${item.parser_version}`,
              `    - 解析产物 SHA-256：${item.parsed_sha256}；引文 SHA-256：${item.quote_sha256 ?? "未生成"}`,
            );
          } else {
            lines.push(
              `  - 全文检索范围：第 ${item.searched_pages.join("、")} 页；检索词：${item.query_terms.join("、")}`,
              `    - 文档版本：${item.document_version_id}；原件 SHA-256：${item.source_sha256}；解析器：${item.parser_version}`,
              `    - 解析产物 SHA-256：${item.parsed_sha256}；解析覆盖：${item.parser_coverage ?? "未确认"}`,
            );
          }
        }
      }
      for (const limitation of category.limitations) lines.push(`- 复核限制：${markdownSafeInline(limitation)}`);
      lines.push("");
    }
    for (const limitation of pair.limitations) lines.push(`- 本组限制：${markdownSafeInline(limitation)}`);
    lines.push("");
  }
  lines.push("## 整体限制", "", ...result.limitations.map((item) => `- ${markdownSafeInline(item)}`), "");
  return lines.join("\n");
}

function downloadReport(response: UpstreamComparisonResults) {
  const content = reportMarkdown(response);
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `上下游合同比对-${response.run.run_id.slice(0, 8)}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function EvidenceBlock({ evidence }: { evidence: ComparisonEvidence }) {
  if (evidence.kind === "absence_evidence") {
    return <div className="comparison-evidence">
      <Text strong>全文检索范围</Text>
      <div>页码：{evidence.searched_pages.join("、")}</div>
      <div>关键词：{evidence.query_terms.join("、")}</div>
      <Text type="secondary">解析覆盖：{evidence.parser_coverage === "complete" ? "所有逻辑页均已检查" : "未确认"}</Text>
    </div>;
  }
  return <div className="comparison-evidence">
    <Text strong>第 {evidence.page ?? "?"} 页 · 原文</Text>
    <blockquote>{evidence.quote ?? "未提供引文"}</blockquote>
    <Text type="secondary">原件 SHA-256：{evidence.source_sha256} · 解析器：{evidence.parser_version}</Text>
    <br />
    <Text type="secondary">引文 SHA-256：{evidence.quote_sha256 ?? "未生成"}</Text>
  </div>;
}

export function UpstreamComparisonResultsPage() {
  const { runId = "" } = useParams();
  const [response, setResponse] = useState<UpstreamComparisonResults | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | null = null;
    const poll = async () => {
      try {
        const next = await getUpstreamComparisonResults(runId, controller.signal);
        if (controller.signal.aborted) return;
        setResponse(next);
        setError(null);
        if (["queued", "running"].includes(next.run.status)) {
          timer = window.setTimeout(() => void poll(), 2500);
        }
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
      }
    };
    setResponse(null);
    setError(null);
    void poll();
    return () => {
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [runId, retryKey]);

  if (error && !response) {
    const denied = [401, 403, 404].includes(error.status);
    return <>
      <nav aria-label="页面层级"><Breadcrumb items={[{ title: <Link to="/cases">案件列表</Link> }, { title: "上下游比对" }]} /></nav>
      <Result
        status={denied ? (error.status === 404 ? "404" : "403") : "500"}
        title={error.status === 401 ? "登录状态已失效" : error.status === 404 ? "比对结果不可用" : error.status === 403 ? "无权查看比对结果" : "比对结果暂时无法读取"}
        subTitle={denied ? "运行不存在、材料授权已撤回，或当前账号无权读取冻结来源。" : "检查服务连接后重试。"}
        extra={<Space><Button onClick={() => setRetryKey((value) => value + 1)}>重试</Button><Link to="/cases">返回案件列表</Link></Space>}
      />
    </>;
  }
  if (!response) return <div className="route-loading" aria-live="polite"><Spin size="large" description="正在读取上下游比对状态…" /></div>;

  const { run, result } = response;
  const hasVerifiedCategory = (pair: NonNullable<typeof result>["pairs"][number]) => pair.categories.some((item) => item.evidence.length > 0 || (item.state === "candidate" && item.applicability === "not_applicable"));
  const comparisonFailed = !!result?.pairs.length && !result.pairs.some(hasVerifiedCategory);
  const caseLink = run.case_id ? `/cases/${run.case_id}?view=results` : "/cases";
  return <>
    <nav aria-label="页面层级"><Breadcrumb items={[
      { title: <Link to="/cases">案件列表</Link> },
      { title: <Link to={caseLink}>案件比对结果</Link> },
      { title: "上下游比对详情" },
    ]} /></nav>
    <div className="case-view-heading">
      <Space align="center"><Title level={2}>上下游合同比对</Title><Tag color={comparisonFailed ? "error" : run.status === "completed" ? "success" : run.status === "failed" ? "error" : "processing"}>{comparisonFailed ? "比对失败" : statusLabels[run.status]}</Tag></Space>
      <Paragraph type="secondary">运行编号：<Text code>{run.run_id}</Text></Paragraph>
    </div>
    {error && <Alert type="warning" showIcon title="状态刷新失败" description="以下仍显示最近一次成功读取的数据。" action={<Button onClick={() => setRetryKey((value) => value + 1)}>重试</Button>} />}
    <Card className="comparison-summary-card">
      <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
        { key: "snapshot", label: "冻结快照", children: result?.input_snapshot_id ?? run.input_snapshot_id ?? "未提供" },
        { key: "type", label: "运行类型", children: "逐份项目客户合同 × 采购合同" },
        { key: "pairs", label: "合同组数", children: result ? `${result.pairs.length} 组` : "处理中" },
        { key: "categories", label: "比对类别", children: "每组 14 类" },
        { key: "trace", label: "执行轨迹哈希", children: result?.execution_trace.trace_sha256 ?? "处理中" },
      ]} />
    </Card>
    <Alert
      className="comparison-candidate-warning"
      type="warning"
      showIcon
      title="这是机器生成的比对候选，需要审核人复核"
      description="页面逐条展示上下游两侧的原文证据。‘候选未覆盖’只表示机器在当前冻结材料中未定位到对应约定，不等于法律或业务层面的最终认定。撤回材料授权后，服务端会停止返回结果。"
    />
    {run.status === "failed" && !result && <Result status="error" title="比对任务失败" subTitle="没有生成候选结果。请检查任务历史和服务配置，再重新冻结并发起比对。" />}
    {run.status !== "completed" && run.status !== "failed" && !result && <Card><Spin description="任务排队或执行中，结果生成后本页会自动刷新。" /></Card>}
    {run.status === "completed" && !result && <Result status="warning" title="本次没有可展示结果" subTitle="任务已结束但没有保存比对结果；请查看运行记录并重试。" />}
    {comparisonFailed && <Alert type="error" showIcon title="本次未形成有效比对结果" description="模型输出未通过校验，不能据此认定合同没有风险或缺少条款。请查看具体失败原因后重新发起比对。" />}
    {result && <>
      <div className="comparison-report-actions"><Button type="primary" onClick={() => downloadReport(response)}>下载 Markdown 比对报告</Button></div>
      {result.pairs.length === 0 ? <Empty description="冻结快照没有可比对的合同组" /> : result.pairs.map((pair) => <Card
        key={pair.upstream_document_version_id}
        className="comparison-pair-card"
        title={`合同组：${pair.upstream_file_name} ↔ ${pair.procurement_file_name}`}
      >
        <Descriptions size="small" column={1} items={[
          { key: "upstream", label: "项目客户合同", children: <>{pair.upstream_file_name}<br /><Text type="secondary" code>{pair.upstream_document_version_id}</Text></> },
          { key: "procurement", label: "采购合同", children: <>{pair.procurement_file_name}<br /><Text type="secondary" code>{pair.procurement_document_version_id}</Text></> },
        ]} />
        {!hasVerifiedCategory(pair) && <Alert type="error" showIcon title="本组比对失败" description="没有形成通过校验的条款结论；下方限制列出具体失败原因。" />}
        {hasVerifiedCategory(pair) && <Collapse
          className="comparison-categories"
          items={pair.categories.map((category) => ({
            key: category.category,
            label: <Space wrap><Text strong>{categoryTitles[category.category] ?? category.category}</Text><Tag color={category.coverage === "missing" || category.coverage === "inconsistent" ? "warning" : category.coverage === "covered" ? "success" : "default"}>{category.coverage ? coverageLabels[category.coverage] ?? category.coverage : "未形成覆盖结论"}</Tag><Text type="secondary">{stateLabels[category.state]}</Text></Space>,
            children: <>
              <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
                { key: "state", label: "候选状态", children: stateLabels[category.state] },
                { key: "applicability", label: "适用性", children: category.applicability ?? "尚未确定" },
              ]} />
              <div className="comparison-evidence-grid">
                {(["upstream", "procurement"] as const).map((side) => <section key={side} aria-label={side === "upstream" ? "项目客户合同证据" : "采购合同证据"}>
                  <Text strong>{side === "upstream" ? "项目客户合同 · 上游要求" : "采购合同 · 下游约定"}</Text>
                  {category.evidence.filter((item) => item.side === side).length === 0
                    ? <Text type="secondary" className="comparison-no-evidence">未提供可核验的原文证据。</Text>
                    : category.evidence.filter((item) => item.side === side).map((item, index) => <EvidenceBlock key={`${item.document_version_id}:${item.page ?? "absence"}:${index}`} evidence={item} />)}
                </section>)}
              </div>
              {category.limitations.length > 0 && <Alert className="comparison-limitations" type="info" showIcon title="复核限制" description={<ul>{category.limitations.map((item) => <li key={item}>{item}</li>)}</ul>} />}
            </>,
          }))}
        />}
        {pair.limitations.length > 0 && <Alert className="comparison-limitations" type="warning" showIcon title="本组限制" description={<ul>{pair.limitations.map((item) => <li key={item}>{item}</li>)}</ul>} />}
      </Card>)}
      <Card title="整体限制" className="comparison-limitations-card">
        <ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
      </Card>
    </>}
  </>;
}
