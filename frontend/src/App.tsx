import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Activity, BrainCircuit, Check, ChevronDown, CircleStop, Gauge, LogOut,
  MessageSquarePlus, Palette, Send, Sparkles, X, Zap,
} from "lucide-react";
import { createSession, login, register, streamChat } from "./api";
import type { CacheStats, ChatMessage, ReasoningEffort, StageItem, StreamEvent, TokenUsage } from "./types";

type BackgroundPreset = "aurora" | "threads" | "grid" | "solid";
interface BackgroundSettings { preset: BackgroundPreset; accent: string; intensity: number; speed: number; }

const defaultBackground: BackgroundSettings = { preset: "aurora", accent: "#22c55e", intensity: 55, speed: 45 };
const uid = () => crypto.randomUUID();

function Background({ settings }: { settings: BackgroundSettings }) {
  return (
    <div
      className={`dynamic-background bg-${settings.preset}`}
      style={{ "--accent": settings.accent, "--intensity": settings.intensity / 100, "--speed": `${14 - settings.speed / 10}s` } as React.CSSProperties}
      aria-hidden="true"
    ><div className="background-orb orb-one" /><div className="background-orb orb-two" /></div>
  );
}

function AuthScreen({ onReady }: { onReady: (userToken: string, sessionToken: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [username, setUsername] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const userToken = mode === "login" ? await login(email, password) : await register(email, password, username);
      const session = await createSession(userToken);
      sessionStorage.setItem("qa-user-token", userToken); sessionStorage.setItem("qa-session-token", session.token);
      onReady(userToken, session.token);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "连接失败"); } finally { setBusy(false); }
  }
  return <main className="auth-shell"><div className="auth-card spotlight-card">
    <div className="brand-mark"><BrainCircuit size={25} /><span>QA Agent</span></div>
    <p className="eyebrow">DEEPSEEK · RAG · LIVE OBSERVABILITY</p>
    <h1>{mode === "login" ? "欢迎回来" : "创建你的工作区"}</h1>
    <p className="muted">连接知识、推理过程和实时性能指标。</p>
    <form onSubmit={submit}>
      {mode === "register" && <label>用户名<input value={username} onChange={(e) => setUsername(e.target.value)} required /></label>}
      <label>邮箱<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      {error && <p className="form-error">{error}</p>}
      <button className="primary-button" disabled={busy}>{busy ? "连接中…" : mode === "login" ? "登录并开始" : "注册并开始"}</button>
    </form>
    <button className="text-button" onClick={() => setMode(mode === "login" ? "register" : "login")}>{mode === "login" ? "没有账号？注册" : "已有账号？登录"}</button>
  </div></main>;
}

function MetricCard({ title, value, detail, icon }: { title: string; value: string; detail: string; icon: React.ReactNode }) {
  return <section className="metric-card spotlight-card"><div className="metric-label">{icon}{title}</div><strong>{value}</strong><span>{detail}</span></section>;
}

