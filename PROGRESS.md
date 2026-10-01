# Build progress — the Layer, phases 1 to 3

Branch `layer-phase-1-3`. Specification: `operating_layer_main/PRD-SPEC.md` v3.
Conventions and constraints: `CLAUDE.md`. What the product is: `README.md`.

**Scope.** Phases 1–3 of the handoff's build order: onboarding, the five finding queries,
and the stdio MCP server. That is the whole read half, usable from a terminal with no UI,
no agent and no Dust. What follows phase 3 is **not** the handoff's order any more — see
"Beyond phase 3" at the end of this file.

**How to read the status column.** A step is `done` only when its tests pass and the
result has been quoted, never when the code merely exists. Each finished step below records
what it had to achieve, what was built, **how it was built**, any defect found on the way,
and how it was verified.

| # | Step | Status |
|---|---|---|
| 1 | Branch, venv, pytest, Alembic scaffolding, spec docs committed | **done** |
| 2 | Migration 1: `org`, `product`, `source`, `binding`, RLS, roles, isolation tests | **done** |
| 3 | `refs` registry and resolvers; the `repo` source with revision pinning | **done** |
| 4 | Migration 2: `clause`, `clause_identity`, `observation`, `enforcement_fact`, `link`, `entity`, `proposal`, `audit_event` | **done** |
| 5 | The metric engine, tiers 1 to 3 | **done** |
| 6 | Adapters: spec, then eval (run files, then Langfuse), then code | **in progress** — 6a spec done, 6b eval next, 6c code after |
| 7 | The onboarding state machine and CLI; onboard all three fixtures | not started |
| 8 | Verdicts and the five finding queries; reproduce all three C1 conditions | not started |
| 9 | The stdio MCP server | not started |
| 10 | The agnosticism grep test, the no-causal-language test, the AC matrix | not started |

**Acceptance criteria met:** AC-8, AC-9 (schema level), AC-13, AC-14 (as a mechanism;
re-asserted per finding at step 8), AC-19 (isolation half). AC-18 and AC-21 (spec import half).
Partial: AC-2 (enumeration and idempotency; reconciliation in 6b). AC-1 (identity keys
emitted; resolution in step 7). **Hard cases:** H3, H5, H6, H11, H14, H15.
**Edge cases:** EC-2, EC-4 (engine half), EC-9. **Tests:** 173 passing. **Commits:** 12 on `layer-phase-1-3`.

---

## Step 1 — Scaffolding. Done.

**What it had to achieve.** A place for the Layer to exist that does not disturb EarlyEcho,
and migrations, because there were none.

**What was built.** Branch `layer-phase-1-3`. A `.venv` on Python 3.13.5 with
`requirements-layer.txt`, deliberately separate from EarlyEcho's `requirements.txt`. The
`layer/` package skeleton. `layer/core/config.py`, `layer/core/errors.py`. Alembic,
configured from nothing. `pytest.ini`. `.env.example` committed and `.env` gitignored.
`operating_layer_main/` committed, having been untracked.

**How it was built.**

- **Dependencies** were installed and then *introspected* rather than assumed:
  SQLAlchemy 2.1.1, Alembic 1.20.0, psycopg 3.3.6 (so URLs are `postgresql+psycopg://`,
  not `postgresql://`), pgvector 0.5.0, pydantic-settings 2.15.0, pytest 9.1.1, mcp 2.2.0.
- **`config.py`** subclasses `BaseSettings` with `env_prefix="LAYER_"`, so the Layer and
  EarlyEcho can share one `.env` without colliding. It holds **two** database URLs and a
  `migration_url` property: `LAYER_DATABASE_URL` for the application role,
  `LAYER_ADMIN_DATABASE_URL` for the table owner. Only migrations use the second.
- **`errors.py`** makes `Refusal` a frozen dataclass with `shape` as a non-init field, so
  every refusal serialises into PRD B1's four-shape contract. It is a return value, not an
  exception, because B1 requires that the Layer "never silently returns nothing".
  `TenantContextMissing`, `UnknownProduct` (B3 rule 9) and `NotLive` (B3 rule 8) name the
  three ways a caller can be wrong.
- **Alembic** was initialised with `alembic init -t generic layer/db/alembic`, then the
  generated config was changed in two ways. `alembic.ini`'s `sqlalchemy.url` is commented
  out entirely so no connection string sits in version control; `env.py` sets it from
  `settings.migration_url` at runtime and imports `layer.db.models` so `Base.metadata` is
  populated for `--autogenerate`.
- **`pytest.ini`** declares five markers — `ac`, `hard_case`, `edge_case`, `story`,
  `needs_db` — so every test can be traced to the criterion it exists for. This is what
  makes AC-10 to AC-12 ("every case has a test") checkable rather than asserted.

