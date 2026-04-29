<p align="center">
  <img src="./agentd-github-banner.png" alt="AgentD" />
</p>

# AgentD

An enterprise-oriented agent runtime with user, skill, and knowledge management built in. AgentD treats agents as a long-running service rather than a chat session — sessions persist, tool calls survive process restarts, and the runtime guards against the rough edges of real-world LLM providers.

> **Status**: `v0.4.3` — Provider Reasoning & Tool Loop Stability Upgrade (2026-04-27)
> **Stack**: Python / FastAPI / LangGraph / PostgreSQL · Next.js / TypeScript · OpenAI-compatible providers (DeepSeek / Qwen / GLM / MiniMax / local llama.cpp)

---

## What AgentD Is

AgentD is what you get when you take "an agent that calls tools" and treat it like a deployed product:

- **Multi-tenant by default** — users, sessions, skills, knowledge, and admin are first-class.
- **DB-backed session persistence** — messages, runs, and full LangGraph checkpoints live in PostgreSQL (15 Alembic migrations, schema `v015`). A run can crash mid-tool-call and resume on a different worker.
- **Scheduler + worker pool for real concurrency** — `SELECT … FOR UPDATE SKIP LOCKED` claim on `agent_runs` with session-level mutual exclusion, lease-expiry recovery for dead workers, and a session-level interrupt flag that aborts at tool boundaries. Multi-worker safe out of the box.
- **Folder-based user sandbox** — every user gets `{user_root}/sessions/{session_id}/` as a hard workspace boundary; bash and file tools reject `..` escapes and absolute paths outside the sandbox; subagents inherit the parent session's directory but stay independent at the message level.
- **Three-layer runtime env** — `user env` (user venv) / `skill env` (skill-specific venv from `.agentd/skills.env`) / `service env` (isolated, no inherited PATH or Python) — chosen automatically per call site.
- **Independent CLI service processes for skill scripts** — skills with heavy script dependencies don't run inline. They're declared in a CLI registry, executed as isolated subprocesses with `owner_skill` gating, and addressable from both foreground (`bash`) and background (`launch_detached_process`) paths.
- **Provider-aware runtime** — handles strict thinking-model providers (DeepSeek tool-call adjacency, reasoning side-channel), streaming-incompatible local models, and provider timeouts as runtime concerns, not bugs to wish away.
- **Skill system** — filesystem-installed packages with their own runtime env; agents discover, load, and execute them with permission gates and a single filesystem source of truth.
- **Knowledge base** — catalog-first retrieval routing (`catalog → search → read`), citation-aware (`[N]` inline + `source_refs`), permission-filtered, with VLM-driven metadata extraction.
- **Workbench frontend** — full Next.js workbench: chat, panels, file preview, task output, knowledge hub, skill square, admin.

If you've used something like Claude Code or OpenCode and wished you could run that pattern *as a platform* — for your team, with permissions and audit, on your own models — AgentD is aiming at that shape.

---

## Highlights

### Persistence (DB-backed sessions)
- `sessions` / `messages` / `agent_runs` tables in PostgreSQL; cascade delete on user removal
- LangGraph state stored via `AsyncPostgresSaver` (psycopg3 pool) — no filesystem dependency for graph state
- Message order is deterministic (auto-incrementing `seq` column); resume reconstructs history from DB
- 15 Alembic migrations (001 → 015); migrations apply on backend startup
- `.agentd/` per-session metadata holds only rolling artifacts: `session_memory.md`, `session_policy.json`, `task_plan.json`, `context_summary.json`

### Scheduler & Worker Pool (multi-worker concurrency)
- `claim_run_concurrent()` uses `SELECT … FOR UPDATE SKIP LOCKED` on `agent_runs` to claim queued runs without races
- Session-level mutex via subquery exclusion — same session never executes twice across workers
- Per-worker memory fast-path (`local_exclude` set) avoids DB round-trips on hot loops
- `lease_expires_at` (5-minute default) + `reclaim_expired_runs()` recovers from dead workers
- `interrupt_requested_at` flag in `sessions` table — checked at every tool boundary for clean abort
- `max_concurrent` (default 4) asyncio tasks per worker; SIGTERM drains active runs

### Runtime
- Three-layer runtime env: `user env` / `skill env` / `service env` (resolved by `runtime_env.py`)
- Foreground `bash` and background `launch_detached_process` share env semantics
- HITL permission gates (`ask` / `allow` / `fsd`) with resume support; permission requests persisted by `tool_call_id` for replay
- Hard compaction with `session_memory.md` (11 fixed sections, VLM-driven incremental patch)
- Microcompact tool-result capsules (preserves provider tool-call adjacency)
- Per-run tool dedup guard with canonical argument tracking

### Providers
- OpenAI-compatible adapter with reasoning side-channel (DeepSeek `reasoning_content`, Qwen, GLM, MiniMax)
- Streaming on/off per model (`capabilities.streaming` for llama.cpp local models)
- Provider payload assertion before request — fails locally, not at the provider
- Checkpoint adjacency repair when transcripts arrive broken

### Tools (16)
`bash` · `file_read` · `file_write` · `file_edit` · `file_inspect` · `skill` · `list_dir` · `glob` · `grep` · `planning` · `todo_update` · `launch_detached_process` · `launch_subagent` · `knowledge_catalog` · `knowledge_search` · `knowledge_read`

Permissions: `bash` / `file_write` / `file_edit` / `launch_detached_process` / `launch_subagent` → `ask`; rest → `allow`.

