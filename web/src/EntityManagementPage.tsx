import { useEffect, useState } from "react";
import { Alert, Button, Form, Input, Modal, Pagination, Switch, Table, Tag, Typography } from "antd";
import EditOutlined from "@ant-design/icons/es/icons/EditOutlined";
import PlusOutlined from "@ant-design/icons/es/icons/PlusOutlined";
import { ApiError, createLegalEntity, listLegalEntities, updateLegalEntity, type LegalEntity, type LegalEntityInput } from "./api";
import { formatDate } from "./case-format";
import { SectionHeading } from "./shared-ui";
import { useAuth } from "./AuthContext";

const { Text } = Typography;

export function EntityManagementPage() {
  const auth = useAuth();
  const canManage = Boolean(auth.user?.permissions.includes("entity_registry.manage"));
  const [form] = Form.useForm<LegalEntityInput>();
  const [query, setQuery] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [items, setItems] = useState<LegalEntity[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState<ApiError | null>(null);
  const [editing, setEditing] = useState<LegalEntity | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    document.title = "主体台账 · 合同审核工作台";
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      void listLegalEntities({
        query: query.trim() || undefined,
        page,
        page_size: pageSize,
        include_inactive: canManage && includeInactive,
      }, controller.signal)
        .then((response) => {
          setItems(response.items);
          setTotal(response.total);
          if (response.page !== page) setPage(response.page);
        })
        .catch((reason: unknown) => {
          if (reason instanceof DOMException && reason.name === "AbortError") return;
          setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, query.trim() ? 250 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [canManage, includeInactive, page, pageSize, query, retry]);

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({ legal_name: "", unified_social_credit_code: null, verification_basis: "", is_active: true });
    setCreateOpen(true);
  };

  const openEdit = (entity: LegalEntity) => {
    setEditing(entity);
    form.setFieldsValue({
      legal_name: entity.legal_name,
      unified_social_credit_code: entity.unified_social_credit_code,
      verification_basis: entity.verification_basis,
      is_active: entity.is_active,
    });
    setCreateOpen(true);
  };

  const save = async (values: LegalEntityInput) => {
    setSaving(true);
    setError(null);
    try {
      const input: LegalEntityInput = {
        legal_name: values.legal_name.trim(),
        unified_social_credit_code: values.unified_social_credit_code?.trim().toUpperCase() || null,
        verification_basis: values.verification_basis.trim(),
        is_active: values.is_active,
      };
      if (editing) await updateLegalEntity(editing.legal_entity_id, input);
      else await createLegalEntity(input);
      setCreateOpen(false);
      setRetry((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : new ApiError(0, "request_failed"));
    } finally {
      setSaving(false);
    }
  };

  const errorCopy = error?.status === 409
    ? "该统一社会信用代码已登记，请检查现有主体记录。"
    : error?.status === 403
      ? "当前账号没有主体台账管理权限。"
      : "主体台账暂时无法读取或保存，请重试。";

  return <main className="entity-management-page">
    <SectionHeading
      eyebrow="组织数据 · 法人主体"
      title="主体台账"
      extra={canManage && <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增主体</Button>}
    />
    <Typography.Paragraph type="secondary" className="entity-management-intro">
      维护经核实的法人主体资料。案件在主合同上传后从台账选择主体；确认后主体成为该案件的材料范围边界。
    </Typography.Paragraph>
    <div className="entity-management-toolbar">
      <Input.Search
        allowClear
        value={query}
        placeholder="搜索名称、统一社会信用代码或 UUID"
        aria-label="搜索主体台账"
        onChange={(event) => {
          setQuery(event.target.value);
          if (!composing) setPage(1);
        }}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={(event) => { setComposing(false); setQuery(event.currentTarget.value); setPage(1); }}
        onSearch={(value) => { setQuery(value); setPage(1); }}
      />
      {canManage && <label className="entity-inactive-filter"><Switch checked={includeInactive} onChange={(checked) => { setIncludeInactive(checked); setPage(1); }} /> <span>显示停用主体</span></label>}
      <Text type="secondary" aria-live="polite">{loading ? "正在读取台账…" : `共 ${total} 个主体`}</Text>
    </div>
    {error && <Alert className="page-error" type="error" showIcon title={errorCopy} action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} />}
    <Table<LegalEntity>
      rowKey="legal_entity_id"
      loading={loading}
      pagination={false}
      dataSource={items}
      locale={{ emptyText: error ? "主体数据未能读取" : query ? "没有匹配的主体" : "台账中还没有主体" }}
      columns={[
        { title: "法人主体", dataIndex: "legal_name", render: (name: string, entity) => <div className="entity-name-cell"><strong>{name}</strong><Text code>{entity.legal_entity_id}</Text></div> },
        { title: "统一社会信用代码", dataIndex: "unified_social_credit_code", render: (value: string | null) => value || <Text type="secondary">未登记</Text> },
        { title: "核验依据", dataIndex: "verification_basis", render: (value: string) => <span className="entity-basis-cell" title={value}>{value}</span> },
        { title: "状态", dataIndex: "is_active", render: (active: boolean) => active ? <Tag color="success">已启用</Tag> : <Tag>待核实 / 已停用</Tag> },
        { title: "更新时间", dataIndex: "updated_at", render: (value: string) => formatDate(value) },
        ...(canManage ? [{ title: "操作", key: "action", render: (_: unknown, entity: LegalEntity) => <Button type="link" icon={<EditOutlined />} onClick={() => openEdit(entity)}>编辑</Button> }] : []),
      ]}
      scroll={{ x: 900 }}
    />
    {total > 0 && <Pagination
      className="case-pagination"
      current={page}
      pageSize={pageSize}
      pageSizeOptions={[10, 20, 50, 100]}
      total={total}
      showSizeChanger
      showTotal={(count, range) => `第 ${range[0]}–${range[1]} 项，共 ${count} 个主体`}
      onChange={(nextPage, nextPageSize) => { setPage(nextPage); setPageSize(nextPageSize); }}
    />}
    <Modal
      open={createOpen}
      title={editing ? "编辑主体资料" : "新增法人主体"}
      okText={editing ? "保存修改" : "创建主体"}
      cancelText="取消"
      confirmLoading={saving}
      onOk={() => form.submit()}
      onCancel={() => { if (!saving) setCreateOpen(false); }}
      closable={!saving}
      mask={{ closable: !saving }}
    >
      {error && <Alert className="document-warning" type="error" showIcon title={errorCopy} />}
      {editing && <div className="entity-edit-uuid"><Text type="secondary">主体 UUID</Text><Text code>{editing.legal_entity_id}</Text></div>}
      {!editing && <Alert className="entity-create-note" type="info" showIcon title="UUID 由系统自动生成" description="创建后即可在案件中搜索和选择该主体。" />}
      <Form form={form} layout="vertical" onFinish={(values) => void save(values)}>
        <Form.Item name="legal_name" label="法人主体名称" rules={[{ required: true, whitespace: true, message: "请输入法人主体名称。" }, { max: 255, message: "名称不能超过 255 个字符。" }]}>
          <Input maxLength={255} placeholder="填写营业执照或签约资料上的全称" />
        </Form.Item>
        <Form.Item name="unified_social_credit_code" label="统一社会信用代码" rules={[{ max: 32, message: "代码不能超过 32 个字符。" }]}>
          <Input maxLength={32} placeholder="选填" />
        </Form.Item>
        <Form.Item name="verification_basis" label="主体核验依据" rules={[{ required: true, whitespace: true, message: "请记录主体核验依据。" }, { max: 2000, message: "核验依据不能超过 2000 个字符。" }]}>
          <Input.TextArea maxLength={2000} showCount autoSize={{ minRows: 3, maxRows: 6 }} placeholder="例如：已核验营业执照、统一社会信用代码及本次签约资料" />
        </Form.Item>
        <Form.Item name="is_active" label="主体状态" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="停用" />
        </Form.Item>
      </Form>
    </Modal>
  </main>;
}
