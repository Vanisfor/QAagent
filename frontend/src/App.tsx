import { useEffect, useRef, useState } from "react";
import { BrainCircuit } from "lucide-react";

import { createSession, deleteSession as deleteSessionApi, getLLMSettings, getSessionMessages, listSessions, renameSession as renameSessionApi, streamChat } from "./api";
import { BackgroundSettingsModal, PRESET_GRADIENTS, defaultChatBackground, type ChatBackgroundSettings } from "./BackgroundSettingsModal";
import { AuthScreen } from "./components/auth/AuthScreen";
import { ChatWorkspace } from "./components/chat/ChatWorkspace";
import { AppShell } from "./components/layout/AppShell";
import { Sidebar } from "./components/layout/Sidebar";
import { TopBar } from "./components/layout/TopBar";
import { RunPanel } from "./components/runtime/RunPanel";
import { ModelSettingsModal } from "./ModelSettingsModal";
import type { ChatMessage, LLMSettings, RunStep, SessionSummary, StreamEvent } from "./types";

const uid = () => crypto.randomUUID();

function emailFromToken(token: string): string {
  try {
    let part = (token.split(".")[1] ?? "").replace(/-/g, "+").replace(/_/g, "/");
    while (part.length % 4) part += "=";
    const payload = JSON.parse(atob(part));
    return payload.sub ?? payload.email ?? payload.username ?? "账户";
  } catch { return "账户"; }
}

function loadBackground(): ChatBackgroundSettings {
  try {
    const parsed = JSON.parse(localStorage.getItem("qa-agent-chat-bg-v1") ?? "null");
    return parsed ? { ...defaultChatBackground, ...parsed } : defaultChatBackground;
  } catch { return defaultChatBackground; }
}

function ChatBackdrop({ settings }: { settings: ChatBackgroundSettings }) {
  const background = settings.imageDataUrl ? `url(${settings.imageDataUrl})` : (PRESET_GRADIENTS[settings.preset] ?? undefined);
  if (!background) return null;
  return <div className="chat-backdrop" aria-hidden="true"><div className="chat-backdrop-image" style={{ background, filter: `brightness(${settings.brightness}) blur(${settings.blur}px)`, opacity: settings.opacity }} /><div className="chat-backdrop-scrim" /></div>;
}

function LoadingWorkspace() {
  return <main className="chat-empty"><span className="brand-mark"><BrainCircuit size={20} /></span><p>正在载入工作区…</p></main>;
}

