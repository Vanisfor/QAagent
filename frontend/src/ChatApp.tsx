import { useEffect, useRef, useState } from "react";
import { BrainCircuit } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router";

import { createSession, deleteSession as deleteSessionApi, getLLMSettings, getSessionMessages, listSessions, renameSession as renameSessionApi, streamChat } from "./api";
import { PRESET_GRADIENTS, defaultChatBackground, type ChatBackgroundSettings } from "./BackgroundSettingsModal";
import { ChatWorkspace } from "./components/chat/ChatWorkspace";
import { AppShell } from "./components/layout/AppShell";
import { Sidebar } from "./components/layout/Sidebar";
import { TopBar } from "./components/layout/TopBar";
import { RunPanel } from "./components/runtime/RunPanel";
import { SettingsModal, type SettingsCategory } from "./settings/SettingsModal";
import { useAccount } from "./user/useAccount";
import { KnowledgePanel } from "./knowledge/KnowledgePanel";
import type { ChatMessage, LLMSettings, RunStep, SessionSummary, StreamEvent } from "./types";

const uid = () => crypto.randomUUID();
const chatPath = (id: string) => `/chat/${encodeURIComponent(id)}`;

function ChatBackdrop({ settings }: { settings: ChatBackgroundSettings }) {
  const background = settings.imageDataUrl ? `url(${settings.imageDataUrl})` : (PRESET_GRADIENTS[settings.preset] ?? undefined);
  if (!background) return null;
  return <div className="chat-backdrop" aria-hidden="true"><div className="chat-backdrop-image" style={{ backgroundImage: background, filter: `brightness(${settings.brightness}) blur(${settings.blur}px)`, opacity: settings.opacity }} /><div className="chat-backdrop-scrim" /></div>;
}

function LoadingWorkspace() {
  return <main className="chat-empty"><span className="brand-mark"><BrainCircuit size={20} /></span><p>正在载入工作区…</p></main>;
}