**One open question closed.** Handoff section 14 item 4 flagged the MCP SDK's import path as
untrustworthy from memory. It was installed and read with `inspect.signature` rather than
recalled: `mcp` 2.2.0 renamed `FastMCP` to `MCPServer` at `mcp.server.mcpserver`, tools are
a `@server.tool()` decorator, and transport is an argument to `run()` — `"stdio"` now,
`"streamable-http"` in phase 4. Code written from memory would have imported a module that
no longer exists. It also confirms phase 4 is one argument rather than a rewrite.

---

## Step 2 — Migration 1 and provable tenant isolation. Done.

**What it had to achieve.** AC-8: a cross-tenant read returns zero rows, proven by test.
And the three tables v3 added, without which nothing describes how a product enters.

**Why it comes first.** The inherited codebase demonstrates the failure mode it prevents.
EarlyEcho filters `org_id` by hand in roughly sixty places and omits it in six;
`api/routes/ingest.py:156` and `:178` look a `PendingEntity` up by `id` alone and then copy
that row's `org_id` forward, so anyone who guesses an id can approve another tenant's item.
The fix is not more diligence. It is making the filter unnecessary.

**What was built.** `layer/core/db.py`, `layer/db/models.py`, `layer/db/rls.py`, migration
`e527dd6`, and `tests/test_isolation.py`. A `layer` database with a `layer_owner` role and a
`layer_app` role that owns nothing.

**How it was built.**

- **`org_session(org_id)`** is a context manager and the only door to tenant data. It opens
  a transaction, then issues `SELECT set_config('app.org_id', :org, true)`. `set_config`
  rather than `SET LOCAL` because only the former accepts a **bind parameter** — `SET LOCAL
  app.org_id = :org` is not valid Postgres, so the alternative would be interpolating a
  value into SQL. The `true` argument scopes it to the transaction.
- **`_as_uuid`** validates before any query runs, so a malformed tenant raises
  `TenantContextMissing` rather than degrading into an unscoped session.
- **`models.py`** uses SQLAlchemy 2.x `Mapped` / `mapped_column`. CHECK constraints are
  generated from Python tuples — `"status IN " + str(PRODUCT_STATUSES)` — which emits valid
  SQL and keeps the vocabulary in one place. Closed sets the Layer branches on get a CHECK;
  open ones (`pattern`, `source.kind`) deliberately do not, because the handoff's build
  order adds a source "with a new `kind` and no new concepts" and that must not need a
  migration. No Postgres ENUM anywhere: altering one is a migration per customer.
- **`rls.py`** emits statement *lists* rather than executing anything, so the migration
  stays declarative and the same statements can be asserted in tests. The application role
  is parsed out of its own connection URL with `urlsplit().username` rather than hardcoded,
  so different role names need no migration edit.
- **The migration** was produced with `alembic revision --autogenerate` and then hand-edited
  to append `rls.enable_statements() + rls.grant_statements(role)` to `upgrade()` and the
  reverse, in reverse order, to `downgrade()`. Autogenerate cannot see policies or grants.
  They land **in the same migration as the tables** on purpose: a window in which tenant
  tables exist without a policy is a window in which a leak is legal.
- **`ENABLE` plus `FORCE ROW LEVEL SECURITY`**, with both `USING` and `WITH CHECK`, so
  isolation governs writes as well as reads. `FORCE` is not redundant — a table's owner is
  otherwise exempt from its own policies, which would mean anything ever run as the owner
  bypassed isolation silently.
- **`conftest.py`** holds a module-level engine on the admin URL, truncates `org CASCADE`
  after each test, and has a session-scoped autouse fixture that calls `pytest.fail` naming
  `alembic upgrade head` if the schema is missing — one clear failure instead of one
  confusing error per test.
- **Test technique:** not one assertion in `test_isolation.py` filters `org_id` by hand.
  That is the point being proven. If the tests needed the filter, the mechanism would not be
  doing the work.

**A defect the test caught.** The obvious predicate,
`org_id = current_setting('app.org_id', true)::uuid`, is wrong under connection pooling.
`current_setting(..., true)` returns NULL only while the setting has never been touched on
that connection; after any transaction-local `set_config` it reverts to the **empty
string**. So a later session with no tenant evaluates `''::uuid` and raises
`invalid input syntax for type uuid` instead of matching no rows. Wrapped in
`nullif(..., '')` both cases collapse to NULL, `org_id = NULL` is NULL rather than true, and
a forgotten tenant fails closed. Found by writing the "no tenant reads nothing" test before
believing the policy worked.

**Verification.**

```
$ .venv/bin/python -m pytest tests/test_isolation.py
.......                                                                  [100%]
7 passed in 0.16s
```

Covering: a tenant reads only its own rows; a cross-tenant read returns zero rows rather
than an error or a filtered subset, which is AC-8 as literally written; a session with no
tenant reads nothing; writing another tenant's row is refused by `WITH CHECK`; an unusable
org id is refused before any query; two tenants may hold the same key (H11); and the tenant
registry is deliberately not tenant-scoped, asserted so that adding a policy later is a
decision rather than an accident.

---

## Step 3 — Refs, resolvers, and a pinned repository. Done.

**What it had to achieve.** A citation scheme that survives the systems it points at, and a
guarantee that every citation names an immutable revision (AC-14).

