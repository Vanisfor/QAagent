import type { HistoryMessage, LLMSettings, LLMSettingsInput, ReasoningEffort, SessionSummary, StreamEvent } from "./types";

const API = "/api/v1";

interface BackendIssue {
  field?: string;
  message?: string;
  loc?: unknown[];
  msg?: string;
}

interface BackendErrorPayload {
  detail?: string | BackendIssue[];
  errors?: BackendIssue[];
}

const FIELD_LABELS: Record<string, string> = { email: "邮箱", password: "密码", username: "用户名" };
const DETAIL_FIELDS: Record<string, string> = {
  "Email already registered": "email",
  "Password must be at least 8 characters long": "password",
  "Password must contain at least one uppercase letter": "password",
  "Password must contain at least one lowercase letter": "password",
  "Password must contain at least one number": "password",
  "Password must contain at least one special character": "password",
};

function translateMessage(message: string): string {
  const normalized = message.replace(/^Value error,\s*/i, "");
  const translations: Record<string, string> = {
    "Email already registered": "该邮箱已经注册，请直接登录或更换邮箱。",
    "Incorrect email or password": "邮箱或密码不正确。",
    "Password must be at least 8 characters long": "密码至少需要 8 个字符。",
    "Password must contain at least one uppercase letter": "密码必须包含至少一个大写字母。",
    "Password must contain at least one lowercase letter": "密码必须包含至少一个小写字母。",
    "Password must contain at least one number": "密码必须包含至少一个数字。",
    "Password must contain at least one special character": "密码必须包含至少一个特殊字符。",
    "Field required": "此项为必填项。",
  };
  if (normalized.startsWith("value is not a valid email address")) return "请输入有效的邮箱地址。";
  return translations[normalized] ?? normalized;
}

export class ApiError extends Error {
  readonly fieldErrors: Record<string, string>;

  constructor(message: string, fieldErrors: Record<string, string> = {}) {
    super(message);
    this.name = "ApiError";
    this.fieldErrors = fieldErrors;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const payload = await response.json() as BackendErrorPayload;
    const issues = payload.errors ?? (Array.isArray(payload.detail) ? payload.detail : []);
    const fieldErrors: Record<string, string> = {};
    for (const issue of issues) {
      const field = issue.field ?? String(issue.loc?.at(-1) ?? "");
      const message = translateMessage(issue.message ?? issue.msg ?? "输入内容无效");
      if (field) fieldErrors[field] = message;
    }
    if (Object.keys(fieldErrors).length > 0) {
      const summary = Object.entries(fieldErrors)
        .map(([field, message]) => `${FIELD_LABELS[field] ?? field}：${message}`)
        .join("\n");
      return new ApiError(summary, fieldErrors);
    }
    if (typeof payload.detail === "string") {
      const message = translateMessage(payload.detail);
      const field = DETAIL_FIELDS[payload.detail];
      return new ApiError(message, field ? { [field]: message } : {});
    }
    return new ApiError(`请求失败（HTTP ${response.status}）`);
  } catch (reason) {
    if (reason instanceof ApiError) return reason;
    return new ApiError(`后端请求失败（HTTP ${response.status}），请确认 FastAPI 已在 8001 端口启动。`);
  }
}

export async function login(email: string, password: string): Promise<string> {
  const body = new URLSearchParams({ email, password, grant_type: "password" });
  const response = await fetch(`${API}/auth/login`, { method: "POST", body });
  if (!response.ok) throw await parseError(response);
  return (await response.json()).access_token;
}

export async function register(email: string, password: string, username: string): Promise<string> {
  const response = await fetch(`${API}/auth/register`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, username }),
  });
  if (!response.ok) throw await parseError(response);
  return (await response.json()).token.access_token;
}

export async function createSession(userToken: string): Promise<SessionSummary> {
  const response = await fetch(`${API}/auth/session`, {
    method: "POST", headers: { Authorization: `Bearer ${userToken}` },
  });
  if (!response.ok) throw await parseError(response);
  const data = await response.json();
  return { sessionId: data.session_id, name: data.name ?? "", token: data.token.access_token };
}

export async function listSessions(userToken: string): Promise<SessionSummary[]> {
  const response = await fetch(`${API}/auth/sessions`, {
    headers: { Authorization: `Bearer ${userToken}` },
  });
  if (!response.ok) throw await parseError(response);
  const data = await response.json() as Array<{ session_id: string; name: string; token: { access_token: string } }>;
  return data.map((session) => ({
    sessionId: session.session_id,
    name: session.name ?? "",
    token: session.token.access_token,
  })).reverse();
}

export async function getSessionMessages(sessionToken: string): Promise<HistoryMessage[]> {
  const response = await fetch(`${API}/chatbot/messages`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) throw await parseError(response);
  const data = await response.json() as { messages: HistoryMessage[] };
  return data.messages;
}

export async function getLLMSettings(userToken: string): Promise<LLMSettings> {
  const response = await fetch(`${API}/users/me/settings/llm`, {
    headers: { Authorization: `Bearer ${userToken}` },
  });
  if (!response.ok) throw await parseError(response);
  return response.json() as Promise<LLMSettings>;
}

export async function testLLMSettings(userToken: string, payload: LLMSettingsInput): Promise<void> {
  const response = await fetch(`${API}/users/me/settings/llm/test`, {
    method: "POST",
    headers: { Authorization: `Bearer ${userToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await parseError(response);
}

export async function saveLLMSettings(userToken: string, payload: LLMSettingsInput): Promise<LLMSettings> {
  const response = await fetch(`${API}/users/me/settings/llm`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${userToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await parseError(response);
  return response.json() as Promise<LLMSettings>;
}

export async function deleteLLMSettings(userToken: string): Promise<void> {
  const response = await fetch(`${API}/users/me/settings/llm`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${userToken}` },
  });
  if (!response.ok) throw await parseError(response);
}

export async function streamChat(
  token: string,
  question: string,
  effort: ReasoningEffort,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API}/chatbot/chat/stream`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ messages: [{ role: "user", content: question }], reasoning_effort: effort }),
    signal,
  });
  if (!response.ok || !response.body) throw await parseError(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((item) => item.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as StreamEvent);
    }
  }
}
