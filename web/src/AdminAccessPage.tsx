import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tree,
  Typography,
} from "antd";
import type { DataNode } from "antd/es/tree";
import PlusOutlined from "@ant-design/icons/es/icons/PlusOutlined";
import EditOutlined from "@ant-design/icons/es/icons/EditOutlined";
import DeleteOutlined from "@ant-design/icons/es/icons/DeleteOutlined";
import TeamOutlined from "@ant-design/icons/es/icons/TeamOutlined";
import UserOutlined from "@ant-design/icons/es/icons/UserOutlined";
import SafetyCertificateOutlined from "@ant-design/icons/es/icons/SafetyCertificateOutlined";
import {
  ApiError,
  createAccessRole,
  createAccessUser,
  createOrganizationUnit,
  deactivateAccessRole,
  deleteOrganizationUnit,
  listAccessAdminData,
  updateAccessRole,
  updateAccessUser,
  updateOrganizationUnit,
  type AdminUser,
  type AppRole,
  type OrganizationUnit,
  type PermissionDefinition,
} from "./api";
import { RouteLoading, SectionHeading } from "./shared-ui";

interface UserFormValues {
  username?: string;
  display_name: string;
  organization_id: string;
  role_ids: string[];
  initial_password?: string;
  new_password?: string;
  is_active?: boolean;
}

interface RoleFormValues {
  name: string;
  description?: string;
  permissions: string[];
}

interface OrganizationFormValues {
  parent_id?: string | null;
  code: string;
  name: string;
}

function messageFor(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 409 && error.code === "organization_not_empty") return "请先移动或停用该部门下的成员和子部门。";
    if (error.status === 409 && error.code === "organization_code_exists") return "部门编号已存在。";
    if (error.status === 409 && error.code === "username_exists") return "该账号已存在。";
    if (error.status === 409 && error.code === "last_system_administrator_required") return "至少保留一位启用状态的系统管理员。";
    if (error.status === 403) return "当前账号没有管理组织、用户或角色的权限。";
    if (error.status === 0 || error.status >= 500) return "服务暂不可用，请稍后重试。";
  }
  return "操作失败，请检查填写内容后重试。";
}

function organizationTreeData(
  units: OrganizationUnit[],
  onEdit: (unit: OrganizationUnit) => void,
  onDelete: (unit: OrganizationUnit) => void,
  parentId: string | null = null,
): DataNode[] {
  return units
    .filter((unit) => unit.parent_id === parentId)
    .map((unit) => ({
      key: unit.id,
      title: <div className="org-tree-node">
        <span>{unit.name}<Typography.Text type="secondary"> {unit.code}</Typography.Text></span>
        <Space size={2}>
          <Button type="text" size="small" aria-label={`编辑${unit.name}`} icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); onEdit(unit); }} />
          {unit.parent_id !== null && <Button type="text" danger size="small" aria-label={`删除${unit.name}`} icon={<DeleteOutlined />} onClick={(event) => { event.stopPropagation(); onDelete(unit); }} />}
        </Space>
      </div>,
      children: organizationTreeData(units, onEdit, onDelete, unit.id),
    }));
}