export default function App() {
  const [userToken, setUserToken] = useState(() => sessionStorage.getItem("qa-user-token") ?? "");
  const [sessionToken, setSessionToken] = useState(() => sessionStorage.getItem("qa-session-token") ?? "");
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem("qa-session-id") ?? "");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [llmSettings, setLLMSettings] = useState<LLMSettings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backgroundOpen, setBackgroundOpen] = useState(false);
  const [chatBackground, setChatBackground] = useState<ChatBackgroundSettings>(loadBackground);
  const [backgroundPersistenceError, setBackgroundPersistenceError] = useState("");
  const [runSteps, setRunSteps] = useState<RunStep[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(() => window.matchMedia("(max-width: 860px)").matches);
  const [theme, setTheme] = useState<"light" | "dark">(() => localStorage.getItem("qa-agent-theme") === "dark" ? "dark" : "light");
  const controller = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const sessionLoadSequence = useRef(0);
  const stepSeq = useRef(0);
  const llmStepRef = useRef<string | null>(null);
  const memoryStepRef = useRef<string | null>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("qa-agent-theme", theme); }, [theme]);
  useEffect(() => {
    try { localStorage.setItem("qa-agent-chat-bg-v1", JSON.stringify(chatBackground)); setBackgroundPersistenceError(""); }
    catch { setBackgroundPersistenceError("浏览器存储空间不足，当前背景可能无法在刷新后保留。"); }
  }, [chatBackground]);
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
    if (!question || streaming) return;
    if (!llmSettings?.configured) { setSettingsOpen(true); return; }
    const assistantId = uid(); setInput("");
    setMessages((current) => [...current, { id: uid(), role: "user", content: question }, { id: assistantId, role: "assistant", content: "" }]);
    setRunSteps([]); llmStepRef.current = null; memoryStepRef.current = null;
    setStreaming(true); controller.current = new AbortController();
    try { await streamChat(sessionToken, question, "off", (event) => handleEvent(event, assistantId), controller.current.signal); }
    catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, content: `请求失败：${reason instanceof Error ? reason.message : "未知错误"}` } : message));
    } finally { setStreaming(false); void listSessions(userToken).then(setSessions).catch(() => undefined); }
  }

  async function activateSession(session: SessionSummary, loadHistory: boolean) {
    const sequence = ++sessionLoadSequence.current;
    controller.current?.abort(); setStreaming(false);
    sessionStorage.setItem("qa-session-id", session.sessionId); sessionStorage.setItem("qa-session-token", session.token);
    setSessionId(session.sessionId); setSessionToken(session.token); setMessages([]);
    setRunSteps([]); llmStepRef.current = null; memoryStepRef.current = null;
    if (!loadHistory) return;
    try {
      const history = await getSessionMessages(session.token);
      if (sequence !== sessionLoadSequence.current) return;
      setMessages(history.map((message) => ({ id: uid(), role: message.role, content: message.content })));
    } catch (reason) {
      if (sequence === sessionLoadSequence.current) setMessages([{ id: uid(), role: "assistant", content: `无法加载会话记录：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    }
  }

  async function newChat() {
    try {
      const session = await createSession(userToken);
      setSessions((current) => [session, ...current.filter((item) => item.sessionId !== session.sessionId)]);
      await activateSession(session, false);
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
        if (remaining.length > 0) await activateSession(remaining[0], true);
        else await newChat();
      }
    } catch (reason) { setMessages([{ id: uid(), role: "assistant", content: `删除失败：${reason instanceof Error ? reason.message : "未知错误"}` }]); }
  }

  function logout() {
    controller.current?.abort(); sessionLoadSequence.current += 1; sessionStorage.clear();
    setUserToken(""); setSessionToken(""); setSessionId(""); setSessions([]); setLLMSettings(null);
  }

  function handleToggleSidebar() { if (isMobile) setSidebarMobileOpen((current) => !current); else setSidebarCollapsed((current) => !current); }

  if (!userToken) return <AuthScreen onReady={(token, session) => { setUserToken(token); setSessionToken(session.token); setSessionId(session.sessionId); }} />;
  if (!sessionToken) return <LoadingWorkspace />;

  const modelConfigured = Boolean(llmSettings?.configured);
  const modelName = llmSettings?.model ?? "DeepSeek";

  return <>
    <AppShell context={<RunPanel steps={runSteps} streaming={streaming} />} sidebar={<Sidebar sessions={sessions} sessionId={sessionId} busy={streaming} collapsed={!isMobile && sidebarCollapsed} mobileOpen={isMobile && sidebarMobileOpen} userLabel={emailFromToken(userToken)} onNewSession={() => void newChat()} onSelectSession={(session) => void activateSession(session, true)} onRenameSession={(session, name) => void handleRenameSession(session, name)} onDeleteSession={(session) => void handleDeleteSession(session)} onToggle={handleToggleSidebar} onOpenSettings={() => setSettingsOpen(true)} onOpenBackground={() => setBackgroundOpen(true)} onLogout={logout} />} main={<><ChatBackdrop settings={chatBackground} /><TopBar theme={theme} modelName={modelName} modelConfigured={modelConfigured} onToggleSidebar={handleToggleSidebar} onOpenSettings={() => setSettingsOpen(true)} onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")} onLogout={logout} /><ChatWorkspace messages={messages} input={input} streaming={streaming} modelConfigured={modelConfigured} endRef={endRef} onInputChange={setInput} onSend={() => void send()} onSendPrompt={(prompt) => void send(prompt)} onStop={() => controller.current?.abort()} onOpenSettings={() => setSettingsOpen(true)} /></>} />
    <ModelSettingsModal open={settingsOpen} userToken={userToken} onClose={() => setSettingsOpen(false)} onChange={setLLMSettings} />
    <BackgroundSettingsModal open={backgroundOpen} settings={chatBackground} persistenceError={backgroundPersistenceError} onClose={() => setBackgroundOpen(false)} onChange={setChatBackground} />
  </>;
}
