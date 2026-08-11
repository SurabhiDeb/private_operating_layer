# EarlyEcho — Private AI Operating Layer

EarlyEcho is a done-for-you, single-tenant AI operating layer for small businesses. It captures everything a company knows — decisions, processes, client facts, beliefs — from Slack, email, and uploaded documents, verifies it with a critic agent, stores it in a three-layer memory system, and makes it queryable by the team in plain English.

Each client runs on their own dedicated server. No shared infrastructure. No shared data.

---

## Architecture Overview

```
Data Sources (Slack, Gmail, CSV, Documents)
        │
        ▼
  Pre-filter (noise removal)
        │
        ▼
  Extractor (LLM — gpt-4o-mini)
        │
        ▼
  Critic Agent (confidence scoring)
        │
   ┌────┴────┐
   │         │
score ≥ 0.7  0.5–0.69       < 0.5
   │         │               │
Auto-approve  Human queue   Auto-reject
   │
   ▼
Three-layer memory:
  ├── Episodic  (PostgreSQL + pgvector)  — semantic search
  ├── Semantic  (Kuzu graph DB)          — entity relationships
  └── Procedural (BusinessProfile)      — org-level config & rules
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI |
| Database | PostgreSQL + pgvector extension |
| Graph DB | Kuzu (embedded, per-org) |
| ORM | SQLAlchemy |
| Pipeline orchestration | LangGraph |
| AI model (extraction, chat, agent) | gpt-4o via OpenAI-compatible API |
| AI model (briefing, critic) | gpt-4o-mini |
| Scheduler | APScheduler |
| Slack integration | Slack Bot Token + Nango OAuth |
| Gmail integration | Google OAuth 2.0 (BYOK — client provides own credentials) |
| Email inbound | Mailgun BCC webhook |
| Frontend | Vanilla JS dashboard (single HTML file) |
| Production supervisor | systemd |
| Monitoring | UptimeRobot (external ping) + internal dead man's switch |

---

## Directory Structure

```
private_operating_layer/
├── api/
│   ├── main.py                  # FastAPI app, scheduler, /health endpoint
│   └── routes/
│       ├── agent.py             # POST /agent/run
│       ├── auth.py              # POST /auth/register, /auth/login
│       ├── chat.py              # POST /chat/ask
│       ├── context.py           # GET /context/{org_id}, evals, pending queue
│       ├── email.py             # POST /email/inbound (Mailgun webhook)
│       ├── ingest.py            # Memory CRUD, approval queue, briefing
│       ├── integrations.py      # Slack, Gmail, CSV, document upload, status
│       └── onboarding.py        # POST /onboarding/create, briefing focus
│
├── ingestion/
│   ├── pipeline.py              # LangGraph pipeline: extract → critic → store
│   ├── extractor.py             # LLM extraction — entities + relationships
│   ├── critic.py                # Confidence scoring per entity
│   ├── pre_filter.py            # Noise removal before LLM call
│   ├── raw_indexer.py           # Stores raw source before processing
│   └── parsers/
│       ├── router.py            # Routes uploaded files to correct parser
│       ├── pdf_parser.py
│       ├── docx_parser.py
│       └── excel_parser.py
│
├── agent/
│   ├── runner.py                # LangGraph agent: load_memory → check_limits → execute
│   ├── tools.py                 # search_memory_tool, draft_content_tool, flag_contradiction_tool
│   └── limits.py                # Belief-based action guardrails
│
├── services/
│   ├── briefing.py              # Daily briefing generation (gpt-4o-mini, cached)
│   ├── chat.py                  # Chat with memory retrieval + citation
│   ├── compile_context.py       # Assembles full context snapshot for an org
│   ├── heartbeat.py             # Dead man's switch — beat() and check_all()
│   ├── embeddings.py            # OpenAI text-embedding-ada-002
│   ├── evaluator.py             # Golden eval runner
│   ├── graph.py                 # Kuzu read/write — entity nodes + relationships
│   ├── slack_backfill.py        # Historical Slack ingestion
│   ├── slack_fetcher.py         # Live Slack message fetching
│   ├── slack_health.py          # Slack connection health check
│   ├── gmail_fetcher.py         # Gmail OAuth token refresh + email fetch
│   ├── backfill_processor.py    # Batch processor for backfill queue
│   ├── token_tracker.py         # Per-org token usage tracking + budget cap
│   ├── csv_import.py            # CSV → entity pipeline
│   ├── entity_mapper.py         # HubSpot-specific field mapping
│   └── nango.py                 # Nango OAuth helper (Slack)
│
├── models/
│   ├── business_profile.py      # Org config, procedural memory, briefing focus
│   ├── entities.py              # Core memory table (pgvector embeddings)
│   ├── pending_entities.py      # Human review queue
│   ├── sources.py               # Raw ingested content
│   ├── session_logs.py          # Chat logs, briefings, pipeline events
│   ├── token_usage.py           # Monthly token counters per org
│   ├── golden_evals.py          # Q&A eval pairs + scores
│   ├── oauth_integrations.py    # Gmail OAuth tokens per org
│   ├── process_heartbeat.py     # Dead man's switch — process last-seen timestamps
│   ├── channel_memberships.py   # Slack channel membership cache
│   └── users.py                 # Auth — email/password per org
│
├── core/
│   ├── config.py                # Pydantic settings — reads from .env
│   └── database.py              # SQLAlchemy engine + session factory
│
├── scripts/
│   └── watchdog.py              # Standalone dead man's switch checker (systemd timer)
│
├── deploy/
│   ├── earlyecho-watchdog.service
│   └── earlyecho-watchdog.timer
│
└── static/
    ├── index.html               # Client dashboard
    ├── client_landing.html      # Client-facing landing page
    └── accelerator_landing.html # Accelerator/investor-facing landing page