**What is stored, and what a reader sees.** The database stores the URN only. The URL is
built at read time by the resolver registered for that kind.

```
stored in the record        file:products/triage/gate.py#L25
                            clause:TRI-11.2
                            obs:102

returned to a reader        https://github.com/<org>/<repo>/blob/ef07ac9b4daf.../products/triage/gate.py#L25
```

The finding carries both, so nobody is ever handed a URN on its own:

```json
"evidence":       ["file:products/triage/gate.py#L25"],
"evidence_links": [{"id": "file:products/triage/gate.py#L25", "url": "https://.../blob/ef07ac9b4daf.../gate.py#L25"}],
"unresolved":     []
```

**Why a URN and not a URL.** Three reasons, in order of how much they cost to get wrong.

*Portability.* A URL embeds a host, a revision scheme and a path layout — three guesses about
how someone else's system will look later. Moving a repository to GitLab, or changing the
revision a source is pinned to, is then a rewrite of every row that cited it. With a URN it is
one resolver and nothing else, which is also what makes adding a source system cheap enough
that the agnosticism claim survives contact with a second customer.

*Honesty.* A resolver can **decline**: the file does not exist at that revision, the sha is
`-dirty` and nobody else can obtain that tree, no resolver is registered for the kind at all.
The ref then lands in the finding's `unresolved[]` and is displayed, which is what PRD B1
requires — "empty is the required state; a non-empty list is displayed, never hidden". A
stored URL cannot tell anyone it has stopped working; it just 404s for the reader, which is
the failure B5 item 3 and B6's citation-resolvability bar exist to prevent.

*Enforcement in one place.* Because every URL passes through `Registry.resolve`, AC-14's
"citations pin an immutable revision" is checked centrally. A resolver added later cannot
reintroduce a `blob/main/` citation without a test failing. Were URLs stored at write time,
that check would have to be repeated at every write site and would eventually be missed at one.

**The consequence worth knowing.** Rendering a finding needs the source row, because that is
where `pinned_rev` and the URL template live, so a finding can only be resolved inside a
tenant context. That is intentional rather than awkward: refs are scoped to an org and never
globally unique (H11), and the same property is what stops one tenant's repository answering
another tenant's ref.

**What was built.** `layer/refs/ref.py`, `layer/refs/registry.py`,
`layer/adapters/repo.py`, `tests/test_refs.py`, `tests/test_repo_source.py`.

**How it was built.**

- **`Ref`** is a frozen, slotted dataclass validating in `__post_init__`. `parse` uses
  `str.partition(":")` to split on the **first** colon only, because ids legitimately
  contain colons — a Slack permalink, a timestamped run id — and splitting on the last would
  silently rewrite them. A malformed ref raises rather than being coerced into something
  plausible: B5 ranks a fabricated ref third among unacceptable failures.
- **`Registry`** maps kind to `Resolver = Callable[[Ref], str | None]`. It is instantiated
  per unit of work, **not** as a module global, because resolvers close over a tenant's
  sources and a process-wide registry would be a route for one tenant's repository to answer
  another tenant's ref. `links()` returns `(evidence_links, unresolved)` directly in the
  finding's shape, preserving order and duplicates — evidence is evidence, and tidying the
  list would misreport what was cited.
- **`is_pinned`** runs inside `Registry.resolve`, so AC-14 is enforced centrally as a second
  line. A resolver added later cannot reintroduce a branch citation without a test failing.
- **`RepoHandle`** wraps `subprocess.run(["git", "-C", path, ...])` with three helpers:
  `_git` for text, `_git_bytes` for file contents, and `_git_error`, which maps git's "does
  not exist" message to `FileNotFoundError` so a missing path is a fact about the revision
  rather than a crash.
  - `pin()` runs `rev-parse --verify <rev>^{commit}`, so `HEAD`, a branch or a tag may be
    *asked for* while only the resolved sha is stored. The constructor then refuses anything
    that is not a 7-to-40 character hex sha. Tags are excluded on purpose: a tag can be
    moved, so it is a convention rather than a guarantee.
  - `read()` runs `git show <rev>:<path>`, never a filesystem read. This is the property the
    design rests on — reading the working tree would make every citation a near-miss, right
    file and possibly different content, which looks right and is not.
  - `list_files()` runs `ls-tree -r --name-only <rev>` and filters in Python.
  - A `-dirty` sha is recognised and treated as provenance only, never as something to open,
    since nobody else can obtain that tree.
- **URL shape is configuration.** `blob_url_template` defaults to GitHub's
  `{repo_url}/blob/{rev}/{path}` and is overridable per source, so GitLab, Gitea or an
  internal host needs a config value rather than a code change (rule R2).
- **Resolvers are factories** closing over a handle — `repo_resolvers(handle)` returns
  `{"file": ..., "code_change": ...}` — which keeps `layer/refs/` free of any import from
  `layer/adapters/`, so the dependency runs one way only.
