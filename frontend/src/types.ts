export type ReasoningEffort = "off" | "high" | "max";
export type StreamEventType =
  | "meta" | "stage" | "reasoning_delta" | "answer_delta"
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
