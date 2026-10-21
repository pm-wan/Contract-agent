"""Create the first workspace administrator from a trusted local terminal."""

from __future__ import annotations

from getpass import getpass
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from contract_app.api_auth import _ensure_builtin_roles
from contract_app.auth_security import configured_tenant_id, database_url, hash_password, set_tenant


def create_first_administrator(
    username: str,
    display_name: str,
    password: str,
    organization_name: str,
) -> None:
    tenant_id = configured_tenant_id()
    actor_id = uuid4()
    username = username.strip()
    username_key = username.casefold()
    display_name = display_name.strip()
    organization_name = organization_name.strip()
    if not 3 <= len(username) <= 80:
        raise ValueError("账号长度必须为 3 到 80 个字符。")
    if not display_name or not organization_name:
        raise ValueError("成员姓名和组织名称不能为空。")
    if len(password) < 12:
        raise ValueError("密码至少需要 12 个字符。")

    with psycopg.connect(database_url(), row_factory=dict_row) as connection:
        set_tenant(connection, tenant_id)
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(tenant_id),))
        existing = connection.execute(
            "SELECT 1 FROM app_users WHERE tenant_id = %s LIMIT 1", (tenant_id,)
        ).fetchone()
        if existing is not None:
            raise RuntimeError("系统中已有账号；首次管理员初始化只能执行一次。")

        root = connection.execute(
            "SELECT id FROM organization_units WHERE tenant_id = %s AND code = 'ROOT'",
            (tenant_id,),
        ).fetchone()
        if root is None:
            root = connection.execute(
                "INSERT INTO organization_units (tenant_id, code, name, created_by, updated_by) "
                "VALUES (%s, 'ROOT', %s, %s, %s) RETURNING id",
                (tenant_id, organization_name, actor_id, actor_id),
            ).fetchone()
        else:
            connection.execute(
                "UPDATE organization_units SET name = %s, is_active = true, "
                "updated_by = %s, updated_at = clock_timestamp() "
                "WHERE tenant_id = %s AND id = %s",
                (organization_name, actor_id, tenant_id, root["id"]),
            )
        assert root is not None
        roles = _ensure_builtin_roles(connection, tenant_id)
        connection.execute(
            "INSERT INTO app_users "
            "(tenant_id, actor_id, username, username_key, display_name, organization_id, "
            "password_hash, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                actor_id,
                username,
                username_key,
                display_name,
                root["id"],
                hash_password(password),
                actor_id,
            ),
        )
        connection.execute(
            "INSERT INTO app_user_roles (tenant_id, actor_id, role_id, granted_by) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, actor_id, roles["workspace_admin"], actor_id),
        )


def main() -> None:
    print("首次管理员初始化（密码不会显示在屏幕上）")
    username = input("登录账号: ").strip()
    display_name = input("成员姓名: ").strip()
    organization_name = input("组织名称 [公司总部]: ").strip() or "公司总部"
    password = getpass("初始密码（至少 12 个字符）: ")
    confirmation = getpass("再次输入初始密码: ")
    if password != confirmation:
        raise SystemExit("两次密码不一致，没有创建账号。")
    try:
        create_first_administrator(username, display_name, password, organization_name)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    except psycopg.errors.UniqueViolation as exc:
        raise SystemExit("账号或组织编号已存在，没有创建账号。") from exc
    print("首位系统管理员已创建。现在可以在登录页使用该账号登录。")


if __name__ == "__main__":
    main()