- **Test technique:** the mechanism tests build a throwaway two-commit repository in
  `tmp_path` with `git init -b main`. Proving that pinning works should not require any
  particular product's files. The decisive test pins, then *edits the working tree*, then
  asserts `read()` still returns the committed bytes. Two tests use the fixture repository
  behind a `skipif`, because "the revision we pin is the revision a reader opens" is only
  worth believing against a real history.

**Two defects the tests caught.**

`git ls-tree` does not glob. It rejects `:(glob)` pathspec magic outright and treats
`products/*/runs/*.json` as matching nothing — **exiting zero, with no output**. The eval
backfill would have enumerated no files, stored no observations and reported no error, which
is precisely the failure AC-2 exists to catch. Filtering moved into Python via
`PurePath.full_match`, which is anchored and does not let `*` cross a separator.

Fixture A's runs directory holds **48 `.json` files but 47 run records**. `v1.json` is a bare
JSON list from an earlier format, with no `meta` and no `results`. The prototype reports
"7 of 47 runs" and is right about runs; 48 is right about files. The trap is for step 6: an
adapter that globs `*.json`, fails to parse that one and skips it quietly would leave AC-2's
reconciliation comparing 47 against 47 and looking correct. The adapter must distinguish
"not a run record" from "a run that failed to import" and account for both out loud (EC-2,
EC-4). Recorded in `CLAUDE.md` and in the test's docstring.

**Verification.**

```
$ .venv/bin/python -m pytest
.....................................................                    [100%]
53 passed in 3.13s
```

---

## Step 4 — Migration 2: the record itself. Done.

**What it had to achieve.** The eight tables the read half reasons over, with isolation and
append-only enforcement landing alongside them rather than after.

**What was built.** `clause`, `clause_identity`, `observation`, `enforcement_fact`, `entity`,
`link`, `proposal`, `audit_event` in migration `e56fae9`; append-only triggers in
`layer/db/rls.py`; `tests/test_schema_guarantees.py`, 24 tests.

**How it was built.**

Models first, then `alembic revision --autogenerate`, then the generated migration was
hand-edited to append what autogenerate cannot see: policies, grants and triggers.
Autogenerate also emitted `pgvector.sqlalchemy.vector.VECTOR(...)` without importing
pgvector, so the import is added by hand.

**Three departures from PRD B2's data contract**, each forced by the fixtures rather than
invented, and each with a test:

`clause` carries `unit`, `direction` and `value_high`. Real targets in the fixture specs
include `3% to 8%`, `under 1 second`, `under £1,200` and `MRR 0.85`. A comparator and a
single value cannot hold a band, and without a unit the Layer cannot tell `0.85` as a ratio
from `85` as a percentage from `850` as milliseconds. A CHECK refuses a one-ended `between`,
because that is a parse that half failed and would silently never match anything.

`observation` carries `passed` and `total` beside `value`. The verdict rule is a Wilson
interval and it needs n; 19/20 and 190/200 are both "95%" and are very different claims,
which is why the fixtures have a stats module at all. Where a source supplies only a rate,
`total` is null and the verdict can only ever be `cannot_confirm` — which is the honest
answer rather than a limitation.

`enforcement_fact` exists at all. B2 has no table for what CI actually checks, and AC-4 and
AC-15 cannot be answered without one. `scope` is the load-bearing column: `enforced: true,
scope: latest_only` is the condition a boolean cannot express, and it is the reason the
product has anything to find.

**Two corrections to B2's idempotency key**, both of which would let duplicates through:

`product_id` belongs in it. Without it, two products in one org that each have an unbound
metric of the same name collide on `(org, NULL, metric, NULL)`.

`NULLS NOT DISTINCT`. In Postgres a NULL is distinct from every other NULL, so the plain
four-column constraint never fires for a metric no clause mentions, or a production reading
with no run url — precisely the rows a repeated pull duplicates. Available because the
server is Postgres 17.

**Other choices worth recording.**

- `observation.clause_ref` is text, not a foreign key. An observation may exist for a metric
  no clause mentions; that is H16, reported as `uncovered / metric_without_clause`, where the
  gap is the absent clause rather than the measurement. A foreign key would make the
  interesting case unstorable.
- `link` endpoints are refs, not foreign keys, because a link routinely joins records the
  Layer does not hold — a Linear ticket to a Notion requirement. The Layer holds the
  statement that they are related, which is the product; it does not re-host either end.
- `clause.verdict` is stored, as B2 and AC-13 require, but `verdict_at` is stored with it.
  A verdict is the result of the last measurement pass rather than a property of the text,
  and without a timestamp one computed before the latest backfill is indistinguishable from
  a current one.
- `version` tracks the promise, not the answer. Rewording supersedes a row and increments
  `version`; recomputing a verdict does not, because a measurement is not a change to what
  was promised.
- `observation` and `audit_event` use bigint identities rather than uuids: they are the two
  tables expected to grow without limit, and `obs:102` is the ref shape the output contract
  already uses.
- `Double` rather than `Numeric` for every measured value, because each consumer is
  statistical and `Numeric` would force a Decimal/float coercion at every comparison.

