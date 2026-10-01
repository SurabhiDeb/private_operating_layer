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
