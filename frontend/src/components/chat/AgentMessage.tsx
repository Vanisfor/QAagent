import { BrainCircuit } from "lucide-react";

import { MarkdownMessage } from "../../MarkdownMessage";
import type { ChatMessage } from "../../types";

interface AgentMessageProps {
  message: ChatMessage;
  streaming: boolean;
}

export function AgentMessage({ message, streaming }: AgentMessageProps) {
  if (message.role === "user") {
    return <div className="message-row user-message"><div className="message-content">{message.content}</div></div>;
  }
  return <div className="message-row agent-message">
    <div className="agent-label"><BrainCircuit size={15} />QA Agent{streaming && <span className="streaming-label">正在生成</span>}</div>
    <div className="message-content markdown-content"><MarkdownMessage>{message.content || (streaming ? "" : "")}</MarkdownMessage></div>
  </div>;
}
