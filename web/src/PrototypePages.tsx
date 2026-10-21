import { useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Breadcrumb,
  Button,
  Card,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Modal,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import LockOutlined from "@ant-design/icons/es/icons/LockOutlined";
import RightOutlined from "@ant-design/icons/es/icons/RightOutlined";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { findings, sources, type Source, type SourceState } from "./fixtures";
import { SectionHeading } from "./shared-ui";

const { Title, Text, Paragraph } = Typography;

const stateLabels: Record<SourceState, string> = {
  ready: "可预览",
  partial: "部分返回",
  unauthorized: "未启用",
  empty: "空结果",
  failed: "查询失败",
};

const stateColors: Record<SourceState, string> = {
  ready: "success",
  partial: "warning",
  unauthorized: "default",
  empty: "default",
  failed: "error",
};

function PrototypeNotice() {
  return (
    <Alert
      className="prototype-notice"
      type="info"
      showIcon
      title="M0 交互原型 · 全部数据为合成示例"
      description="尚未接入真实身份、授权、来源查询或冻结协议。这里的状态和发现不能用于实际合同审查。"
    />
  );
}

function SourceCard({ source, selected, onSelect, onPreview }: {
  source: Source;
  selected: boolean;
  onSelect: (value: boolean) => void;
  onPreview: () => void;
}) {
  const canSelect = source.state === "ready";
  return <div className={`source-card ${selected ? "is-selected" : ""}`}>
    <div className="source-card-top">
      <div><span className="eyebrow">{source.kind}</span><h3>{source.name}</h3></div>
      <Tag color={stateColors[source.state]}>{stateLabels[source.state]}</Tag>
    </div>
    <p className="source-purpose">{source.purpose}</p>
    <dl className="source-meta"><div><dt>查询范围</dt><dd>{source.scope}</dd></div><div><dt>查询时间</dt><dd>{source.queriedAt}</dd></div><div><dt>完整性</dt><dd>{source.completeness}</dd></div></dl>
    <div className="source-actions">
      <Button onClick={onPreview}>查看来源说明</Button>
      {source.id !== "main" && <Tooltip title={!canSelect ? "状态未就绪，不能加入材料集" : "仅记录原型中的选择意图，未产生授权或冻结"}>
        <Button disabled={!canSelect} type={selected ? "primary" : "default"} onClick={() => onSelect(!selected)} aria-pressed={selected}>{selected ? "已选作背景" : "选择作背景"}</Button>
      </Tooltip>}
    </div>
  </div>;
}

export function WorkbenchPage() {
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<Source | null>(null);
  const [params, setParams] = useSearchParams();
  const evidence = findings.find((item) => item.id === params.get("evidence"));
  const scope = useMemo(() => ["待审合同 v3", ...selected.map((id) => sources.find((source) => source.id === id)?.name).filter(Boolean)].join("、"), [selected]);
  const toggle = (id: string, value: boolean) => setSelected((current) => value ? [...current.filter((item) => item !== id), id] : current.filter((item) => item !== id));
  return <>
    <Breadcrumb items={[{ title: <Link to="/cases">案件列表</Link> }, { title: "示例案件 CA-2026-024" }]} />
    <SectionHeading eyebrow="02 / 审核准备" title="华东设备服务合同" extra={<Tag color="processing">准备中</Tag>} />
    <PrototypeNotice />
    <div className="case-summary"><div><span>待审版本</span><strong>v3 · 18 页</strong></div><div><span>相对方</span><strong>示例供应商</strong></div><div><span>本次用途</span><strong>合同风险审查</strong></div><div><span>来源状态</span><strong>仅合成材料</strong></div></div>
    <SectionHeading eyebrow="SOURCE SCOPE" title="选择与预览来源" extra={<Text type="secondary">先核对范围，再决定是否纳入审查</Text>} />
    <div className="source-grid">{sources.map((source) => <SourceCard key={source.id} source={source} selected={selected.includes(source.id)} onSelect={(value) => toggle(source.id, value)} onPreview={() => setPreview(source)} />)}</div>
    <div className="freeze-panel"><div><span className="eyebrow">本次拟纳入</span><strong>{scope}</strong><Text type="secondary">选择仅保存在当前页面；未创建授权、快照或运行版本。</Text></div><Tooltip title="真实身份、用途授权和冻结协议未接入，M1 后端闭环前不可执行"><Button type="primary" size="large" icon={<LockOutlined />} disabled>预览并冻结（未接入）</Button></Tooltip></div>
    <SectionHeading eyebrow="DOCUMENT / EVIDENCE" title="原文定位示例" extra={<Link to="/cases/demo/results">查看示例发现 <RightOutlined /></Link>} />
    <div className="evidence-preview"><div className="page-rail"><span>PDF / V3</span><strong>P. {String(evidence?.page ?? 6).padStart(2, "0")}</strong><span>示例页</span></div><div className="document-page"><span className="document-overline">华东设备服务合同 · 合成节选</span>{evidence?.id === "F-02" ? <><h3>第九条 · 附件与交付</h3><p>9.1 双方根据项目要求确认交付范围。</p><p className="evidence-highlight">具体交付清单及验收标准以双方确认的附件为准。</p><p>9.2 附件与本合同具有同等效力。</p></> : <><h3>第六条 · 价款与支付</h3><p>6.1 双方按照经确认的工作范围和交付清单履行各自义务。</p><p className={evidence?.id === "F-01" ? "evidence-highlight" : ""}>6.2 乙方提交阶段成果并经甲方书面确认后，甲方于约定期限内支付对应款项。</p><p>6.3 付款申请材料及审核方式以双方最终确认的附件为准。</p></>}<Divider /><p className="muted-copy">本页为合成文本。正式引用必须绑定文件哈希、解析版本、页号和字符偏移，并校验当前用户可见性。</p></div></div>
    <Modal open={!!preview} title={preview?.name} onCancel={() => setPreview(null)} footer={<Button onClick={() => setPreview(null)}>关闭</Button>}>
      {preview && <><Tag color={stateColors[preview.state]}>{stateLabels[preview.state]}</Tag><Paragraph className="modal-detail">{preview.detail}</Paragraph><Descriptions column={1} size="small" items={[{ key: "scope", label: "查询范围", children: preview.scope }, { key: "time", label: "查询时间", children: preview.queriedAt }, { key: "complete", label: "完整性", children: preview.completeness }, { key: "purpose", label: "用途", children: preview.purpose }]} /><Alert type="warning" showIcon title="仅供原型演示，未完成服务端授权与来源冻结" /></>}
    </Modal>
    <Drawer open={!!evidence} onClose={() => setParams({})} title="证据定位" size={420}>
      {evidence && <><Tag>{evidence.id}</Tag><Title level={4}>{evidence.title}</Title><Descriptions column={1} items={[{ key: "file", label: "来源", children: "待审合同 v3（合成）" }, { key: "page", label: "页码", children: `第 ${evidence.page} 页` }, { key: "clause", label: "条款", children: evidence.clause }]} /><blockquote>{evidence.excerpt}</blockquote><Alert type="warning" showIcon title={evidence.note} /></>}
    </Drawer>
  </>;
}

export function ResultsPage() {
  const navigate = useNavigate();
  return <>
    <Breadcrumb items={[{ title: <Link to="/cases">案件列表</Link> }, { title: <Link to="/cases/demo">示例案件</Link> }, { title: "运行结果" }]} />
    <SectionHeading eyebrow="03 / 运行结果" title="审查发现与证据" extra={<Tag color="warning">示例 · 未运行模型</Tag>} />
    <PrototypeNotice />
    <Alert type="warning" showIcon title="当前仅展示引用回跳交互，不能作为审核结论" description="规则执行、法律依据、来源覆盖和人工确认尚未完成。未显示的风险不代表不存在。" />
    <div className="result-grid"><Card title="合同审核发现" className="result-card">{findings.map((item) => <div className="finding" key={item.id}><div className="finding-head"><Text code>{item.id}</Text><Tag color="warning">{item.status}</Tag></div><h3>{item.title}</h3><p>{item.note}</p><Button type="link" onClick={() => navigate(`/cases/demo?evidence=${item.id}`)}>打开 {item.clause} · 第 {item.page} 页 <RightOutlined /></Button></div>)}</Card><div className="result-side"><Card title="签约决策参考"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无可用于决策的结论" /></Card><Card title="审核范围与数据提示"><Space orientation="vertical"><Badge status="warning" text="客户合同版本待核验" /><Badge status="default" text="真实财务来源未接入" /><Badge status="default" text="法律/企业依据未发布" /></Space></Card></div></div>
  </>;
}