export function ChatApp({ userToken, onLogout }: { userToken: string; onLogout: () => void }) {
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams();
  const [sessionsReady, setSessionsReady] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const initialSessions = useRef<Promise<SessionSummary[]> | null>(null);
  const activeSession = useRef<string | null>(null);
  const previousRoute = useRef(routeSessionId);
  const [sessionToken, setSessionToken] = useState(() => sessionStorage.getItem("qa-session-token") ?? "");
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem("qa-session-id") ?? "");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [llmSettings, setLLMSettings] = useState<LLMSettings | null>(null);
  const user = useAccount(userToken);
  const [settingsCategory, setSettingsCategory] = useState<SettingsCategory | null>(null);
  const chatBackground = user.appearance?.background ?? defaultChatBackground;
  const [runSteps, setRunSteps] = useState<RunStep[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(() => window.matchMedia("(max-width: 860px)").matches);
  const theme = user.appearance?.theme ?? "light";
  const [settingsError, setSettingsError] = useState("");
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [knowledgeSpaceSlugs, setKnowledgeSpaceSlugs] = useState<string[]>([]);
  const controller = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const sessionLoadSequence = useRef(0);
  const stepSeq = useRef(0);
  const llmStepRef = useRef<string | null>(null);
  const memoryStepRef = useRef<string | null>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => { if (user.appearance) setSidebarCollapsed(user.appearance.sidebar_collapsed); }, [user.appearance]);

  function toggleTheme() {
    if (!user.appearance) return;
    setSettingsError("");
    void user.saveAppearance({ ...user.appearance, theme: theme === "dark" ? "light" : "dark" }).catch((reason: Error) => setSettingsError(reason.message));
  }
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 860px)");
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!userToken) { setLLMSettings(null); return; }
    let cancelled = false;
    void getLLMSettings(userToken).then((value) => {
      if (cancelled) return;
      setLLMSettings(value);
      if (!value.configured) setSettingsCategory("model");
    }).catch((reason) => {
      if (!cancelled) setMessages([{ id: uid(), role: "assistant", content: `无法读取模型设置：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    initialSessions.current ??= listSessions(userToken).then(async (available) => available.length ? available : [await createSession(userToken)]);
    void initialSessions.current.then((available) => {
      if (cancelled) return;
      setSessions(available); setSessionsReady(true);
    }).catch((reason) => {
      if (!cancelled) setLoadError(`无法加载历史会话：${reason instanceof Error ? reason.message : "未知错误"}`);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!sessionsReady) return;
    if (!routeSessionId) {
      const selected = sessions.find((session) => session.sessionId === sessionId) ?? sessions[0];
      if (selected) navigate(chatPath(selected.sessionId), { replace: true });
      return;
    }
    const selected = sessions.find((session) => session.sessionId === routeSessionId);
    if (!selected) {
      controller.current?.abort(); activeSession.current = null;
      sessionLoadSequence.current += 1; setStreaming(false);
      return;
    }
    if (activeSession.current !== selected.sessionId) void activateSession(selected);
  }, [routeSessionId, sessionsReady, sessions, sessionId, navigate]);

  useEffect(() => {
    setSidebarMobileOpen(false);
    if (previousRoute.current && previousRoute.current !== routeSessionId) {
      setSettingsCategory(null);
    }
    previousRoute.current = routeSessionId;
    document.title = "聊天 · QA Agent";
  }, [routeSessionId]);

  useEffect(() => () => {
    controller.current?.abort(); sessionLoadSequence.current += 1;
    activeSession.current = null;
  }, []);

  function addRunStep(partial: Omit<RunStep, "id">): string {
    const id = `run-${stepSeq.current++}`;
    setRunSteps((current) => [...current, { ...partial, id }]);
    return id;
  }

  function updateRunStep(id: string, patch: Partial<RunStep>) {
    setRunSteps((current) => current.map((step) => step.id === id ? { ...step, ...patch } : step));
  }

  function toolLabel(name: string): string {
    switch (name) {
      case "knowledge_search": return "知识库检索";
      case "duckduckgo_search": return "网页搜索";
      case "ask_human": return "人工确认";
      default: return name;
    }
  }

  function toolArgsDetail(name: string, args: Record<string, unknown>): string {
    if (name === "knowledge_search") return `查询：${String(args.query ?? "")}`;
    if (name === "duckduckgo_search") return `搜索：${String(args.query ?? "")}`;
    if (name === "ask_human") return `提问：${String(args.question ?? args.prompt ?? "")}`;
    try {
      const text = JSON.stringify(args);
      return text.length > 100 ? `${text.slice(0, 100)}…` : text;
    } catch { return ""; }
  }

  function handleEvent(event: StreamEvent, assistantId: string) {
    if (event.type === "answer_delta") {
      if (!llmStepRef.current) llmStepRef.current = addRunStep({ kind: "llm", title: "生成回答", status: "running" });
      setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, content: message.content + event.content } : message));
    } else if (event.type === "stage") {
      const stage = String(event.data.stage ?? "");
      const status = String(event.data.status ?? "");
      if (stage === "memory") {
        if (status === "started") {
          memoryStepRef.current = addRunStep({ kind: "memory", title: "记忆检索", detail: "正在检索你的长期记忆…", status: "running" });
        } else if (status === "completed" && memoryStepRef.current) {
          const count = Number(event.data.count ?? 0);
          updateRunStep(memoryStepRef.current, { status: "done", detail: count > 0 ? `找到 ${count} 条相关记忆` : "没有找到相关记忆" });
        }
      }
    } else if (event.type === "tool_call") {
      const name = String(event.data.name ?? "tool");
      addRunStep({ kind: "tool", title: toolLabel(name), detail: toolArgsDetail(name, (event.data.args ?? {}) as Record<string, unknown>), status: "running" });
    } else if (event.type === "tool_result") {
      const name = String(event.data.name ?? "");
      const summary = String(event.data.summary ?? "完成");
      setRunSteps((current) => {
        let matched = false;
        return current.map((step) => {
          if (!matched && step.kind === "tool" && step.title === toolLabel(name) && step.status === "running") {
            matched = true;
            return { ...step, status: "done", detail: summary };
          }
          return step;
        });
      });
    } else if (event.type === "rag_plan") {
      const queries = Array.isArray(event.data.queries) ? event.data.queries.map(String) : [];
      const listed = queries.join("、");
      addRunStep({ kind: "rag", title: "查询规划", detail: queries.length > 0 ? `生成 ${queries.length} 条检索词：${listed.length > 80 ? `${listed.slice(0, 80)}…` : listed}` : "已生成检索计划", status: "done" });
    } else if (event.type === "rag_evaluate") {
      const sufficient = Boolean(event.data.sufficient);
      const reason = String(event.data.reason_code ?? "not_evaluated");
      addRunStep({ kind: "rag", title: "证据评估", detail: sufficient ? "证据充分，可以作答" : `证据不足（${reason}），继续补充检索`, status: "done" });
    } else if (event.type === "done") {
      if (llmStepRef.current) updateRunStep(llmStepRef.current, { status: "done", detail: "回答完成" });
    } else if (event.type === "error") {
      if (llmStepRef.current) updateRunStep(llmStepRef.current, { status: "error", detail: "回答生成失败" });
      setRunSteps((current) => current.map((step) => step.status === "running" ? { ...step, status: "error", detail: step.detail ?? "已中断" } : step));
    }
  }

  async function send(prompt?: string) {
    const question = (prompt ?? input).trim();
    if (!question || streaming || sessionLoading || activeSession.current !== routeSessionId) return;
    if (!llmSettings?.configured) { setSettingsCategory("model"); return; }
    const assistantId = uid(); setInput("");
    setMessages((current) => [...current, { id: uid(), role: "user", content: question }, { id: assistantId, role: "assistant", content: "" }]);
    setRunSteps([]); llmStepRef.current = null; memoryStepRef.current = null;
    setStreaming(true);
    const requestController = new AbortController();
    controller.current = requestController;
    const sequence = sessionLoadSequence.current;
    try { await streamChat(sessionToken, question, "off", (event) => {
      if (sequence === sessionLoadSequence.current) handleEvent(event, assistantId);
    }, requestController.signal, knowledgeSpaceSlugs); }
    catch (reason) {
      if (sequence === sessionLoadSequence.current && !(reason instanceof DOMException && reason.name === "AbortError")) setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, content: `请求失败：${reason instanceof Error ? reason.message : "未知错误"}` } : message));
    } finally {
      if (sequence === sessionLoadSequence.current) {
        setStreaming(false);
        void listSessions(userToken).then((available) => {
          if (sequence === sessionLoadSequence.current) setSessions(available);
        }).catch(() => undefined);
      }
    }
  }

  async function activateSession(session: SessionSummary) {
    const sequence = ++sessionLoadSequence.current;
    activeSession.current = session.sessionId;
    controller.current?.abort(); setStreaming(false); setInput(""); setSessionLoading(true);
    sessionStorage.setItem("qa-session-id", session.sessionId); sessionStorage.setItem("qa-session-token", session.token);
    setSessionId(session.sessionId); setSessionToken(session.token); setMessages([]);
    setRunSteps([]); llmStepRef.current = null; memoryStepRef.current = null;
    try {
      const history = await getSessionMessages(session.token);
      if (sequence !== sessionLoadSequence.current) return;
      setMessages(history.map((message) => ({ id: uid(), role: message.role, content: message.content })));
    } catch (reason) {
      if (sequence === sessionLoadSequence.current) setMessages([{ id: uid(), role: "assistant", content: `无法加载会话记录：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    } finally { if (sequence === sessionLoadSequence.current) setSessionLoading(false); }
  }

  async function newChat(replace = false) {
    try {
      const session = await createSession(userToken);
      setSessions((current) => [session, ...current.filter((item) => item.sessionId !== session.sessionId)]);
      navigate(chatPath(session.sessionId), { replace });
    } catch (reason) { setMessages([{ id: uid(), role: "assistant", content: `无法创建新会话：${reason instanceof Error ? reason.message : "未知错误"}` }]); }
  }

  async function handleRenameSession(session: SessionSummary, name: string) {
    const trimmed = name.trim();
    if (!trimmed || trimmed === session.name) return;
    try {
      const updated = await renameSessionApi(session.token, session.sessionId, trimmed);
      setSessions((current) => current.map((item) => item.sessionId === session.sessionId ? { ...item, name: updated.name, token: updated.token } : item));
      if (session.sessionId === sessionId) setSessionToken(updated.token);
    } catch (reason) { setMessages([{ id: uid(), role: "assistant", content: `重命名失败：${reason instanceof Error ? reason.message : "未知错误"}` }]); }
  }

  async function handleDeleteSession(session: SessionSummary) {
    if (!window.confirm(`确定删除会话「${session.name || "新对话"}」吗？`)) return;
    try {
      await deleteSessionApi(session.token, session.sessionId);
      const remaining = sessions.filter((item) => item.sessionId !== session.sessionId);
      setSessions(remaining);
      if (session.sessionId === sessionId) {
        if (remaining.length > 0) navigate(chatPath(remaining[0].sessionId), { replace: true });
        else await newChat(true);
      }
    } catch (reason) { setMessages([{ id: uid(), role: "assistant", content: `删除失败：${reason instanceof Error ? reason.message : "未知错误"}` }]); }
  }

  function handleToggleSidebar() { if (isMobile) setSidebarMobileOpen((current) => !current); else setSidebarCollapsed((current) => !current); }

  if (!sessionsReady && !loadError) return <LoadingWorkspace />;

  const missingSession = Boolean(routeSessionId && !sessions.some((session) => session.sessionId === routeSessionId));
  const modelConfigured = Boolean(llmSettings?.configured);
  const modelName = llmSettings?.model ?? "DeepSeek";

  return <>
    <AppShell context={<RunPanel steps={runSteps} streaming={streaming} />} sidebar={<Sidebar sessions={sessions} sessionId={sessionId} busy={streaming} collapsed={!isMobile && sidebarCollapsed} mobileOpen={isMobile && sidebarMobileOpen} userLabel={user.account?.email ?? "账户"} displayName={user.account?.profile.display_name ?? "账户"} avatar={user.avatar} onNewSession={() => void newChat()} onSelectSession={(session) => navigate(chatPath(session.sessionId))} onRenameSession={(session, name) => void handleRenameSession(session, name)} onDeleteSession={(session) => void handleDeleteSession(session)} onToggle={handleToggleSidebar} onOpenSettings={setSettingsCategory} onOpenKnowledge={() => setKnowledgeOpen(true)} onLogout={onLogout} />} main={<><ChatBackdrop settings={chatBackground} /><TopBar theme={theme} modelName={modelName} modelConfigured={modelConfigured} onToggleSidebar={handleToggleSidebar} onOpenSettings={() => setSettingsCategory("model")} onToggleTheme={toggleTheme} />{loadError ? <main className="chat-empty"><p role="alert">{loadError}</p><button className="secondary-button" onClick={() => window.location.reload()}>重试</button></main> : missingSession ? <main className="chat-empty"><h1>会话不可用</h1><p>该会话可能已删除，或不属于当前账户。</p><Link className="secondary-button" to="/chat" replace>返回聊天</Link></main> : sessionLoading || activeSession.current !== routeSessionId ? <LoadingWorkspace /> : <ChatWorkspace messages={messages} input={input} streaming={streaming} modelConfigured={modelConfigured} endRef={endRef} onInputChange={setInput} onSend={() => void send()} onSendPrompt={(prompt) => void send(prompt)} onStop={() => controller.current?.abort()} onOpenSettings={() => setSettingsCategory("model")} />}</>} />
    {(user.error || settingsError) && <div className="account-error" role="alert">{user.error || settingsError}<button className="secondary-button" onClick={() => { setSettingsError(""); user.retry(); }}>重试</button></div>}
    <SettingsModal category={settingsCategory} onCategory={setSettingsCategory} token={userToken} user={user} onClose={() => setSettingsCategory(null)} onModelChange={setLLMSettings} />
    <KnowledgePanel open={knowledgeOpen} token={userToken} selected={knowledgeSpaceSlugs} onSelected={setKnowledgeSpaceSlugs} onClose={() => setKnowledgeOpen(false)} />
  </>;
}
