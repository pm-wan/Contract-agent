"""Built-in feature permissions and starter role templates."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    key: str
    name: str
    description: str


PERMISSIONS = (
    PermissionDefinition("cases.use", "合同审核", "查看和处理自己有权访问的案件、材料和审核结果。"),
    PermissionDefinition("tasks.use", "人工任务", "查看并处理分配给自己的审核任务。"),
    PermissionDefinition("entity_registry.use", "主体查询", "查找、选择已核实的法人主体。"),
    PermissionDefinition("entity_registry.manage", "主体管理", "维护法人主体台账和主体资料状态。"),
    PermissionDefinition(
        "entity_corrections.use", "主体纠错", "查看主体纠错队列并更正已确认的案件主体。"
    ),
    PermissionDefinition("company_rules.use", "公司知识库查看", "查看审核时可用的法律资料和已发布公司制度。"),
    PermissionDefinition("company_rules.manage", "公司知识库管理", "上传、发布和停用法律资料与公司制度文件。"),
)
ADMIN_PERMISSION = "admin.manage"
SELECTABLE_PERMISSION_KEYS = frozenset(item.key for item in PERMISSIONS)


@dataclass(frozen=True, slots=True)
class BuiltinRole:
    key: str
    name: str
    description: str
    permission_keys: frozenset[str]


BUILTIN_ROLES = (
    BuiltinRole(
        "workspace_admin",
        "系统管理员",
        "管理组织、账号和角色，并访问全部业务功能。",
        frozenset({ADMIN_PERMISSION, *SELECTABLE_PERMISSION_KEYS}),
    ),
    BuiltinRole(
        "contract_reviewer",
        "合同审核人",
        "处理本人有权访问的合同案件和人工任务，并查阅公司知识库。",
        frozenset({"cases.use", "tasks.use", "entity_registry.use", "company_rules.use"}),
    ),
    BuiltinRole(
        "entity_admin",
        "主体管理员",
        "维护主体台账并处理租户范围内的已确认主体纠错。",
        frozenset({"entity_registry.use", "entity_registry.manage", "entity_corrections.use"}),
    ),
)
