"""Row level security, applied uniformly to every tenant table.

PRD SEC-2 wants isolation "enforced in the data access layer or by row level
security, with a passing test that proves a cross-tenant read returns zero rows".
This module is the second half of that; `layer/core/db.py` is the first.

Two details that matter:

The predicate wraps the setting in `nullif(..., '')`, which is not decoration.
`current_setting('app.org_id', true)` returns NULL only while the setting has never
been touched on that connection. After any transaction-local `set_config` the
setting reverts to the empty string rather than to NULL, and because connections are
pooled, a later session with no tenant would hit `''::uuid` and raise
`invalid input syntax for type uuid` instead of matching no rows. `nullif` collapses
both cases to NULL, `org_id = NULL` is NULL rather than true, and so a session that
never named a tenant sees nothing. That makes "forgot to set the org" fail closed.

`FORCE ROW LEVEL SECURITY` is applied as well as `ENABLE`, because a table's owner
is otherwise exempt from its own policies. Without it the migration role, and
anything that ever ran as the owner, would bypass isolation silently.
"""

from __future__ import annotations

from urllib.parse import urlsplit

ORG_SETTING = "app.org_id"

#: Tenant tables carry `org_id` and get a policy. `org` is the tenant registry and
#: is handled separately.
TENANT_TABLES: tuple[str, ...] = ("product", "source", "binding")

_PREDICATE = (
    f"org_id = nullif(current_setting('{ORG_SETTING}', true), '')::uuid"
)


def app_role_from_url(url: str) -> str:
    """The role the application connects as, taken from its own connection URL.

    Read from configuration rather than hardcoded so a deployment with different
    role names needs no migration edit.
    """
    user = urlsplit(url).username
    if not user:
        raise ValueError(f"no username in database url: {url!r}")
    return user


def enable_statements(tables: tuple[str, ...] = TENANT_TABLES) -> list[str]:
    out: list[str] = []
    for table in tables:
        out += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY {table}_tenant ON {table} "
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})",
        ]
    return out


def disable_statements(tables: tuple[str, ...] = TENANT_TABLES) -> list[str]:
    out: list[str] = []
    for table in reversed(tables):
        out += [
            f"DROP POLICY IF EXISTS {table}_tenant ON {table}",
            f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
        ]
    return out


def grant_statements(role: str, tables: tuple[str, ...] = TENANT_TABLES) -> list[str]:
    """Privileges for the application role.

    The role is granted DML and never ownership, so its reads and writes pass
    through the policies above. `org` is readable and insertable because a tenant
    has to be resolvable before any tenant-scoped work can start.
    """
    out = [f'GRANT USAGE ON SCHEMA public TO "{role}"']
    for table in tables:
        out.append(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{role}"')
    out.append(f'GRANT SELECT, INSERT ON org TO "{role}"')
    return out


def revoke_statements(role: str, tables: tuple[str, ...] = TENANT_TABLES) -> list[str]:
    out = [f'REVOKE ALL ON org FROM "{role}"']
    for table in reversed(tables):
        out.append(f'REVOKE ALL ON {table} FROM "{role}"')
    out.append(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
    return out
