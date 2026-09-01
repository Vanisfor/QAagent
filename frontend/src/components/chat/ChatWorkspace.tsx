import { ArrowUp, BookOpen, CircleStop, Code2, FileText, Languages, Settings, Sparkles } from "lucide-react";
import type { RefObject } from "react";

import { AgentMessage } from "./AgentMessage";
import type { ChatMessage } from "../../types";

const SUGGESTIONS = [
  { icon: <BookOpen size={16} />, text: "用通俗的语言解释什么是 RAG" },
  { icon: <Code2 size={16} />, text: "写一个 Python 函数：去重并保持顺序" },
  { icon: <FileText size={16} />, text: "总结一份长文档的核心要点" },
  { icon: <Languages size={16} />, text: "把一段中文翻译成英文并润色" },
];

interface ChatWorkspaceProps {
  messages: ChatMessage[];
  input: string;
  streaming: boolean;
  modelConfigured: boolean;
  endRef: RefObject<HTMLDivElement | null>;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onSendPrompt: (prompt: string) => void;
  onStop: () => void;
  onOpenSettings: () => void;
}

export function ChatWorkspace(props: ChatWorkspaceProps) {
  const disabled = !props.modelConfigured;
  const emptyTitle = props.modelConfigured ? "有什么可以帮你？" : "连接你的模型";
  const emptyDescription = props.modelConfigured ? "选择下方的一个建议开始，或直接输入你的问题。" : "添加并验证你的 DeepSeek API Key 后即可开始对话。";

  return <section className="chat-workspace">
    <div className="chat-scroll"><div className="chat-column">
      {props.messages.length === 0
        ? <div className="chat-empty">
            <span className="brand-mark"><Sparkles size={20} /></span>
            <h1>{emptyTitle}</h1>
            <p>{emptyDescription}</p>
            {props.modelConfigured && <div className="suggestion-grid">{SUGGESTIONS.map((item) => <button type="button" key={item.text} className="suggestion-card" onClick={() => props.onSendPrompt(item.text)}>{item.icon}<span>{item.text}</span></button>)}</div>}
            {!props.modelConfigured && <button className="secondary-button" onClick={props.onOpenSettings}><Settings size={15} />模型设置</button>}
          </div>
        : props.messages.map((message, index) => <AgentMessage key={message.id} message={message} streaming={props.streaming && index === props.messages.length - 1} />)}
      <div ref={props.endRef} />
    </div></div>
    <footer className="composer-region"><div className="composer-column">
      <div className="composer">
        <textarea disabled={disabled} value={props.input} onChange={(event) => props.onInputChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); props.onSend(); } }} placeholder={disabled ? "请先配置模型 API" : "发送消息…"} aria-label="聊天输入" />
        <div className="composer-actions"><button className="send-button" disabled={disabled || (!props.input.trim() && !props.streaming)} onClick={props.streaming ? props.onStop : props.onSend} aria-label={props.streaming ? "停止生成" : "发送消息"}>{props.streaming ? <CircleStop size={17} /> : <ArrowUp size={17} />}</button></div>
      </div>
      <p className="composer-hint">Enter 发送 · Shift+Enter 换行</p>
    </div></footer>
  </section>;
}