function App() {
  const [userToken, setUserToken] = useState(() => sessionStorage.getItem("qa-user-token") ?? "");
  const [sessionToken, setSessionToken] = useState(() => sessionStorage.getItem("qa-session-token") ?? "");
  const [messages, setMessages] = useState<ChatMessage[]>([]); const [input, setInput] = useState("");
  const [effort, setEffort] = useState<ReasoningEffort>("off"); const [streaming, setStreaming] = useState(false);
  const [stages, setStages] = useState<StageItem[]>([]); const [usage, setUsage] = useState<TokenUsage>({ input: 0, output: 0, total: 0, reasoning: 0, exact: false });
  const [cache, setCache] = useState<CacheStats>({ hits: 0, misses: 0, hitRate: 0, scope: "instance" });
  const [background, setBackground] = useState<BackgroundSettings>(() => {
    try { return JSON.parse(localStorage.getItem("qa-agent-background-v1") ?? "null") ?? defaultBackground; } catch { return defaultBackground; }
  });
  const [studioOpen, setStudioOpen] = useState(false); const [reasoningOpen, setReasoningOpen] = useState(true);
  const controller = useRef<AbortController | null>(null); const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => { localStorage.setItem("qa-agent-background-v1", JSON.stringify(background)); }, [background]);
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
    const question = input.trim(); if (!question || streaming) return;
    const assistantId = uid(); setInput(""); setStages([]); setUsage({ input: 0, output: 0, total: 0, reasoning: 0, exact: false });
    setMessages((current) => [...current, { id: uid(), role: "user", content: question }, { id: assistantId, role: "assistant", content: "", reasoning: "" }]);
    setStreaming(true); controller.current = new AbortController();
    try { await streamChat(sessionToken, question, effort, (event) => handleEvent(event, assistantId), controller.current.signal); }
    catch (reason) { if (!(reason instanceof DOMException && reason.name === "AbortError")) setMessages((current) => current.map((m) => m.id === assistantId ? { ...m, content: `请求失败：${reason instanceof Error ? reason.message : "未知错误"}` } : m)); }
    finally { setStreaming(false); }
  }
  function logout() { sessionStorage.clear(); setUserToken(""); setSessionToken(""); }
  async function newChat() {
    try {
      const session = await createSession(userToken);
      sessionStorage.setItem("qa-session-token", session.token);
      setSessionToken(session.token); setMessages([]); setStages([]);
    } catch (reason) {
      setMessages([{ id: uid(), role: "assistant", content: `无法创建新会话：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    }
  }
  if (!userToken || !sessionToken) return <><Background settings={background} /><AuthScreen onReady={(u, s) => { setUserToken(u); setSessionToken(s); }} /></>;

  return <div className="app-shell"><Background settings={background} />
    <header className="topbar glass-panel"><div className="brand-mark"><BrainCircuit size={22} /><span>QA Agent</span></div><div className="model-pill"><span className="status-dot" />DeepSeek V4 Flash</div><div className="top-actions"><button onClick={() => setStudioOpen(true)} title="背景设置"><Palette size={18} /></button><button onClick={logout} title="退出"><LogOut size={18} /></button></div></header>
    <aside className="sidebar glass-panel"><button className="new-chat" onClick={() => void newChat()}><MessageSquarePlus size={18} />新建对话</button><p className="section-label">当前会话</p><button className="session-item active">实时问答<span>当前</span></button></aside>
    <main className="chat-panel glass-panel">
      <div className="messages">{messages.length === 0 && <div className="empty-state"><div className="hero-icon"><Sparkles /></div><p className="eyebrow">KNOWLEDGE-AWARE ASSISTANT</p><h1>今天想探索什么？</h1><p>连接知识库、实时推理和可观测数据。</p></div>}
        {messages.map((message) => <article key={message.id} className={`message ${message.role}`}>
          {message.role === "assistant" && message.reasoning && <div className="reasoning-card"><button onClick={() => setReasoningOpen(!reasoningOpen)}><BrainCircuit size={16} /><span>模型推理过程</span><small>{effort.toUpperCase()}</small><ChevronDown size={15} className={reasoningOpen ? "rotate" : ""} /></button>{reasoningOpen && <pre>{message.reasoning}</pre>}</div>}
          <div className="message-body">{message.role === "assistant" ? <ReactMarkdown>{message.content || (streaming ? "正在生成…" : "")}</ReactMarkdown> : message.content}</div>
        </article>)}<div ref={endRef} /></div>
      <div className="composer-wrap"><div className="effort-control"><span>推理强度</span>{(["off", "high", "max"] as ReasoningEffort[]).map((item) => <button className={effort === item ? "selected" : ""} onClick={() => setEffort(item)} key={item}>{item === "off" ? "关闭" : item.toUpperCase()}</button>)}</div><div className="composer"><textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} placeholder="输入问题，Enter 发送…" /><button className="send-button" onClick={streaming ? () => controller.current?.abort() : () => void send()}>{streaming ? <CircleStop /> : <Send />}</button></div></div>
    </main>
    <aside className="inspector glass-panel"><div className="inspector-heading"><div><p className="eyebrow">LIVE INSPECTOR</p><h2>实时推理</h2></div><Activity className={streaming ? "pulse" : ""} /></div>
      <div className="pipeline"><p className="section-label">AGENT PIPELINE</p>{stages.length === 0 ? <p className="muted small">发送问题后显示实时阶段</p> : stages.map((item) => <div className="stage-row" key={item.stage}><span className={`stage-icon ${item.status}`}>{item.status === "completed" ? <Check size={13} /> : <span />}</span><span>{item.stage}</span><small>{item.elapsedMs ? `${item.elapsedMs}ms` : item.status}</small></div>)}</div>
      <div className="metric-grid"><MetricCard icon={<Zap size={15} />} title="Token" value={`${usage.exact ? "" : "~"}${usage.exact ? usage.total : estimatedOutput}`} detail={usage.exact ? `输入 ${usage.input} · 输出 ${usage.output} · 推理 ${usage.reasoning}` : "流式估算，完成后校准"} /><MetricCard icon={<Gauge size={15} />} title="缓存命中" value={`${Math.round(cache.hitRate * 100)}%`} detail={`${cache.hits} hits / ${cache.misses} misses · ${cache.scope}`} /></div>
      <section className="inference-card"><div><span className="status-dot" /><strong>{streaming ? effort === "off" ? "回答生成中" : "推理流式传输中" : "等待请求"}</strong></div><div className={streaming ? "inference-bar running" : "inference-bar"}><span /></div><p>Reasoning effort: <b>{effort}</b></p></section>
    </aside>
    {studioOpen && <div className="drawer-backdrop" onClick={() => setStudioOpen(false)}><aside className="background-studio glass-panel" onClick={(e) => e.stopPropagation()}><div className="drawer-title"><div><p className="eyebrow">APPEARANCE</p><h2>背景工作室</h2></div><button onClick={() => setStudioOpen(false)}><X /></button></div><label>预设<div className="preset-grid">{(["aurora", "threads", "grid", "solid"] as BackgroundPreset[]).map((preset) => <button className={background.preset === preset ? "active" : ""} onClick={() => setBackground({ ...background, preset })} key={preset}><span className={`preset-preview bg-${preset}`} />{preset}</button>)}</div></label><label>强调色<input type="color" value={background.accent} onChange={(e) => setBackground({ ...background, accent: e.target.value })} /></label><label>强度 <b>{background.intensity}%</b><input type="range" min="0" max="100" value={background.intensity} onChange={(e) => setBackground({ ...background, intensity: Number(e.target.value) })} /></label><label>速度 <b>{background.speed}%</b><input type="range" min="0" max="100" value={background.speed} onChange={(e) => setBackground({ ...background, speed: Number(e.target.value) })} /></label><button className="secondary-button" onClick={() => setBackground(defaultBackground)}>恢复默认</button></aside></div>}
  </div>;
}

export default App;
