# QA Agent Frontend

React + TypeScript dashboard for the FastAPI/LangGraph backend.

```bash
pnpm install
pnpm dev
```

The Vite development server runs on `http://localhost:3002` and proxies `/api`
to `http://localhost:8001`.

Features include authentication, session creation, POST/SSE chat streaming,
DeepSeek reasoning-effort controls, live reasoning and answer streams, token and
cache telemetry, pipeline stages, responsive layout, reduced-motion support,
and locally persisted animated background settings.
