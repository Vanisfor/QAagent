import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, BrainCircuit, Check, ChevronDown, CircleStop, Gauge, LogOut,
  MessageSquarePlus, Palette, Send, Settings as SettingsIcon, Sparkles, Zap,
} from "lucide-react";
import { ApiError, createSession, getLLMSettings, getSessionMessages, listSessions, login, register, streamChat } from "./api";
import { BackgroundStudio, defaultBackground, type BackgroundSettings } from "./BackgroundStudio";
import { MarkdownMessage } from "./MarkdownMessage";
import { ModelSettingsDrawer } from "./ModelSettingsDrawer";
import type { CacheStats, ChatMessage, LLMSettings, ReasoningEffort, SessionSummary, StageItem, StreamEvent, TokenUsage } from "./types";

const uid = () => crypto.randomUUID();

function Background({ settings }: { settings: BackgroundSettings }) {
  return (
    <div
      className={`dynamic-background bg-${settings.preset}`}
      style={{ "--accent": settings.accent, "--intensity": settings.intensity / 100, "--speed": `${14 - settings.speed / 10}s` } as React.CSSProperties}
      aria-hidden="true"
    >{settings.preset === "custom" && settings.imageDataUrl && <div className="custom-background-image" style={{ backgroundImage: `url(${settings.imageDataUrl})`, opacity: settings.intensity / 100 }} />}<div className="background-orb orb-one" /><div className="background-orb orb-two" /></div>
  );
}

function AuthScreen({ onReady }: { onReady: (userToken: string, session: SessionSummary) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [username, setUsername] = useState(""); const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({}); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setFieldErrors({});
    try {
      const userToken = mode === "login" ? await login(email, password) : await register(email, password, username);
      const existingSessions = await listSessions(userToken);
      const session = existingSessions[0] ?? await createSession(userToken);
      sessionStorage.setItem("qa-user-token", userToken); sessionStorage.setItem("qa-session-token", session.token); sessionStorage.setItem("qa-session-id", session.sessionId);
      onReady(userToken, session);
    } catch (reason) {
      if (reason instanceof ApiError && Object.keys(reason.fieldErrors).length > 0) setFieldErrors(reason.fieldErrors);
      else setError(reason instanceof Error ? reason.message : "连接失败");
    } finally { setBusy(false); }
  }
  return <main className="auth-shell"><div className="auth-card spotlight-card">
    <div className="brand-mark"><BrainCircuit size={25} /><span>QA Agent</span></div>
    <p className="eyebrow">DEEPSEEK · RAG · LIVE OBSERVABILITY</p>
    <h1>{mode === "login" ? "欢迎回来" : "创建你的工作区"}</h1>
    <p className="muted">连接知识、推理过程和实时性能指标。</p>
    <form onSubmit={submit}>
      {mode === "register" && <label htmlFor="auth-username">用户名<input id="auth-username" value={username} onChange={(e) => setUsername(e.target.value)} aria-invalid={Boolean(fieldErrors.username)} aria-describedby={fieldErrors.username ? "auth-username-error" : undefined} required />{fieldErrors.username && <small id="auth-username-error" className="field-error">{fieldErrors.username}</small>}</label>}
      <label htmlFor="auth-email">邮箱<input id="auth-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={Boolean(fieldErrors.email)} aria-describedby={fieldErrors.email ? "auth-email-error" : undefined} required />{fieldErrors.email && <small id="auth-email-error" className="field-error">{fieldErrors.email}</small>}</label>
      <label htmlFor="auth-password">密码<input id="auth-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? "auth-password-error" : mode === "register" ? "auth-password-hint" : undefined} required />{fieldErrors.password ? <small id="auth-password-error" className="field-error">{fieldErrors.password}</small> : mode === "register" && <small id="auth-password-hint" className="form-hint">至少 8 位，并包含大小写字母、数字和特殊字符。</small>}</label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="primary-button" disabled={busy}>{busy ? "连接中…" : mode === "login" ? "登录并开始" : "注册并开始"}</button>
    </form>
    <button className="text-button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); setFieldErrors({}); }}>{mode === "login" ? "没有账号？注册" : "已有账号？登录"}</button>
  </div></main>;
}

function MetricCard({ title, value, detail, icon }: { title: string; value: string; detail: string; icon: React.ReactNode }) {
  return <section className="metric-card spotlight-card"><div className="metric-label">{icon}{title}</div><strong>{value}</strong><span>{detail}</span></section>;
}

