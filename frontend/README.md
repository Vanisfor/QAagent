# QA Agent Frontend

React + TypeScript developer workspace for the FastAPI/LangGraph backend.

```bash
pnpm install
pnpm dev
```

The Vite development server runs on `http://localhost:3002` and proxies `/api`
to `http://localhost:8001`.

The UI uses a dark-first, documentation-inspired shell with semantic CSS design
tokens, desktop and mobile navigation, a real command menu, session history,
POST/SSE chat streaming, runtime telemetry, BYOK model settings, and locally
persisted appearance preferences. Motion for React is limited to layout and
state transitions; streamed tokens are never animated individually, and user
reduced-motion preferences are respected.
