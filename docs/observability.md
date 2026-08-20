# Observability

The application uses a self-contained tracing layer. It has no remote tracing
service and never records prompts, answers, tool arguments or document content.

## Trace data

Daily JSONL files are written to `TRACE_DIR` (default `logs/traces`). Recorded
fields are limited to performance, parent/child call IDs, provider token counts,
and sanitized errors with stable error codes.

View a trace in the terminal:

```bash
uv run python scripts/trace_view.py <trace_id>
```

## Configuration

```env
TRACING_ENABLED=true
TRACE_DIR=logs/traces
TRACE_FILE_RETENTION_DAYS=7
TRACE_SLOW_SPAN_THRESHOLD_MS=2000
```

Prometheus exposes aggregate span duration, status, token and error counters.
Trace IDs, user IDs, session IDs and error messages are never metric labels.

The HTTP `X-Request-ID` becomes the trace ID. Nested Agent, RAG, Tool, LLM,
streaming and memory spans inherit it through `ContextVar` state.