function App() {
  const [userToken, setUserToken] = useState(() => sessionStorage.getItem("qa-user-token") ?? "");
  const [sessionToken, setSessionToken] = useState(() => sessionStorage.getItem("qa-session-token") ?? "");
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem("qa-session-id") ?? "");
  const [sessions, setSessions] = useState<SessionSummary[]>([]); const [sessionLoading, setSessionLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]); const [input, setInput] = useState("");
  const [effort, setEffort] = useState<ReasoningEffort>("off"); const [streaming, setStreaming] = useState(false);
  const [stages, setStages] = useState<StageItem[]>([]); const [usage, setUsage] = useState<TokenUsage>({ input: 0, output: 0, total: 0, reasoning: 0, exact: false });
  const [cache, setCache] = useState<CacheStats>({ hits: 0, misses: 0, hitRate: 0, scope: "instance" });
  const [llmSettings, setLLMSettings] = useState<LLMSettings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [background, setBackground] = useState<BackgroundSettings>(() => {
    try { return JSON.parse(localStorage.getItem("qa-agent-background-v1") ?? "null") ?? defaultBackground; } catch { return defaultBackground; }
  });
  const [backgroundPersistenceError, setBackgroundPersistenceError] = useState("");
  const [studioOpen, setStudioOpen] = useState(false); const [reasoningOpen, setReasoningOpen] = useState(true);
  const controller = useRef<AbortController | null>(null); const endRef = useRef<HTMLDivElement>(null);
  const sessionLoadSequence = useRef(0);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => {
    try { localStorage.setItem("qa-agent-background-v1", JSON.stringify(background)); setBackgroundPersistenceError(""); }
    catch { setBackgroundPersistenceError("浏览器存储空间不足，当前背景可能无法在刷新后保留。"); }
  }, [background]);
  useEffect(() => {
    if (!userToken) { setLLMSettings(null); return; }
    let cancelled = false;
    void getLLMSettings(userToken).then((value) => {
      if (cancelled) return;
      setLLMSettings(value);
      if (!value.configured) setSettingsOpen(true);
    }).catch((reason) => {
      if (!cancelled) setMessages([{ id: uid(), role: "assistant", content: `无法读取模型设置：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    });
    return () => { cancelled = true; };
  }, [userToken]);
  useEffect(() => {
    if (!userToken) { setSessions([]); return; }
    let cancelled = false;
    void (async () => {
      try {
        let available = await listSessions(userToken);
        if (available.length === 0) available = [await createSession(userToken)];
        if (cancelled) return;
        setSessions(available);
        const selected = available.find((session) => session.sessionId === sessionId) ?? available[0];
        await activateSession(selected, true);
      } catch (reason) {
        if (!cancelled) setMessages([{ id: uid(), role: "assistant", content: `无法加载历史会话：${reason instanceof Error ? reason.message : "未知错误"}` }]);
      }
    })();
    return () => { cancelled = true; };
  }, [userToken]);
  const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  const estimatedOutput = useMemo(() => Math.ceil((latestAssistant?.content.length ?? 0) / 3.5), [latestAssistant]);

  function handleEvent(event: StreamEvent, assistantId: string) {
    if (event.type === "reasoning_delta" || event.type === "answer_delta") {
      setMessages((current) => current.map((message) => message.id === assistantId ? {
        ...message,
        content: event.type === "answer_delta" ? message.content + event.content : message.content,
        reasoning: event.type === "reasoning_delta" ? (message.reasoning ?? "") + event.content : message.reasoning,
      } : message));
    } else if (event.type === "stage") {
      const stage = String(event.data.stage ?? "agent"); const status = String(event.data.status ?? "started") as StageItem["status"];
      setStages((current) => [...current.filter((item) => item.stage !== stage), { stage, status, elapsedMs: Number(event.data.elapsed_ms ?? 0) || undefined }]);
    } else if (event.type === "usage") {
      const data = event.data;
      setUsage({ input: Number(data.input_tokens ?? data.input ?? 0), output: Number(data.output_tokens ?? data.output ?? 0), total: Number(data.total_tokens ?? data.total ?? 0), reasoning: Number((data.output_token_details as Record<string, unknown> | undefined)?.reasoning ?? data.reasoning ?? 0), exact: true });
    } else if (event.type === "cache") {
      setCache({ hits: Number(event.data.hits ?? 0), misses: Number(event.data.misses ?? 0), hitRate: Number(event.data.hit_rate ?? 0), scope: String(event.data.scope ?? "instance") });
    }
  }

  async function send() {
    const question = input.trim(); if (!question || streaming || sessionLoading) return;
    if (llmSettings?.configured === false) { setSettingsOpen(true); return; }
    const assistantId = uid(); setInput(""); setStages([]); setUsage({ input: 0, output: 0, total: 0, reasoning: 0, exact: false });
    setMessages((current) => [...current, { id: uid(), role: "user", content: question }, { id: assistantId, role: "assistant", content: "", reasoning: "" }]);
    setStreaming(true); controller.current = new AbortController();
    try { await streamChat(sessionToken, question, effort, (event) => handleEvent(event, assistantId), controller.current.signal); }
    catch (reason) { if (!(reason instanceof DOMException && reason.name === "AbortError")) setMessages((current) => current.map((m) => m.id === assistantId ? { ...m, content: `请求失败：${reason instanceof Error ? reason.message : "未知错误"}` } : m)); }
    finally {
      setStreaming(false);
      void listSessions(userToken).then(setSessions).catch(() => undefined);
    }
  }
  async function activateSession(session: SessionSummary, loadHistory: boolean) {
    const sequence = ++sessionLoadSequence.current;
    controller.current?.abort(); setSessionLoading(true);
    sessionStorage.setItem("qa-session-id", session.sessionId); sessionStorage.setItem("qa-session-token", session.token);
    setSessionId(session.sessionId); setSessionToken(session.token); setStages([]);
    setUsage({ input: 0, output: 0, total: 0, reasoning: 0, exact: false });
    setMessages([]);
    if (!loadHistory) { setMessages([]); setSessionLoading(false); return; }
    try {
      const history = await getSessionMessages(session.token);
      if (sequence !== sessionLoadSequence.current) return;
      setMessages(history.map((message) => ({ id: uid(), role: message.role, content: message.content })));
    } catch (reason) {
      if (sequence === sessionLoadSequence.current) setMessages([{ id: uid(), role: "assistant", content: `无法加载会话记录：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    } finally {
      if (sequence === sessionLoadSequence.current) setSessionLoading(false);
    }
  }
  function logout() { controller.current?.abort(); sessionLoadSequence.current += 1; sessionStorage.clear(); setUserToken(""); setSessionToken(""); setSessionId(""); setSessions([]); setLLMSettings(null); }
  async function newChat() {
    try {
      const session = await createSession(userToken);
      setSessions((current) => [session, ...current.filter((item) => item.sessionId !== session.sessionId)]);
      await activateSession(session, false);
    } catch (reason) {
      setMessages([{ id: uid(), role: "assistant", content: `无法创建新会话：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    }
  }
  if (!userToken) return <><Background settings={background} /><AuthScreen onReady={(u, session) => { setUserToken(u); setSessionToken(session.token); setSessionId(session.sessionId); }} /></>;
  if (!sessionToken) return <><Background settings={background} /><main className="auth-shell"><div className="auth-card glass-panel"><div className="brand-mark"><BrainCircuit size={25} /><span>QA Agent</span></div><p className="muted">正在加载会话…</p></div></main></>;

  return <div className="app-shell"><Background settings={background} />
    <header className="topbar glass-panel"><div className="brand-mark"><BrainCircuit size={22} /><span>QA Agent</span></div><button className={`model-pill ${llmSettings?.configured ? "" : "unconfigured"}`} onClick={() => setSettingsOpen(true)}><span className="status-dot" />{llmSettings?.configured ? llmSettings.model : "需要配置模型"}</button><div className="top-actions"><button onClick={() => setSettingsOpen(true)} title="模型与 API 设置"><SettingsIcon size={18} /></button><button onClick={() => setStudioOpen(true)} title="背景设置"><Palette size={18} /></button><button onClick={logout} title="退出"><LogOut size={18} /></button></div></header>
    <aside className="sidebar glass-panel"><button className="new-chat" onClick={() => void newChat()} disabled={sessionLoading || streaming}><MessageSquarePlus size={18} />新建对话</button><p className="section-label">历史会话</p><nav className="session-list" aria-label="历史会话">{sessions.length === 0 ? <p className="muted small">暂无会话</p> : sessions.map((session) => <button type="button" className={`session-item ${session.sessionId === sessionId ? "active" : ""}`} onClick={() => void activateSession(session, true)} disabled={sessionLoading || streaming} aria-current={session.sessionId === sessionId ? "page" : undefined} key={session.sessionId}><strong>{session.name || "新对话"}</strong><span>{session.sessionId === sessionId ? "当前会话" : "点击切换"}</span></button>)}</nav></aside>
    <main className="chat-panel glass-panel">
      <div className="messages">{messages.length === 0 && <div className="empty-state"><div className="hero-icon"><Sparkles /></div><p className="eyebrow">KNOWLEDGE-AWARE ASSISTANT</p><h1>{sessionLoading ? "正在加载历史记录…" : llmSettings?.configured === false ? "先连接你的模型" : "今天想探索什么？"}</h1><p>{sessionLoading ? "正在恢复该会话的 LangGraph 消息。" : llmSettings?.configured === false ? "添加并验证你自己的 DeepSeek API Key 后即可开始对话。" : "连接知识库、实时推理和可观测数据。"}</p>{!sessionLoading && llmSettings?.configured === false && <button className="configure-cta" onClick={() => setSettingsOpen(true)}><SettingsIcon size={17} />打开模型设置</button>}</div>}
        {messages.map((message) => <article key={message.id} className={`message ${message.role}`}>
          {message.role === "assistant" && message.reasoning && <div className="reasoning-card"><button onClick={() => setReasoningOpen(!reasoningOpen)}><BrainCircuit size={16} /><span>模型推理过程</span><small>{effort.toUpperCase()}</small><ChevronDown size={15} className={reasoningOpen ? "rotate" : ""} /></button>{reasoningOpen && <pre>{message.reasoning}</pre>}</div>}
          <div className="message-body">{message.role === "assistant" ? <MarkdownMessage>{message.content || (streaming ? "正在生成…" : "")}</MarkdownMessage> : message.content}</div>
        </article>)}<div ref={endRef} /></div>
      <div className="composer-wrap"><div className="effort-control"><span>推理强度</span>{(["off", "high", "max"] as ReasoningEffort[]).map((item) => <button disabled={sessionLoading || (item !== "off" && !llmSettings?.thinking_enabled)} className={effort === item ? "selected" : ""} onClick={() => setEffort(item)} key={item}>{item === "off" ? "关闭" : item.toUpperCase()}</button>)}</div><div className="composer"><textarea disabled={sessionLoading || llmSettings?.configured === false} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} placeholder={sessionLoading ? "正在加载历史会话…" : llmSettings?.configured === false ? "请先配置模型 API" : "输入问题，Enter 发送…"} /><button disabled={sessionLoading || llmSettings?.configured === false} className="send-button" onClick={streaming ? () => controller.current?.abort() : () => void send()}>{streaming ? <CircleStop /> : <Send />}</button></div></div>
    </main>
    <aside className="inspector glass-panel"><div className="inspector-heading"><div><p className="eyebrow">LIVE INSPECTOR</p><h2>实时推理</h2></div><Activity className={streaming ? "pulse" : ""} /></div>
      <div className="pipeline"><p className="section-label">AGENT PIPELINE</p>{stages.length === 0 ? <p className="muted small">发送问题后显示实时阶段</p> : stages.map((item) => <div className="stage-row" key={item.stage}><span className={`stage-icon ${item.status}`}>{item.status === "completed" ? <Check size={13} /> : <span />}</span><span>{item.stage}</span><small>{item.elapsedMs ? `${item.elapsedMs}ms` : item.status}</small></div>)}</div>
      <div className="metric-grid"><MetricCard icon={<Zap size={15} />} title="Token" value={`${usage.exact ? "" : "~"}${usage.exact ? usage.total : estimatedOutput}`} detail={usage.exact ? `输入 ${usage.input} · 输出 ${usage.output} · 推理 ${usage.reasoning}` : "流式估算，完成后校准"} /><MetricCard icon={<Gauge size={15} />} title="记忆缓存" value={`${Math.round(cache.hitRate * 100)}%`} detail={`命中 ${cache.hits} · 未命中 ${cache.misses} · ${cache.scope === "instance" ? "当前后端实例" : cache.scope}`} /></div>
      <section className="inference-card"><div><span className="status-dot" /><strong>{streaming ? effort === "off" ? "回答生成中" : "推理流式传输中" : "等待请求"}</strong></div><div className={streaming ? "inference-bar running" : "inference-bar"}><span /></div><p>Reasoning effort: <b>{effort}</b></p></section>
    </aside>
    <ModelSettingsDrawer open={settingsOpen} userToken={userToken} onClose={() => setSettingsOpen(false)} onChange={(value) => { setLLMSettings(value); if (!value.thinking_enabled) setEffort("off"); }} />
    <BackgroundStudio open={studioOpen} settings={background} persistenceError={backgroundPersistenceError} onChange={setBackground} onClose={() => setStudioOpen(false)} />
  </div>;
}

export default App;
