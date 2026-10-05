# Build progress — the Layer, phases 1 to 3

Branch `layer-phase-1-3`. Specification: `operating_layer_main/PRD-SPEC.md` v3.
Conventions and constraints: `CLAUDE.md`. What the product is: `README.md`.

**Scope.** Phases 1–3 of the handoff's build order: onboarding, the five finding queries,
and the stdio MCP server. That is the whole read half, usable from a terminal with no UI,
no agent and no Dust. What follows phase 3 is **not** the handoff's order any more — see
"Beyond phase 3" at the end of this file.

**How to read the status column.** A step is `done` only when its tests pass and the
result has been quoted, never when the code merely exists. Each finished step below records
what it had to achieve, **a table of every file it added or modified and why**, how it was
built, any defect found on the way, and how it was verified. The file tables are taken from
the commits, not from recollection, so a reader can go from a line here to the code without
searching for it.

| # | Step | Status |
|---|---|---|
| 1 | Branch, venv, pytest, Alembic scaffolding, spec docs committed | **done** |
| 2 | Migration 1: `org`, `product`, `source`, `binding`, RLS, roles, isolation tests | **done** |
| 3 | `refs` registry and resolvers; the `repo` source with revision pinning | **done** |
| 4 | Migration 2: `clause`, `clause_identity`, `observation`, `enforcement_fact`, `link`, `entity`, `proposal`, `audit_event` | **done**; `case_result` and the freshness columns land in step 9 |
| 5 | The metric engine, tiers 1 to 3 | **done**; per-case outcomes added in step 10 |
| 6 | Adapters: spec, then eval (run files, then Langfuse), then code | **done** for the aggregate half; 6b's case rows are step 10 |
| 7 | The onboarding state machine and CLI; onboard all three fixtures | **done**; the backfill's case rows are step 10 |
| 8 | The five finding queries; reproduce all three C1 conditions | **done**; `failing_cases[]` and staleness are steps 10 and 11 |
| 9 | Migration 5: `case_result`, the freshness columns, `harvest_cap` | **done** |
| 10 | The evidence spine through the pipeline: redaction, per-case outcomes, storage, `failing_cases` | **done** |
| 11 | Freshness: a verdict degrades rather than freezing | **done** |
| 12 | The stdio MCP server, to PRD B11 | **done** |
| 13 | The agnosticism grep test, the AC matrix, the user-story tests | not started |

**Why the MCP server is step 12 and not step 9.** PRD B11 is the specification for that
server and one of its tools is `failing_cases`, which cannot be built over data the store
does not hold. Writing the server before the evidence spine means writing that tool twice.

**Coverage, taken from the test markers rather than from prose.** PRD C3 is **39**
criteria, AC-1 to AC-39. This file said 36 until step 11 counted them: AC-37 to AC-39
arrived with the 3 October revision and are recorded in its own Part D3.

| | |
|---|---|
| Acceptance criteria met | AC-1, AC-2, AC-3, AC-4, AC-5, AC-7, AC-8, AC-9, AC-13, AC-14, AC-15, AC-17, AC-18, AC-19, AC-20, AC-21, AC-22, AC-23, AC-24, AC-25, AC-26, AC-27, AC-28, AC-29, AC-30, AC-31, AC-32 |
| Criteria with tests that do **not** yet meet them | AC-33 is proven for storage only, since nothing reads `harvest_cap` until the write half exists. AC-37 and AC-38 have behaviour and tests that no marker connects to them, and AC-39 has no direct test at all — see "What is left" below. AC-23 holds with one limit stated in step 10: a deleted trace is recognised from the window the source declares, not from a fetch that found the body gone, because AC-24 forbids the live call in that path |
| Hard cases | H1, H3, H5, H6, H8, H11, H14, H15, H16 |
| Edge cases | EC-2, EC-4, EC-6, EC-9 |
| User stories | **No test carries the `story` marker**, the one of `pytest.ini`'s five never wired up. PRD A7 holds thirteen stories, US-1 to US-13, and AC-11 wants a test per criterion of each. Step 13's job |

A marker is a weaker claim than it looks, in both directions. It says a criterion has a
test, not that the criterion is met — hence the second row. And an absent marker does not
mean absent behaviour: much of what the stories ask for works, but nothing connects a
story's criterion to the test covering it, so AC-11 cannot be answered by running the suite.

**AC-3, AC-4 and AC-5 are the demo, and they pass.** PRD C2 requires them to do so "with no
UI and no agent, from committed data alone, and against at least two independently onboarded
products" — both reference products are onboarded from their own sources in
`tests/test_findings.py`.

What is still outstanding, and where each piece lands, is the table in "What is left, at
a glance" below. It is kept in one place so the answer does not have to be assembled from
this file's prose.

**Tests:** 457 passing. **Migrations:** 5. **Commits:** 40 on `layer-phase-1-3`, ahead of
`main`, with the last three unpushed: step 12, its record, and a correction to this line. The count is
`git rev-list --count main..HEAD`, not recollection — it said 29 until step 10 and was stale
by four. Step 9 and the first half of step 10 both arrived in `455dcb5`, which is why step
9's file table and part of step 10's name the same commit.

---

## What is left, at a glance

Kept here so the answer to "what is outstanding" is one table rather than a reading of the
whole file. Every row names where the work lands, so nothing in it is a surprise later. The
criteria are PRD C3's, which holds **39** of them, AC-1 to AC-39.

| Criterion | What it needs | Lands in |
|---|---|---|
| AC-6 | **The walk is done and proven** over a six-link chain in step 12; what is left is a source that produces requirements, decisions and tickets, so a real product's chain is complete rather than hand-written | phase 6 |
| AC-10 | A test per hard case, H1 to H16 | step 13 |
| AC-11 | A test per criterion of each user story. **No test carries the `story` marker**, the one of `pytest.ini`'s five never wired up, so this cannot be answered by running the suite today | step 13 |
| AC-12 | The AC matrix itself: every criterion mapped to the test that proves it, and the ones belonging to later phases stated as such rather than reported as failures | step 13 |
| AC-16 | 20 real proposals decided by a person, scoring inside the 50–85% band. Needs the write half and the critic | phase 5 |
| ~~AC-31~~ | Met in step 12. Asserted against the served tool list **and** by grepping the module for the human-only definitions | done |
| ~~AC-32~~ | Met in step 12: every tool returns one of B1's four shapes, no tool takes an `org_id`, and one test drives `layer serve` over a real pipe | done |
| AC-33 | Something that **reads** `harvest_cap`. Stored since step 9 and read by nothing until the write half exists | phase 5 |
| AC-34 | `proposal.rank` and the signals behind it, so a human can disagree with the ordering | phase 5 |
| AC-35 | A harvested case surviving into an eval suite with its provenance | phase 5 |
| AC-36 | `harvested_case_yield` computed over a full eval cycle, as a real number rather than null | phase 5 |
| AC-37 | A marker and a named test. `enforcement_fact` records what CI checks and `scope: undetermined` works — `tests/test_enforcement_adapter.py` covers it — but nothing connects either to this criterion | step 13 |
| AC-38 | A marker and a named test. The band, duration and currency round-trip is covered in `tests/test_spec_adapter.py` and `tests/test_schema_guarantees.py`, and again nothing connects it | step 13 |
| AC-39 | **A test that does not exist.** The Wilson interval at the pinned Z is exercised only through onboarding verdicts; `layer/verdicts/stats.py` is tested nowhere on its own, and "a clause with few cases returns `cannot_confirm` rather than `met` on a favourable point estimate" is asserted by no test directly | step 13 |

Everything else in C3 is met and marked: AC-1 to AC-5, AC-7 to AC-9, AC-13 to AC-15, AC-17
to AC-32. Counted by grepping the specification and the test markers together rather than
from recollection, which is how the 36-versus-39 discrepancy above came to light.

**Four open items, none of them blocking.** Each is written up in full under "Open items
created during the build" below; these are the one-line versions.

1. **The Langfuse transport has never met a live instance.** Fake-tested against a surface
   that was read rather than recalled, so pagination at scale, rate limits and timestamp
   types are unverified. Credentials are in the fixture repository's `.env`; roughly half an
   hour closes it, and it should happen before that path carries a history anyone depends on.
2. **The LLM pass over spec prose is designed for and not built.** A bar stated in a way no
   deterministic pattern reaches yields no clause, silently. The interface is shaped so the
   pass can sit above the deterministic one without moving anything.
3. **AC-25 and AC-26 want the criterion amended, not the code bent.** AC-26 asks for an
   input on every failing row, which a source carrying no text cannot give; AC-25 asks for
   proof over corpora that contain no PII to find. Steps 9 and 10 record what the code does
   instead, and neither criterion has been changed to match.
4. **`layer/db/partitions.py` declares six months and nothing extends them.** Anything
   outside lands in the `DEFAULT` partition, which is correct rather than broken, but
   declaring a new month is DDL by the table owner and there is no maintenance command for
   it. It becomes real the first time a scheduled pull runs unattended, which is the same
   phase as audit item P11's alerting.

---

## Step 1 — Scaffolding. Done.

**What it had to achieve.** A place for the Layer to exist that does not disturb EarlyEcho,
and migrations, because there were none.

**Files.** Commit `189cbb1`, shared with step 2.

| File | | What it holds and why |
|---|---|---|
| `requirements-layer.txt` | added | The Layer's dependencies, kept separate from EarlyEcho's `requirements.txt` so the two products cannot drag each other's versions around |
| `pytest.ini` | added | Test discovery plus five markers — `ac`, `hard_case`, `edge_case`, `story`, `needs_db` — which is what makes AC-10 to AC-12 ("every case has a test") checkable rather than asserted |
| `alembic.ini` | added | Migration config with `sqlalchemy.url` **commented out**, so no connection string sits in version control |
| `layer/db/alembic/env.py` | added | Reads the URL from `settings.migration_url` at runtime and imports `layer.db.models`, so `--autogenerate` can see the metadata |
| `layer/db/alembic/script.py.mako`, `README` | added | Alembic's own scaffolding, unmodified |
| `layer/core/config.py` | added | Pydantic-settings with the `LAYER_` prefix so both products can share one `.env`. Holds **two** database URLs, because migrations must connect as the table owner and the application must not |
| `layer/core/errors.py` | added | The refusal vocabulary. `Refusal` is a return value rather than an exception, because PRD B1 requires every response to be one of four shapes and "the Layer never silently returns nothing". `TenantContextMissing`, `UnknownProduct` (B3 rule 9) and `NotLive` (B3 rule 8) name the three ways a caller can be wrong |
| `layer/{__init__,core,db,refs,adapters,metrics,onboarding,verdicts,findings,mcp}/__init__.py` | added | Package skeleton, one directory per concern so later steps have a settled home |
| `.gitignore` | modified | Added `.venv/` and `.pytest_cache/`; it already covered `.env` |
| `operating_layer_main/{HANDOFF,PRD-SPEC}.md`, `Operating Layer.html` | added | The specification, previously untracked in a repository it is the source of truth for |

