# Maritime Crew Orchestrator
### Autonomous Maritime Crew Sign-On / Sign-Off Orchestrator using Claude Managed Agents

---

## Overview

An enterprise-grade maritime crew management system that demonstrates **autonomous AI orchestration** using the **Claude Managed Agents** product (`client.beta.agents` / `sessions` / `environments`). The agent loop runs on Anthropic's orchestration layer: a persisted **coordinator agent** natively delegates to 4 persisted **specialist agents**, and the FastAPI backend drives one coordinator session per workflow, resolving the specialists' **custom tools** (over local mock data) and streaming events to the UI in real time. The five agents are created once and are visible in the Console (as Sessions per run).

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│                      Frontend (Next.js 15)                       │
│  Dashboard | Workflow Viewer | Monitoring | ROI Dashboard        │
└──────────────────────┬──────────────────────────────────────────┘
                       │ REST + WebSocket
┌──────────────────────▼──────────────────────────────────────────┐
│                      Backend (FastAPI)                           │
│  Workflow Service | State Service | WebSocket Manager            │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       │  create session + stream events
┌──────────────────────▼──────────────────────────────────────────┐
│        Claude Managed Agents  (Anthropic-hosted orchestration)   │
│                                                                  │
│  🧭 Coordinator Agent  (multiagent — natively delegates)         │
│       ├── 👥 Crew Matching   (searchCrew, rankCrew)              │
│       ├── ✈️  Travel          (generateTicket, clearance)        │
│       ├── 📧 Notification    (sendMail × 4)                      │
│       └── 🛡️  Compliance      (validateDocuments)                │
│                                                                  │
│  custom tools (type: custom) resolve back in the FastAPI backend │
│  via agent.custom_tool_use → user.custom_tool_result             │
└─────────────────────────────────────────────────────────────────┘
```

> **Created once, reused per run.** The environment + 5 agents are provisioned by
> `python -m scripts.setup_managed_agents` (see Quick Start). Each workflow opens
> ONE coordinator session and reuses it across the sign-off → (wait for Sign On) →
> compliance phases.

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15, TypeScript, Tailwind CSS, Framer Motion |
| State Management | Zustand |
| Charts | Recharts |
| Backend | FastAPI, Python 3.12 |
| AI Agents | Anthropic SDK, Claude claude-sonnet-4-6 |
| Real-time | WebSockets (FastAPI + Next.js) |
| Database | PostgreSQL (required — crew data store) |
| Cache | Redis (optional) |
| Observability | Langfuse (optional) |

---

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 22+
- **PostgreSQL 14+** running locally (the backend reads/writes crew data from it)
- An Anthropic API key (`claude-sonnet-4-6` access)
- **Managed Agents beta access** on your Anthropic organization (`client.beta.agents` /
  `sessions` / `environments`). Without it, the one-time setup below returns a 403.
- `anthropic>=0.92.0` (pinned in `requirements.txt`)

### 1. Clone & Configure

```bash
cd /home/thinkpalm/CognixOneCrewMgmt
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### 2. Database (PostgreSQL) — Local Setup

The backend stores crew data in PostgreSQL. The committed code does **not** include a
database dump — each developer creates and seeds their own local database. (`backend/.env`
is gitignored, so set your own connection string below.)

```bash
# 1. Create the database (init_db creates TABLES on startup, but NOT the database itself):
psql -U postgres -c "CREATE DATABASE maritime_crew;"

# 2. Point the backend at your local Postgres — in backend/.env set DATABASE_URL
#    to match YOUR credentials (note the +asyncpg driver):
#    DATABASE_URL=postgresql+asyncpg://postgres:<your_password>@localhost:5432/maritime_crew
```

Tables are created automatically the first time the backend starts (`init_db()` runs
`Base.metadata.create_all`). To create **and** seed the table with the 40 sample crew
records in one step, run the seed script (see the next step, after deps are installed):

```bash
python -m scripts.seed_crew
```

> `seed_crew.py` drops, recreates, and reseeds the `crew` table — safe to re-run anytime
> you want a clean dataset. Skip it if you only want empty tables.

### 3. Start Backend

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy env
cp .env.example .env
# Add your ANTHROPIC_API_KEY and set DATABASE_URL (see step 2) in .env

# Create + seed the crew table (requires the database from step 2 to exist)
python -m scripts.seed_crew

# Provision the Managed Agents (environment + 5 agents) — RUN ONCE.
# Caches IDs to backend/managed_agents.json, which the app loads at startup.
python -m scripts.setup_managed_agents

# Run backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Backend API docs: http://localhost:8000/docs

> The setup step creates persisted agents on your Anthropic org and is required once
> before the first workflow run. Re-running it creates duplicates — only run it again
> if you want to recreate the agents. To change an agent's prompt/tools later, prefer
> `client.beta.agents.update` (versioned) over recreating.

