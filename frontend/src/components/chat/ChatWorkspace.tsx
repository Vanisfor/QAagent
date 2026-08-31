import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowUp, CircleStop, Settings, Sparkles } from "lucide-react";
import type { RefObject } from "react";

import { AgentMessage } from "./AgentMessage";
import type { ChatMessage, ReasoningEffort } from "../../types";

interface ChatWorkspaceProps {
  title: string;
  messages: ChatMessage[];
  input: string;
  effort: ReasoningEffort;
  streaming: boolean;
  sessionLoading: boolean;
  modelConfigured: boolean;
  thinkingEnabled: boolean;
  reasoningOpen: boolean;
  endRef: RefObject<HTMLDivElement | null>;
  onInputChange: (value: string) => void;
  onEffortChange: (effort: ReasoningEffort) => void;
  onSend: () => void;
  onStop: () => void;
  onOpenSettings: () => void;
  onToggleReasoning: () => void;
}

export function ChatWorkspace(props: ChatWorkspaceProps) {
  const reduceMotion = useReducedMotion();
  const unavailable = props.sessionLoading || !props.modelConfigured;
  const emptyTitle = props.sessionLoading ? "正在加载历史记录…" : !props.modelConfigured ? "连接你的模型" : "从一个问题开始";
  const emptyDescription = props.sessionLoading ? "正在恢复当前会话的 LangGraph 消息。" : !props.modelConfigured ? "添加并验证你的 DeepSeek API Key 后即可开始。" : "QA Agent 会在需要时调用记忆、知识库和工具，并展示真实执行阶段。";

  return <motion.section id="workspace" className="chat-workspace" initial={{ opacity: 0, y: reduceMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0.1 : 0.28 }}>
    <header className="page-header"><div><p className="breadcrumb"><span>Workspace</span><b>/</b>Chat</p><h1>{props.title || "新对话"}</h1><p>与你的知识增强 Agent 协作，查看推理与运行状态。</p></div><span className={`model-status ${props.modelConfigured ? "ready" : "needs-setup"}`}><span />{props.modelConfigured ? "Model ready" : "Setup required"}</span></header>
    <div className="message-scroll"><div className="message-column"><AnimatePresence initial={false}>{props.messages.length === 0 ? <motion.div key="empty" className="empty-state" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}><span className="empty-icon"><Sparkles size={19} /></span><h2>{emptyTitle}</h2><p>{emptyDescription}</p>{!props.sessionLoading && !props.modelConfigured && <button className="secondary-button inline-action" onClick={props.onOpenSettings}><Settings size={15} />Models & API</button>}</motion.div> : props.messages.map((message, index) => <AgentMessage key={message.id} message={message} effort={props.effort} reasoningOpen={props.reasoningOpen} streaming={props.streaming && index === props.messages.length - 1} onToggleReasoning={props.onToggleReasoning} />)}</AnimatePresence><div ref={props.endRef} /></div></div>
    <footer className="composer-region"><div className="composer-column"><div className="effort-control"><span>Reasoning</span>{(["off", "high", "max"] as ReasoningEffort[]).map((item) => <button type="button" disabled={props.sessionLoading || (item !== "off" && !props.thinkingEnabled)} className={props.effort === item ? "selected" : ""} onClick={() => props.onEffortChange(item)} key={item}>{item}</button>)}</div><div className="composer"><textarea disabled={unavailable} value={props.input} onChange={(event) => props.onInputChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); props.onSend(); } }} placeholder={props.sessionLoading ? "正在加载会话…" : !props.modelConfigured ? "请先配置模型 API" : "Ask QA Agent…"} aria-label="聊天输入" /><button className="send-button" disabled={unavailable} onClick={props.streaming ? props.onStop : props.onSend} aria-label={props.streaming ? "停止生成" : "发送消息"}>{props.streaming ? <CircleStop size={17} /> : <ArrowUp size={17} />}</button></div><p className="composer-hint">Enter 发送 · Shift + Enter 换行</p></div></footer>
  </motion.section>;
}