**Also done outside the file tree.** Branch `layer-phase-1-3` cut from `main`. A `.venv` on
Python 3.13.5. `.env` written and gitignored, `.env.example` committed so the setup is
reproducible — the explorer found neither existed.

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

Plain version, point by point.

The open question. Handoff item 4 said the MCP software library had been reorganised and that no one should write code from memory about it. That was a warning, not a fact.

What they did instead of guessing. Installed the real library and asked it to describe itself. inspect.signature is a Python command meaning "tell me what arguments this function actually takes". Reading the library rather than recalling it.

What it turned out to be.

Thing	What it is now
The main class	MCPServer, not FastMCP
Where it lives	mcp.server.mcpserver
How you add a tool	A decorator, @server.tool(), written above the function
How you choose the connection	An argument to run(). "stdio" now, "streamable-http" later

Why it mattered. Code written from memory would have tried to import something that no longer exists and failed on the first line. Ten minutes of checking saved a confusing failure.

The bonus finding, which is the more valuable half. I claimed phase 4 was "one argument, not a rewrite". That was reasoning. This proves it, because the connection type really is just a parameter on run(). So moving from a terminal to Dust changes one word.

Want me to mark handoff item 4 resolved with these specifics, the way we did item 1?
---

## Step 2 — Migration 1 and provable tenant isolation. Done.

**What it had to achieve.** AC-8: a cross-tenant read returns zero rows, proven by test.
And the three tables v3 added, without which nothing describes how a product enters.

**Files.** Commit `189cbb1`.

| File | | What it holds and why |
|---|---|---|
| `layer/core/db.py` | added | `org_session(org_id)`, the only door to tenant data, plus `unscoped_session` for the tenant registry and `current_org` for assertions. This file is the reason no query in the Layer filters `org_id` by hand |
| `layer/db/models.py` | added | `Org`, `Product`, `Source`, `Binding`. CHECK constraints generated from Python tuples so the vocabulary lives in one place, and deliberately **no** CHECK on `pattern` or `source.kind`, which must stay open |
| `layer/db/rls.py` | added | Emits the `ENABLE`/`FORCE`/`CREATE POLICY` and `GRANT` statements as lists rather than executing them, so migrations stay declarative and the same statements can be asserted in a test. Reads the app role out of its own connection URL rather than hardcoding it |
| `layer/db/alembic/versions/e527dd6eadd6_*.py` | added | Migration 1. Autogenerated, then hand-edited to append the policies and grants autogenerate cannot see — in the **same** migration as the tables, because a window where tenant tables exist without a policy is a window where a leak is legal |
| `tests/conftest.py` | added | A module-level admin engine, `TRUNCATE org CASCADE` between tests, and a session-scoped check that fails loudly naming `alembic upgrade head` rather than erroring once per test |
| `tests/test_isolation.py` | added | Seven tests for AC-8 and H11. Not one assertion filters `org_id` by hand — that is the thing being proven |

**Why it comes first.** The inherited codebase demonstrates the failure mode it prevents.
EarlyEcho filters `org_id` by hand in roughly sixty places and omits it in six;
`api/routes/ingest.py:156` and `:178` look a `PendingEntity` up by `id` alone and then copy
that row's `org_id` forward, so anyone who guesses an id can approve another tenant's item.
The fix is not more diligence. It is making the filter unnecessary.

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

**Files.** Commit `fe00155`.

| File | | What it holds and why |
|---|---|---|
| `layer/refs/ref.py` | added | The `Ref` value type, `parse` and `parse_all`. Splits on the **first** colon only, because ids legitimately contain colons and splitting on the last would silently rewrite them |
| `layer/refs/registry.py` | added | Kind-to-resolver mapping, `links()` returning `(evidence_links, unresolved)` in the finding's own shape, and `is_pinned` as the central AC-14 guard. Instantiated per unit of work, never as a module global |
| `layer/adapters/repo.py` | added | `RepoHandle`: `pin()` stores only a resolved sha, `read()` goes through `git show <rev>:<path>`, `list_files()` filters with `PurePath.full_match`, and `repo_resolvers()` supplies the `file:` and `code_change:` resolvers |
| `tests/test_refs.py` | added | 23 tests: parsing, the first-colon rule, malformed refs, unknown kinds, order and duplicate preservation, and the branch-URL guard |
| `tests/test_repo_source.py` | added | Pinning, refusal on a moving revision, reading at the commit rather than the working tree, URL templating, and the resolvers — against a throwaway repository built in the test |
| `CLAUDE.md` | modified | Recorded the two gotchas this step paid for: `ls-tree` does not glob, and a run directory contains files that are not runs |

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

> **Two things here are superseded by step 9, which a reader of this section needs.** The
> `source.status` this migration writes as `bound` is no longer a legal value: it is a CHECKed
> set now and migration 5 maps it to `healthy`. And the eight tables below are nine, because
> `case_result` sits beneath `observation`.

**What it had to achieve.** The eight tables the read half reasons over, with isolation and
append-only enforcement landing alongside them rather than after.

**Files.** Commit `b46bf08`.

| File | | What it holds and why |
|---|---|---|
| `layer/db/models.py` | modified | Added `Clause`, `ClauseIdentity`, `Observation`, `EnforcementFact`, `Entity`, `Link`, `Proposal`, `AuditEvent`, and the vocabularies they validate against. Replaced the three-table `TENANT_TABLES` with the full eleven |
| `layer/db/rls.py` | modified | Added `append_only_statements` / `drop_append_only_statements` — two triggers, because the row-level one cannot see TRUNCATE. **Removed the default `tables` argument from every function**, which is what the downgrade bug below required |
| `layer/db/alembic/versions/e56fae9886ab_*.py` | added | Migration 2. Autogenerated, then hand-edited for the policies, grants and triggers, plus an `import pgvector.sqlalchemy` autogenerate omits while still emitting `pgvector.sqlalchemy.vector.VECTOR(...)` |
| `layer/db/alembic/versions/e527dd6eadd6_*.py` | modified | Migration 1 now names its own three tables instead of reading the live constant |
| `tests/conftest.py` | modified | Truncation goes through the `app.erasure` hatch, so the harness does not enjoy a privilege production lacks; the schema check reads `TENANT_TABLES` |
| `tests/test_schema_guarantees.py` | added | 24 tests for what the schema *refuses*: duplicate observations including both NULL cases, an incoherent sample, independent state and verdict, a one-ended band, coexisting versions, an unapproved proposal, an audit row that cannot be edited |

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

> **The engine also emits a case per row, which step 10 covers and this section does not.**
> `rate`, `accuracy`, `recall` and `precision` each return a `CaseOutcome` per case alongside
> the number; the tiers, the refusals and the fixture numbers below are unaffected. The table
> headed "Where the engine declines" is complete for the number and silent on the cases.

**What it had to achieve.** Turn a document the Layer has never seen into a measured
number, for any product, without learning anything about that product.

**Files.** Commit `cb9a8f3`.

| File | | What it holds and why |
|---|---|---|
| `layer/metrics/pointer.py` | added | RFC 6901 JSON Pointer resolution, and `resolve_field` for a plain key or a pointer. Returns a `MISSING` sentinel rather than None, because "absent" and "present but null" lead to different findings |
| `layer/metrics/predicates.py` | added | The predicate tree evaluator over a closed operator set, plus `to_bool` (interprets an encoded boolean, refuses anything else) and `truthy` (is there content). Nothing here evaluates a string, because a definition arrives in tenant-supplied config |
| `layer/metrics/engine.py` | added | `compute()` and the nine aggregations, `MetricValue`, `Unmeasurable`, and `supports()` so onboarding can ask before confirming a binding. Tier 3 — the refusal — lives here |
| `layer/metrics/__init__.py` | modified | The package's public surface, so callers import from `layer.metrics` rather than reaching into modules |
| `tests/test_metrics.py` | added | 56 tests, including the five places the engine declines instead of returning a number, and the fixture checks that reproduce 94.0% and 90.91% |

**Why this is the crux.** Neither specification says how a number is obtained, and the
fixtures show why that gap matters: their committed run files contain **no metric values at
all**. Every headline number is derived by iterating per-case rows, and one product's come
from a module carrying a hardcoded article blacklist and rank semantics. A Layer that
computed those itself would be reimplementing each customer's scorer, which is building the
eval half — the thing handoff section 4 forbids outright.

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

## Step 6 — The adapters. Done.

Three adapters, built and verified in order. All three done.

### 6a — The spec adapter. Done.

**What it had to achieve.** Read a specification document into clause candidates, for a
document the Layer has never seen, with everything product-specific in configuration.

**Files.** Commit `5514fe7`.

| File | | What it holds and why |
|---|---|---|
| `layer/adapters/base.py` | added | What every adapter emits — `ClauseCandidate`, `ObservationCandidate`, `EnforcementCandidate` — and `ImportReport`, whose arithmetic must balance. The report is the part AC-2 rests on |
| `layer/adapters/parse/markdown.py` | added | Sections, pipe tables and list items with line numbers carried throughout, so a citation resolves to the lines that stated a promise. Tables are found by the divider row, not by pipes; an unnumbered subsection inherits its numbered ancestor's ref |
| `layer/adapters/spec/values.py` | added | `parse_target`, which turns `85%`, `3% to 8%`, `under £1,200` and `0.85` into a comparator, value, value_high, unit and direction — and records any assumption it made. `normalise_metric_name` meets the eval source halfway |
| `layer/adapters/spec/file_spec.py` | added | `SpecAdapter` and `SpecConfig`. Everything product-specific is in the config: which columns carry the metric, target and rationale, how section titles map to clause kinds, which sections to ignore |
| `layer/adapters/{parse,spec}/__init__.py` | added | Package markers |
| `tests/fixtures/alien_spec.md` | added | The third fixture, written for this test in a deliberately unfamiliar style. It found four defects the two reference specs could not |
| `tests/test_spec_adapter.py` | added | 40 tests, of which the `AC-21` class is the agnosticism test |

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

