import { Alert, Tag, Typography } from 'antd';
import { listCaseRuns, getUpstreamComparisonResults, type RunResults, type UpstreamComparisonResults } from './api';
import { categoryTitles, coverageLabels, stateLabels, EvidenceBlock, reportMarkdown } from './UpstreamComparisonResultsPage';
export interface ReportComparison { response: UpstreamComparisonResults | null; notice: string; }
export async function loadReportComparison(data: RunResults, signal?: AbortSignal): Promise<ReportComparison> {
  const versions = new Set(data.results.flatMap(item => item.evidence.map(e => e.document_version_id).filter(Boolean)));
  let cursor: string | undefined;
  do {
    const page = await listCaseRuns(data.run.case_id, {cursor, limit: 100}, signal);
    for (const run of page.items) {
      if (run.task_type !== 'upstream_comparison') continue;
      if (['queued', 'running'].includes(run.status)) return {response: null, notice: '上下游比对正在执行，报告尚未包含该部分结果。'};
      const response = await getUpstreamComparisonResults(run.run_id, signal);
      if (!response.result) return {response, notice: '最近的上下游比对未生成结果，报告尚不完整。'};
      const pairs = response.result.pairs;
      if (!pairs.length || !pairs.every(pair => versions.has(pair.procurement_document_version_id))) {
        return {response: null, notice: '最近比对与本报告的主合同版本无法确认一致，未合并历史结果。'};
      }
      return {response, notice: ''};
    }
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  return {response: null, notice: '本案尚未执行上下游比对；本报告仅包含合同审查结果。'};
}
export function comparisonReportMarkdown(value: ReportComparison): string {
  if (!value.response?.result) return '\n\n## 四、上下游合同比对\n\n' + value.notice;
  return '\n\n' + reportMarkdown(value.response).replace(/^# /, '## 四、');
}
export function ReportComparisonSection({value}: {value: ReportComparison | null}) {
  return <section className="report-chapter" aria-label="上下游合同比对报告"><Typography.Title level={3}>四、上下游合同比对</Typography.Title>
    {!value ? <Alert type="info" title="正在核对上下游比对结果…" /> : value.notice ? <Alert type="warning" title={value.notice} /> : null}
    {value?.response?.result?.pairs.map(pair => <section key={pair.upstream_document_version_id}>
      <Typography.Title level={4}>{pair.upstream_file_name} ↔ {pair.procurement_file_name}</Typography.Title>
      <Typography.Paragraph type="secondary">比对批次：{value.response?.run.run_id} · {value.response?.run.created_at}</Typography.Paragraph>
      {!pair.categories.some(c => c.evidence.length || c.applicability === 'not_applicable') && <Alert type="error" title="本组比对失败，未形成可核验结论" />}
      {pair.categories.some(c => c.evidence.length || c.applicability === 'not_applicable') && pair.categories.map(category => <section key={category.category} style={{padding:'20px 0', borderBottom:'1px solid #e2e7eb'}}>
        <Typography.Title level={5}>{categoryTitles[category.category] ?? category.category} <Tag>{category.coverage ? coverageLabels[category.coverage] : stateLabels[category.state]}</Tag></Typography.Title>
        {(['upstream','procurement'] as const).map(side => <section key={side}><Typography.Text strong>{side === 'upstream' ? '项目客户合同 · 上游要求' : '采购合同 · 下游约定'}</Typography.Text>
          {category.evidence.filter(e => e.side === side).map((e,index) => <EvidenceBlock key={index} evidence={e} />)}
        </section>)}
        {category.limitations.length > 0 && <Typography.Paragraph type="secondary">待核实事项：{category.limitations.join('；')}</Typography.Paragraph>}
      </section>)}
      {pair.limitations.length > 0 && <Alert type="warning" title="本组限制" description={pair.limitations.join('；')} />}
    </section>)}
  </section>;
}
