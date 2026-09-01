export type ReasoningEffort = "off" | "high" | "max";
export type StreamEventType =
  | "meta" | "stage" | "reasoning_delta" | "answer_delta"
  | "tool_call" | "tool_result"
  | "rag_plan" | "rag_evaluate"
  | "usage" | "cache" | "error" | "done";

export interface StreamEvent {
  type: StreamEventType;
  content: string;
  done: boolean;
  data: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
}

export interface TokenUsage {
  input: number;
  output: number;
  total: number;
  reasoning: number;
  exact: boolean;
}

export interface StageItem {
  stage: string;
  status: "started" | "completed" | "failed";
  elapsedMs?: number;
}

export interface CacheStats { hits: number; misses: number; hitRate: number; scope: string }

export interface RunStep {
  id: string;
  kind: "memory" | "tool" | "llm" | "rag" | "done" | "error";
  title: string;
  detail?: string;
  status: "running" | "done" | "error";
}

export interface SessionSummary {
  sessionId: string;
  name: string;
  token: string;
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface LLMSettings {
  configured: boolean;
  provider: "deepseek";
  model: string;
  base_url: string;
  api_key_masked: string | null;
  temperature: number;
  max_tokens: number;
  thinking_enabled: boolean;
  validated_at: string | null;
}

export interface LLMSettingsInput {
  provider: "deepseek";
  model: string;
  base_url: string;
  api_key?: string;
  temperature: number;
  max_tokens: number;
  thinking_enabled: boolean;
}