### 6b — The eval adapter. Done.

> **This adapter is not finished.** Handoff §12 phase 1 requires a backfill to write
> `observation` rows **and** the `case_result` rows beneath them, because a per-run aggregate
> cannot prove a drift finding. Lifting case ids, inputs and trace pointers is step 10 and is
> not built yet. Everything below — the reconciliation, the classification distinction, both
> reference conditions — holds for the aggregate half.

**What it had to achieve.** Turn committed run records into observations, and earn AC-2:
"no duplicates and **no run omitted**, proven by counting source runs against stored
observations".

**Files.** Commit `248a5dd`.

| File | | What it holds and why |
|---|---|---|
| `layer/adapters/eval/run_files.py` | added | `RunFilesAdapter` and `Reader`. Classifies each document as a run, a non-run or a failure; joins sidecars to the run they name; delegates measurement to the metric engine. The reconciliation that proves AC-2 comes out of here |
| `layer/adapters/eval/langfuse.py` | added | `LangfuseSource`, which normalises a dataset run into the same `{meta, results}` document a run file has and contains **no** measurement code. The client is injected so the one unverified seam is also the one that is testable |
| `layer/adapters/base.py` | modified | Added `SourceDocument` (identifier, content, caller-supplied URL) and `ImportReport.unmeasured`, deliberately outside the document arithmetic |
| `layer/adapters/eval/__init__.py` | added | Package marker |
| `tests/test_eval_adapter.py` | added | 34 tests: the classification distinction, provenance, sidecars, metric-level outcomes, and the fixture checks that reproduce both reference conditions |

**Two departures from PRD B2, both load-bearing.**

*Observations are keyed by metric, not by clause.* Every candidate carries
`clause_ref = None`; the clause-to-metric relationship lives in `binding`. B2 puts
`clause_ref` on the observation, which taken literally means binding a metric to a second
clause requires rewriting history, and H5 — one metric legitimately serving two clauses —
would double every stored row. With the relationship in `binding`, H5 costs nothing and
the idempotency key collapses to `(org, product, metric, run_url)`, which is what it
should have been. The column stays for a source that names a clause itself, such as a
production metric mapped directly.

*A file that is not a run is not a failure.* `not_a_run_record` means the document was
never a run of this kind; a failure means one was and did not import. **Collapsing these is
exactly how AC-2's reconciliation comes to compare 47 against 47 and look correct while a
run is missing.** Everything else in this adapter follows from keeping them apart.

**The reconciliation itself.** Against the committed history: 48 files enumerated, 47
imported, one classified `not_a_run_record`, zero failed, arithmetic balanced. Reading the
same documents twice produces identical candidates, which is the precondition for the
database refusing the duplicate — the constraint can only work if the adapter presents the
same identity each time.

**Both reference conditions reproduce, every figure checkable by hand.**

| | Reproduced | Source of truth |
|---|---|---|
| Condition 1 | `escalation_recall` breaches 99% in **7 of 47** runs, worst **0.800 (4/5)** in run `20260915-133223Z-v2`, missing case `14`, while the newest run is at 1.0 | The prototype's drift record, arrived at independently |
| Condition 2 | `status_ok` 0.74 → 0.84 → 0.88 → 0.88 → 0.92 → 0.92 → 0.94 while `critical_pass_rate` peaks at **0.9091** and never clears 100% | Every row matches the handoff's PolicyDesk table |
| Condition 3 | Runs sharing a recorded `prompt_sha` and `corpus_sha` while disagreeing on score are detectable from stored provenance | H1 — and nothing attempts to say why |

The last row of condition 1 is the whole product in one line: the latest run is clean, so
anything reading only the newest file sees nothing wrong.

**Other behaviour worth recording.**

- A **sidecar** carrying no clock of its own — a judge's verdict written beside a run —
  inherits the time of the run it names. One with no primary is a *failure* rather than
  being dated now, because guessing a date fabricates history.
- Primaries are read before sidecars regardless of arrival order, so the result does not
  depend on how a filesystem happened to list the files.
- The **more specific reader wins**: `*-groundedness.json` also matches `*.json`, and the
  broader pattern winning would read a verdict as a run.
- A run with **no usable timestamp is not stored**. An observation with no time cannot sit
  in a series, and dating it `now` would put a two-week-old run at the end of the history
  and invent a trend.
- One **uncomputable metric does not lose the run**. It is recorded in `report.unmeasured`,
  deliberately outside the document arithmetic, so onboarding can say "this metric was
  unmeasurable in 47 of 47 runs" instead of leaving a clause quietly unmeasured. That list
  is the input to the `uncovered / no_metric` finding.
- A run shaped correctly that measures **nothing at all** is a failure, not a silent
  import, since otherwise the reconciliation counts a run that contributed no history.
- A nested revision such as `{"commit": "...", "dirty": true}` is flattened to
  `abc1234-dirty` and kept as provenance only — it is never used to build a citation.

**Langfuse, as a second transport with no measurement code.** It normalises a dataset run
into the same `{meta, results}` document a run file already has and hands it to the same
adapter, so a metric definition written for one works for the other unchanged. One engine,
two transports.

The API surface was **read from langfuse 4.15.4, not recalled** — the same discipline that
caught the MCP rename. `Langfuse().api` is a `LangfuseAPI` exposing
`datasets.get_runs(dataset_name, page, limit)`, `datasets.get_run(dataset_name, run_name)`
returning items with `trace_id`, and `scores.get_many(..., dataset_run_id=...)` over a
five-type discriminated union of numeric, categorical, boolean, correction and text scores.

**What is not verified, and why that is confined.** No live Langfuse instance was reached,
so pagination at scale, rate limits and exact timestamp types are untested against a real
project. The client is injected for that reason: the normalisation is testable without a
network, and the unverified risk sits in one seam. Pagination stops on a short page rather
than on a reported total, because a total that disagrees with the data would either loop
forever or truncate history, and AC-2 forbids omitting a run. Credentials exist in the
fixture repository's `.env`, so this is verifiable whenever the user wants it — recorded as
an open item below.

A test caught `langfuse:dataset/run` matching no glob, because `*` does not cross `/` and
the identifier mixed separators. Identifiers are path-shaped throughout now, so one
matching rule covers every source; otherwise two sources would disagree about what a glob
means.

**Verification.**

```
$ .venv/bin/python -m pytest
...............................................................          [100%]
207 passed in 8.85s
```

### 6c — The code adapter. Done.

**What it had to achieve.** Record what CI actually checks, as distinct from what the
specification says, including the run set it checks it against.

**Files.** Commit `ddbe8ab`.

| File | | What it holds and why |
|---|---|---|
| `layer/adapters/code/enforcement.py` | added | `EnforcementAdapter`: the name patterns, the narrowing and iteration selectors that decide `scope`, the toothless-check detection, and the two-step threshold reading that refuses to guess |
| `layer/db/models.py` | modified | `ENFORCEMENT_SCOPES` gains `undetermined`, with the reason recorded beside it |
| `layer/db/alembic/versions/a1c3e7f90b22_*.py` | added | Migration 3, a CHECK change. Its downgrade deletes `undetermined` rows rather than inventing an enforcement claim for them |
| `layer/adapters/base.py` | modified | Added `Expectation` — the stated bar the scan goes looking for, which is what makes this a search rather than a code-comprehension problem |
| `layer/adapters/code/__init__.py` | added | Package marker |
| `tests/test_enforcement_adapter.py` | added | 35 tests, mostly about the scan declining to claim things: scope, threshold reading, naming, toothless checks, and the real gate |

**It searches for stated bars; it does not comprehend code.** The scan is given the
product's threshold clauses and asks, for each, whether a CI file compares that number to
something bearing that name. Understanding an arbitrary gate script in general is a research
problem; answering "is 0.85 compared against something called team accuracy" is a search,
and a search is what generalises to a repository nobody has seen.

**Migration 3 adds a fourth scope value, `undetermined`.** It earns its place the same way
`cannot_confirm` does for a verdict. A scan that cannot tell which run set a gate reads must
not pick a side: `all_runs` asserts full coverage and hides exactly the gap condition 1 is
about, while `latest_only` manufactures a finding that may not exist. The downgrade deletes
`undetermined` rows rather than guessing values for them, since inventing an enforcement
claim on the way down is worse than losing the row.

**Against the committed gate.**

| | Result |
|---|---|
| `team_accuracy` | `enforced: true`, `threshold: 0.85`, **`scope: latest_only`**, noting that a breach in any earlier run is never seen — H14 read out of real code |
| `escalation_recall` | Found only by alias; value reported unread, which is accurate — the gate requires zero missed escalations and never writes a rate down |
| `escalation_precision`, `needs_clarification_rate`, `p95_latency` | Stated in the specification, absent from CI. This is AC-4 |
| `.github/workflows/ci.yml` | Skipped as `no_threshold_found`. It runs the gate rather than checking anything, and mistaking that for enforcement would be a false positive on every repository |

### Three times the scan claimed more than it knew

Each was caught by a test, and each fix narrows what the adapter is willing to assert.

**Aliases were necessary, not a convenience.** The gate enforces escalation recall by
counting `missed` escalations and requiring zero, never writing the words "escalation
recall" anywhere. Without an alias the clause was reported as unenforced — not a
conservative error but a **wrong finding**, which B6 puts at zero tolerance.
`metric_aliases` lives in `source.config` and is confirmed by the same human who confirms a
binding, because it is the same kind of assertion: this number is that promise.

**The threshold fallback was guessing.** It fell back to "the first ratio between 0 and 1",
so a metric whose gate happened to sit beside a sampling constant would be reported as
enforced at 0.5. A fabricated threshold is worse than an unread one because it reads as
knowledge. A number is now read from the same line as the metric's name, or confirmed in the
neighbourhood only when it is the stated value, and otherwise reported as unread.

