# FastAPI LangGraph Agent Template

A production-ready template for building AI agent backends with FastAPI and LangGraph. Handles the hard parts — stateful conversations, long-term memory, tool calling, observability, rate limiting, auth — so you can focus on your agent logic.

**Built for AI engineers** who want a solid foundation, not a tutorial project.

---

## Official DeepSeek API

The agent uses the official China-region DeepSeek API through its
OpenAI-compatible Chat Completions protocol. Runtime credentials use
DeepSeek-specific environment variable names; no request is sent to OpenAI.

### Quick Setup

**Step 1 — Create an API key:** [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)

**Step 2 — Update `.env.development`:**

```env
DEEPSEEK_API_KEY=<your-deepseek-api-key>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_THINKING_ENABLED=false
DEFAULT_LLM_MODEL=deepseek-v4-flash
```

**Step 3 — Or use directly in code:**

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="deepseek-v4-flash",
    base_url="https://api.deepseek.com",
    api_key="<your-deepseek-api-key>",
    max_tokens=2000,
    extra_body={"thinking": {"type": "disabled"}},
)
```

Thinking is disabled by default because the current Agent tool loop does not yet
guarantee that DeepSeek's `reasoning_content` is preserved across tool-call
round trips. Enable it only after that path has its own online integration test.

---

## What's included

- **LangGraph** stateful agent with checkpointing, tool calling, and human-in-the-loop support
- **RAG knowledge base** — pgvector + free SiliconFlow embeddings, document ingestion CLI, web-search fallback
- **Long-term memory** via mem0 + pgvector — semantic search per user, cache-backed
- **LLM service** with circular model fallback, exponential backoff retries, and total timeout budget
- **Local JSONL tracing** for performance, call chains, tokens and errors; Prometheus + Grafana
- **JWT auth** with session management; rate limiting via slowapi
- **Alembic** migrations; optional Valkey/Redis cache layer
- **Structured logging** with request/session/user context on every line

## Quickstart

```bash
git clone <repo-url> my-agent && cd my-agent
cp .env.example .env.development   # fill in your keys
make install
make docker-up                     # starts API + PostgreSQL
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) to see the interactive API.

### React frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Open [http://localhost:3002](http://localhost:3002). The Vite development server
proxies `/api` to FastAPI on port 8000. The UI includes streamed answers and
reasoning, reasoning-effort controls, live token/cache telemetry, pipeline
status, responsive layouts, and locally persisted animated backgrounds.

> For local development without Docker see [docs/getting-started.md](docs/getting-started.md).

## Documentation

| Guide | What it covers |
|---|---|
| [Getting Started](docs/getting-started.md) | Prerequisites, local setup, first API call |
| [Architecture](docs/architecture.md) | System design, request flow, component diagrams |
| [Configuration](docs/configuration.md) | All environment variables with defaults |
| [Authentication](docs/authentication.md) | JWT flow, sessions, endpoint reference |
| [Database & Migrations](docs/database.md) | Schema, Alembic migrations, pgvector |
| [LLM Service](docs/llm-service.md) | Models, retries, fallback, timeout budget |
| [Memory](docs/memory.md) | mem0 long-term memory, cache layer |
| [RAG Knowledge Base](docs/rag.md) | RAG Q&A: document ingestion, pgvector retrieval, free embeddings |
| [Observability](docs/observability.md) | Local tracing, structured logging, Prometheus, profiling |
| [Evaluation](docs/evaluation.md) | Eval framework, custom metrics, reports |
| [Docker](docs/docker.md) | Docker, Compose, full monitoring stack |

## Project structure

```
app/
  api/v1/          # Route handlers
  core/
    langgraph/     # Agent graph + tools
    prompts/       # System prompt template
    cache.py       # Valkey/Redis + in-memory fallback
    config.py      # Settings
    middleware.py  # Metrics, logging context, profiling
    limiter.py     # Rate limiting
  models/          # SQLModel ORM models
  schemas/         # Pydantic request/response schemas
  services/        # LLM, database, memory services
alembic/           # Database migrations
evals/             # LLM evaluation framework
```

## Contributing

PRs welcome. Please read [docs/getting-started.md](docs/getting-started.md) to get your environment set up, then follow the coding conventions in [AGENTS.md](AGENTS.md).

Report security issues privately — see [SECURITY.md](SECURITY.md).

## License

See [LICENSE](LICENSE).

## FAQ

### General

**What is this template?**
A production-ready foundation for AI agent backends built on FastAPI + LangGraph. It bundles the components you'd otherwise wire up by hand: stateful conversations, long-term memory, tool calling, observability, rate limiting, and JWT auth.

**How does this differ from a basic LangGraph setup?**
The base LangGraph quickstart stops at "agent runs locally". This template adds Alembic migrations, mem0 + pgvector long-term memory, local JSONL tracing, Prometheus + Grafana dashboards, JWT sessions, slowapi rate limiting, structured logging with per-request context, and a circular-fallback LLM service.

### Setup & Configuration

**Do I need Docker?**
Recommended but not required. `make docker-up` starts the API + PostgreSQL together. For local-only setup see [docs/getting-started.md](docs/getting-started.md).

**Which LLM provider is configured by default?**
The agent uses the official DeepSeek endpoint with `deepseek-v4-flash`. Configure it through `DEEPSEEK_BASE_URL`, `DEEPSEEK_API_KEY`, and `DEFAULT_LLM_MODEL` in `.env.development`.

**How do I configure long-term memory?**
Long-term memory is self-hosted: mem0 runs in-process and persists into your existing PostgreSQL via pgvector. DeepSeek V4 Flash extracts memories through the official DeepSeek API, while BGE-M3 creates memory embeddings through SiliconFlow, so configure both API keys and enable pgvector. See [docs/memory.md](docs/memory.md) for details.

### Development

**How do I add a custom tool?**
Drop a LangChain `@tool`-decorated function in `app/core/langgraph/tools/` and register it in the `tools` list exported from that package. The agent picks it up on next start; no graph changes needed.

**How does the LLM service handle failures?**
Two layers: (1) per-call exponential-backoff retry via `tenacity`, (2) **circular fallback** — if the active model exhausts its retries, the service rotates to the next model in `LLMRegistry` and continues. A total timeout budget caps the whole call so latency stays bounded. See [docs/llm-service.md](docs/llm-service.md).

**Where are traces stored?**
The self-developed tracing layer writes daily JSONL files under `logs/traces`. It records only performance, call-chain IDs, token counters and sanitized errors.

### Troubleshooting

**The API won't start**
- Ensure PostgreSQL is running (`make docker-up` brings it up alongside the API)
- Confirm `.env.development` exists — copy from `.env.example` and fill in required keys
- Apply migrations: `make migrate`

**Memory / semantic search returns nothing**
- Verify the `pgvector` extension is enabled in your PostgreSQL instance
- Confirm `DEEPSEEK_API_KEY` and `SILICONFLOW_API_KEY` are valid
- Check `LONG_TERM_MEMORY_MODEL` and `LONG_TERM_MEMORY_EMBEDDER_MODEL` are set in `.env.development`

**Rate limiting is too aggressive**
Limits are defined in `app/core/limiter.py` (slowapi). Adjust per-route decorators or the default rate in that file. See [docs/configuration.md](docs/configuration.md) for the related env vars.
