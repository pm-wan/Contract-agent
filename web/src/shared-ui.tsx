import { useEffect } from "react";
import { Spin, Typography } from "antd";
import type { ReactNode } from "react";

const { Title } = Typography;

export function SectionHeading({ eyebrow, title, description, extra, id, level = 2 }: { eyebrow: string; title: string; description?: string; extra?: ReactNode; id?: string; level?: 2 | 3 | 4 }) {
  return (
    <div className="section-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <Title level={level} id={id}>{title}</Title>
        {description && <Typography.Paragraph className="section-heading-description" type="secondary">{description}</Typography.Paragraph>}
      </div>
      {extra}
    </div>
  );
}

export function RouteLoading({ title }: { title: string }) {
  useEffect(() => {
    document.title = `${title} — 合同审查工作台`;
  }, [title]);
  return <div className="route-loading" role="status" aria-live="polite"><Spin size="large" description={`正在载入${title}…`} /></div>;
}