**A bar of 0, 1 or 100 cannot be confirmed by a bare digit, even on the same line.**
`broken = sum(1 for r in results if r["problem"])` names the metric and contains a 1;
reading that as a threshold reported the gate as enforcing contract validity at 1.0 when the
digit was a loop accumulator. For those values the number must sit where a threshold sits —
beside a comparator, or alone on the right of an assignment.

**Other behaviour worth recording.**

- Narrowing beats iteration. A file may glob every run and then index the last one, which is
  precisely the shape that hides a mid-sequence breach.
- A narrowing token on something that is not a run collection says nothing about scope, so
  `name[-1]` does not make a gate `latest_only`.
- A check that runs and **cannot fail a build** — `continue-on-error`, `|| true`, `set +e`,
  `xfail`, a disabled check — is recorded as `enforced: false` with `known_failing`, because
  it looks enforced on every dashboard and is not.
- A metric no file mentions yields **no fact at all**. Absence is the finding query's job; a
  row saying `enforced: false` would duplicate it and overclaim, since the scan knows it
  found nothing rather than that nothing exists.
- No expectations supplied is reported as "enforcement was not assessed", explicitly not as
  "CI enforces nothing".

**Verification.**

```
$ .venv/bin/python -m pytest
........................................................................ [ 89%]
..........................                                               [100%]
242 passed in 9.69s
```

---

## Step 7 — Onboarding. Done.

> **Two parts of the run below are incomplete.** The `141 observations stored` line is half
> the arithmetic a finished backfill reports, because `persist.py` does not write `case_result`
> rows yet (step 10). And the verdict counts will move at step 11, when a verdict resting on a
> measurement outside its source's `freshness_window` degrades to `cannot_confirm` instead of
> holding.
>
> `bind_source` also takes a `freshness_window` now, which is PRD B11's signature for it, and
> `source.status` defaults to `healthy`. The seven steps, the gates and the five defects are
> as recorded.

**What it had to achieve.** Make onboarding the only way content enters, with each of PRD
B2's seven steps gated by the one before it, and a product reaching `live` with no code
change to the Layer.

**Files.** Commits `5812046` and `6c6e9ed`.

| File | | What it holds and why |
|---|---|---|
| `layer/verdicts/stats.py` | added | The Wilson score interval at `Z_95 = 1.959963984540054`. Reimplemented rather than imported, because a fixture may never be a dependency, and the constant is the fixtures' own so a number the Layer reports and one the product reports agree to the last digit |
| `layer/verdicts/rules.py` | added | `judge()` and `state_for()`. The verdict is read from the most recent observation, because "right now" is what it means; whether a bar was ever missed is the drift finding's question |
| `layer/core/audit.py` | added | The only writer to `audit_event`. Takes the caller's session so the event and the change it describes commit or roll back together — a log that survives a failed transaction is a log of things that did not happen |
| `layer/onboarding/identity.py` | added | `resolve()`: metric name, then recorded identity key, then statement similarity. Deterministic via difflib rather than embeddings, so AC-1 holds without a model provider being reachable |
| `layer/onboarding/persist.py` | added | The three writers. Clauses are versioned, observations are upserted and never updated, enforcement facts are replaced per source because what CI checks is a current fact and not a history |
| `layer/onboarding/bindings.py` | added | Step 5. Proposes pairings and **stops**; `decide()` validates a human's decision and records it either way |
| `layer/onboarding/state.py` | added | The seven steps, the status transitions, and `require_live()` — the single function every proposal path calls, so B3 rule 8 is structural rather than remembered |
| `layer/onboarding/run.py` | added | Wires adapters to the state machine, so the state machine depends on no adapter and an adapter depends on no database |
| `layer/cli.py`, `layer/__main__.py` | added | `python -m layer`. Every command takes `--org` and `--as`; there is deliberately no flag that skips the gate |
| `layer/db/models.py` | modified | `Binding` gains `decision` and `decision_note` |
| `layer/db/alembic/versions/b7d2f4a16c38_*.py` | added | Migration 4. Its downgrade deletes rejections rather than keeping them, since a rejection without the column becomes a confirmation nobody made |
| `layer/core/db.py` | modified | Autoflush turned on, with the reason recorded — see the stale reads below |
| `tests/fixtures/alien_runs/*.json`, `alien_gate.py` | added | The third fixture gains a run format and a gate idiom neither reference product uses, so AC-21 covers all three source roles rather than only the spec |
| `tests/test_onboarding.py` | added | 27 tests: the step gates, identity, the binding gate, verdicts, the audit trail, and the three agnosticism criteria |
| `tests/test_cli.py` | added | 21 tests over the operator surface — mostly asserting output, because for phases 1 and 2 the output is the product |

**The seven steps, running against a real product.**

```
registering -> sources_bound -> assertions_confirmed -> live

spec        : 51 new, 0 reworded, 0 unchanged
spec again  : 0 new, 0 reworded, 51 unchanged
backfill    : 141 observations stored, 0 duplicates refused, 47 of 48 documents imported
backfill x2 : 0 observations stored, 141 duplicates refused, 47 of 48 documents imported
scan        : 3 enforcement fact(s); 7 stated metric(s) not checked anywhere
measure     : not_applicable 41, cannot_confirm 3, not_measured 7
```

The second line of each pair is the point. A re-import writes nothing and a repeated backfill
refuses every row, so AC-1 and AC-2 are demonstrated by a number rather than by the absence
of a change.

**`cannot_confirm` for the three bound clauses is correct, not a shortfall.** 47 of 50 team
accuracy against a bar of 85% gives an interval of 0.838 to 0.979, which does not sit wholly
on either side. PRD B2 expects this to dominate and calls it "the honest state rather than a
defect. A system that reports met or missed for everything is guessing."

### Five defects, and what each would have cost

**Two distinct clauses were merged into one ref.** "p50 latency under 1 second" and "p95
latency under 3 seconds" score **0.906** on text similarity, so the p95 clause was stored as
*version 2 of the p50 clause* and the p50 promise silently disappeared. A differing metric
name now vetoes a similarity match outright, and resolution excludes rows the same import
just wrote. This is the failure PRD B2 means by "identity is the load-bearing requirement":
text similarity is a weak signal and must never outrank an explicit disagreement.

**A product whose bars are all in prose could never leave the gate.** Such clauses carry no
metric name — the spec adapter refuses to invent one — so nothing name-matched and nothing
could be confirmed. `decide` now validates the *components* of a pairing rather than
requiring the pair to have been pre-computed, because pairing a measured number with a
promise the source never named is the main thing step 5 is for. Found only because the third
fixture states its bars in prose; both reference products use tables.

**A bound metric and its clause could disagree on unit.** A p95 measured in milliseconds
against a bar stated in seconds compares 1200 to 4 and reports a confident breach that is
purely a unit error — a wrong finding, which B6 puts at zero tolerance. Refused at the gate
rather than converted: guessing which side is authoritative is how a bar gets quietly
rescaled.

**The enforcement scan could not look for a prose clause's bar**, having no metric name to
search for. The confirmed binding is the bridge — it is a human saying which measured number
answers that promise, which is also the name the gate is likely to use — and a clause with no
bound metric is now reported as not looked for rather than passed over in silence.

**Two stale reads that both looked like logic bugs.** The session ran with `autoflush=False`,
inherited from EarlyEcho's pattern. Onboarding writes and then reads back within one
transaction: it records a binding and then asks whether any remain undecided, and it computes
verdicts by mutating clause rows and then counts them. Both reads returned the pre-write
rows, so a product sat at the gate with nothing left to decide, and every clause reported the
verdict it was created with. Autoflush is on, which is also SQLAlchemy's default.

**Verification.**

```
$ .venv/bin/python -m pytest
..                                                                       [100%]
290 passed in 18.69s
```

Three products onboard to `live` — the two reference ones and the alien fixture, whose spec
states bars in prose, whose runs use a format neither reference product uses, and whose gate
is written in a different idiom. Nothing about any of them appears anywhere in `layer/`.

---

## Step 8 — The five findings. Done.

> **Read this before the condition 1 output below.** The summary quoted there ends
> `Failing case ids across these runs: 14.` Those ids come from
> `observation.detail["missed_ids"]`, a JSONB blob holding ids and nothing else — no outcome,
> no trace pointer, nothing a citation resolves to. AC-22 requires a drift finding to carry
> `failing_cases[]` of `{case_id, outcome, run_url, trace_url, trace_available}`, each entry
> resolving to a stored `case_result`. **So condition 1 reproduces, and not yet to
> specification.** `failing_cases[]` is step 10.
>
> Two more things below are provisional. Findings carry no `as_of` or `stale`, and the global
> `STALE_AFTER = timedelta(days=30)` this step introduced retires at step 11 in favour of the
> per-source `freshness_window`, so the `uncovered / not_measured_recently` behaviour described
> here will change. The five queries, the drift-versus-verdict distinction, the two refusals
> and the no-causal-language test are as recorded.

**What it had to achieve.** Turn the stored record into the conditions a reader cares about,
with citations that open, and no sentence that states a cause. This is the milestone the whole
build is pointed at.

**Files.** Commit `3cb5d6f`.

| File | | What it holds and why |
|---|---|---|
| `layer/findings/shapes.py` | added | `Finding` exactly as PRD B1 defines it, and `FindingSet`, which carries either findings or a refusal. A refusal rather than an empty list, because an empty list from a query that cannot run reads as "all clear" — the most expensive wrong answer available |
| `layer/findings/citations.py` | added | The database-backed resolvers: an observation resolves to the run it came from, a clause to the **lines** that stated it. Built per product and per request, never shared, because refs are not globally unique (H11) |
| `layer/findings/queries.py` | added | The five queries, the summaries, and `find_all`. No metric name and no product name appears in any of them |
| `layer/cli.py` | modified | A `findings` command, with `--kind` and `--citations`. A refusal prints as prominently as a finding, because an empty section would read as "nothing to see here" |
| `tests/test_findings.py` | added | 22 tests. Both reference products onboarded from their own sources, with every number asserted against what can be counted by hand in the committed files |

### Condition 1, reproduced from committed data

Given a product and nothing else — no metric, no run, no hint:

```
TRI-11.2 Escalation recall >= 99% was missed in 7 of 47 runs, worst 80% (4 of 5) in run
20260915-133223Z-v2. The latest run, 20260915-150650Z-v2, is at 100%, so a check on the
latest run alone shows nothing. Failing case ids across these runs: 14. Breaching runs
record ca6d836-dirty. Runs at 98380d1, baf3df2-dirty, f05a88a-dirty do not breach.
```