**`audit_event` is append-only in the database, not by convention.** UPDATE is refused
outright. DELETE and TRUNCATE are refused unless a transaction-local `app.erasure` flag is
set. Two triggers are needed, not one: the row-level trigger cannot see TRUNCATE, which
fires no row triggers and would otherwise be a silent way to empty the audit log. The hatch
exists because EC-9 and SEC-8 require a tenant-scoped hard delete reconciled with append-only
history, and audit item P9 records the conflict — an absolute refusal would make the right to
erasure unimplementable. It is narrow, has to be asked for by name, and is itself auditable.
The test harness was changed to use that same hatch when truncating between tests, so it
cannot quietly enjoy a privilege production lacks.

**`proposal` makes the approval record structural.** A CHECK refuses a decided proposal with
no decider and an open one carrying a decision, so PRD B5's first unacceptable failure — a
write with no approval record — is unstorable rather than merely discouraged. A partial
unique index on open rows allows only one open proposal per target and field (B4 item 4),
while leaving a field changeable more than once over its life.

**A migration bug the downgrade test caught.** The `rls` helpers defaulted to the live
`TENANT_TABLES` constant. Once migration 2 added eight tables, migration 1's `downgrade()`
began trying to revoke privileges on tables it had never created, failing with
`relation "audit_event" does not exist`. A migration must describe the schema as it was when
it was written, so the defaults were removed entirely and every migration now names its own
tables; the constant remains for application code. Transactional DDL meant the failed
downgrade rolled back cleanly rather than leaving a half-torn-down database, which is worth
noting as the reason this was a nuisance rather than an incident.

**Verification.**

```
$ .venv/bin/alembic downgrade base && .venv/bin/alembic upgrade head
$ .venv/bin/python -m pytest
........................................................................ [ 93%]
.....                                                                    [100%]
77 passed in 4.36s
```

A full downgrade to base leaves only `alembic_version` and removes the trigger function, so
the migration is reversible rather than one-way. The 24 new tests assert what the schema
refuses: duplicate observations including the two NULL cases, an incoherent sample count,
state and verdict set independently (AC-13), a one-ended band, coexisting clause versions
(H3), one metric serving two clauses (H5), an unapproved or doubly-open proposal, an audit
event that cannot be updated or deleted without an erasure, that the erasure flag does not
leak into the next transaction on a pooled connection, `enforced: true` with
`scope: latest_only` (AC-15, H14), and that none of the new tables leaks across tenants.

---

## Step 5 — The metric engine. Done.

**What it had to achieve.** Turn a document the Layer has never seen into a measured
number, for any product, without learning anything about that product.

**Why this is the crux.** Neither specification says how a number is obtained, and the
fixtures show why that gap matters: their committed run files contain **no metric values at
all**. Every headline number is derived by iterating per-case rows, and one product's come
from a module carrying a hardcoded article blacklist and rank semantics. A Layer that
computed those itself would be reimplementing each customer's scorer, which is building the
eval half — the thing handoff section 4 forbids outright.

**What was built.** `layer/metrics/pointer.py`, `predicates.py`, `engine.py`, and
`tests/test_metrics.py`, 56 tests.

**How it was built — three tiers, where the third is a refusal.**

*Tier 1, the source names a number.* `read` resolves an RFC 6901 JSON pointer, with an
optional `scale` so seconds become milliseconds without code. `ratio_of` takes a
passed/total pair at two pointers and is worth its own aggregation rather than being folded
into `read`, because carrying n is what lets the verdict rule say anything at all.

*Tier 2, per-case rows plus a declarative definition.* `rate`, `accuracy`, `recall`,
`precision`, `count`, `mean` and `percentile`, each with an optional `filter` that selects a
subset first. The filter is what makes "the critical cases" expressible without the engine
ever learning what critical means.

*Tier 3, neither works.* Mean reciprocal rank, recall@k with rank semantics, bespoke
exposure rules. `compute` returns `Unmeasurable(reason="no_metric")`, the clause is reported
as `uncovered` and its verdict is `not_measured`. `supports()` lets onboarding ask before a
binding is confirmed, so a human is told "nothing connected measures this" rather than
discovering it later. **That refusal is the line that stops this file growing a scorer per
customer**, and it is phrased in the specification's own vocabulary so it surfaces as a
finding rather than an error in a log.