### 4. Start Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run frontend
npm run dev
```

Frontend: http://localhost:3000

### 5. Docker (Optional — Full Stack)

Docker is the simplest path: the `postgres` service auto-creates the `maritime_crew`
database and the backend creates the tables on startup — no manual DB setup needed.

```bash
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY

docker-compose up --build

# Seed the crew table inside the running backend container (one-time):
docker-compose exec backend python -m scripts.seed_crew
```

---

## Usage Guide

### Sign-Off Flow

1. Open http://localhost:3000
2. The **Sign Off** tab shows 20 crew members currently onboard
3. Click **"Initiate Sign Off"** on any crew member
4. Watch the AI orchestration panel on the right:
   - Master Agent activates and routes to 3 agents in parallel
   - Crew Matching Agent searches and ranks replacement candidates
   - Travel Agent generates flight ticket and port clearance
   - Notification Agent sends 4 mock emails
5. The matched candidate is highlighted in the **Sign On** tab (auto-switched)

### Sign-On Flow

6. Switch to **Sign On** tab — see the AI-highlighted candidate with match score
7. Click **"Sign On"** on the recommended candidate
8. Compliance Agent runs full document validation:
   - Passport, Visa, Medical, STCW, Certifications
   - Port restrictions check
   - Generates compliance report with score
9. See compliance result in the Workflow Timeline panel

### Monitoring

- Click **Monitoring** in the nav to see:
  - KPI cards (success rate, tokens, cost, duration)
  - ROI metrics (time saved, cost savings, accuracy)
  - Charts: workflow activity, agent performance, quality radar
  - Agent-level metrics per agent

- Click **Workflow** to see:
  - Live agent orchestration graph
  - Full workflow history
  - Agent execution details per workflow

### Human Controls

In the Workflow Timeline panel:
- **Pause** — pause a running workflow
- **Resume** — resume a paused workflow
- **Cancel** — cancel the workflow entirely

---

## Project Structure

```
CognixOneCrewMgmt/
├── backend/
│   ├── agents/
│   │   ├── base_agent.py          # Abstract base — custom-tool provider (no Claude loop)
│   │   ├── master_agent.py        # Coordinator-session orchestrator (1 session/workflow)
│   │   ├── crew_matching_agent.py # Finds & ranks replacement crew (custom-tool logic)
│   │   ├── travel_agent.py        # Generates travel docs (custom-tool logic)
│   │   ├── notification_agent.py  # Mock email notifications (custom-tool logic)
│   │   ├── compliance_agent.py    # Document validation (custom-tool logic)
│   │   └── managed/
│   │       ├── registry.py        # Agent/coordinator configs + custom-tool routing
│   │       └── client.py          # ManagedAgentsClient: setup + SSE session driver
│   ├── scripts/
│   │   └── setup_managed_agents.py # One-time: create env + 5 agents → managed_agents.json
│   ├── api/
│   │   ├── routes/
│   │   │   ├── crew.py            # GET /crew/sign-on, /crew/sign-off
│   │   │   ├── workflow.py        # POST /workflow/sign-off, /workflow/sign-on
│   │   │   └── monitoring.py      # GET /monitoring/metrics, /monitoring/roi
│   │   └── websockets/
│   │       └── workflow_ws.py     # WebSocket connection manager
│   ├── services/
│   │   ├── workflow_service.py    # Orchestration + background tasks
│   │   └── state_service.py      # In-memory state (thread-safe)
│   ├── database/
│   │   └── models.py             # Pydantic models for all entities
│   ├── mock_data/
│   │   └── crew_data.py          # 20 sign-on + 20 sign-off crew members
│   ├── monitoring/
│   │   └── metrics.py            # Langfuse integration
│   ├── config.py                 # Settings (pydantic-settings)
│   └── main.py                   # FastAPI app entry point
│
├── frontend/
│   └── src/
│       ├── app/
│       │   ├── page.tsx           # Main dashboard (Sign On / Sign Off tabs)
│       │   ├── workflow/page.tsx  # Workflow visualization + agent graph
│       │   └── monitoring/page.tsx # Metrics, ROI, charts
│       ├── components/
│       │   ├── dashboard/
│       │   │   ├── SignOffTab.tsx  # Crew table + Initiate Sign Off button
│       │   │   └── SignOnTab.tsx   # Crew pool + AI match highlight + Sign On
│       │   ├── agents/
│       │   │   └── AgentOrchestrationPanel.tsx  # Live agent hierarchy
│       │   └── workflow/
│       │       └── WorkflowTimeline.tsx  # Timeline + compliance result
│       ├── hooks/
│       │   └── useWebSocket.ts    # Auto-reconnecting WebSocket hook
│       ├── store/
│       │   └── workflowStore.ts   # Zustand store + WebSocket event handler
│       ├── types/index.ts         # All TypeScript types
│       └── lib/
│           ├── api.ts             # Axios API client
│           └── utils.ts           # Helpers (statusBg, formatDuration, etc.)
│
├── ARCHITECTURE.md                # 6 Mermaid diagrams
├── docker-compose.yml
└── README.md
```

---

## Agent Design

### Coordinator Agent (the "Master") — Pure Orchestrator
- A persisted **multiagent coordinator** (`multiagent: {type: "coordinator", agents: [...]}`)
- Runs on Anthropic's orchestration layer and **natively delegates** to the specialists
  (each runs in its own session thread) — no `asyncio.gather` in our process
- Phase 1: delegates to Crew Matching + Travel + Notification in parallel, then the
  session goes idle awaiting human confirmation
- Phase 2: on the **same session**, delegates to Compliance + Notification
- `agents/master_agent.py` drives the session and extracts results; it **never performs
  business logic itself**

### Crew Matching Agent
Tools: `searchCrew()`, `rankCrew()`, `getCrewProfile()`

Scoring weights:
- Rank match: 40%
- Grade match: 20%
- Port proximity: 15%
- Valid documents: 15%
- Experience: 10%

### Travel Agent
Tools: `generateTicket()`, `generatePortClearance()`, `createTravelSummary()`

Generates: flight booking (with realistic airline/flight data), port clearance document, complete travel package.

### Notification Agent
Tools: `sendMail()`, `createNotificationLog()`

Sends to: Captain, Shore Manager, Sign-Off Crew, Sign-On Crew (4 notifications, mock only).

### Compliance Agent
Tools: `validateDocuments()`, `checkPortRestrictions()`, `generateComplianceReport()`

Checks: Passport validity, Visa status, Medical certificate, STCW certificates, Required certifications, Port restrictions, Seafarer's book, Flag state endorsement.

Output: Compliance score (0-100), Status (Passed/Warning/Failed), Recommendation.

---

## Harness Components

| Component | Implementation |
|-----------|---------------|
| **Agent Loop** | Anthropic-hosted (Managed Agents); driven via `client.beta.sessions.events.stream` |
| **Multi-agent** | Coordinator agent with `multiagent` roster of 4 specialists (session threads) |
| **Tools** | Custom tools (`type: custom`); resolved client-side via `agent.custom_tool_use` → `user.custom_tool_result` |
| **Context Management** | Stateful coordinator session (reused across both phases) + workflow `memory` |
| **State Management** | `StateService` (thread-safe asyncio.Lock) |
| **Memory** | Short-term: current workflow context; Long-term: past operations |
| **Guardrails** | Input validation, confidence thresholds, retry limits (3 max) |
| **Human-in-the-loop** | Pause/Resume/Cancel via `/workflow/{id}/control` |
| **Observability** | Langfuse tracing + in-memory metrics |
| **Real-time** | WebSocket broadcasts for every agent event |

---

## API Reference

### Crew
```
GET  /api/v1/crew/sign-on          # List available sign-on crew (20)
GET  /api/v1/crew/sign-off         # List onboard sign-off crew (20)
GET  /api/v1/crew/{crew_id}        # Get crew member details
```

### Workflow
```
POST /api/v1/workflow/sign-off     # Initiate sign-off {crew_id, reason}
POST /api/v1/workflow/sign-on      # Initiate sign-on {workflow_id, candidate_crew_id}
GET  /api/v1/workflow/             # List all workflows
GET  /api/v1/workflow/{id}         # Get workflow details
POST /api/v1/workflow/{id}/control # Pause/Resume/Cancel {action}
```

### Monitoring
```
GET  /api/v1/monitoring/metrics             # System metrics
GET  /api/v1/monitoring/roi                 # ROI calculations
GET  /api/v1/monitoring/workflows/active    # Active workflows
GET  /api/v1/monitoring/agents/status       # Per-agent status
```

### WebSocket
```
ws://localhost:8000/ws              # Global event stream
ws://localhost:8000/ws/{workflow_id} # Workflow-scoped events
```

---

## ROI Metrics

| Metric | Value |
|--------|-------|
| Manual sign-off time | ~8 hours |
| Automated sign-off time | ~2-5 minutes |
| Time saved per operation | ~7.9 hours |
| Manual cost estimate | $250/operation |
| AI cost per operation | ~$0.01-0.05 |
| Savings per operation | ~$245 |
| Crew match accuracy | 85-95% |
| Compliance accuracy | 90-98% |

---

## Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...        # Required (Managed Agents beta access)
MANAGED_ENVIRONMENT_ID=env_...      # Optional — else loaded from managed_agents.json
MANAGED_COORDINATOR_AGENT_ID=agent_... # Optional — else loaded from managed_agents.json
LANGFUSE_PUBLIC_KEY=                # Optional
LANGFUSE_SECRET_KEY=                # Optional
DATABASE_URL=postgresql+asyncpg://postgres:<pwd>@localhost:5432/maritime_crew  # Required (auto-set in Docker)
REDIS_URL=redis://...               # Optional (auto-set in Docker)
```

> `scripts/setup_managed_agents.py` writes `backend/managed_agents.json`; the app loads
> the environment + coordinator IDs from there automatically, so the `MANAGED_*` env vars
> are only needed for container deploys where that file isn't present.