Alongside it, from the enforcement scan:

```
TRI-11.1 Overall team accuracy states >= 85% and is checked in products/triage/gate.py,
but only against the newest run. A breach in any earlier run of the same prompt is never
seen.
```

Every citation in both resolves to a pinned commit, and a clause citation lands on the line
of `SPEC.md` that stated the bar rather than on the file.

The final sentence of the drift summary is the one that does the work. It states what a
narrower check can see, which is a fact about the check rather than a claim about the product.

### Condition 2, and a structural finding nobody arranged for

`critical_pass_rate` misses its 100% bar in **7 of 7** runs, worst 0.4545, and still holds,
while `status_ok` climbs 0.74 to 0.94. Both are reported: a reader sees that the metric
improving is not the metric that matters.

The policy product then demonstrated **H16** without the fixture being arranged to. Its
specification states a bar for the critical subset and **none at all for the headline**, so
`status_ok` is reported as a measurement no clause promises — where, as H16 puts it, "the gap
is the absent clause, not the metric". It also means that product cannot leave the binding
gate on name matching alone: a human has to pair `critical_pass_rate` with the clause that
names the subset differently, which is exactly what step 5 is for.

### Drift compares the number; the verdict compares the interval

The distinction that matters most here, and it is deliberate.

| | Question | Answer for TRI-11.2 |
|---|---|---|
| Drift | Did a run come in under the bar? | Yes, in 7 of 47 |
| Verdict | Can we say the product meets its bar right now? | `cannot_confirm` — seven of seven cannot establish a 99% rate |

Both are true. B6 sets drift detection at 100% precisely because it is deterministic — a
number was written down — while a verdict is a claim about evidence. Collapsing them would
lose one of them, and which one got lost would depend on the day.

### Two things the tests forced

**Resolution is no longer the caller's job.** Only `find_all` resolved citations, so a query
used on its own returned findings whose `evidence_links` were empty *and whose `unresolved`
was empty too* — which reads as "every citation resolved" when none had been tried. That is
AC-7 and AC-14 broken by omission rather than by a wrong URL, which is the harder kind to
notice. Every query now resolves before returning, and `find_all` shares one registry because
building it opens the repository.

**A rejected pairing is not a coverage gap.** A human looked and said this number does not
answer that promise; surfacing it as `uncovered` would re-open a closed question and make
review a treadmill.

### What refuses, and why that is the right answer

`find_stalled_decisions` and `find_underspecified` cannot run in this phase and say so by
name, each listing the source it lacks. H8 — the eval passes while production sits outside its
band — is the highest-value finding in the specification, and answering it empty would be a
claim about production readings the Layer has never seen.

**Verification.**

```
$ .venv/bin/python -m pytest
........................                                                 [100%]
312 passed in 40.73s
```

The no-causal-language test greps every generated summary and refusal across both products
for twelve causal constructions — `because`, `caused`, `due to`, `led to`, `resulted in`,
`attributable` among them — and requires none. It asserts against generated text rather than
intent, because B3 rule 6 is about what a reader is told.

---

## Step 9 — Migration 5: the evidence spine and the freshness columns. Done.

**What it had to achieve.** Somewhere to put the proof, and somewhere to record how old the
proof is, with isolation, immutability and partitioning arriving alongside the table rather
than after it.

**Files.** Commit `455dcb5`.

| File | | What it holds and why |
|---|---|---|
| `layer/db/models.py` | modified | `CaseResult`; `SOURCE_STATUSES` and `CASE_OUTCOMES`; `Source.freshness_window`, `overdue_since` and a CHECKed `status`; `Product.harvest_cap`. `TENANT_TABLES` grows to 12 |
| `layer/db/partitions.py` | added | The partition statements, emitted rather than executed, for the same reason `rls.py` emits rather than executes: a migration stays declarative and the same statements can be asserted in a test |
| `layer/db/alembic/versions/c4e8b1a07f55_*.py` | added | Migration 5. Hand-written DDL rather than `op.create_table`, because the partitioning clause, the two partition levels and the composite key have to arrive together |
| `layer/db/rls.py` | modified | `case_result` joins `audit_event` in `APPEND_ONLY_TABLES`, and `drop_append_only_statements` gains a required `drop_function` argument — see the defect below |
| `layer/db/alembic/versions/e56fae9886ab_*.py` | modified | Migration 2 now says `drop_function=True`, because it is the migration that created the function |
| `layer/core/durations.py` | added | `30d` to a `timedelta` and back. A window is typed by a human at onboarding, so it has to be writable on a command line |
| `layer/core/errors.py` | modified | `Unreadable`, so an operator's bad input reaches them as `refused:` rather than as a traceback, while a `ValueError` escaping stays what it should be — a defect |
| `layer/onboarding/state.py` | modified | `bind_source` takes `freshness_window`, which is PRD B11's signature for it, and records it in the audit detail |
| `layer/cli.py` | modified | `source bind --freshness`, and it prints the consequence either way |
| `tests/test_schema_guarantees.py` | modified | 19 tests: 11 on `case_result` (AC-26, AC-27, AC-22's immutability, AC-23's storage), 6 on the freshness CHECKs, 2 on `harvest_cap`, plus `case_result` added to the cross-tenant sweep |
| `tests/test_cli.py` | modified | Three tests over the operator surface of a freshness window |

**Why `case_result` is partitioned the way it is.** AC-27 asks for partitioning by `org_id`
**and** month. `LIST (org_id)` at the top would give the cheapest possible erasure — one
`DROP TABLE` per tenant — but it needs DDL every time a tenant is registered, and the
application role deliberately does not own these tables, so registering a tenant would
become a migration. Monthly `RANGE` partitions, each `HASH` split on `org_id`, keep
registration pure DML, keep a tenant delete to one statement, and still allow an expired
month to be dropped whole.

**The DEFAULT partition is not laziness.** The application role cannot create a partition,
so a run whose `measured_at` falls outside every declared month has nowhere to go and
Postgres rejects the insert. Losing a run because a maintenance job did not run is worse
than a partition holding mixed months, and AC-2 forbids omitting a run.

**What the numbers cost, measured rather than assumed.** The first version declared 24
months at a hash modulus of 4: **126 relations**, and `TRUNCATE org CASCADE` — which the
harness runs after every test — went from roughly 20ms to **125ms**, about 40 seconds
across the suite. Six months at a modulus of 2 is 22 relations and 36ms, and answers exactly
the same questions, because per-tenant volume follows eval suite size rather than traffic.
Both numbers are tuning, not correctness, and both are recorded where they are set.

**Postgres 17 was asked rather than recalled.** Before the migration was written, a throwaway
transaction confirmed that a partitioned table accepts `FORCE ROW LEVEL SECURITY`, a policy,
a row-level `BEFORE UPDATE OR DELETE` trigger **and** a statement-level `BEFORE TRUNCATE`
trigger, and that hash subpartitioning under a range parent works with a `DEFAULT`. It also
surfaced the constraint that shaped the key: **a unique constraint on a partitioned table
must contain the partition keys**, so B2's `UNIQUE (org_id, observation_id, case_id)` carries
`measured_at` too. That stays idempotent only because `measured_at` is the observation's own
time rather than the clock's — one run has one time — and it is why there is no surrogate id:
a primary key must contain the partition keys, so the composite key *is* the identity and a
case is cited as `case:<observation_id>/<case_id>`.

**AC-26 cannot be met as literally written, and the CHECK carries only the half that can
be.** The criterion requires `input_redacted` to be non-null on every failing row. The third
fixture's per-case rows hold a reference, a prediction, a label and a duration — **no text at
all** — so a CHECK demanding text there makes an honest source unstorable, and writing a
placeholder would be fabricated evidence, which PRD B5 ranks fourth among unacceptable
failures. So the database enforces the privacy half, that an input may exist only for a case
that failed or errored, and "this source declares no input" is reported as `not_applicable` —
the same answer AC-24 already requires for a source with no tracing. Recorded as an open item
below, because it wants a deliberate amendment to AC-26 rather than silent divergence here.

**Also done in this step, to the specification documents themselves.** Two criteria had
drifted out of step with the parts of the document they index: AC-10 read "every H1 to H12
case has a test" while B8's hard-case table runs to H16, and the handoff's stack table routed
case results to ClickHouse or Timescale, which contradicts the standing decision to stay on
Postgres behind an `ObservationStore` interface. Both corrected, both recorded in the PRD's
Part D3 item 8 so the change is auditable there rather than only here.

**A defect the downgrade caught, and it is the same defect twice.** `drop_append_only_statements`
ended with `DROP FUNCTION IF EXISTS layer_append_only()`, which was right while `audit_event`
was the only append-only table. With two, migration 5's downgrade tried to drop a function
`audit_event`'s triggers still depend on, failed, and rolled the whole downgrade back. This is
the same shape as the defect step 4 recorded — a helper whose convenient default stops being
correct once a second caller exists — so the argument is now required and has no default, and
each migration states whether it owns the function. Found by running the downgrade rather than
by reading it.

**Verification.**

```
$ .venv/bin/alembic downgrade base && .venv/bin/alembic upgrade head
$ .venv/bin/python -m pytest tests/test_schema_guarantees.py
...........................................                              [100%]
43 passed in 2.18s
```

A full downgrade to base leaves only `alembic_version`, with the trigger function gone.
Covering: an input on a passing or skipped case refused; every outcome in the vocabulary
storable and a fifth refused; the same case twice refused; a case unable to be edited or
deleted after it was cited; a trace pointer surviving its body; rows routing to two
different monthly partitions, asserted against where the row actually landed rather than
against the DDL issued; an undeclared month landing in `DEFAULT`; a tenant-scoped delete
clearing every partition and sparing the other tenant; and the four freshness CHECKs,
including that a source cannot be `overdue` against a window nobody set.

---

## Step 10 — The evidence spine through the pipeline. Done.

**What it had to achieve.** Carry a case from the run file that recorded it to the finding
that cites it, redacted on the way in and resolvable on the way out.

**Files.** Commit `1306461`, except for five rows — `redact.py`, the PII corpus,
`test_redaction.py`, `engine.py` and `test_metrics.py` — which arrived with step 9 in
`455dcb5` and are listed here because they are this step's first half.