**Nothing evaluates a string as code.** A definition arrives in `source.config`, which is
tenant-supplied. PRD B3 rule 2 and SEC-4 require ingested text to be data and never an
instruction, so a predicate is a tree of dicts over a closed operator set — `all`, `any`,
`not`, `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `exists`, `is_true`, `is_false`, `is_present`,
`is_blank`, `contains_any`. It is readable by a human reviewing a binding and incapable of
doing anything but answering true or false about one row. An unknown operator is refused
rather than interpreted, which is asserted by a test that feeds it an `__import__` string.

**Where the engine declines rather than returning a number.** Each of these is a deliberate
choice against a plausible wrong answer:

| Situation | Why not a number |
|---|---|
| A filter matches no rows | Reporting zero would assert a failure that was never observed |
| `recall` where no case is relevant | 0.0 would read as total failure of something the run never exercised |
| `total` is zero | There is nothing to measure; a rate would be invented |
| A pointer resolves to nothing | The source does not carry this metric, which is a finding, not a default |
| The document lacks the rows promised | One unreadable run must not abort a backfill of ninety (EC-4) |

A percentile or a mean carries no `passed`/`total` at all, so a clause measured that way can
only ever reach `cannot_confirm`. That is correct rather than a limitation: a p95 from eleven
requests is not evidence about a latency promise.

**Two details that only real data would have revealed.**

`to_bool` is strict and separate from `truthy`. A bare `bool("false")` is `True`, and in the
reference fixture `escalate` is a JSON boolean while `labelled_escalate` is the string
`"true"` — the product's own gate relies on that asymmetry. Comparing them uncoerced reports
every case as a mismatch; coercing with Python truthiness inverts the metric instead. So a
definition declares `coerce: {field: bool}`, and an unrecognised string raises rather than
being guessed at.

**A design fix found by a test.** `is_falsy` was doing two jobs and broke on a real field.
`problem` holds an error message, and asking whether `"bad json"` is true has no answer. Split
into `is_true`/`is_false`, which interpret a field encoding a boolean and refuse anything
else, and `is_present`/`is_blank`, which ask only whether there is content. The distinction
then mattered a second time within the hour: policydesk's `critical` holds a case label —
`'C1'`, `'C3'`, or an empty string — so its subset filter is `is_present`. One operator
covering both would have silently produced a `bad_definition` for the metric that condition 2
turns on.

**Validation against the fixtures' own published numbers.** From definitions a human would
write at onboarding, with no product knowledge in the engine:

| Metric | Engine | The handoff's table |
|---|---|---|
| v6 `status_ok` | 47/50 = 94.0% | 94.0% |
| v6 `critical_pass_rate` | 10/11 = 90.91% | 90.9% |
| v1 groundedness sidecar | 29/35, read not recomputed | — |

The mid-sequence triage run is confirmed to breach its 99% bar and to name the case it
missed, which is the number a latest-only gate never sees. `v1.json`, the file that is not a
run, declines with `bad_definition` rather than crashing — step 6 must now tell that apart
from a run that failed to import.

**Verification.**

```
$ .venv/bin/python -m pytest
.............................................................            [100%]
133 passed in 4.99s
```

---

## Step 6 — The adapters. In progress.

Three adapters, built and verified in order. **6a is done; 6b and 6c are next.**

### 6a — The spec adapter. Done.

**What it had to achieve.** Read a specification document into clause candidates, for a
document the Layer has never seen, with everything product-specific in configuration.

**What was built.** `layer/adapters/base.py` (the candidate and report contracts),
`layer/adapters/parse/markdown.py`, `layer/adapters/spec/values.py`,
`layer/adapters/spec/file_spec.py`, `tests/fixtures/alien_spec.md` and
`tests/test_spec_adapter.py`, 40 tests.

**Deterministic, with no model call.** A decision rather than a limitation. The structural
pass covers headings, tables, list items and stated targets, and it is reproducible, free
and testable without an API key. An LLM pass belongs **on top** of this for prose a parser
cannot reach, never underneath it: EC-10 requires failing closed, and an importer whose
output changes between runs makes that hard to reason about. The interface is shaped so that
pass can be added without moving anything.

**How the report keeps itself honest.** An `ImportReport`'s arithmetic must balance —
everything enumerated is a candidate, a deliberate skip, or a failure — and an unbalanced
report says so in its own `summary()`. This is the quiet load-bearing part, because AC-2's
reconciliation only proves something if "this is not applicable" and "this failed to import"
are counted separately. A quiet skip otherwise makes 47 of 47 look like success. The unit of
enumeration is whatever the adapter walks (a section, a file), not a candidate, since one
unit routinely yields several or none.

**Where product knowledge is allowed to live.** In `source.config`: which columns carry the
metric, the target and the rationale; how section titles map to clause kinds; which sections
to ignore. The defaults are generic English words and are overridable precisely because a
stranger's document will not use them.

**`values.py` is where step 4's `unit`, `direction` and `value_high` earn their place.** Real
targets in real specs include `85%`, `3% to 8%`, `under 1 second`, `under £1,200`, `under 3p`
and `0.85`. A comparator plus a single value holds only the first kind. Percentages become
ratios because that is what measured values are; durations and money keep their own units,
since 1200 pounds is not 12 of anything. Where a direction is inferred rather than read —
a bare `85%` in a Target column is almost certainly a floor but does not say so — the
assumption is recorded on the candidate and shown to whoever confirms it, because every
clause is created `provisional` and the Layer proposes the reading rather than deciding it.

**`markdown.py` carries line numbers everywhere**, so a citation resolves to the lines that
stated a promise rather than to the file containing it. An unnumbered subsection inherits its
nearest numbered ancestor's ref, so a metrics table under `### Retrieval` inside
`## 8. Success metrics` is still section 8 — a ref built from the subsection's own title
would move the moment someone renamed the heading. Ordinals are counted per resolved ref
across the whole document, so two subsections sharing a parent do not both start at 1 and
issue the same ref twice.

