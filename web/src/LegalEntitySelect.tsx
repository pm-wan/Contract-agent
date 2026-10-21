import { useEffect, useMemo, useState } from "react";
import { Select, Typography } from "antd";
import { ApiError, listLegalEntities, type LegalEntity } from "./api";

type LegalEntitySelectProps = {
  value?: string;
  onChange?: (value: string | undefined) => void;
  disabled?: boolean;
  placeholder?: string;
  excludeId?: string;
};

export function LegalEntitySelect({
  value,
  onChange,
  disabled = false,
  placeholder = "搜索主体名称、统一社会信用代码或 UUID",
  excludeId,
}: LegalEntitySelectProps) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<LegalEntity[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<LegalEntity | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let timer = 0;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    timer = window.setTimeout(() => {
      void listLegalEntities({ query: query.trim(), page: 1, page_size: 50 }, controller.signal)
        .then((response) => {
          setItems(response.items);
          if (value) {
            const match = response.items.find((entity) => entity.legal_entity_id === value);
            if (match) setSelectedEntity(match);
          }
        })
        .catch((reason: unknown) => {
          if (reason instanceof DOMException && reason.name === "AbortError") return;
          setError(reason instanceof ApiError && reason.status === 403
            ? "当前账号没有主体查询权限。"
            : "主体目录暂时无法读取，请稍后重试。");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, query ? 250 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, value]);

  const options = useMemo(() => {
    const available = [...items];
    if (value && selectedEntity?.legal_entity_id === value
      && !available.some((entity) => entity.legal_entity_id === value)) {
      available.unshift(selectedEntity);
    }
    return available
      .filter((entity) => entity.legal_entity_id !== excludeId)
      .map((entity) => ({
        value: entity.legal_entity_id,
        label: entity.legal_name,
        entity,
      }));
  }, [excludeId, items, selectedEntity, value]);

  const selected = value && selectedEntity?.legal_entity_id === value ? selectedEntity : null;

  return <div className="legal-entity-select">
    <Select
      showSearch
      allowClear
      filterOption={false}
      value={value}
      disabled={disabled}
      loading={loading}
      placeholder={placeholder}
      options={options}
      onSearch={setQuery}
      onChange={(nextValue: string | undefined) => {
        const nextEntity = items.find((entity) => entity.legal_entity_id === nextValue)
          ?? (selectedEntity?.legal_entity_id === nextValue ? selectedEntity : null);
        setSelectedEntity(nextEntity);
        onChange?.(nextValue);
      }}
      optionRender={(option) => {
        const entity = (option.data as { entity: LegalEntity }).entity;
        return <div className="legal-entity-option">
          <strong>{entity.legal_name}</strong>
          <span>{entity.unified_social_credit_code || "未登记统一社会信用代码"}</span>
          <small>{entity.legal_entity_id}</small>
        </div>;
      }}
      notFoundContent={loading ? "正在查找主体…" : "没有匹配的已启用主体"}
    />
    {error && <Typography.Text className="legal-entity-select-error" type="danger">{error}</Typography.Text>}
    {value && <Typography.Text className="legal-entity-selected-id" type="secondary">
      主体 UUID：<Typography.Text code>{value}</Typography.Text>
      {selected?.verification_basis && <span> · 台账核验依据：{selected.verification_basis}</span>}
    </Typography.Text>}
  </div>;
}
