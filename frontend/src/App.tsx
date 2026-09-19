import { Link, Navigate, Route, Routes, matchPath, useLocation, useNavigate } from "react-router";

import { useAuth } from "./auth/AuthProvider";

import { ChatApp } from "./ChatApp";
import { AuthScreen } from "./components/auth/AuthScreen";

export default function App() {
  const auth = useAuth();
  const userToken = auth.token;
  const location = useLocation();
  const navigate = useNavigate();
  const requestedPath: unknown = location.state?.from;
  const returnPath = typeof requestedPath === "string" && matchPath("/chat/:sessionId?", requestedPath) ? requestedPath : "/chat";

  function logout() { void auth.logout(); }

  if (!auth.ready) return <main className="chat-empty"><p>正在恢复登录…</p></main>;
  if (auth.error && !userToken) return <main className="chat-empty"><p role="alert">{auth.error}</p><button className="secondary-button" onClick={auth.retry}>重试</button></main>;

  return <>{auth.error && <div className="account-error" role="alert">{auth.error}</div>}<Routes>
    <Route path="/" element={<Navigate to="/chat" replace />} />
    <Route path="/login" element={userToken ? <Navigate to={returnPath} replace /> : <AuthScreen onReady={(token) => { auth.login(token); navigate(returnPath, { replace: true }); }} />} />
    <Route path="/chat/:sessionId?" element={userToken ? <ChatApp key={auth.identityKey} userToken={userToken} onLogout={logout} /> : <Navigate to="/login" replace state={{ from: location.pathname }} />} />
    <Route path="*" element={<main className="chat-empty"><h1>页面不存在</h1><p>请检查地址，或返回聊天继续使用。</p><Link className="secondary-button" to="/chat" replace>返回聊天</Link></main>} />
  </Routes></>;
}
