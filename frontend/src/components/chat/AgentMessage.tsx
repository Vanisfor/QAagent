import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { BrainCircuit, ChevronDown } from "lucide-react";

import { MarkdownMessage } from "../../MarkdownMessage";
import type { ChatMessage, ReasoningEffort } from "../../types";

interface AgentMessageProps {
  message: ChatMessage;
  effort: ReasoningEffort;
  reasoningOpen: boolean;
  streaming: boolean;
  onToggleReasoning: () => void;
}

export function AgentMessage({ message, effort, reasoningOpen, streaming, onToggleReasoning }: AgentMessageProps) {
  const reduceMotion = useReducedMotion();
  if (message.role === "user") return <motion.article layout className="message-row user-message" initial={{ opacity: 0, y: reduceMotion ? 0 : 6 }} animate={{ opacity: 1, y: 0 }}><div className="message-meta"><span>You</span></div><div className="message-content">{message.content}</div></motion.article>;
  return <motion.article layout className="message-row agent-message" initial={{ opacity: 0, y: reduceMotion ? 0 : 6 }} animate={{ opacity: 1, y: 0 }}>
    <div className="message-meta"><span className="agent-avatar"><BrainCircuit size={13} /></span><span>QA Agent</span>{streaming && <span className="streaming-label">Streaming</span>}</div>
    {message.reasoning && <section className="reasoning-disclosure"><button type="button" onClick={onToggleReasoning} aria-expanded={reasoningOpen}><BrainCircuit size={15} /><span>Reasoning</span><small>{effort.toUpperCase()}</small><ChevronDown size={14} className={reasoningOpen ? "rotate" : ""} /></button><AnimatePresence initial={false}>{reasoningOpen && <motion.div className="reasoning-content" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: reduceMotion ? 0.1 : 0.2 }}><pre>{message.reasoning}</pre></motion.div>}</AnimatePresence></section>}
    <div className="message-content markdown-content"><MarkdownMessage>{message.content || (streaming ? "正在生成…" : "")}</MarkdownMessage></div>
  </motion.article>;
}
