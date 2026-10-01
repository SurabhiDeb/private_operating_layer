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
| 4 | Migration 2: `clause`, `clause_identity`, `observation`, `enforcement_fact`, `link`, `entity`, `proposal`, `audit_event` | next |
| 5 | The metric engine, tiers 1 to 3 | not started |
| 6 | Adapters: spec, then eval (run files, then Langfuse), then code | not started |
| 7 | The onboarding state machine and CLI; onboard all three fixtures | not started |
| 8 | Verdicts and the five finding queries; reproduce all three C1 conditions | not started |
| 9 | The stdio MCP server | not started |
| 10 | The agnosticism grep test, the no-causal-language test, the AC matrix | not started |

**Acceptance criteria met:** AC-8, AC-14 (as a mechanism; re-asserted per finding at step 8).
Partial: AC-2 (enumeration only). **Hard cases:** H11.
**Tests:** 53 passing. **Commits:** 6 on `layer-phase-1-3`.

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

**Why a URN and not a URL.** Storing a URL means storing a guess about how that system will
look later: its host, its revision scheme, its path layout. Storing identity and deriving
the URL at read time is what makes adding a source system one resolver with no change to the
query layer. Refs are scoped to an org and never globally unique (H11), so resolution always
happens inside a tenant context.

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

## Next

Step 4, migration 2: `clause`, `clause_identity`, `observation`, `enforcement_fact`, `link`,
`entity`, `proposal` and `audit_event`, with an append-only trigger on `audit_event` and row
level security extended to each.

Two shapes in it are departures from PRD B2 and are the reason this step is worth care.
`clause` carries `unit`, `direction` and `value_high`, because real spec targets include
`3% to 8%`, `under 1 second` and `under £1,200`, none of which `comparator + value` can
hold. `observation` carries `passed` and `total` as well as `value`, because the Wilson
interval needs n and a rate alone cannot supply it — a point the specification's data
contract does not account for.

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