| File | | What it holds and why |
|---|---|---|
| `layer/core/redact.py` | added | Masking by shape, labelled — `[email]`, not asterisks, because a human triaging a failing case needs to know an email was there to read the input at all. Reimplemented rather than imported from `chatbot-lab`, for the same reason `verdicts/stats.py` is: a fixture is test data and may never be a dependency |
| `tests/fixtures/pii_corpus.json` | added | Not a product fixture: a corpus written to contain each shape the redactor claims, because the reference corpora contain none. Each case carries the literal strings that must not survive, written by hand so the assertion does not share its patterns with the code under test |
| `tests/test_redaction.py` | added | 14 tests, including one that fails when a pattern is added to the module with no example beside it, and one that documents the limit rather than claiming a capability |
| `layer/metrics/engine.py` | modified | `CaseOutcome`, and per-case outcomes from `rate`, `accuracy`, `recall` and `precision`. Tier 1, `count`, `mean` and `percentile` emit none |
| `layer/adapters/base.py` | modified | `ObservationCandidate.cases`, and the arithmetic that decides whether they are evidence: `cases_reproduce_the_value` per candidate, `ImportReport.cases` and `cases_disagreeing` per import, and a loud line in `summary()` when a set does not add up |
| `layer/adapters/eval/run_files.py` | modified | `Reader.cases`, the closed `CASE_FIELDS` set and `Reader.definition()`, which puts a source's case shape under every metric it computes. The cases are carried onto the candidate exactly as the engine produced them |
| `layer/onboarding/persist.py` | modified | `write_cases` and `CaseWrite`. Rows are attached through the observation's own idempotency key rather than through the insert's `RETURNING`, and every refusal is named |
| `layer/onboarding/run.py` | modified | The backfill's own line reports case rows stored, duplicates refused and refusals recorded. A backfill that loaded the numbers and none of the proof beneath them used to look identical to one that loaded both |
| `layer/findings/traces.py` | added | The three trace states and per-source retention, read by both the drift query and the `case` resolver. One module because a finding saying the body is gone while its own citation hands over the dead link would break B3 rule 11 in the space of one screen |
| `layer/findings/queries.py` | modified | `failing_cases[]`, `failing_cases_total` and `failing_cases_state` on the drift detail; `case:` refs in its evidence; the sentence that covers all three states |
| `layer/findings/citations.py` | modified | The `case` resolver: to the trace where the source keeps one, to the run where it does not, and to the run rather than a dead link past retention |
| `tests/test_metrics.py` | modified | 15 tests on the per-case layer, plus a sweep over 94 metric-run pairs in the committed history and the single case inside the breaching run that is condition 1's proof |
| `tests/test_eval_adapter.py` | modified | 9 tests: the reader's case shape, the closed key set, a tier 1 metric carrying no cases, the arithmetic over both committed histories, and the trace pointers Langfuse already builds arriving through the same measurement path |
| `tests/test_onboarding.py` | modified | 13 tests: every case of every run stored, the counts reproduced in SQL rather than in Python, the observation's own `measured_at`, a second backfill storing nothing, the input rule per source, the case that is `fail` under one metric and `skipped` under another, and the four refusals |
| `tests/test_findings.py` | modified | 10 tests: the cases named on the finding, every case ref resolving and pinned, the three trace states end to end, and B3 rule 10 asserted over every clause of both products rather than the one the condition was written for |
| `tests/test_refs.py` | modified | 2 tests: `case:<observation_id>/<case_id>`, including a positional case id |

**AC-25's "proven by test over the fixture corpora" cannot prove anything on its own.** 876
input fields across both reference products contain no email, phone, card, sort code,
postcode, IBAN or National Insurance number: their authors wrote clean synthetic text, so a
test over them passes whether the redactor runs or not. The non-circular test is a corpus
written to contain each shape, with the forbidden strings listed **by hand** so the assertion
does not share its patterns with the code it checks — the same technique as `alien_spec.md` in
step 6a. The sweep over the reference corpora is kept beside it and labelled as the weaker
regression guard it is. Also an open item below: the criterion wants amending.

**The load-bearing property is that the cases reproduce the number they are evidence for.**
`passed` equals the number of passing cases and `total` equals the number of counted ones,
across every metric and every committed run — 94 metric-run pairs, asserted in a sweep
rather than on a sampled run, and asserted a second time in SQL against the stored rows
after the write. If those could disagree, a finding would cite evidence that does not add up
to its own claim, which is worse than citing none. So a candidate whose cases disagree has
its cases **refused**: the number is stored, the cases are not, and the measurement is named
in the import report, in the audit event and in the backfill's own output.

**`skipped` is a third thing, and it is why the invariant needs stating.** A true negative is
in neither recall's denominator nor precision's: the run exercised the case and this metric
does not measure it. Calling that a failure would invent a breach; dropping it would leave
the run only partly accounted for. So it is stored and excluded from the arithmetic, and the
same row is `fail` under one metric and `skipped` under another — an outcome is a property of
the metric, not of the row. There is a test for exactly that over the committed history, as
AC-26 asks.

**Two defaults that are deliberately asymmetric.** `id_field` defaults to `id` and
`trace_id_field` to `trace_id`, because a wrong guess there costs a missing pointer.
`input_field` has **no default at all**: guessing which field holds the customer's own words
and storing it would be the one default in this codebase whose failure mode is retaining
other people's personal data that nobody asked for.

**The case shape is stated once per reader, and its key set is closed.** A run file's rows
have one shape, so `cases: {input_field: ...}` sits on the reader and a metric may override
it. An unknown key is refused at read time rather than ignored, because the failure mode of
a typo here is silence: `input_fields` would leave every failing case with no input, every
finding with nothing to show, and nothing anywhere saying why.

**A privacy defect found by printing output, not by a test.** The rule was first written as
the specification words it — an input "only where `outcome` is not `pass`" — which stored the
text of every `skipped` case: **nine of fourteen cases per run** for one fixture's recall
metric, including a bereavement and a financial-distress disclosure, for cases that metric
never measured. The reasoning behind the rule is that nobody asks to see the input of a case
that passed, and nobody asks to see the input of a case that was not measured either. The
rule is now `input_redacted IS NULL OR outcome IN ('fail', 'error')`, enforced by the CHECK as
well as by the engine, and it broke one of step 9's own tests when it landed, which is the
test doing its job.

**An arithmetic error found the same way, and worth recording because it looks right.** The
first count written for the stored rows was `47 runs × 14 cases`, taken from a run's own
`meta.cases`. The committed runs hold between 1 and 20 cases each and 506 in total, so the
assertion was wrong by 60% while reading like the obvious hand-check. The tests now carry 506
and say where it comes from.

**`write_cases` keys through the idempotency key, not through the insert.** The observation
upsert returns only the rows it created, which on a repeated backfill is none of them — so
reading the ids back is the only thing that can attach cases to an observation stored before
this step existed, and it makes the second run free: the case rows collide on the primary key
and are refused exactly as the observations are. `measured_at` is copied from the observation
and never read off the clock, because it is part of the primary key as the partition key, and
the clock would write a second copy of every case on every backfill (AC-27).

**Four refusals, each named rather than counted.** Cases that disagree with their aggregate;
cases whose observation is absent; two cases in one run sharing an id, where the second cannot
be addressed individually and overwriting the first would make `case:102/14` resolve to
whichever row was written last; and a case id longer than the column, where truncating would
turn two cases into one piece of evidence. Each lands in the audit event by name and in the
step's output as a count, because a smaller number of rows is not a report.

**`trace_available` is three states, not a boolean, and none of them calls the eval platform.**
AC-24 requires the proof "with no log reading and no live call to the eval platform", so
`available` is a claim about the pointer and the source's declared retention window. A source
that records no trace scores `not_applicable`, never a silent false, which would read as a
Layer that lost the pointer (B6). A pointer past its source's window is `past_retention`: the
finding says the body is gone, keeps the recorded outcome, and its citation goes to the run
rather than to the dead link (AC-23, B3 rule 11). Retention is declared per source as
`source.config["trace_retention"]`, a duration — a fact about somebody else's platform and
tier, so the Layer is told it rather than knowing it, and adding it needed no migration (R3).
An unparseable window is treated as undeclared, because reading a typo as "every trace is
gone" would retire the evidence behind every old finding at once.

**`failing_cases_state` exists so an empty list cannot mean two things.** B3 rule 10 makes an
empty `failing_cases` beside `runs_missed > 0` a defect — but it is also the honest answer for
a tier 1 metric, where the source reports a number and there are no rows beneath it the Layer
ever saw. So the three states are `cited`, `not_applicable` and `no_failing_cases`, each with
its own sentence in the summary, and the B3 rule 10 test runs over every clause of both
products rather than the one clause the condition was written for.

**The cited cases are the ones the metric recorded as failing, which is a statement about the
metric and not about the bar.** For a floor — accuracy at least 85% — they are the same rows.
For a cap expressed over a per-case predicate they would not be, so the summary says "recorded
as failing" and no sentence claims one produced the other (B3 rule 6). No clause in the three
fixtures is a cap over a per-case metric: their `<=` bars are latency and cost, which are
`percentile` and `read` and emit no cases at all.

**AC-22, AC-23 and AC-24 are now met end to end**, with one limit stated rather than hidden:
`past_retention` is derived from the window the source declares, not from a fetch that found
the body gone. The live fetch belongs to the tool a human reaches for when they want to read
the conversation itself, which is step 12's `failing_cases` and phase 4's transport. Deriving
it here is not a shortcut — AC-24 forbids the live call in this path.

**Verification.**

```
$ .venv/bin/python -m pytest
........................................................................ [ 90%]
.....................................                                    [100%]
397 passed in 73.85s (0:01:13)
```

34 tests added since the first half of this step's 363. The output was also read rather than
only asserted, for all three trace states and both reference products:

```
TRI-11.2 Escalation recall >= 99% was missed in 7 of 47 runs, worst 80% (4 of 5) in run
20260915-133223Z-v2. The latest run, 20260915-150650Z-v2, is at 100%, so a check on the
latest run alone shows nothing. 7 case(s) across these runs are recorded as failing. Case
ids: 14. This source records no trace pointers, so each case cites its run. Breaching runs
record ca6d836-dirty. Runs at 98380d1, baf3df2-dirty, f05a88a-dirty do not breach.

PD-8.8 Critical C1 to C6 >= 100% was missed in 7 of 7 runs, worst 45.5% (5 of 11) in run
20260917-071445Z-v1. [...] 14 case(s) across these runs are recorded as failing. Case ids:
q28, q33, q36, q39, q40, q42, q41. 14 of the cited cases point at a trace the source no
longer keeps; the recorded outcome is shown instead of a dead link.
```

Covering: a candidate carrying the cases behind its number; a reader's case shape reaching
every metric it computes and a metric overriding it; a misspelled case key refused rather
than ignored; a tier 1 metric carrying no cases without that counting as a disagreement; the
cases reproducing their own `passed` and `total` over both committed histories at the adapter
and again in SQL after the write; every case of every run stored rather than the failures
alone; a case carrying its observation's time and not the clock's; a second backfill storing
nothing; an input stored for a failing case and for no other, and none at all from the source
whose rows carry no text; one case that is `fail` under one metric and `skipped` under
another, from real data; the four refusals, each named rather than counted; the cases named
on the drift finding with every `case:` ref resolving to a pinned URL and `unresolved` empty;
all three trace states end to end, including the citation for a deleted trace going to the
run rather than the dead link; and B3 rule 10 asserted over every clause of both products
rather than the one clause the condition was written for.

---

## Step 11 — Freshness: a verdict degrades rather than freezing. Done.

**What it had to achieve.** Stop an absent measurement from reading as a passing one. The
columns arrived in step 9 and nothing read them, so a clause could sit at `met` forever
because the thing that would have changed it stopped running.

**Files.** Commit `c96191c`.

| File | | What it holds and why |
|---|---|---|
| `layer/core/freshness.py` | added | The rule, and the two ages it keeps apart. `SourceWindow` answers "has the pipe stopped", `Staleness` answers "how old is the number this rests on", and both render themselves in words |
| `layer/core/durations.py` | modified | `approximate_duration`, for an age. `format_duration` stays exact and is now only for a window — see the defect below |
| `layer/verdicts/rules.py` | modified | `judge` takes a `Staleness`; `Judgement` gains `as_of`, `stale` and `degraded_from`; `Measurement` carries `measured_at` and `source_id` |
| `layer/onboarding/state.py` | modified | `measure` resolves each clause's source window, applies the rule, names every degraded clause in the audit event, and refreshes source status on its way out. `Status` carries `stale_sources` and prints them |
| `layer/onboarding/run.py` | modified | Every import stamps `last_sync_at` on the source it read, so a dead pull is distinguishable from a quiet one |
| `layer/findings/shapes.py` | modified | `Finding.as_of` and `Finding.stale`; `FindingSet.stale_sources`, on every set including a refusal |
| `layer/findings/queries.py` | modified | The global `STALE_AFTER` retires in favour of the source's own window; drift and `uncovered` carry `as_of`, `stale` and the staleness in prose |
| `layer/cli.py` | modified | `findings` prints the stale sources before the findings they qualify and marks a stale finding; `measure` says why it just produced a wall of `cannot_confirm` |
| `tests/test_freshness.py` | added | 26 tests: the rule with no database, a source's own age, and the dead-pull condition end to end over a reference product |

**Two different ages are measured, and conflating them is the easy mistake.** A
*measurement's* age is `now - observation.measured_at` and it decides a verdict. A
*source's* age is `now - source.last_sync_at` and it decides `source.status`. They are
usually close and they are not the same: a source that synced ten minutes ago and found
nothing new leaves a fresh source and a stale measurement, and both are reported. The
distinction is in the module docstring because **the first version of these tests got it
wrong**: they advanced a clock inside `measure` and then asserted that the source was
overdue, which produced a state the world cannot reach — a row saying it synced seconds
ago while the answer claimed it was weeks late. The tests now make each age stale the way
it really goes stale, by backdating `last_sync_at` for the pipe and by advancing the clock
for the measurement.

**A window nobody set degrades nothing.** `freshness_window` is null until a human states
a cadence, and PRD B2 is explicit that staleness is then reported without a verdict being
degraded by a window nobody set. This is why the global `STALE_AFTER = timedelta(days=30)`
is gone rather than kept as a default: a quarterly review source is not overdue at 31 days
and a nightly eval is overdue long before that, so one threshold cannot be right for both,
and a Layer that picked one would have invented a cadence for somebody else's eval suite.

**The rule is applied after the verdict, not instead of it.** `judge` computes the verdict
from the interval as before and then ages it, which is what lets `degraded_from` say what
the stored number would otherwise have read. Without that, a degraded clause is
indistinguishable from one that was never confident, and the operator has no way to find
what moved. A verdict already reading `cannot_confirm` keeps it and gains the age: it was
claiming nothing, so there is nothing to degrade and a `degraded_from` there would invent a
verdict the Layer never held.

**`stale_sources` is derived, never read off `status`.** The column is a cache that
something has to refresh, and an answer that trusted it would report "nothing is stale" for
exactly as long as nobody ran the refresh — the same silence this step exists to break. The
column is still written, because the operator surfaces and B11's `source_status` read a
source rather than a finding, and the CHECK from step 9 requires `overdue_since` beside it.

**`refresh` never runs from a read path.** It is called by the measure pass, which is where
the Layer reassesses what it knows, and by the operator's own command. A query that wrote
would make `layer findings` a mutation, and two of them racing would each think it was the
one that noticed.

**A source that has never reported is judged from when it was bound.** Treating "never" as
"just now" would make a dead pipe look healthy for one whole window, and a source bound an
hour ago with a 30 day window is correctly not overdue. `overdue_since` is the moment it
became overdue — `last_sync_at + window` — and not the moment the Layer noticed, because
when the Layer looked is not information about the source.

**Two defects found by printing output, neither caught by a test.** An age of 17 days and 6
hours came out of `format_duration` as **`1491958s`**, which is accurate and tells a reader
nothing; ages now round down to a single unit while a window stays exact, because a window
is policy a human typed and will check against what they set. And a stale drift summary
read "The latest run, X, is at 90.9% and still misses the bar" one sentence before "this
measurement is not current" — asserting and withdrawing the same thing in one paragraph.
The stale wording now states the record ("the newest run on record … below the bar") and
leaves the present tense alone. Both have tests now; neither had one before the output was
read.

**The end-to-end tests use the policy product, and that was probed rather than assumed.**
A degradation can only be shown where there is a verdict to degrade. Triage has none: its
committed runs hold between 1 and 20 cases, so every Wilson interval is too wide to clear
or fall below a bar and all its measured clauses read `cannot_confirm` while perfectly
fresh. The first version of this file asserted `met` on triage and failed for that reason.
The policy product's critical subset is 11 cases against a 100% bar and reads `missed`,
which is a verdict with something to lose.

**A discrepancy found while counting criteria, and it is not step 11's to fix.** This file
said "PRD C3 is 36 criteria, AC-1 to AC-36". The specification holds **39**: AC-37, AC-38
and AC-39 arrived with the 3 October revision and are recorded in its own Part D3. Counted
rather than recalled, by grepping the spec and the test markers together. Corrected above,
and the three are now in the outstanding table — AC-37 and AC-38 have behaviour and tests
that nothing connects to them, while **AC-39 has no direct test at all**: the Wilson
interval is exercised only through onboarding verdicts, and `layer/verdicts/stats.py` is
not tested on its own anywhere. That is step 13's work and it is listed there.

**Verification.**

```
$ .venv/bin/python -m pytest
........................................................................ [ 85%]
...............................................................          [100%]
423 passed in 76.49s (0:01:16)
```

The output was read as well as asserted, for the same product with its pull alive and then
dead 30 days:

```
THE PULL IS ALIVE
  verdicts      missed 1, not_applicable 37, not_measured 12

THE PULL DIED 30 DAYS AGO, WINDOW 1d
  verdicts      cannot_confirm 1, not_applicable 37, not_measured 12
  OVERDUE       the eval source (repo) last synced 30d ago, 29d past its 1d window
  [PD-8.8] stale=True
    PD-8.8 Critical C1 to C6 >= 100% was missed in 7 of 7 runs, worst 45.5% (5 of 11)
    in run 20260917-071445Z-v1. The newest run on record, 20260918-080101Z-v6, is at
    90.9%, below the bar. [...] The newest run here is not current — this rests on a
    measurement from the eval source (repo) taken 17d ago, outside its 1d window, so
    it is shown with its age rather than as current.
  [PD-8.8] uncovered/not_measured_recently
    PD-8.8 was last measured in run 20260918-080101Z-v6, and that run is 17d old
    against a 1d window on the eval source (repo). A number that stopped being taken
    is not evidence that nothing changed.
```

Covering: a `met` and a `missed` each degrading to `cannot_confirm` outside the window, and
`cannot_confirm` keeping its verdict and gaining the age; `degraded_from` recording what
the number would have read; a window nobody set degrading nothing even at 4000 days; the
clock alone moving a verdict between two passes with nothing else changed; a source inside,
past and without a window; a source that never reported judged from when it was bound, and
one bound an hour ago not overdue for it; `overdue_since` being when it happened rather
than when it was noticed; an age rounded while a window stays exact; a stale drift summary
stating the record and a fresh one keeping the present tense; every finding on a stale
source naming that source and its age in prose; a fresh product carrying no staleness
anywhere; the overdue row carrying `status` and `overdue_since` together; and a backfill
stamping the source it read even when it stored nothing.

---

## Step 12 — The stdio MCP server, to PRD B11. Done.

**What it had to achieve.** The whole product reachable from a terminal with no UI, no
agent runtime and no Dust, with the approval boundary inside the server rather than in
anybody's prompt.

**Files.** Commit `d187173`.

