# Build progress — the Layer, phases 1 to 3

Branch `layer-phase-1-3`. Specification: `operating_layer_main/PRD-SPEC.md` v3.
Conventions and constraints: `CLAUDE.md`.

**Scope.** Phases 1–3 of the handoff's build order: onboarding, the five finding queries,
and the stdio MCP server. That is the whole read half, usable from a terminal with no UI,
no agent and no Dust. Phases 4–7 (Dust as a client, the write half, the remaining sources,
operations) come after and are unchanged from the handoff.

**How to read the status column.** A step is `done` only when its tests pass and the
result has been quoted, never when the code merely exists.

| # | Step | Status |
|---|---|---|
| 1 | Branch, venv, pytest, Alembic scaffolding, spec docs committed | **done** |
| 2 | Migration 1: `org`, `product`, `source`, `binding`, RLS, roles, isolation tests | **done** |
| 3 | `refs` registry and resolvers; the `repo` source with revision pinning | not started |
| 4 | Migration 2: `clause`, `clause_identity`, `observation`, `enforcement_fact`, `link`, `entity`, `proposal`, `audit_event` | not started |
| 5 | The metric engine, tiers 1 to 3 | not started |
| 6 | Adapters: spec, then eval (run files, then Langfuse), then code | not started |
| 7 | The onboarding state machine and CLI; onboard all three fixtures | not started |
| 8 | Verdicts and the five finding queries; reproduce all three C1 conditions | not started |
| 9 | The stdio MCP server | not started |
| 10 | The agnosticism grep test, the no-causal-language test, the AC matrix | not started |

**Acceptance criteria met so far:** AC-8. Hard cases: H11.

---

## Step 1 — Scaffolding. Done.

**What was built.** Branch `layer-phase-1-3` off `main`. A `.venv` on Python 3.13.5 with
`requirements-layer.txt` kept separate from EarlyEcho's `requirements.txt`. The `layer/`
package skeleton: `core`, `db`, `refs`, `adapters`, `metrics`, `onboarding`, `verdicts`,
`findings`, `mcp`. `pytest.ini` declaring five markers — `ac`, `hard_case`, `edge_case`,
`story`, `needs_db` — so every test can be traced back to the criterion it exists for.
`.env.example` committed, `.env` gitignored, and `.venv/` plus `.pytest_cache/` added to
`.gitignore`. `operating_layer_main/` committed, having been untracked.

**`layer/core/config.py`.** Pydantic-settings with the `LAYER_` env prefix, so the Layer
and EarlyEcho can share a `.env` without colliding. Two database URLs, not one:
`LAYER_DATABASE_URL` for the application role and `LAYER_ADMIN_DATABASE_URL` for the table
owner. Migrations use the second; nothing else does.

**`layer/core/errors.py`.** The refusal vocabulary. `Refusal` is a return value rather than
an exception, because PRD B1 requires every response to be one of four shapes and "the
Layer never silently returns nothing". `TenantContextMissing`, `UnknownProduct` (B3 rule 9)
and `NotLive` (B3 rule 8) name the three ways a caller can be wrong.

**Alembic.** Configured from scratch. It was listed in `requirements.txt` but had no
`alembic.ini`, no migrations directory and no `env.py`; the actual mechanism was
`Base.metadata.create_all` at import in `api/main.py:26` plus a stale hand-written
`db/schema.sql` missing columns the models already had. `env.py` reads the URL from
settings rather than the ini file, so a connection string never sits in version control.

**One open question closed.** Handoff section 14 item 4 flagged the MCP Python SDK's import
path as untrustworthy from memory. Installed and read: `mcp` 2.2.0 renamed `FastMCP` to
`MCPServer` at `mcp.server.mcpserver`, tools are a `@server.tool()` decorator, and
transport is an argument to `run()` — `"stdio"` now, `"streamable-http"` in phase 4. That
confirms the plan's claim that phase 4 changes one line rather than rewriting the server.
Code written from recall would have imported a module that no longer exists.

---

## Step 2 — Migration 1 and provable tenant isolation. Done.