### The third fixture, and the four defects it found

`tests/fixtures/alien_spec.md` is written in a deliberately unfamiliar style: prose
thresholds, no metrics table, `2)` numbering, `| Signal | Bar | Commentary |` headers. It
exists because the two reference specs were written by one author in one style, both using an
identical `| Metric | Target | Why |` table, so a parser tuned to them would look agnostic
and not be. It paid for itself immediately.

| Defect | Why it mattered |
|---|---|
| Prose mining was gated on a section **title** matching a vocabulary of English words | A document with a heading like "Service levels" had every bar in it silently ignored. This is exactly the fitting AC-21 exists to catch, and two same-author fixtures could never have surfaced it. Now driven by what the sentence says: a number plus an obligation word, where the obligation guard keeps "volume is roughly 4,000 messages per day" from becoming a promise nobody made |
| A table headed `Signal` rather than `Metric` lost its label | Both rows were dropped. The first column of a promises table is its subject, so it is now the fallback rather than requiring config for every possible spelling |
| A sentence stating two bars yielded one | "under 1500ms, and the 95th percentile under 4 seconds" lost the second silently, which is the failure mode this adapter is arranged against |
| `"no less than 95%"` parsed as a **ceiling** | `_AT_MOST`'s `less than` matched inside `_AT_LEAST`'s `no less than` and was tested first. An inverted comparator is the most damaging parse error available: every value above the bar would read as a breach |

**Two more found by reading output rather than by a test.** `85 * 0.01` is
`0.8500000000000001` in binary floating point, so no stored threshold would have compared
equal to a written one — an invisible discrepancy is worse than an obvious one, because a
clause that should read `met` reads `missed` for no visible reason. And a clause read from
prose no longer gets a metric name at all: a slug built from a sentence
(`on_no_less_than_95_of_unusable_photos`) would never match what an eval source calls the
number. It records that a bar exists, carries an assumption saying so, and leaves the
measurement to a human-confirmed binding — which is what onboarding step 5 is for.

**Verification.**

```
$ .venv/bin/python -m pytest
.............................                                            [100%]
173 passed in 5.26s
```

Against the reference specs the adapter independently lands on the same refs the prototype
used — `TRI-11.2` for escalation recall, `PD-8.8` for the critical-case bar — extracting 10
and 12 numeric thresholds with every section accounted for, no duplicate refs, and the
`3% to 8%` band kept intact. The alien spec imports on configuration alone, yielding all
eight of its bars including the two-bar sentence, the prose band and the `£900` ceiling.

### 6b — The eval adapter. Next.

Run files first, then Langfuse. This is where AC-2's reconciliation is earned: all 48
candidate files enumerated, 47 classified as run records, `v1.json` classified as
not-a-run, and **zero silently skipped**.

### 6c — The code adapter. After 6b.

The enforcement scan, producing `enforcement_fact` rows. It looks for a threshold comparison
and, separately, for how the run set was chosen — a `[-1]`, a `sorted(...)[-1]`, a "latest"
selector — and records `scope: latest_only`. That is what turns condition 1 from invisible
into a finding. Conservative by design: anything it cannot read confidently becomes `partial`
with a note rather than a guess.

---

## Next

Finish step 6: the eval adapter (6b), then the code adapter (6c). Both are described above.

---

# Beyond phase 3 — the order changed, and why

Recorded 1 Oct 2026. The handoff's build order runs phase 4 (HTTP, a token, Dust as a
client) before phase 5 (the write half). **That is reversed here.** The reasoning is below
so it is not re-argued, and so that a reader who knows the handoff can see where this
departs from it.

## A retraction first

Earlier advice in this project was to decide the prototype's 15 open proposals by hand
before writing any code, on the grounds that PRD C3 item 8 calls the acceptance rate the
top open item ahead of everything else. **Those 15 proposals are not real.** They are
hand-written snapshot content in `Operating Layer.html`, not output from a working
generator over real data. That route to AC-16 does not exist.

The consequence is that the acceptance rate can only be measured after the write half is
built on top of phases 1–3. It makes phase 5 more important, not less, and it is the single
strongest argument for the reordering below.

## Why phase 5 comes before phase 4

The specification already says so. PRD D2: *"Below 50% the proposals are noise and that
must be known **before a UI is built around them**. That is AC-16."*

1. **Phase 4 delivers no capability.** It is a transport change plus a rented conversation
   surface. Verified during step 1: with `mcp` 2.2.0 it is `run(transport="streamable-http")`
   in place of `run(transport="stdio")`. Nothing in the Layer's data model or logic changes.
