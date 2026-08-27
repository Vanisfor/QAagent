import { useEffect, useMemo, useRef, useState } from "react";
import { MotionConfig } from "motion/react";
import { Activity, History, MessageSquarePlus, Palette, Settings } from "lucide-react";

import { BackgroundStudio, defaultBackground, type BackgroundSettings } from "./BackgroundStudio";
import { ModelSettingsDrawer } from "./ModelSettingsDrawer";
import { createSession, getLLMSettings, getSessionMessages, listSessions, streamChat } from "./api";
import { AuthScreen } from "./components/auth/AuthScreen";
import { ChatWorkspace } from "./components/chat/ChatWorkspace";
import { AppShell } from "./components/layout/AppShell";
import { MobileNav } from "./components/layout/MobileNav";
import { Sidebar } from "./components/layout/Sidebar";
import { TopNav } from "./components/layout/TopNav";
import { RuntimePanel } from "./components/runtime/RuntimePanel";
import { CommandMenu, type CommandAction } from "./components/ui/CommandMenu";
import { Drawer } from "./components/ui/Drawer";
import type { CacheStats, ChatMessage, LLMSettings, ReasoningEffort, SessionSummary, StageItem, StreamEvent, TokenUsage } from "./types";

const uid = () => crypto.randomUUID();
const emptyUsage: TokenUsage = { input: 0, output: 0, total: 0, reasoning: 0, exact: false };

function AppBackdrop({ settings }: { settings: BackgroundSettings }) {
  return <div className={`app-backdrop bg-${settings.preset}`} style={{ "--accent": settings.accent, "--intensity": settings.intensity / 100, "--speed": `${18 - settings.speed / 10}s` } as React.CSSProperties} aria-hidden="true">{settings.preset === "custom" && settings.imageDataUrl && <div className="custom-background-image" style={{ backgroundImage: `url(${settings.imageDataUrl})`, opacity: settings.intensity / 100 }} />}<div className="ambient-orb" /></div>;
}

function LoadingWorkspace({ background }: { background: BackgroundSettings }) {
  return <><AppBackdrop settings={background} /><main className="loading-page"><span className="loading-mark" /><p>Loading workspace</p></main></>;
}