This step exists because PRD SEC-2, AC-8 and B5 rank a cross-tenant read second among
unacceptable failures, and because the inherited codebase demonstrates the failure mode:
EarlyEcho filters `org_id` by hand in roughly sixty places and omits it in six.
`api/routes/ingest.py:156` and `:178` look a `PendingEntity` up by `id` alone and then copy
that row's `org_id` forward, so anyone who guesses an id can approve another tenant's item.
The fix is not more diligence. It is making the filter unnecessary.

**`layer/core/db.py`.** `org_session(org_id)` is the only door to tenant data. It opens a
transaction and binds the tenant with `SELECT set_config('app.org_id', :org, true)` —
`set_config` rather than `SET LOCAL` because only the former accepts a bind parameter, and
the alternative is interpolating a value into SQL. `unscoped_session()` exists for the
tenant registry and reads nothing tenant-scoped by design. A malformed org id raises
`TenantContextMissing` before any query runs, so a bad value cannot degrade into an
unscoped session.

**`layer/db/rls.py` and migration `e527dd6`.** Every tenant table gets
`ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY` and a policy with both `USING` and
`WITH CHECK`, so isolation governs writes as well as reads. `FORCE` is not redundant: a
table's owner is otherwise exempt from its own policies, which would mean anything ever run
as the owner bypassed isolation silently. The policy is created in the **same migration as
the tables** — a window in which tenant tables exist without a policy is a window in which
a leak is legal. The application role is read out of its own connection URL rather than
hardcoded, so different role names need no migration edit.

**The schema.** `org` is the tenant registry and carries no `org_id`, so it carries no
policy; the test asserts that, so adding one later is a decision rather than an accident.
`product` holds `key`, `name`, nullable `pattern`, nullable `ref_prefix`, `status` and
`onboarding_step`. `source` holds `role`, `kind`, a JSONB `config`, and `pinned_rev` — the
immutable revision every citation from that source is built from. `binding` holds
`metric`, `clause_ref`, a JSONB `metric_definition`, `confirmed_by` and `confirmed_at`.

`source` and `binding` are v3's addition and the reason onboarding is phase 1. Without them
nothing describes how a product enters the Layer, which is the defect v3's revision log
calls "the one that matters most": an implementer reading v2 would start generating
proposals against nothing.

Closed vocabularies the Layer branches on — `product.status`, `source.role` — are CHECK
constraints. Open ones — `pattern`, `source.kind` — are not, because the handoff's build
order adds a source "as a new `source` row with a new `kind` and no new concepts" and that
must not require a migration. No Postgres ENUM anywhere; altering one is a migration per
customer.

**A real bug the test caught.** The obvious predicate,
`org_id = current_setting('app.org_id', true)::uuid`, is wrong under connection pooling.
`current_setting(..., true)` returns NULL only while the setting has never been touched on
that connection; after any transaction-local `set_config` it reverts to the **empty
string**. So a later session with no tenant evaluates `''::uuid` and raises
`invalid input syntax for type uuid` instead of matching no rows. Wrapped in
`nullif(..., '')` both cases collapse to NULL, `org_id = NULL` is NULL rather than true, and
a forgotten tenant fails closed. This was found by writing the "no tenant reads nothing"
test before believing the policy worked.

**Tests.** `tests/test_isolation.py`, seven tests, green:

```
$ .venv/bin/python -m pytest tests/test_isolation.py
.......                                                                  [100%]
7 passed in 0.16s
```

They cover: a tenant reads only its own rows; a cross-tenant read returns zero rows rather
than an error or a filtered subset (AC-8 as literally written); a session with no tenant
reads nothing; writing another tenant's row is refused by `WITH CHECK`; an unusable org id
is refused before any query; two tenants may hold the same product key (H11); and the
tenant registry is deliberately not tenant-scoped. `tests/conftest.py` truncates `org`
with `CASCADE` after each test and fails the session loudly if the database is not
migrated, rather than erroring once per test.

---

## Next

Step 3, the `refs` registry: the `kind:id` URN scheme (`clause:`, `obs:`, `file:`,
`eval_metric:`, `decision:`, `ticket:`, `code_change:`) with one resolver per kind turning a
ref into an immutable URL. This is the mechanism that makes adding a source system a matter
of one resolver and one adapter, with no change to the query layer, and it is what AC-14
and AC-7 are enforced through. It lands before migration 2 because `clause` and
`observation` store refs and should be written against a settled scheme.