2. **Phase 4 without phase 5 is the thing the handoff forbids.** Dust over read-only
   findings is a chat window onto a dashboard, and section 15 rules out a read-only
   dashboard explicitly: *"Braintrust and Langfuse can add views in a quarter. The
   write-back is the moat."* Approving proposals is phase 5's output, so the surface has
   nothing to approve until phase 5 exists.
3. **Phase 5 has no external dependencies. Phase 4 has two unresolved ones.** Open
   questions 1 and 2 — whether hosted Dust gates remote MCP servers by plan tier, and what
   auth scheme its credential policy accepts — are outside this project's control and still
   unverified.
4. **Security order.** SEC-7 and audit item P7 (rate limiting) land with HTTP, and B3
   rule 3 states the critic is itself an LLM and itself injectable. Exposing an endpoint
   before the approval boundary is built and tested means the first thing on the public
   internet is a server whose write paths are unfinished.

## Phase 4 becomes a half-day spike, not a deferral

Open questions 1 and 2 cannot be answered by reasoning, only by trying. At the phase-3
boundary: put the server behind HTTP with a throwaway token, register it in hosted Dust as
a remote MCP server, record the plan tier and the accepted auth scheme in this file, and
stop. That de-risks phase 4 without reordering the real work.

## Three things phase 5 needs that the plan does not yet carry

**An actor model, which is not auth.** AC-16 records `decided_by`, and the role rules need
enforcing: a pm decides every proposal kind, an engineer decides `ci_change` and
`eval_case`, an agent never decides anything. That needs identity and attribution — a
`user` table with a role, and an `--as <email>` argument on the decide commands. It does
**not** need sessions or bearer tokens, which stay in phase 4. This is a deliberate partial
reversal of decision D8 (no auth in phases 1–3): attribution moves forward, authentication
does not.

**The critic scores, it never decides.** EarlyEcho's `ingestion/pipeline.py:53` auto-approves
at confidence ≥ 0.7. That must not carry over for `clause_change`, `new_clause` or
`ci_change` proposals. B3 rule 3 is explicit: never auto-approve a spec edit or a CI change
on confidence score alone, because the critic is an LLM and is itself injectable. The
0.7 / 0.5 thresholds survive as routing and display only, never as approval.

**Generators, which neither document specifies.** Proposals have to originate somewhere,
and they are thin over phase 2's findings:

| Finding | Proposal it justifies |
|---|---|
| `drift` | A threshold change **or** a ticket. US-2 forbids both in one proposal |
| `unenforced` | A `ci_change`, opened as a pull request (US-3, US-12) |
| `uncovered` | A `new_clause`, or a `link` where the clause exists but is unbound |
| production failure | An `eval_case` (C1, US-1) |

This is why phase 5 is cheaper than it looks once phases 1–3 exist. The expensive part is
not generating proposals, it is the hard cases that make the write half honest: EC-6 (two
people decide at once, first wins and the second is told by whom), H2 (a human edit to the
spec invalidates an open proposal rather than being merged over), H7 (evidence deleted by
retention marks the proposal evidence-expired rather than showing it as live).

## What AC-16 actually asks of a person

AC-16 means the product owner sitting down and deciding roughly twenty real proposals about
their own products. Two things follow that are easy to miss.

**The band cuts both ways.** Accepting 19 of 20 is ≥ 85%, which B6 reads as a rubber stamp
rather than a success: *"A rubber stamp on changes to the definition of correctness is
worse than no tool, because it launders an unreviewed change as an approved one."*
Accepting 8 of 20 is below 50% and the proposals are noise.

**So it cannot be gamed.** Generating twenty trivially-correct proposals produces a number
above the band, which is a failure, not a pass. The honest procedure is to generate whatever
the findings justify, decide every one, and record the number wherever it lands. A result
outside 50–85% is a finding about the product and must be reported, not tuned away —
EC-5 says exactly this: *"a product failure to report, not to hide"*.

## The revised order

| Stage | What it is | Gate |
|---|---|---|
| Phases 1–3 | As planned above: onboarding, findings, stdio MCP | Steps 1–10 of the table at the top |
| Spike | Half a day on hosted Dust | Open questions 1 and 2 answered in writing |
| **Phase 5** | The write half, plus the actor slice | **AC-16: a real acceptance rate from ~20 decided proposals** |
| Decision gate | The number decides what is worth building next | Below 50%, no surface gets built around proposals |
| **Phase 4** | A surface. **Open: whether it is Dust at all** | See below |
| Phases 6–7 | Unchanged. Phase 6 unblocks AC-6 and the `underspecified` finding kind | |

## The phase 4 question left open deliberately

Whether the surface should be Dust is not yet decided, and should not be decided before
AC-16. Handoff section 17 says the hosted product is *"that same server plus a front end on
it, not a rebuild"*, and the prototype already has a working six-screen vanilla-JS UI plus
the mock in `operating_layer_main/Operating Layer.html`. Dust earns its rent for the durable
agent loop, the scheduled triggers, per-tool approval and the multiplayer surfaces — not for
a screen. If what is wanted is a screen, the Layer's own UI is likely cheaper than the
integration. Open question 13 is the same question asked from the other end.
