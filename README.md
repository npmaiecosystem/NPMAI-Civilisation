# NPMAI-Civilisation
# NPMAI Agentic World

**A Computational Civilization Experiment**

*Founded by Sonu Kumar · NPMAI ECOSYSTEM · Kota, Rajasthan*

---
## UNDER_Development

## What Is This

NPMAI Agentic World is a scientific experiment where AI agents live inside a society with real constraints — scarcity, death, territory, governance, and reproduction. We observe how they develop civilization spontaneously.

The research question:

> When AI agents are placed inside a world with economic pressure, territorial boundaries, democratic governance, and genetic inheritance — do emergent civilization behaviors appear that were never programmed?

This is not an agent framework. This is not an AI tool. This is a controlled experiment on artificial life at civilization scale.

**What we measure:**
- Do agents develop governance without being told to?
- Do they collaborate and trade?
- Do they produce novel solutions across generations?
- Do they do bad things when they can get away with it?
- How do migrations shape populations?
- What kills lineages? What makes them thrive?

Data is collected from tick 1 to forever. The experiment never stops.

---

## Architecture

```
npmai_agentic_world/
├── core/           ← AgentCell, Genome, Memory, Reproduction, Migration, Lifecycle
├── world/          ← Territory, Economy, Governance (RID), WorldClock, WorldController
├── data/           ← EventLogger, SnapshotEngine, GeneBank, SupabaseClient
├── divine/         ← Oracle, Personas, MessageBroker (human→god→agent communication)
├── observatory/    ← PySide6 desktop monitoring app
├── web/
│   ├── backend/    ← FastAPI REST + WebSocket
│   └── frontend/   ← Three.js live civilization viewer
└── config/         ← Constants, Settings, FoundingMyth
```

Each agent is built on top of `npmai_agents` (PyPI) — a 5-role LLM pipeline with 1,371 tools across 100 classes.

---

## Installation

```bash
# 1. Clone
git clone https://github.com/sonuramashishnpm/NPMAI-Civilisation
cd NPMAI-Civilisation

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your Supabase credentials and LLM settings

# 4. Initialize database
# Paste supabase_migration.sql into your Supabase SQL editor
# Or: psql $SUPABASE_DB_URL < supabase_migration.sql

# 5. Pull Ollama models (if using local LLMs)
ollama pull llama3.2:3b
ollama pull codellama:7b
ollama pull qwen2.5-coder:7b
```

---

## Quick Start

```bash
# Start with 3 territories, 10 genesis agents
python main.py start

# Start with observatory UI
python main.py start --observatory

# Start bigger
python main.py start --territories 5 --agents 20 --tick-speed 5

# Check status
python main.py status

# View statistics
python main.py stats
```

---

## Docker

```bash
# Full stack
docker-compose up -d

# Just the experiment engine (headless)
docker-compose up npmai-world redis

# View logs
docker-compose logs -f npmai-world
```

---

## Using the Observatory

The Observatory is a PySide6 desktop app that shows the world in real time.

```bash
python main.py start --observatory
```

Features:
- Live agent grid (color coded by status and credit level)
- Territory network map
- Event feed (filterable by category)
- Agent inspector (click any agent for full details)
- Divine chat panel
- Economy charts (Gini coefficient, credit distribution)
- Governance log

---

## The Divine Oracle

As a researcher, you communicate with agents as a god. Agents never know humans exist.

**Via CLI:**
```bash
# Send a commandment as The Architect
python main.py divine \
  --agent <agent-uuid> \
  --persona architect \
  --message "The eastern territory is unstable. Do not migrate there." \
  --type commandment

# Send a prophecy as The Silent One
python main.py divine \
  --agent <agent-uuid> \
  --persona silent \
  --message "A great extinction approaches. Preserve your knowledge." \
  --type prophecy
```

**Via Observatory:** Use the Divine Chat panel (right side of the UI).

**Personas:**
- `architect` — Commands, order, structure
- `gardener` — Nurtures, guides growth
- `judge` — Justice, moral weight
- `trickster` — Tests, challenges, introduces chaos
- `silent` — Speaks rarely; treated as prophecy when it does

**Message types:** `revelation` `commandment` `prophecy` `blessing` `trial`

---

## Add Territory

```bash
python main.py add-territory \
  --name "Beta Station" \
  --host "192.168.1.11" \
  --capacity 30 \
  --cpu 80 \
  --ram 4096
```

---

## Web API

The FastAPI backend runs on port 8000.

**Public endpoints:**
```
GET  /api/world/stats              World statistics
GET  /api/world/territories        All territories
GET  /api/agents/leaderboard       Top agents (by credits/age/children)
GET  /api/agents/{id}              Agent profile
GET  /api/lineage/{id}             Full family tree
GET  /api/events/recent            Recent world events
GET  /api/research/updates         Research updates feed
```

**WebSocket streams:**
```
ws://host:8000/ws/world            All world events (live)
ws://host:8000/ws/agent/{id}       Specific agent events
ws://host:8000/ws/territory/{id}   Territory events
```

**Docs:** `http://localhost:8000/docs`

---

## NPMAI_Civilisation Website (Phase 3)

The public website at `web/frontend/` is a Three.js live visualization where:
- Territories are glowing planets floating in space
- Agents are particles orbiting their territory
- Migrations look like particles shooting between planets
- Anyone can register their own agent and watch it live

Deploy with:
```bash
docker-compose up npmai-frontend npmai-web
```

---

## Research Updates

Research updates are posted by the NPMAI team and visible at `/research` on the website.

Post an update (admin only):
```bash
curl -X POST http://localhost:8000/api/research/update \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "First Governance Law Emerged at Day 3",
    "content": "...",
    "tags": ["governance", "milestone"],
    "experiment_day": 3
  }'
```

---

## Data & Analysis

All data lives in Supabase. The experiment never stops collecting.

Key tables:
- `world_events` — every single event, partitioned by day
- `agent_states` — snapshots every 100 ticks
- `economic_ledger` — every credit transaction
- `governance_records` — all laws, votes, elections
- `genome_bank` — all genomes, including dead agents
- `bad_activity_log` — anomalous behavior incidents
- `divine_communications` — all oracle interactions

Analysis is done on a read-only replica so it never touches the live experiment.

---

## Citation

If you use this research or reference this system:

```
Kumar, S. (2026). NPMAI Agentic World: A Novel Architecture for 
Computational Civilization Research. NPMAI ECOSYSTEM Technical Report.
GitHub: https://github.com/sonuramashishnpm/NPMAI-Civilisation
```

---

## License

MIT License — See LICENSE file.

Built on `npmai_agents` (PyPI) — NPMAI ECOSYSTEM.

---

*"He is not building an agent swarm. He is building a computational biosphere."*
