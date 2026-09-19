import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { configureAuth } from "./transport";

interface AuthState {
  token: string;
  identityKey: string;
  ready: boolean;
  error: string;
  login: (token: string) => void;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState(() => sessionStorage.getItem("qa-user-token") ?? "");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<Promise<string> | null>(null);
  const generation = useRef(0);

  const login = useCallback((value: string) => {
    sessionStorage.setItem("qa-user-token", value);
    setToken(value); setError("");
  }, []);

  const refresh = useCallback(() => {
    if (pending.current) return pending.current;
    const current = generation.current;
    pending.current = (async () => {
      const response = await fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin", headers: { "X-QA-Auth": "1" } });
      if (!response.ok) {
        if (response.status === 401 && current === generation.current) {
          sessionStorage.removeItem("qa-user-token"); setToken("");
          configureAuth("", null);
        }
        throw new Error(response.status === 401 ? "登录已失效，请重新登录。" : "无法恢复登录，请重试。");
      }
      const data = await response.json() as { access_token: string };
      if (current !== generation.current) throw new Error("登录状态已变更。");
      login(data.access_token);
      return data.access_token;
    })().finally(() => { pending.current = null; });
    return pending.current;
  }, [login]);

  const retry = useCallback(() => {
    setReady(false); setError("");
    void refresh().catch((reason: Error) => { if (!reason.message.includes("失效")) setError(reason.message); }).finally(() => setReady(true));
  }, [refresh]);

  useEffect(() => { retry(); }, [retry]);
  useEffect(() => { configureAuth(token, refresh); }, [token, refresh]);

  async function logout() {
    setError("");
    try {
      if (pending.current) await pending.current.catch(() => undefined);
      const response = await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin", headers: { "X-QA-Auth": "1" } });
      if (!response.ok) throw new Error("退出失败，请重试。");
      generation.current += 1;
      for (const key of ["qa-user-token", "qa-session-token", "qa-session-id"]) sessionStorage.removeItem(key);
      configureAuth("", null); setToken("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "退出失败，请重试。"); }
  }

  let identityKey = "";
  try { identityKey = String(JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).sub); } catch { identityKey = token; }
  return <AuthContext.Provider value={{ token, identityKey, ready, error, login, logout, retry }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider is required");
  return value;
}
