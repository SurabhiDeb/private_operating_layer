# The Layer

**Finds where an AI product has drifted from what it promised, and stops it drifting again.**

An AI team's promises live in a document. Its reality lives in eval tools. Nothing holds
both, so nobody can reliably answer *"is our product still doing what we said it would"*.
Eval platforms hold the runs but not the clause. Ticket trackers hold the clause but not
the runs. The Layer holds **the binding between them** — which measured number answers
which written promise — with the full history of both, and proposes changes a human decides.

It is **product agnostic**. It knows nothing about any AI product until that product is
onboarded from its own spec and its own eval sources, with no code change.

---

## Status

**Phases 1 to 3 and phase 5 are done — 17 steps of 17.** Both halves work end to end: a
product can be onboarded, interrogated from a terminal or over MCP, and the findings turned
into proposals a named person accepts or rejects. Every answer cites records a human can
open. See [`PROGRESS.md`](PROGRESS.md) for the plan, what each step did, and what is left.

| Working now | Not built yet |
|---|---|
| Multi-tenant schema with provable isolation (Postgres row level security) | The critic, which scores and never decides. Deliberately last: it can never gate a spec edit, so it is not on the path to the one metric that matters |
| All 14 tables, reversibly migrated, with an append-only audit log | The remaining sources: requirement, decision, ticket, config, production |
| Refs: the `kind:id` citation scheme and its resolver registry | Opening the pull request for an accepted CI change, which needs a scoped GitHub app installation (SEC-3). The approval and the diff are recorded |
| A git source pinned to an immutable commit | Dust, or any agent runtime. Phase 4 is one `run(transport=...)` argument |
| The metric engine: reads a number, computes one, or declines to | |
| Spec, eval and code adapters, with Langfuse as a second transport | |
| Onboarding: all seven steps, with the binding gate | |
| Verdicts, via a Wilson interval, degrading when their evidence goes stale | |
| **All five findings, with resolvable citations** | |
| **The evidence spine: the individual cases a finding cites** | |
| **Four generators: findings into ranked, capped proposals** | |
| **The decide path: accept and reject, by a named person with a role** | |
| **The proposal acceptance rate, reported against its band in both directions** | |
| **A stdio MCP server, 20 tools, with the human-only tier absent from it** | |
| **A CLI: `python -m layer`** | |
| 675 tests | |

`find_drift`, `find_unenforced` and `find_uncovered` reproduce real breaches from committed
data, naming the individual cases that missed each bar, with every citation resolving to a
pinned commit. `find_stalled_decisions` and `find_underspecified` refuse by name until their
sources exist, which is the designed behaviour rather than a gap.

The write half turns those findings into proposals and stops: a drift becomes a threshold
change **or** a ticket and never both, an unenforced clause becomes a CI change, a measured
metric nothing promises becomes a clause to write, and a bar nothing has approached in months
becomes a candidate for raising. Each one names a single change to a single field, cites
evidence that resolves, states what happens if it is turned down, and waits. Accepting is
human-only — absent from the MCP server in any phase, not merely undecorated — and bounded by
a role: a product manager decides anything, an engineer decides the gate and the eval suite,
an agent decides nothing. What is missing is the number: twenty proposals decided by a person,
which needs a person.

---

## How it is meant to work

Onboarding is the only way anything enters the Layer. There is no demo product, no seeded
clause set, and no built-in knowledge of any product's metric names or file layout.

```
  1  register a product                         human
  2  bind sources   >= 1 spec, >= 1 eval        human
  3  import the spec        -> clauses          Layer     all provisional, not_measured
  4  backfill the evals     -> observations     Layer     every run, none skipped
  5  propose bindings, then STOP                Layer proposes, human confirms
  6  first measurement pass -> verdicts         Layer     product becomes live
  7  findings, then proposals                   Layer     read-only findings first
  8  decide each proposal                       human     by name, bounded by a role
```

