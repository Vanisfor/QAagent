import type { ReasoningEffort, StreamEvent } from "./types";

const API = "/api/v1";

async function parseError(response: Response): Promise<string> {
  try { return (await response.json()).detail ?? `请求失败（HTTP ${response.status}）`; }
  catch { return `后端请求失败（HTTP ${response.status}），请确认 FastAPI 已在 8001 端口启动。`; }
}

export async function login(email: string, password: string): Promise<string> {
  const body = new URLSearchParams({ email, password, grant_type: "password" });
  const response = await fetch(`${API}/auth/login`, { method: "POST", body });
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()).access_token;
}

export async function register(email: string, password: string, username: string): Promise<string> {
  const response = await fetch(`${API}/auth/register`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, username }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()).token.access_token;
}

export async function createSession(userToken: string): Promise<{ sessionId: string; token: string }> {
  const response = await fetch(`${API}/auth/session`, {
    method: "POST", headers: { Authorization: `Bearer ${userToken}` },
  });
  if (!response.ok) throw new Error(await parseError(response));
  const data = await response.json();
  return { sessionId: data.session_id, token: data.token.access_token };
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
  if (!response.ok || !response.body) throw new Error(await parseError(response));
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