| File | | What it holds and why |
|---|---|---|
| `layer/answers/shapes.py` | added | B1's `Answer` and `ProposedChange`. The Answer enforces its own rules in the constructor: a non-empty `stale_sources` caps confidence at medium and adds its caveat, and lowering confidence without saying why is refused outright |
| `layer/answers/reads.py` | added | The read tier as functions over a session — products, sources, clauses, metric history, the proof tool, the chain. Knows nothing about MCP |
| `layer/answers/writes.py` | added | The write tier, with every B3 and B4 check in one place so no proposal path can skip one |
| `layer/answers/__init__.py` | added | The package's own argument for existing: the answers are independent of the transport that serves them |
| `layer/mcp/server.py` | added | The transport. Nineteen tools, the human-only tier absent, the org and the actor bound at startup |
| `layer/cli.py` | modified | `layer serve --transport stdio`, printing nothing on success because stdout is the protocol's channel |
| `layer/findings/queries.py` | modified | `violates` and `failing_cases_for` made public, so the tool and the finding cannot drift apart |
| `layer/findings/citations.py` | modified | The clause resolver no longer resolves a ref no clause carries — see the defect below |
| `tests/test_mcp.py` | added | 34 tests: the boundary, the shapes swept across the surface, the proof tool, the chain, the write tier, and one real stdio pipe |

**The boundary is structural, and the test is written to catch the way it would really
break.** AC-31 asserts against the served tool list, and a second test greps the module
for `def register_product(`, `def confirm_binding(` and the rest: B11 requires the third
tier to be **absent**, not undecorated, because a function sitting in the file is one
decorator away from being served and a reviewer would have to notice an absence from a
list rather than an absence from the file. `confirm_binding` is the one that is easy to
miss — an agent able to confirm its own binding manufactures B3 rule 8's precondition and
can then propose anything at all, which turns the gate into a formality.

**`layer/answers/` exists so that phase 4 really is one argument.** PRD D2's reason for
taking phase 5 first is that phase 4 "adds no capability (one `run(transport=...)`
argument)", and that is only true if the answers do not live inside the transport. They
do not: `layer/mcp/server.py` is nineteen thin wrappers, and the same functions already
serve the CLI.

**B11's own sketch contradicts B11's rule 4, and rule 4 wins.** The tool list is written
`list_products(org)`, while rule 4 of the same section is "every tool is tenant-scoped in
the data layer. No tool takes an `org_id` from its caller". An argument would be a
documented route across the boundary, so the org is bound to the process at startup — from
`--org` or `LAYER_ORG_ID` — and a test sweeps every tool's input schema for an argument
whose name contains "org". Recorded here rather than silently resolved.

**The actor is identity, not authentication.** Writes are attributed to `--as`, the same
argument every other command takes, and the server cannot verify the claim. That is the
same line the phase 5 decision draws, and sessions and bearer tokens stay in phase 4.

**`find_underspecified` is served although B11 lists four of the five findings.** B11 rule
6 is that the surface is the whole product — "anything a human can learn from the Layer is
learnable through these tools" — and `underspecified` is a finding kind B1 defines and the
CLI already answers. Omitting it would make the MCP surface narrower than the terminal's,
which A6 forbids.

**`trace_chain` carries its path, so a cycle terminates.** One recursive CTE walks both
directions and appends each new ref to an array that the next hop is checked against, so
two records pointing at each other stop rather than running to the depth cap. The cap is
ten and not six: a walk that stopped exactly at AC-6's length would answer the criterion
and silently truncate anything longer. Gaps are named against `EXPECTED_CHAIN`, which is
six link types held as data — US-5 requires an absent link to be named, and that is only
possible against a stated expectation.

**What AC-6 is and is not proven by.** The six-link chain resolves end to end in one call,
with every link's type and source kept, over links **written by hand in the test**. No
source the Layer can bind produces a requirement, a decision or a ticket until phase 6, so
the mechanism is proven and a real product's chain is not. The test says so and so does
the outstanding table; the marker alone would imply both.

**A defect in step 8's code, found by a test written to fail.** The clause resolver read a
clause's `source_locator` and then built the spec file's URL **whether or not the clause
existed**, so `clause:NOPE-1` resolved to a real URL. A proposal citing it was therefore
accepted as having resolvable evidence — B3 rule 7, "never cite a record it did not read",
broken by a missing null check since step 8, and invisible because every ref the suite had
ever resolved was real. An unknown ref now lands in `unresolved`, where B1 requires it to
be displayed.

**Four tests in this file passed vacuously, and the fix is in the test rather than the
code.** The fixture onboarded a product inside an open `org_session` and never committed,
the server opened its own session as it really will, saw nothing, and refused every call —
and a refusal is one of B1's four shapes, so a sweep asserting "every tool returns one of
the four shapes" was satisfied by a server that could see no data at all. The fixture now
commits, and the sweep names the eight tools that must answer rather than refuse. The
lesson generalises: a shape test over a vocabulary that includes the failure mode proves
nothing unless something also asserts the success.

**One test drives a real pipe.** `python -m layer serve` in a subprocess, an MCP
`initialize`, `tools/list`, and a `find_drift` call returning findings. Everything else in
the file calls the server in-process, which would pass just as well if the transport were
broken, the CLI entry point missing, or stdout polluted by a banner — that last being the
easy mistake, since stdout is the protocol's own channel and anything written to it is a
parse error at the other end.

**Verification.**

```
$ .venv/bin/python -m pytest
........................................................................ [ 94%]
.........................                                                [100%]
457 passed in 115.07s (0:01:55)
```

The output was read as well as asserted:

```
LIST_PRODUCTS  -> answer   1 product(s) onboarded: triage (live).
GET_CLAUSE     -> answer   TRI-11.2 Escalation recall states >= 0.99. It is measured
                           and reads cannot_confirm, latest 1 in run 20260915-150650Z-v2.
                           0 link(s) touch it.          citations 2, resolved 2
FAILING_CASES  -> answer   TRI-11.2 >= 0.99: 7 of 47 run(s) miss the bar, and 7 case(s)
                           across them are recorded as failing.   citations 8, resolved 8
TRACE_CHAIN    -> answer   clause:TRI-11.2 reaches 0 other record(s) over 0 link(s).
                           6 expected link type(s) are absent.    confidence
                           cannot_determine, 1 caveat naming all six
```

Covering: no human-only tool served and none defined in the module; `confirm_binding`,
`accept_proposal` and `reject_proposal` by name; no tool accepting an `org_id`; every
B11 tool present and described; every read returning one of the four shapes **and** the
eight that must answer answering; every answer stating a confidence and carrying a caveat
whenever it is not high; no statement longer than five sentences; no answer reporting
neither a link nor an unresolved ref; a clean install refusing every read by name with
zero proposals; the proof tool naming 7 of 47 runs and the same cases the finding cites; a
window narrowing the runs and an unreadable one refusing; a six-link chain end to end, a
gap named, a link walked in either direction, a cycle terminating, and a non-ref refused;
a proposal created open and undecided with an audit event, and refused for unresolvable
evidence, no evidence, no `if_rejected`, a duplicate open target, an unknown field, an
unknown link type, and a product with no confirmed binding; `propose_binding` leaving the
bindings untouched; a recorded case input redacted whatever the caller sent and dropped on
a passing case; and the server answering over a real stdio pipe.

---

## Next

**Step 13**, the last of phases 1 to 3: the agnosticism grep test, and the AC matrix that
closes AC-10 to AC-12 across **39** criteria — including the user-story tests that nothing
covers yet, the markers AC-37 and AC-38 are missing over behaviour that already works, the
Wilson interval test AC-39 asks for and nothing has, and a statement of which criteria
belong to phases 5 to 7 rather than reporting them as failures.

**And two documents that disagree with the code.** `CLAUDE.md` says `README.md` documents
EarlyEcho, when `README.md` is the Layer's since `de21e88`; its closed-set list omits
`source.status`; and its hard constraints do not carry B3 rules 10 to 12 or the
`confirm_binding` rule. `README.md` says "All 12 tables" and "Three of the five findings",
where it is now 13 and five. Both are step 13's tail, not optional.

---

# Beyond phase 3 — the order changed, and why

The handoff's build order runs phase 4 (HTTP, a token, Dust as a client) before phase 5 (the
write half). **That is reversed here.** The reasoning is below so it is not re-argued, and so
that a reader who knows the handoff can see where this departs from it.

## Open items created during the build

1. **The Langfuse transport is unverified against a live instance.** The normalisation is
   tested with a fake shaped like langfuse 4.15.4's real surface, and the SDK surface was
   read rather than recalled, but pagination at scale, rate limits and timestamp types have
   not met a real project. Credentials exist in the fixture repository's `.env`. Half an
   hour with a live project closes this, and it should happen before the Langfuse path is
   relied on for a product whose history matters.
2. **An LLM pass over spec prose is designed for but not built.** The deterministic
   importer covers tables, list items and sentences that state an obligation. A document
   that states a bar in a way no pattern reaches will silently yield no clause for it. The
   interface is shaped so the pass can be added above the deterministic one without moving
   anything, and it must stay above it: EC-10 requires failing closed, which a
   non-deterministic importer makes hard to reason about.
3. **AC-26 and AC-25 both need the criterion amended rather than the code bent.** AC-26 asks
   for an input on every failing row, which a source carrying no text cannot give; AC-25 asks
   for proof over corpora that contain no PII to find. Steps 9 and 10 record what each one
   does instead. The code takes the honest reading in both cases and neither criterion has
   been changed to match.
4. **`layer/db/partitions.py` declares six months and nothing extends them.** Anything outside
   lands in the `DEFAULT` partition, which is correct rather than broken, but declaring a new
   month is DDL by the table owner and there is no maintenance command for it yet. It becomes
   real the first time a scheduled pull runs unattended, which is the same phase as audit item
   P11's alerting.

---

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

**Open question 1 is answered, and the answer changes nothing structural.** Hosted Dust gates
remote MCP servers behind a paid plan tier. That blocks nothing, because phases 1 to 3 contain
no Dust and PRD A6 requires every criterion in Part C to pass over stdio — and the free test
harness for stdio is Claude Code or Claude Desktop, neither of which has a plan tier or needs
HTTP. Paying for Dust becomes a decision only when multiplayer and scheduled triggers are
wanted, which is phase 4, and the recorded free alternatives are self-hosted Dust under MIT,
or LibreChat and Open WebUI without the durable agent loop.

**Open question 2 is still open and is now the only Dust question that matters**: what auth
scheme its credential policy accepts for a remote MCP server. It cannot be answered by
reasoning, only by trying.

So the spike shrinks rather than disappearing. At the phase-3 boundary: put the server behind
HTTP with a throwaway token, register it in hosted Dust as a remote MCP server, record the
accepted auth scheme in this file, and stop. That de-risks phase 4 without reordering the real
work.

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