**Step 5 is the gate the whole design turns on.** Before it, the Layer does not know which
number answers which promise, so anything it proposed would be invented. After it, every
proposal rests on an assertion a named human confirmed at a recorded time. A product that
stops at step 4 is still useful — it answers metric history questions and surfaces coverage
gaps — and it proposes nothing.

### What comes out

Five kinds of finding, all plain SQL over what onboarding loaded:

| Finding | The condition |
|---|---|
| `drift` | An observation somewhere in the history violates its clause |
| `unenforced` | A stated threshold that CI does not check — or checks against the wrong run set |
| `uncovered` | A promise nothing measures, or a measurement nothing promised |
| `stalled_decision` | A decision that produced no pull request, ticket or spec change |
| `underspecified` | The eval passes and production is still out of band |

Then proposals: a threshold change, a ticket, a new clause, a link, an eval case, a CI
change. Each names one change to one field, cites evidence that resolves, states what
happens if it is turned down, and is meant to be decidable in under a minute.

**The Layer never decides.** Accepting and rejecting are human-only — absent from the MCP
server in any phase rather than merely undecorated, because a boundary enforced by an
agent's tool allowlist is not a boundary — and bounded by a role: a product manager decides
anything, an engineer decides the gate and the eval suite, an agent decides nothing. A
proposal whose target a human edits meanwhile is closed as invalidated rather than merged
over their edit, and one whose evidence retention has deleted is closed as
evidence-expired rather than shown as live with a dead citation. Neither counts as a
decision, so neither moves the acceptance rate.

---

## The three rules that keep it product agnostic

These are enforced by tests, not by convention.

**No product knowledge in code.** Everything product-specific is a row — `product`,
`source.config`, `binding`, `clause`. No product name, metric name or ref prefix appears
anywhere outside `tests/`.

**Shape in config, mechanism in the adapter.** An adapter may be told *where* to look: a
glob, a JSON pointer, a heading pattern, a metric definition. It may never contain
`if product.key == ...`.

**Open vocabularies are data.** Adding a source system or a clause kind must not require a
migration. There are no Postgres enums.

A product whose spec the author never saw must onboard with no code change. That is the
whole bar, and it is why there are three test fixtures rather than two: two specs written by
one author in one style would prove nothing.

---

## Running it

Requires Postgres 14+ with `pgvector`, and Python 3.13.

```bash
# 1. Dependencies, in this repo's own virtualenv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-layer.txt

# 2. A database, an owner role, and an application role that owns nothing.
#    The split is load-bearing: a table's owner is exempt from its own row level
#    security policies, so the application must not be the owner.
psql -d postgres <<'SQL'
CREATE ROLE layer_owner LOGIN PASSWORD 'change-me';
CREATE ROLE layer_app   LOGIN PASSWORD 'change-me-too';
CREATE DATABASE layer OWNER layer_owner;
SQL
psql -d layer -c 'CREATE EXTENSION IF NOT EXISTS vector'

# 3. Configuration
cp .env.example .env     # then fill in the two database URLs

# 4. Schema
.venv/bin/alembic upgrade head

# 5. Tests
.venv/bin/python -m pytest
```

```bash
# 6. Onboard a product. Nothing exists in the Layer until this runs.
.venv/bin/python -m layer org create acme
.venv/bin/python -m layer product register --org <id> --as you@example.com triage
.venv/bin/python -m layer source bind   --org <id> --as you@example.com triage \
    --role spec --kind repo --config '{"local_path":"...","repo_url":"...","path":"SPEC.md"}'
.venv/bin/python -m layer spec     --org <id> --as you@example.com triage
.venv/bin/python -m layer backfill --org <id> --as you@example.com triage
.venv/bin/python -m layer bindings list --org <id> --as you@example.com triage
#   ... confirm or reject each candidate, then:
.venv/bin/python -m layer measure  --org <id> --as you@example.com triage
.venv/bin/python -m layer status   --org <id> --as you@example.com triage

# 7. What the record says.
.venv/bin/python -m layer findings --org <id> --as you@example.com triage --citations
```