```

---

## Three-Layer Memory System

### 1. Episodic Memory — PostgreSQL + pgvector
Stores every verified fact as a vector embedding. Queried via cosine similarity search.

Table: `entities`
- `entity_type`: Belief, Decision, Process, Client, Project, Person
- `content`: the fact in plain text
- `embedding`: 1536-dimension vector (text-embedding-ada-002)
- `status`: active | superseded
- `superseded_by_id`: points to the newer entity if this one has been replaced

### 2. Semantic Memory — Kuzu (graph)
Stores relationships between entities. One Kuzu database per org, stored at `kuzu_data/{org_id}/`.

Example: `TechFlow (Client) —[works_with]→ Alice (Person)`

Used when the chat agent needs to understand connections, not just facts.

### 3. Procedural Memory — BusinessProfile
Org-level configuration stored in PostgreSQL.
- `team_members`: JSON array of people, roles, expertise
- `workflows`: JSON array of named processes
- `approval_rules`: per-entity-type confidence thresholds
- `briefing_focus`: client-defined instructions for the morning briefing
- `slack_workspace_domain`: used to construct Slack permalink URLs

---

## Ingestion Pipeline (LangGraph)

Every piece of content — Slack message, email, CSV row, uploaded document — goes through the same three-node LangGraph pipeline.

### Node 1: Extract
Calls gpt-4o-mini with the raw content. Returns a list of structured entities (type, name, content) and relationships (from, to, type).

### Node 2: Critic
For each entity, calls gpt-4o-mini again to score confidence (0.0–1.0) and verify the extraction makes sense in context.

Routing:
- `score ≥ 0.7` → auto-approved, goes to store node
- `0.5 ≤ score < 0.7` → sent to human review queue (PendingEntity table)
- `score < 0.5` → silently rejected, logged

### Node 3: Store
For auto-approved entities:
1. Deduplication check — if same name + type already exists as active, skip
2. Generate embedding and store in `entities` table
3. Write entity node to Kuzu
4. Run contradiction check against existing memory — if conflict found, surface as a reviewable item in the pending queue

For pending entities: written to `pending_entities` with `review_type="standard"`.

For contradictions: written to `pending_entities` with `review_type="contradiction"`.

Queue cap: if pending queue reaches 20 items, pipeline pauses and logs a warning.

---

## Agent (LangGraph)

The agent is a secretary, not an autonomous system. It only acts on verified memory and is blocked by active company beliefs.

### Node 1: Load Memory
Searches pgvector for relevant facts. If no memory found, blocks immediately — the agent cannot act without verified context.

### Node 2: Check Limits
Reads active Beliefs from memory. If the requested task conflicts with a stored belief, the action is blocked with an explanation.

### Node 3: Execute
Runs the task:
- `draft`: drafts content using memory context
- `flag`: checks for contradictions in the provided statement

---

## Background Jobs (APScheduler)

All jobs run inside the FastAPI process via APScheduler. Each job calls `beat()` when it completes so the dead man's switch can track it.

| Job | Schedule | What it does |
|---|---|---|
| `run_daily_ingestion` | Daily 7:00am | Fetches last 24h of Slack messages per org, 5-min gap between orgs |
| `run_daily_briefings` | Daily 8:00am | Generates and caches morning briefing per org |
| `run_micro_batch` | Every 30 min | Fetches last 30min of Slack messages — keeps memory current |
| `_process_backfill_batch` | Every 2 min | Processes queued historical Slack backfill sources |
| `_run_all_evals` | 1st of month, 6:00am | Runs golden evals against all orgs, scores memory quality |
| `_run_all_health_checks` | Daily 6:30am | Checks Slack connection health per org |

---

## Dead Man's Switch

Every background job calls `services/heartbeat.beat(process_name)` on completion. This writes a timestamp to the `process_heartbeats` table.

`scripts/watchdog.py` runs every 5 minutes via systemd timer. It calls `check_all()` and alerts if any process hasn't reported within 1.5× its expected interval.

The `/health` endpoint returns live heartbeat status for all processes alongside database connectivity. This is what UptimeRobot watches from outside.

To add real alerting, fill in the `alert()` function in `scripts/watchdog.py` with a Slack webhook or Mailgun call.

---

## API Endpoints

### Auth
| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create user + org in one step |
| POST | `/auth/login` | Returns org_id on success |

### Memory
| Method | Path | Description |
|---|---|---|
| POST | `/memory/ingest` | Manually add content to memory |
| GET | `/memory/list/{org_id}` | List entities (supports `entity_type`, `limit`, `offset`) |
| GET | `/memory/briefing/{org_id}` | Load cached daily briefing |
| GET | `/memory/contradictions/{org_id}` | List contradiction flags |
| POST | `/memory/queue/{id}/approve` | Approve a pending item |
| DELETE | `/memory/queue/{id}/reject` | Reject a pending item |
| POST | `/memory/queue/bulk-approve/{org_id}` | Approve all pending items |
| DELETE | `/memory/queue/bulk-reject/{org_id}` | Reject all pending items |

### Chat & Agent
| Method | Path | Description |
|---|---|---|
| POST | `/chat/ask` | Ask a question — returns answer with cited source |
| POST | `/agent/run` | Run an agent task (draft / flag) |

### Context & Evals
| Method | Path | Description |
|---|---|---|
| GET | `/context/{org_id}` | Full context snapshot |
| GET | `/context/usage/{org_id}` | Token usage summary |
| GET | `/context/pending/{org_id}` | Pending queue items |
| POST | `/context/evals/{org_id}` | Add a golden eval pair |
| POST | `/context/evals/{org_id}/run` | Run evals manually |
| GET | `/context/entities/{org_id}` | List entities (with status filter) |
| POST | `/context/entities/{org_id}/supersede` | Mark entity as superseded |

### Integrations
| Method | Path | Description |
|---|---|---|
| POST | `/integrations/slack/backfill` | Trigger Slack historical backfill |
| GET | `/integrations/slack/health/{org_id}` | Check Slack connection |
| GET | `/integrations/slack/backfill/status/{org_id}` | Backfill progress |
| POST | `/integrations/csv/import` | Upload and ingest CSV |
| POST | `/integrations/document/upload` | Upload PDF, DOCX, or XLSX |
| POST | `/integrations/gmail/credentials/{org_id}` | Save Gmail OAuth credentials |
| GET | `/integrations/gmail/connect/{org_id}` | Start Gmail OAuth flow |
| GET | `/integrations/gmail/callback` | Gmail OAuth callback |
| POST | `/integrations/gmail/backfill/{org_id}` | Ingest Gmail history |
| GET | `/integrations/status/{org_id}` | Full status: Gmail, last sync, memory count, queue size |

### Onboarding
| Method | Path | Description |
|---|---|---|
| POST | `/onboarding/create` | Create an org (used internally by /auth/register) |
| GET | `/onboarding/briefing-focus/{org_id}` | Get current briefing focus |
| PATCH | `/onboarding/briefing-focus/{org_id}` | Update briefing focus |

### Health
| Method | Path | Description |
|---|---|---|
| GET | `/health` | DB connectivity + per-process heartbeat status |

---

## Environment Variables (.env)

```env
# Database
DATABASE_URL=postgresql+psycopg2://localhost/earlyecho

