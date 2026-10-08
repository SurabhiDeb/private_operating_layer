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

**Every function here takes its tables explicitly, with no default.** These are called
from migrations, and a migration has to describe the schema as it was when it was
written. An earlier version defaulted to the live `TENANT_TABLES`, so when migration 2
added eight tables, migration 1's downgrade began trying to revoke privileges on tables
it had never created and failed with `relation "audit_event" does not exist`. The
constant below is for application code; migrations name their own.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from layer.db.models import TENANT_TABLES

ORG_SETTING = "app.org_id"

#: The transaction-local flag that permits an erasure. Narrow by design: see
#: `append_only_statements` and PRD EC-9 / SEC-8.
ERASURE_SETTING = "app.erasure"

__all__ = [
    "ORG_SETTING",
    "ERASURE_SETTING",
    "TENANT_TABLES",
    "app_role_from_url",
    "enable_statements",
    "disable_statements",
    "grant_statements",
    "revoke_statements",
    "append_only_statements",
    "drop_append_only_statements",
]

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


def enable_statements(tables: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for table in tables:
        out += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY {table}_tenant ON {table} "
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})",
        ]
    return out


def disable_statements(tables: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for table in reversed(tables):
        out += [
            f"DROP POLICY IF EXISTS {table}_tenant ON {table}",
            f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
        ]
    return out


def grant_statements(role: str, tables: tuple[str, ...]) -> list[str]:
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


def revoke_statements(role: str, tables: tuple[str, ...]) -> list[str]:
    out = [f'REVOKE ALL ON org FROM "{role}"']
    for table in reversed(tables):
        out.append(f'REVOKE ALL ON {table} FROM "{role}"')
    out.append(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
    return out


# -- append-only -----------------------------------------------------------------

#: Tables that may be inserted into and never changed afterwards.
#:
#: `case_result` joins the audit log here because it is the evidence a finding cites.
#: A row that can be edited after the fact is not proof of anything, and the one thing
#: that legitimately changes about a case — whether its trace body still exists at the
#: source — is resolved live and reported, never written back over the record (B3
#: rule 11). Erasure uses the same narrow hatch, which is what makes PRD EC-9's
#: tenant-scoped hard delete possible on a table that is otherwise immutable.
APPEND_ONLY_TABLES: tuple[str, ...] = ("audit_event", "case_result")

_APPEND_ONLY_FUNCTION = f"""
CREATE OR REPLACE FUNCTION layer_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION '% is append-only: UPDATE is never permitted', TG_TABLE_NAME;
    END IF;

    -- DELETE and TRUNCATE are permitted only under an explicit erasure. PRD EC-9 and
    -- SEC-8 require a tenant-scoped hard delete reconciled with append-only history,
    -- so an absolute refusal here would make the right to erasure unimplementable.
    -- The hatch is narrow, transaction-local, and has to be asked for by name.
    IF coalesce(current_setting('{ERASURE_SETTING}', true), '') <> 'on' THEN
        RAISE EXCEPTION
            '% is append-only: % requires an explicit erasure (set {ERASURE_SETTING})',
            TG_TABLE_NAME, TG_OP;
    END IF;

    IF TG_OP = 'TRUNCATE' THEN
        RETURN NULL;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
"""


def append_only_statements(tables: tuple[str, ...]) -> list[str]:
    """Make a table append-only in the database rather than by convention.

    Both triggers are needed. The row-level one catches UPDATE and DELETE; the
    statement-level one catches TRUNCATE, which does not fire row triggers at all and
    would otherwise be a silent way to empty the audit log.
    """
    out = [_APPEND_ONLY_FUNCTION]
    for table in tables:
        out += [
            f"CREATE TRIGGER {table}_append_only_rows "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION layer_append_only()",
            f"CREATE TRIGGER {table}_append_only_truncate "
            f"BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION layer_append_only()",
        ]
    return out


def drop_append_only_statements(
    tables: tuple[str, ...], *, drop_function: bool
) -> list[str]:
    """Remove the triggers, and the shared function only when asked.

    `drop_function` has no default on purpose. The function is shared by every
    append-only table, so the migration that introduced it is the only one that may
    drop it; a later migration dropping it while an earlier table's triggers still
    depend on it fails with `cannot drop function ... because other objects depend on
    it`, and the whole downgrade rolls back. That is the same class of defect as the
    `tables` defaults removed above, and it was caught the same way — by running the
    downgrade rather than assuming it.
    """
    out: list[str] = []
    for table in reversed(tables):
        out += [
            f"DROP TRIGGER IF EXISTS {table}_append_only_truncate ON {table}",
            f"DROP TRIGGER IF EXISTS {table}_append_only_rows ON {table}",
        ]
    if drop_function:
        out.append("DROP FUNCTION IF EXISTS layer_append_only()")
    return out