```bash
# 8. The write half. Register who may decide, then turn findings into proposals.
.venv/bin/python -m layer actor add you@example.com --role pm \
    --org <id> --as you@example.com
.venv/bin/python -m layer generate --org <id> --as you@example.com triage

# 9. Decide them. `--as` must resolve to a registered actor here, and the role is checked.
.venv/bin/python -m layer proposals list   --org <id> --as you@example.com --product triage
.venv/bin/python -m layer proposals accept --org <id> --as you@example.com <proposal-id>
.venv/bin/python -m layer proposals reject --org <id> --as you@example.com <proposal-id> \
    --reason "the bar is right; the product is not"

# The one number the product is judged by, with its band in view.
.venv/bin/python -m layer proposals acceptance --org <id> --as you@example.com
```

```bash
# The MCP server, over stdio. The human-only tools are absent from it, in any phase.
.venv/bin/python -m layer serve --org <id> --as you@example.com
```

---

## Layout

```
layer/
  core/        config, the org-scoped session, actors and roles, the refusal vocabulary
  db/          models, migrations, row level security
  refs/        the kind:id citation scheme and its resolver registry
  adapters/    sources: spec, eval and code over a pinned git repo, plus Langfuse
  metrics/     the declarative aggregation engine           (step 5)
  onboarding/  the seven-step state machine                 (step 7)
  verdicts/    state and verdict, via a Wilson interval     (step 8)
  findings/    the five queries and the evidence spine      (steps 8, 10)
  answers/     the read tier, the write tier, and the human-only decide path
  proposals/   the generators: findings into ranked proposals  (step 16)
  mcp/         the stdio server                             (step 12)
tests/         the suite; tests/fixtures/ is the only place a real product is named
docs/          EARLYECHO.md, the previous occupant of this file
operating_layer_main/   the specification, the handoff, and a UI mock
```

## Documents

| File | What it is |
|---|---|
| [`PROGRESS.md`](PROGRESS.md) | The build plan, step status, and how each finished step was built |
| [`CLAUDE.md`](CLAUDE.md) | Working context: constraints, decisions taken, gotchas already paid for |
| `operating_layer_main/PRD-SPEC.md` | The specification. Defines what correct means. Cited throughout as `AC-n`, `H-n`, `EC-n`, `US-n` |
| `operating_layer_main/HANDOFF.md` | Decision history, the stack, the security audit |
| `operating_layer_main/Operating Layer.html` | A UI mock. Reference for output shapes only — its data is hand-written, not generated |

---

## Design decisions worth knowing

- **Postgres only** for observations, behind a store interface. The specification suggests a
  columnar store; at this volume it would buy nothing and cost a week.
- **Verdicts use a Wilson score interval**, so a clause reads `met` only when the interval
  clears its bar. A 7-run sample claiming 99% recall is a guess, and most clauses will
  honestly read `cannot_confirm`. That is the intended behaviour, not a gap.
- **Citations pin a commit, never a branch.** A finding citing `blob/main/SPEC.md` becomes
  wrong the moment someone edits the file, and the reader cannot tell.
- **It never states a cause.** "X first failed at v3 and the prompt sha changed at v3" is
  allowed. "The prompt change caused it" is not. One wrong confident attribution would
  discredit everything else the system says.
- **Nothing is written without an approval record**, and the approval boundary lives inside
  the server rather than in an agent's prompt, so it holds however an agent is configured.

## EarlyEcho

This repository also contains EarlyEcho, a single-tenant Slack and Gmail business-memory
product, under `agent/`, `api/`, `core/`, `ingestion/`, `models/`, `services/`, `scripts/`,
`static/`, `deploy/` and `db/`. Its documentation is [`docs/EARLYECHO.md`](docs/EARLYECHO.md).

The two share a repository and nothing else — no imports, no tables, no configuration. The
Layer borrowed four ideas from it (versioned entities, a critic that scores proposals, a
human review queue, `org_id` on every row) and reimplemented them. Anything outside `layer/`
and `tests/` is EarlyEcho's.