# AI (OpenAI-compatible)
AI_API_KEY=your_key
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o
AI_MODEL_FAST=gpt-4o-mini

# Slack
SLACK_BOT_TOKEN=xoxb-...
NANGO_SECRET_KEY=...

# Gmail inbound (Mailgun)
MAILGUN_SIGNING_KEY=...
MAILGUN_DOMAIN=inbound.yourdomain.com

# App
APP_NAME=EarlyEcho
DEBUG=false
```

---

## Local Setup

```bash
# 1. Clone and create virtual environment
python -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env from the template above

# 4. Create the database
createdb earlyecho

# 5. Enable pgvector extension
psql earlyecho -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 6. Start the server (tables are auto-created on startup)
uvicorn api.main:app --reload

# 7. Open the dashboard
# http://localhost:8000
```

---

## Adding a New Column to an Existing Table

`Base.metadata.create_all()` only creates tables that don't exist yet. For columns added to existing tables, run an `ALTER TABLE` manually:

```bash
psql postgresql://localhost/earlyecho -c \
  "ALTER TABLE table_name ADD COLUMN IF NOT EXISTS column_name TYPE;"
```

---

## Production Deployment

Each client gets a dedicated DigitalOcean droplet running:
- FastAPI via systemd (auto-restart on crash)
- nginx as reverse proxy (HTTPS via certbot)
- systemd timer for the watchdog (every 5 min)
- UptimeRobot watching `/health` from outside

See `deploy/` for systemd unit files.

---

## Phase 2 (Planned)

- Docker + centralised control panel
- Native PM tool integrations (Linear, Jira, Notion)
- Meeting transcript ingestion
- Per-client managed API keys (no BYOK)
- Flat capped pricing tier
- Alembic migrations (replace manual ALTER TABLE)
