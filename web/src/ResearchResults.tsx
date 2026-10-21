import { Alert, Button, Card, Collapse, Empty, Space, Tag, Typography } from "antd";

export interface ResearchReview {
  summary: string; understood_contract: string; reviewed_topics: string[]; limitations: string[];
  snapshot_sha256: string; sources_sha256: string;
  findings: Array<{ domain: "legal" | "company"; title: string; status: string; severity: string;
    issue_type: string; unresolved_facts: string[];
    reasoning: string; applicability: string; next_action: string;
    contract_evidence: Array<{ document_version_id: string; page: number; quote: string }>;
    source_evidence: Array<{ source_id: string; quote: string }> }>;
  sources: Array<{ source_id: string; origin: string; kind: string; title: string; url: string;
    locator: string; text: string; content_sha256: string; version?: number; document_id?: string;
    current_status?: string; effective_from?: string; effective_until?: string; retrieved_at?: string }>;
  searches: Array<{ channel: string; query: string; keywords: string[]; source_ids: string[]; warning?: string; searched_at: string }>;
}
const statuses: Record<string, string> = { risk_candidate: "法律风险候选", policy_deviation: "公司制度偏离", no_issue_found: "本项未发现问题", needs_material: "需补充材料", undetermined: "暂不能判断", human_review: "需人工复核" };
const channels: Record<string, string> = { legal_local: "本地法规", legal_web: "联网法律", company: "公司制度" };
export function ResearchResults({ review, onContractQuote }: { review: ResearchReview; onContractQuote?: (quote: { document_version_id: string; page: number; quote: string }) => void }) {
  return <section className="research-results" aria-label="法律与公司制度审核结果">
    <Card title="一、合同概况与审查范围"><Typography.Paragraph><Typography.Text strong>审查摘要：</Typography.Text>{review.summary}</Typography.Paragraph><Typography.Paragraph>{review.understood_contract}</Typography.Paragraph><Space wrap>{review.reviewed_topics.map((topic) => <Tag key={topic}>{topic}</Tag>)}</Space></Card>
    {(["legal", "company"] as const).map((domain) => <section key={domain} className="report-chapter"><Typography.Title level={3}>{domain === "legal" ? "二、法律合规审查" : "三、公司制度审查"}</Typography.Title><Space orientation="vertical" size={16} style={{ width: "100%" }}>
      {review.findings.filter((finding) => finding.domain === domain).map((finding, index) => <Card key={index} title={finding.title} extra={<Tag color={finding.status === "no_issue_found" ? "green" : finding.status === "needs_material" ? "gold" : "orange"}>{finding.issue_type === "drafting" && finding.status === "risk_candidate" ? "条款完善建议" : statuses[finding.status] ?? finding.status}</Tag>}>
        <Typography.Paragraph>{finding.reasoning}</Typography.Paragraph>
        <Typography.Paragraph><Typography.Text strong>适用性：</Typography.Text>{finding.applicability}</Typography.Paragraph>
        {finding.unresolved_facts?.length > 0 && <Alert type="warning" title="需要核实的事实" description={finding.unresolved_facts.join("；")} />}
        {finding.contract_evidence.map((quote, i) => <div key={i}><Typography.Text type="secondary">合同原文 · 第 {quote.page} 页</Typography.Text>{onContractQuote && <Button type="link" onClick={() => onContractQuote(quote)}>打开原文位置</Button>}<blockquote style={{ whiteSpace: "pre-wrap" }}>{quote.quote}</blockquote></div>)}
        {finding.source_evidence.map((citation, i) => { const source = review.sources.find((item) => item.source_id === citation.source_id); return <div key={i} className="sales-check-basis"><Space wrap><Typography.Text strong>{source?.title ?? "依据"}</Typography.Text><Tag>{source?.origin === "web" ? "联网原文" : `本地资料 v${source?.version ?? ""}`}</Tag>{source?.current_status === "withdrawn" && <Tag color="gold">本资料现已停用</Tag>}</Space><Typography.Paragraph type="secondary">{source?.locator} · 生效：{source?.effective_from ?? "需核实"}{source?.effective_until ? ` ～ ${source.effective_until}` : ""}</Typography.Paragraph><blockquote style={{ whiteSpace: "pre-wrap" }}>{citation.quote}</blockquote>{source?.url && <a href={source.url} target="_blank" rel="noreferrer">查看原文来源</a>}</div>; })}
        <Typography.Paragraph style={{ marginTop: 16, marginBottom: 0 }}><Typography.Text strong>建议处理：</Typography.Text>{finding.next_action}</Typography.Paragraph>
      </Card>)}
      {!review.findings.some((finding) => finding.domain === domain) && <Empty description="本维度没有形成可核验的比对结果" />}
    </Space></section>)}
    {review.limitations.length > 0 && <Alert type="warning" showIcon title="待核实事项与覆盖限制" description={<ul>{review.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul>} />}
    <Collapse style={{ marginTop: 16 }} items={[{ key: "searches", label: `查看检索过程（${review.searches.length} 次）`, children: <>{review.searches.map((search, i) => <section key={i} style={{ marginBottom: 16 }}><Tag>{channels[search.channel]}</Tag><Typography.Text strong>{search.query}</Typography.Text><Typography.Paragraph type="secondary">{new Date(search.searched_at).toLocaleString()} · 关键词：{search.keywords.join("、")} · 命中 {search.source_ids.length} 个原文段落</Typography.Paragraph>{search.warning && <Alert type="warning" title={search.warning} />}</section>)}<Typography.Paragraph type="secondary">本次资料快照：{review.sources_sha256}</Typography.Paragraph></> }]} />
  </section>;
}