export function AdminAccessPage() {
  const [organizations, setOrganizations] = useState<OrganizationUnit[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<AppRole[]>([]);
  const [permissions, setPermissions] = useState<PermissionDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("organizations");
  const [orgModal, setOrgModal] = useState<{ open: boolean; edit?: OrganizationUnit; parentId?: string | null }>({ open: false });
  const [userModal, setUserModal] = useState<{ open: boolean; edit?: AdminUser }>({ open: false });
  const [roleModal, setRoleModal] = useState<{ open: boolean; edit?: AppRole }>({ open: false });
  const [submitting, setSubmitting] = useState(false);
  const [orgForm] = Form.useForm<OrganizationFormValues>();
  const [userForm] = Form.useForm<UserFormValues>();
  const [roleForm] = Form.useForm<RoleFormValues>();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listAccessAdminData();
      setOrganizations(data.organizations);
      setUsers(data.users);
      setRoles(data.roles);
      setPermissions(data.permissions);
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    document.title = "组织与权限 · 合同审计工作台";
    void load();
  }, [load]);

  const orgOptions = useMemo(
    () => organizations.filter((unit) => unit.is_active).map((unit) => ({ label: unit.name, value: unit.id })),
    [organizations],
  );
  const roleOptions = useMemo(
    () => roles.filter((role) => role.is_active).map((role) => ({ label: role.name, value: role.id })),
    [roles],
  );

  const openCreateOrganization = (parentId: string | null = null) => {
    setOrgModal({ open: true, parentId });
    orgForm.resetFields();
    orgForm.setFieldsValue({ parent_id: parentId, code: "", name: "" });
  };
  const openEditOrganization = (unit: OrganizationUnit) => {
    setOrgModal({ open: true, edit: unit });
    orgForm.setFieldsValue({ parent_id: unit.parent_id, code: unit.code, name: unit.name });
  };
  const closeOrganization = () => { setOrgModal({ open: false }); orgForm.resetFields(); };
  const submitOrganization = async (values: OrganizationFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      if (orgModal.edit) {
        await updateOrganizationUnit(orgModal.edit.id, {
          parent_id: values.parent_id ?? null,
          code: values.code,
          name: values.name,
        });
      } else {
        await createOrganizationUnit({ parent_id: values.parent_id ?? orgModal.parentId ?? null, code: values.code, name: values.name });
      }
      closeOrganization();
      await load();
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setSubmitting(false);
    }
  };
  const confirmDeleteOrganization = (unit: OrganizationUnit) => {
    Modal.confirm({
      title: `删除部门“${unit.name}”？`,
      content: "只有没有成员和子部门的部门可以删除。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await deleteOrganizationUnit(unit.id);
          await load();
        } catch (reason) {
          setError(messageFor(reason));
        }
      },
    });
  };

  const openCreateUser = () => {
    setUserModal({ open: true });
    userForm.resetFields();
    userForm.setFieldsValue({ role_ids: [], is_active: true });
  };
  const openEditUser = (user: AdminUser) => {
    setUserModal({ open: true, edit: user });
    userForm.setFieldsValue({
      display_name: user.display_name,
      organization_id: user.organization_id,
      role_ids: user.roles.map((role) => role.id),
      is_active: user.is_active,
      new_password: "",
    });
  };
  const closeUser = () => { setUserModal({ open: false }); userForm.resetFields(); };
  const submitUser = async (values: UserFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      if (userModal.edit) {
        await updateAccessUser(userModal.edit.actor_id, {
          display_name: values.display_name,
          organization_id: values.organization_id,
          role_ids: values.role_ids,
          is_active: values.is_active ?? true,
          ...(values.new_password ? { new_password: values.new_password } : {}),
        });
      } else {
        await createAccessUser({
          username: values.username ?? "",
          display_name: values.display_name,
          organization_id: values.organization_id,
          role_ids: values.role_ids,
          initial_password: values.initial_password ?? "",
        });
      }
      closeUser();
      await load();
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const openCreateRole = () => {
    setRoleModal({ open: true });
    roleForm.resetFields();
    roleForm.setFieldsValue({ permissions: [] });
  };
  const openEditRole = (role: AppRole) => {
    setRoleModal({ open: true, edit: role });
    roleForm.setFieldsValue({ name: role.name, description: role.description, permissions: role.permissions });
  };
  const closeRole = () => { setRoleModal({ open: false }); roleForm.resetFields(); };
  const submitRole = async (values: RoleFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      const input = { name: values.name, description: values.description ?? "", permissions: values.permissions };
      if (roleModal.edit) await updateAccessRole(roleModal.edit.id, input);
      else await createAccessRole(input);
      closeRole();
      await load();
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setSubmitting(false);
    }
  };
  const confirmDeactivateRole = (role: AppRole) => {
    Modal.confirm({
      title: `停用角色“${role.name}”？`,
      content: "停用后该角色会从成员账号撤销，已登录用户的权限也会在下次请求时更新。",
      okText: "停用",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await deactivateAccessRole(role.id);
          await load();
        } catch (reason) {
          setError(messageFor(reason));
        }
      },
    });
  };

  const userColumns = [
    { title: "姓名", dataIndex: "display_name", key: "display_name" },
    { title: "账号", dataIndex: "username", key: "username" },
    { title: "所属部门", dataIndex: "organization_name", key: "organization_name" },
    { title: "角色", key: "roles", render: (_: unknown, user: AdminUser) => <Space wrap>{user.roles.map((role) => <Tag key={role.id}>{role.name}</Tag>)}</Space> },
    { title: "状态", key: "status", render: (_: unknown, user: AdminUser) => <Tag color={user.is_active ? "green" : "default"}>{user.is_active ? "启用" : "停用"}</Tag> },
    { title: "操作", key: "actions", render: (_: unknown, user: AdminUser) => <Button type="link" onClick={() => openEditUser(user)}>编辑</Button> },
  ];

  const roleColumns = [
    { title: "角色名称", dataIndex: "name", key: "name", render: (name: string, role: AppRole) => <Space>{name}{role.is_system && <Tag color="blue">系统内置</Tag>}{!role.is_active && <Tag>已停用</Tag>}</Space> },
    { title: "权限", key: "permissions", render: (_: unknown, role: AppRole) => role.permissions.includes("admin.manage") ? "组织、账号、角色及全部业务功能" : <Space wrap>{role.permissions.map((key) => <Tag key={key}>{permissions.find((item) => item.key === key)?.name ?? key}</Tag>)}</Space> },
    { title: "说明", dataIndex: "description", key: "description" },
    { title: "操作", key: "actions", render: (_: unknown, role: AppRole) => role.is_system ? <Typography.Text type="secondary">固定权限</Typography.Text> : <Space><Button type="link" onClick={() => openEditRole(role)}>编辑</Button><Button type="link" danger onClick={() => confirmDeactivateRole(role)}>停用</Button></Space> },
  ];

  if (loading && organizations.length === 0) return <RouteLoading title="组织与权限" />;

  return <main className="admin-access-page">
    <SectionHeading eyebrow="工作区管理" title="组织与权限" description="维护组织层级、成员账号和角色功能权限。组织用于归属和管理，案件数据仍由成员关系与材料授权控制。" />
    {error && <Alert className="admin-access-alert" type="error" showIcon message={error} closable onClose={() => setError(null)} />}
    <Tabs
      activeKey={tab}
      onChange={setTab}
      items={[
        {
          key: "organizations",
          label: <span><TeamOutlined /> 组织架构</span>,
          children: <Card className="admin-access-card" title="部门层级" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => openCreateOrganization(null)}>新建部门</Button>}>
            <Typography.Paragraph type="secondary">组织树用于维护部门归属；一个成员属于一个部门，可配置多个角色。</Typography.Paragraph>
            {organizations.length === 0
              ? <Empty description="尚未创建部门" />
              : <Tree className="org-tree" treeData={organizationTreeData(organizations, openEditOrganization, confirmDeleteOrganization)} defaultExpandAll blockNode />}
          </Card>,
        },
        {
          key: "users",
          label: <span><UserOutlined /> 成员账号</span>,
          children: <Card className="admin-access-card" title="成员账号" extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreateUser}>创建账号</Button>}>
            <Typography.Paragraph type="secondary">管理员为成员创建账号、设置初始密码、所属部门和角色。停用账号会立即撤销其登录会话。</Typography.Paragraph>
            <Table rowKey="actor_id" columns={userColumns} dataSource={users} loading={loading} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 850 }} />
          </Card>,
        },
        {
          key: "roles",
          label: <span><SafetyCertificateOutlined /> 角色与权限</span>,
          children: <Card className="admin-access-card" title="功能角色" extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreateRole}>新建角色</Button>}>
            <Typography.Paragraph type="secondary">系统管理员管理组织和账号；业务角色通过权限集合决定可访问的功能。用户可以同时拥有多个角色。</Typography.Paragraph>
            <Table rowKey="id" columns={roleColumns} dataSource={roles} loading={loading} pagination={false} scroll={{ x: 900 }} />
          </Card>,
        },
      ]}
    />

    <Modal
      title={orgModal.edit ? "编辑部门" : "新建部门"}
      open={orgModal.open}
      onCancel={closeOrganization}
      onOk={() => orgForm.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnHidden
    >
      <Form form={orgForm} layout="vertical" onFinish={submitOrganization}>
        <Form.Item label="上级部门" name="parent_id">
          <Select allowClear placeholder="作为顶层部门" options={organizations
            .filter((unit) => unit.is_active && unit.id !== orgModal.edit?.id)
            .map((unit) => ({ label: unit.name, value: unit.id }))} />
        </Form.Item>
        <Form.Item label="部门名称" name="name" rules={[{ required: true, message: "请输入部门名称" }]}><Input maxLength={120} /></Form.Item>
        <Form.Item label="部门编号" name="code" rules={[{ required: true, message: "请输入部门编号" }]}><Input maxLength={64} placeholder="例如：LEGAL、PROCUREMENT" /></Form.Item>
      </Form>
    </Modal>

    <Modal
      title={userModal.edit ? `编辑成员：${userModal.edit.display_name}` : "创建成员账号"}
      open={userModal.open}
      onCancel={closeUser}
      onOk={() => userForm.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnHidden
    >
      <Form key={userModal.edit?.actor_id ?? "new-user"} form={userForm} layout="vertical" onFinish={submitUser}>
        {!userModal.edit && <Form.Item label="登录账号" name="username" rules={[{ required: true, min: 3, message: "账号至少 3 个字符" }]}><Input autoComplete="off" maxLength={80} /></Form.Item>}
        <Form.Item label="成员姓名" name="display_name" rules={[{ required: true, message: "请输入成员姓名" }]}><Input maxLength={120} /></Form.Item>
        <Form.Item label="所属部门" name="organization_id" rules={[{ required: true, message: "请选择所属部门" }]}><Select showSearch optionFilterProp="label" options={orgOptions} /></Form.Item>
        <Form.Item label="角色" name="role_ids" rules={[{ required: true, type: "array", min: 1, message: "至少分配一个角色" }]}>
          <Select mode="multiple" showSearch optionFilterProp="label" options={roleOptions} placeholder="可多选" />
        </Form.Item>
        {!userModal.edit && <Form.Item label="初始密码" name="initial_password" rules={[{ required: true, min: 12, message: "初始密码至少 12 个字符" }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>}
        {userModal.edit && <>
          <Form.Item label="重置密码" name="new_password" extra="留空表示不更改；输入新密码后，成员的现有登录会话将失效。">
            <Input.Password autoComplete="new-password" placeholder="至少 12 个字符" />
          </Form.Item>
          <Form.Item label="账号状态" name="is_active" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
        </>}
      </Form>
    </Modal>

    <Modal
      title={roleModal.edit ? `编辑角色：${roleModal.edit.name}` : "新建业务角色"}
      open={roleModal.open}
      onCancel={closeRole}
      onOk={() => roleForm.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnHidden
    >
      <Form key={roleModal.edit?.id ?? "new-role"} form={roleForm} layout="vertical" onFinish={submitRole}>
        <Form.Item label="角色名称" name="name" rules={[{ required: true, message: "请输入角色名称" }]}><Input maxLength={120} /></Form.Item>
        <Form.Item label="角色说明" name="description"><Input.TextArea rows={2} maxLength={500} /></Form.Item>
        <Form.Item label="可访问功能" name="permissions" rules={[{ required: true, type: "array", min: 1, message: "至少选择一项功能" }]}>
          <Checkbox.Group className="permission-checkboxes">
            {permissions.map((permission) => <Checkbox key={permission.key} value={permission.key}>
              <span className="permission-checkbox-copy"><strong>{permission.name}</strong><small>{permission.description}</small></span>
            </Checkbox>)}
          </Checkbox.Group>
        </Form.Item>
      </Form>
    </Modal>
  </main>;
}
