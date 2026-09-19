let accessToken = "";
let refreshHandler: (() => Promise<string>) | null = null;
const conversations = new Map<string, string>();
const latestConversationTokens = new Map<string, string>();

export function configureAuth(token: string, refresh: (() => Promise<string>) | null) {
  accessToken = token;
  refreshHandler = refresh;
  if (!token) { conversations.clear(); latestConversationTokens.clear(); }
}

export function rememberConversation(id: string, token: string) {
  conversations.set(token, id);
  latestConversationTokens.set(id, token);
}

export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const bearer = headers.get("Authorization")?.replace("Bearer ", "");
  const isUserRequest = input.includes("/users/") || input.endsWith("/auth/sessions") || input.endsWith("/auth/session");
  if (bearer && isUserRequest && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const conversationId = bearer ? conversations.get(bearer) : undefined;
  if (conversationId && latestConversationTokens.has(conversationId)) headers.set("Authorization", `Bearer ${latestConversationTokens.get(conversationId)}`);
  let response = await fetch(input, { ...init, headers });
  if (response.status !== 401 || !bearer || !refreshHandler || init?.signal?.aborted) return response;
  const token = await refreshHandler();
  if (isUserRequest) headers.set("Authorization", `Bearer ${token}`);
  else {
    const id = conversations.get(bearer);
    if (!id) return response;
    const sessions = await fetch("/api/v1/auth/sessions", { headers: { Authorization: `Bearer ${token}` } });
    if (!sessions.ok) return response;
    const available = await sessions.json() as Array<{ session_id: string; token: { access_token: string } }>;
    const selected = available.find((session) => session.session_id === id);
    if (!selected) return response;
    rememberConversation(id, selected.token.access_token);
    headers.set("Authorization", `Bearer ${selected.token.access_token}`);
  }
  response = await fetch(input, { ...init, headers });
  return response;
}