function App() {
  const [userToken, setUserToken] = useState(() => sessionStorage.getItem("qa-user-token") ?? "");
  const [sessionToken, setSessionToken] = useState(() => sessionStorage.getItem("qa-session-token") ?? "");
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem("qa-session-id") ?? "");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [effort, setEffort] = useState<ReasoningEffort>("off");
  const [streaming, setStreaming] = useState(false);
  const [stages, setStages] = useState<StageItem[]>([]);
  const [usage, setUsage] = useState<TokenUsage>(emptyUsage);
  const [cache, setCache] = useState<CacheStats>({ hits: 0, misses: 0, hitRate: 0, scope: "instance" });
  const [llmSettings, setLLMSettings] = useState<LLMSettings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [reasoningOpen, setReasoningOpen] = useState(true);
  const [theme, setTheme] = useState<"dark" | "light">(() => localStorage.getItem("qa-agent-theme") === "light" ? "light" : "dark");
  const [background, setBackground] = useState<BackgroundSettings>(() => {
    try { return JSON.parse(localStorage.getItem("qa-agent-background-v1") ?? "null") ?? defaultBackground; }
    catch { return defaultBackground; }
  });
  const [backgroundPersistenceError, setBackgroundPersistenceError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const sessionLoadSequence = useRef(0);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("qa-agent-theme", theme); }, [theme]);
  useEffect(() => {
    try { localStorage.setItem("qa-agent-background-v1", JSON.stringify(background)); setBackgroundPersistenceError(""); }
    catch { setBackgroundPersistenceError("浏览器存储空间不足，当前背景可能无法在刷新后保留。"); }
  }, [background]);
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setCommandOpen((current) => !current); }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
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

  const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  const estimatedOutput = useMemo(() => Math.ceil((latestAssistant?.content.length ?? 0) / 3.5), [latestAssistant]);
  const activeSession = sessions.find((session) => session.sessionId === sessionId);
  const busy = sessionLoading || streaming;

  function handleEvent(event: StreamEvent, assistantId: string) {
    if (event.type === "reasoning_delta" || event.type === "answer_delta") {
      setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, content: event.type === "answer_delta" ? message.content + event.content : message.content, reasoning: event.type === "reasoning_delta" ? (message.reasoning ?? "") + event.content : message.reasoning } : message));
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
    const question = input.trim();
    if (!question || busy) return;
    if (!llmSettings?.configured) { setSettingsOpen(true); return; }
    const assistantId = uid(); setInput(""); setStages([]); setUsage(emptyUsage);
    setMessages((current) => [...current, { id: uid(), role: "user", content: question }, { id: assistantId, role: "assistant", content: "", reasoning: "" }]);
    setStreaming(true); controller.current = new AbortController();
    try { await streamChat(sessionToken, question, effort, (event) => handleEvent(event, assistantId), controller.current.signal); }
    catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, content: `请求失败：${reason instanceof Error ? reason.message : "未知错误"}` } : message));
    } finally { setStreaming(false); void listSessions(userToken).then(setSessions).catch(() => undefined); }
  }

  async function activateSession(session: SessionSummary, loadHistory: boolean) {
    const sequence = ++sessionLoadSequence.current;
    controller.current?.abort(); setSessionLoading(true);
    sessionStorage.setItem("qa-session-id", session.sessionId); sessionStorage.setItem("qa-session-token", session.token);
    setSessionId(session.sessionId); setSessionToken(session.token); setStages([]); setUsage(emptyUsage); setMessages([]);
    if (!loadHistory) { setSessionLoading(false); return; }
    try {
      const history = await getSessionMessages(session.token);
      if (sequence !== sessionLoadSequence.current) return;
      setMessages(history.map((message) => ({ id: uid(), role: message.role, content: message.content })));
    } catch (reason) {
      if (sequence === sessionLoadSequence.current) setMessages([{ id: uid(), role: "assistant", content: `无法加载会话记录：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    } finally { if (sequence === sessionLoadSequence.current) setSessionLoading(false); }
  }

  async function newChat() {
    try {
      const session = await createSession(userToken);
      setSessions((current) => [session, ...current.filter((item) => item.sessionId !== session.sessionId)]);
      await activateSession(session, false);
    } catch (reason) { setMessages([{ id: uid(), role: "assistant", content: `无法创建新会话：${reason instanceof Error ? reason.message : "未知错误"}` }]); }
  }

  function logout() {
    controller.current?.abort(); sessionLoadSequence.current += 1; sessionStorage.clear();
    setUserToken(""); setSessionToken(""); setSessionId(""); setSessions([]); setLLMSettings(null);
  }

  const commandActions = useMemo<CommandAction[]>(() => [
    { id: "new-session", label: "New session", group: "Workspace", hint: "N", icon: <MessageSquarePlus size={16} />, run: () => void newChat() },
    { id: "runtime", label: "Open execution", group: "Runtime", icon: <Activity size={16} />, run: () => setRuntimeOpen(true) },
    { id: "models", label: "Models & API", group: "Settings", icon: <Settings size={16} />, run: () => setSettingsOpen(true) },
    { id: "appearance", label: "Appearance", group: "Settings", icon: <Palette size={16} />, run: () => setAppearanceOpen(true) },
    ...sessions.map((session) => ({ id: `session-${session.sessionId}`, label: session.name || "新对话", group: "History", icon: <History size={16} />, run: () => void activateSession(session, true) })),
  ], [sessions]);

  if (!userToken) return <MotionConfig reducedMotion="user"><AppBackdrop settings={background} /><AuthScreen onReady={(token, session) => { setUserToken(token); setSessionToken(session.token); setSessionId(session.sessionId); }} /></MotionConfig>;
  if (!sessionToken) return <MotionConfig reducedMotion="user"><LoadingWorkspace background={background} /></MotionConfig>;

  const sidebarProps = {
    sessions, sessionId, busy,
    onChat: () => document.getElementById("workspace")?.scrollIntoView({ behavior: "smooth" }),
    onNewSession: () => void newChat(),
    onSelectSession: (session: SessionSummary) => void activateSession(session, true),
    onOpenRuntime: () => setRuntimeOpen(true),
    onOpenSettings: () => setSettingsOpen(true),
    onOpenAppearance: () => setAppearanceOpen(true),
  };
  const runtime = <RuntimePanel stages={stages} usage={usage} cache={cache} estimatedOutput={estimatedOutput} streaming={streaming} effort={effort} />;

  return <MotionConfig reducedMotion="user"><AppBackdrop settings={background} /><AppShell
    topNav={<TopNav theme={theme} modelName={llmSettings?.model ?? "DeepSeek"} modelConfigured={Boolean(llmSettings?.configured)} onOpenMobile={() => setMobileNavOpen(true)} onOpenCommand={() => setCommandOpen(true)} onOpenRuntime={() => setRuntimeOpen(true)} onOpenSettings={() => setSettingsOpen(true)} onOpenAppearance={() => setAppearanceOpen(true)} onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")} onLogout={logout} />}
    sidebar={<Sidebar {...sidebarProps} collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((current) => !current)} />}
    context={runtime}
  ><ChatWorkspace title={activeSession?.name || "新对话"} messages={messages} input={input} effort={effort} streaming={streaming} sessionLoading={sessionLoading} modelConfigured={Boolean(llmSettings?.configured)} thinkingEnabled={Boolean(llmSettings?.thinking_enabled)} reasoningOpen={reasoningOpen} endRef={endRef} onInputChange={setInput} onEffortChange={setEffort} onSend={() => void send()} onStop={() => controller.current?.abort()} onOpenSettings={() => setSettingsOpen(true)} onToggleReasoning={() => setReasoningOpen((current) => !current)} /></AppShell>
    <MobileNav open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} {...sidebarProps} />
    <CommandMenu open={commandOpen} actions={commandActions} onClose={() => setCommandOpen(false)} />
    <Drawer open={runtimeOpen} eyebrow="LIVE RUNTIME" title="Execution" className="runtime-drawer" onClose={() => setRuntimeOpen(false)}>{runtime}</Drawer>
    <ModelSettingsDrawer open={settingsOpen} userToken={userToken} onClose={() => setSettingsOpen(false)} onChange={(value) => { setLLMSettings(value); if (!value.thinking_enabled) setEffort("off"); }} />
    <BackgroundStudio open={appearanceOpen} settings={background} persistenceError={backgroundPersistenceError} onChange={setBackground} onClose={() => setAppearanceOpen(false)} />
  </MotionConfig>;
}

export default App;