### Knowledge
- `knowledge/files/` + `knowledge/raw/` two-layer storage
- YAML frontmatter metadata, owner + public/private permission filter
- Catalog-first hard precondition (no bypassing into `search`/`read`)
- Citation-aware: inline `[N]` + persisted `source_refs` + `ref_index`
- System-level import (POST `/api/knowledge/import`) with progress persistence

### Skills
- Filesystem-installed packages (`agentd/skills/*`)
- Skill metadata injected into prompt; model loads on demand via `skill load`
- Single source of truth (filesystem); profile / square / sidebar always agree
- Skill-owned CLI services supported (`owner_skill: <name>`)

### Frontend
- Next.js 14 App Router, full visual system (v0.4.2)
- Panel content protocol (`structured` / `html_sandbox`) with tabs
- Workspace file tree + upload + preview
- Knowledge hub, skill square, admin, model config, diagnostics
- SSE streaming with reasoning, tool calls, panel updates, task lifecycle, knowledge import progress

### CLI Service Integration (script-heavy skills run as independent processes)
Some skills carry heavy script dependencies (OCR engines, classification models, file-conversion toolchains). Running them inline would pollute the agent's runtime; AgentD runs them as **independent CLI service processes**:

- Read-only registry: `agentd/cli_registry.json` (gitignored, local truth) + `cli_registry.example.json` template
- Each entry declares: `entrypoint` (absolute path), `cwd_policy` (e.g. `session_dir`), `env_kind` (`isolated` = empty inherited env), `supports_detached`, `owner_skill` (which skill is allowed to invoke; `*` = open)
- `registry.resolve_command()` swaps the service name for the absolute entrypoint at execution time
- `env_kind="isolated"` runs services with empty `env_bin` and `path_prefix` — clean process boundary, no leakage from user/skill venvs
- Foreground via `bash` (synchronous, blocks the run); background via `launch_detached_process` (returns immediately, streams stdout to `.agentd/tasks/{task_id}/stdout.log`)
- Sample services: `employee-risk-classifier`, `pdf-rename-cli`, `ocr-cli`

### User Sandbox & Permission Boundary (folder-based)
- Per-user root: `{user_root}/sessions/` (all sessions), `{user_root}/skills/` (installed skill catalog)
- Per-session workspace: `{user_root}/sessions/{session_id}/` is the hard boundary
- `bash` / `file_*` tools reject paths outside the workspace via regex (absolute paths, `../` escapes); blacklist blocks `rm -rf /`, `sudo`, fork bombs
- Subagents inherit the parent session's directory but keep their own message stream; metadata in `.agentd/child_session_meta.json` (parent_session_id, allowed_tools, resolved_tools)
- Permission system: `SessionPolicy` from `.agentd/session_policy.json`; modes `manual` (HITL ask), `autopilot` (rule-based pre-approval), `fsd` (auto-allow-all); rules match on tool + criteria like `exact_command` or `any_path_within_session`
- Permission requests persisted in `permission_requests` table keyed by `tool_call_id` — survives checkpoint resume for audit and replay

---

## Architecture

```
┌─────────────────────────┐
│  web/  (Next.js)        │  Chat · Panels · Knowledge · Skills · Admin
└────────────┬────────────┘
             │ SSE + REST
┌────────────┴────────────┐
│  agentd/  (FastAPI)     │  Auth · Sessions · Permissions · Models · Knowledge
│  ├── agent/             │  Runtime · Executor · Microcompact · Provider Reasoning
│  ├── tools/             │  16 tools + dedup guard + canonical args
│  ├── skills/            │  Skill registry + runtime env
│  └── core/              │  CLI registry · Checkpointer · Diagnostics
└────────────┬────────────┘
             │
┌────────────┴────────────┐
│  PostgreSQL (v015)      │  Sessions · Messages · Tasks · Knowledge · Checkpoints
└─────────────────────────┘
```

---

## Quick Start

```bash
# Backend
cd agentd
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # configure DATABASE_URL, model providers
python -m agentd.main

# Frontend
cd web
npm install
cp .env.local.example .env.local  # configure NEXT_PUBLIC_API_BASE
npm run dev

# Optional: CLI service registry
cp agentd/cli_registry.example.json agentd/cli_registry.json
# edit entrypoint paths to absolute paths on your machine
```

DB schema is at `v015`. Migrations run on backend startup.

---

## Release Line

| Version | Theme | Date |
|---------|-------|------|
| `v0.4.3` | Provider Reasoning & Tool Loop Stability | 2026-04-27 |
| `v0.4.2` | Output Contract & UI Experience Upgrade | 2026-04-26 |
| `v0.4.1` | Phase 7 Runtime Stability + CLI Service + Routing Tightening | 2026-04-11 |
| `v0.4.0` | Phase P1-P6 Beta Readiness / Task & Knowledge Workbench | 2026-04-06 |
| `v0.3.1` | Runtime Continuity + Skill Runtime + Compaction + File Understanding + VLM | 2026-03-31 |
| `v0.3.0` | Tool Upgrade + Skills Package Management + Frontend | 2026-03-21 |

See [GitHub Releases](https://github.com/edisonhsu9919/AgentD/releases) for full notes.

---

## What's Next

`v0.4.4 — Agent Runtime Core Hardening`

- Provider timeout recovery (retry from `checkpoint.next=["model"]`)
- Unified exception diagnostics
- Executor responsibility split (`RunController` / `CheckpointManager` / `ProviderAdapter` / `RecoveryPolicy` / `DiagnosticsRecorder`)
- Runtime state machine cleanup

---

## License

To be finalized — see open discussion in this repo. Pending decision, treat the code as "source-available, no usage rights granted." A formal `LICENSE` file will land before any tagged release after `v0.4.3` is published as public.
