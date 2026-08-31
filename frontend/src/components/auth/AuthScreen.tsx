import { useState, type FormEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, BrainCircuit, KeyRound, Radio, ShieldCheck } from "lucide-react";

import { ApiError, createSession, listSessions, login, register } from "../../api";
import type { SessionSummary } from "../../types";

interface AuthScreenProps {
  onReady: (userToken: string, session: SessionSummary) => void;
}

export function AuthScreen({ onReady }: AuthScreenProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const reduceMotion = useReducedMotion();

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setFieldErrors({});
    try {
      const userToken = mode === "login" ? await login(email, password) : await register(email, password, username);
      const existingSessions = await listSessions(userToken);
      const session = existingSessions[0] ?? await createSession(userToken);
      sessionStorage.setItem("qa-user-token", userToken);
      sessionStorage.setItem("qa-session-token", session.token);
      sessionStorage.setItem("qa-session-id", session.sessionId);
      onReady(userToken, session);
    } catch (reason) {
      if (reason instanceof ApiError && Object.keys(reason.fieldErrors).length > 0) setFieldErrors(reason.fieldErrors);
      else setError(reason instanceof Error ? reason.message : "连接失败");
    } finally { setBusy(false); }
  }

  function switchMode(next: "login" | "register") { setMode(next); setError(""); setFieldErrors({}); }

  return <main className="auth-page">
    <motion.section className="auth-intro" initial={{ opacity: 0, y: reduceMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0.1 : 0.28 }}>
      <a className="auth-brand" href="/"><span><BrainCircuit size={20} /></span>QA Agent</a>
      <p className="eyebrow">AGENT WORKSPACE</p>
      <h1>让知识与模型，<br />在一个工作区协同。</h1>
      <p className="auth-lede">面向开发者的 LangGraph 工作台。连接私有模型配置、知识检索与实时执行状态。</p>
      <ul className="auth-capabilities"><li><Radio size={16} /><span><strong>Streaming runtime</strong>逐步呈现真实 Agent 状态</span></li><li><KeyRound size={16} /><span><strong>Private BYOK</strong>用户凭据加密保存</span></li><li><ShieldCheck size={16} /><span><strong>Content-free traces</strong>业务内容不进入追踪</span></li></ul>
    </motion.section>
    <motion.section className="auth-panel" initial={{ opacity: 0, y: reduceMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0.1 : 0.28, delay: reduceMotion ? 0 : 0.04 }}>
      <div className="auth-tabs" role="tablist" aria-label="认证方式"><button className={mode === "login" ? "active" : ""} onClick={() => switchMode("login")} role="tab" aria-selected={mode === "login"}>登录</button><button className={mode === "register" ? "active" : ""} onClick={() => switchMode("register")} role="tab" aria-selected={mode === "register"}>注册</button></div>
      <header><p className="eyebrow">{mode === "login" ? "WELCOME BACK" : "CREATE WORKSPACE"}</p><h2>{mode === "login" ? "继续你的会话" : "创建你的账户"}</h2><p>{mode === "login" ? "登录后恢复历史会话和个人模型配置。" : "注册后即可配置自己的 DeepSeek API。"}</p></header>
      <form onSubmit={submit}>
        {mode === "register" && <label htmlFor="auth-username">用户名<input id="auth-username" value={username} onChange={(event) => setUsername(event.target.value)} aria-invalid={Boolean(fieldErrors.username)} aria-describedby={fieldErrors.username ? "auth-username-error" : undefined} required />{fieldErrors.username && <small id="auth-username-error" className="field-error">{fieldErrors.username}</small>}</label>}
        <label htmlFor="auth-email">邮箱<input id="auth-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} aria-invalid={Boolean(fieldErrors.email)} aria-describedby={fieldErrors.email ? "auth-email-error" : undefined} required />{fieldErrors.email && <small id="auth-email-error" className="field-error">{fieldErrors.email}</small>}</label>
        <label htmlFor="auth-password">密码<input id="auth-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? "auth-password-error" : mode === "register" ? "auth-password-hint" : undefined} required />{fieldErrors.password ? <small id="auth-password-error" className="field-error">{fieldErrors.password}</small> : mode === "register" && <small id="auth-password-hint" className="form-hint">至少 8 位，并包含大小写字母、数字和特殊字符。</small>}</label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary-button auth-submit" disabled={busy}>{busy ? "连接中…" : mode === "login" ? "登录并继续" : "注册并开始"}<ArrowRight size={16} /></button>
      </form>
    </motion.section>
  </main>;
}
