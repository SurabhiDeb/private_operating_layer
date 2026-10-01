# Build progress — the Layer, phases 1 to 3

Branch `layer-phase-1-3`. Specification: `operating_layer_main/PRD-SPEC.md` v3.
Conventions and constraints: `CLAUDE.md`.

**Scope.** Phases 1–3 of the handoff's build order: onboarding, the five finding queries,
and the stdio MCP server. That is the whole read half, usable from a terminal with no UI,
no agent and no Dust. What follows phase 3 is **not** the handoff's order any more — see
"Beyond phase 3" at the end of this file.

**How to read the status column.** A step is `done` only when its tests pass and the
result has been quoted, never when the code merely exists.

| # | Step | Status |
|---|---|---|
| 1 | Branch, venv, pytest, Alembic scaffolding, spec docs committed | **done** |
| 2 | Migration 1: `org`, `product`, `source`, `binding`, RLS, roles, isolation tests | **done** |
| 3 | `refs` registry and resolvers; the `repo` source with revision pinning | **done** |
| 4 | Migration 2: `clause`, `clause_identity`, `observation`, `enforcement_fact`, `link`, `entity`, `proposal`, `audit_event` | not started |
| 5 | The metric engine, tiers 1 to 3 | not started |
| 6 | Adapters: spec, then eval (run files, then Langfuse), then code | not started |
| 7 | The onboarding state machine and CLI; onboard all three fixtures | not started |
| 8 | Verdicts and the five finding queries; reproduce all three C1 conditions | not started |
| 9 | The stdio MCP server | not started |
| 10 | The agnosticism grep test, the no-causal-language test, the AC matrix | not started |

**Acceptance criteria met so far:** AC-8, AC-14 (the mechanism; re-asserted per finding at step 8). Partial: AC-2 (enumeration only). Hard cases: H11.

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

## Step 3 — Refs, resolvers, and a pinned repository. Done.

**Why a URN and not a URL.** Every citation in the system is a `kind:id` string —
`clause:TRI-11.2`, `obs:102`, `file:products/triage/gate.py#L25`. Storing a URL would mean
storing a guess about how that system will look later: its host, its revision scheme, its
path layout. Storing identity and deriving the URL at read time is what makes adding a
source system a matter of one resolver, with no change to the query layer. Refs are scoped
to an org and are never globally unique (H11), so resolution always happens inside a tenant
context.

**`layer/refs/ref.py`.** Parsing splits on the first colon only, because ids legitimately
contain colons — a Slack permalink, a timestamped run id — and splitting on the last would
silently rewrite them. A malformed ref raises rather than being coerced into something
plausible; PRD B5 ranks a fabricated ref third among unacceptable failures, so a
half-readable one must not be quietly repaired.

**`layer/refs/registry.py`.** Kind to resolver, instantiated per unit of work rather than
as a module global, because resolvers close over a tenant's sources and a process-wide
registry would be a route for one tenant's repository to answer another tenant's ref.
Declining to resolve is a first-class outcome, not an error: `links()` returns
`(evidence_links, unresolved)` in the finding's own shape, preserves order and duplicates
— evidence is evidence, and tidying the list would misreport what was cited — and drops
nothing. AC-14 is enforced here as well as at the source, as a second line, so that a
resolver added later cannot reintroduce a branch citation without a test failing.

**`layer/adapters/repo.py`.** A repository held at one revision, with three guarantees that
otherwise rot quietly:

- `pin()` accepts `HEAD`, a branch or a tag as a *request*, and stores only the sha that
  resolved to. The constructor refuses anything that is not a commit sha, so no later code
  path can cite a moving target.
- `read()` uses `git show <rev>:<path>` rather than reading the working tree. A clause
  imported from a spec is then the text at the revision its citation names. Reading the
  working tree would make every citation a near-miss — right file, possibly different
  content — which is the worst kind of wrong because it looks right.
- A `-dirty` sha, a real shape in committed eval metadata, is kept as provenance and never
  used to build a URL. Nobody else can obtain that tree, so offering a link to it would be
  a citation that cannot resolve for the reader.

The URL shape is a config template defaulting to GitHub's, so a GitLab, Gitea or internal
host needs a configuration value rather than a code change (rule R2).

**Two defects the tests caught.**

`git ls-tree` does not glob. It rejects `:(glob)` pathspec magic outright and treats a bare
`products/*/runs/*.json` as matching nothing — exiting zero, with no output. The eval
backfill would have enumerated no files, stored no observations and reported no error,
which is precisely the failure AC-2 exists to catch. Filtering moved into Python via
`PurePath.full_match`, which is anchored and does not let `*` cross a separator.

Fixture A's runs directory holds **48 `.json` files but 47 run records**. `v1.json` is a
bare JSON list from an earlier format, with no `meta` and no `results`. The prototype
reports "7 of 47 runs" and is right about runs; 48 is right about files. The trap is for
step 6: an adapter that globs `*.json`, fails to parse that one and skips it quietly would
leave AC-2's reconciliation comparing 47 against 47 and looking correct. The adapter must
distinguish "not a run record" from "a run that failed to import" and account for both out
loud (EC-2, EC-4). Recorded in `CLAUDE.md` and in the test's docstring.

**Tests.** 53 pass, 30 of them new.

```
$ .venv/bin/python -m pytest
.....................................................                    [100%]
53 passed in 3.13s
```

`tests/test_refs.py` covers parsing, the first-colon rule, malformed refs, unknown kinds
resolving to None rather than raising, order and duplicate preservation, and the branch-URL
guard. `tests/test_repo_source.py` covers pinning, the refusal to construct on a moving
revision, reading at the commit rather than the working tree, listing at the revision, URL
templating, and the resolvers — the mechanism tests against a throwaway repository built in
the test, because proving that pinning works should not require a particular product's
files, and two against the fixture repository, because "the revision we pin is the revision
a reader opens" is only worth believing against a real history.

---

## Next

Step 4, migration 2: `clause`, `clause_identity`, `observation`, `enforcement_fact`,
`link`, `entity`, `proposal` and `audit_event`, with the append-only trigger on
`audit_event` and row level security extended to each. `clause` carries `unit`, `direction`
and `value_high` for the reasons in the decision table, and `observation` carries `passed`
and `total` as well as `value`, because the Wilson interval needs n and a rate alone cannot
supply it.
