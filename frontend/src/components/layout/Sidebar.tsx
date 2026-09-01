import { useState } from "react";
import { Image as ImageIcon, LogOut, MessageSquare, MessageSquarePlus, Pencil, Search, Settings, Trash2 } from "lucide-react";

import type { SessionSummary } from "../../types";

interface SidebarProps {
  sessions: SessionSummary[];
  sessionId: string;
  busy: boolean;
  collapsed: boolean;
  mobileOpen: boolean;
  userLabel: string;
  onNewSession: () => void;
  onSelectSession: (session: SessionSummary) => void;
  onRenameSession: (session: SessionSummary, name: string) => void;
  onDeleteSession: (session: SessionSummary) => void;
  onToggle: () => void;
  onOpenSettings: () => void;
  onOpenBackground: () => void;
  onLogout: () => void;
}

export function Sidebar(props: SidebarProps) {
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const filtered = props.sessions.filter((session) => session.name.toLowerCase().includes(query.trim().toLowerCase()));
  const initials = props.userLabel.slice(0, 1).toUpperCase() || "U";

  function startRename(session: SessionSummary) {
    setEditingId(session.sessionId);
    setEditName(session.name || "");
  }

  function commitRename(session: SessionSummary) {
    setEditingId(null);
    props.onRenameSession(session, editName);
  }

  return <>
    {props.mobileOpen && <div className="sidebar-backdrop" onClick={props.onToggle} />}
    <aside className={`sidebar ${props.collapsed ? "collapsed" : ""} ${props.mobileOpen ? "mobile-open" : ""}`} aria-label="会话侧边栏">
      <div className="sidebar-header"><button className="new-chat-button" disabled={props.busy} onClick={props.onNewSession}><MessageSquarePlus size={16} /><span>新建对话</span></button></div>
      <div className="sidebar-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索对话…" aria-label="搜索对话" /></div>
      <p className="sidebar-label">聊天记录</p>
      <nav className="session-list" aria-label="历史会话">
        {filtered.length === 0
          ? <p className="sidebar-empty">{props.sessions.length === 0 ? "暂无会话" : "没有匹配的对话"}</p>
          : filtered.map((session) => {
            const active = session.sessionId === props.sessionId;
            const editing = editingId === session.sessionId;
            return <div key={session.sessionId} className={`session-item ${active ? "active" : ""}`} role="button" tabIndex={0} onClick={() => { if (!editing) props.onSelectSession(session); }} onKeyDown={(event) => { if (event.key === "Enter" && !editing) props.onSelectSession(session); }} aria-current={active ? "page" : undefined}>
              <MessageSquare size={14} className="session-item-icon" />
              {editing
                ? <input className="session-item-edit" value={editName} autoFocus onChange={(event) => setEditName(event.target.value)} onBlur={() => commitRename(session)} onKeyDown={(event) => { if (event.key === "Enter") commitRename(session); if (event.key === "Escape") setEditingId(null); }} onClick={(event) => event.stopPropagation()} aria-label="会话名称" />
                : <span className="session-item-name">{session.name || "新对话"}</span>}
              {!editing && <span className="session-item-actions" onClick={(event) => event.stopPropagation()}>
                <button type="button" className="session-item-action" onClick={() => startRename(session)} aria-label="重命名" title="重命名"><Pencil size={13} /></button>
                <button type="button" className="session-item-action" onClick={() => props.onDeleteSession(session)} aria-label="删除" title="删除"><Trash2 size={13} /></button>
              </span>}
            </div>;
          })}
      </nav>
      <div className="sidebar-footer">
        <span className="user-avatar">{initials}</span>
        <span className="user-name">{props.userLabel}</span>
        <button className="icon-button" onClick={props.onOpenBackground} aria-label="聊天背景" title="聊天背景"><ImageIcon size={16} /></button>
        <button className="icon-button" onClick={props.onOpenSettings} aria-label="模型与 API 设置" title="模型设置"><Settings size={16} /></button>
        <button className="icon-button" onClick={props.onLogout} aria-label="退出登录" title="退出登录"><LogOut size={16} /></button>
      </div>
    </aside>
  </>;
}
