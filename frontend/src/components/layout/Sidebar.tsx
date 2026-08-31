import { motion, useReducedMotion } from "motion/react";
import { Activity, History, MessageSquare, Palette, PanelLeftClose, PanelLeftOpen, Plus, Settings } from "lucide-react";

import type { SessionSummary } from "../../types";

interface SidebarContentProps {
  sessions: SessionSummary[];
  sessionId: string;
  busy: boolean;
  compact?: boolean;
  scope: string;
  onChat: () => void;
  onNewSession: () => void;
  onSelectSession: (session: SessionSummary) => void;
  onOpenRuntime: () => void;
  onOpenSettings: () => void;
  onOpenAppearance: () => void;
}

function ActionButton({ icon, label, active = false, disabled = false, onClick }: { icon: React.ReactNode; label: string; active?: boolean; disabled?: boolean; onClick: () => void }) {
  const reduceMotion = useReducedMotion();
  return <motion.button type="button" className={`nav-item ${active ? "active" : ""}`} disabled={disabled} onClick={onClick} aria-label={label} title={label} whileHover={reduceMotion ? undefined : { x: 2 }} whileTap={reduceMotion ? undefined : { scale: 0.985 }}>{icon}<span>{label}</span></motion.button>;
}

export function SidebarContent(props: SidebarContentProps) {
  return <div className={`sidebar-content ${props.compact ? "compact" : ""}`}>
    <nav aria-label="工作区导航">
      <p className="nav-group-label">Workspace</p>
      <ActionButton icon={<MessageSquare size={16} />} label="Chat" active onClick={props.onChat} />
      <ActionButton icon={<Plus size={16} />} label="New session" disabled={props.busy} onClick={props.onNewSession} />
      <p className="nav-group-label">Runtime</p>
      <ActionButton icon={<Activity size={16} />} label="Execution" onClick={props.onOpenRuntime} />
      <p className="nav-group-label">Settings</p>
      <ActionButton icon={<Settings size={16} />} label="Models & API" onClick={props.onOpenSettings} />
      <ActionButton icon={<Palette size={16} />} label="Appearance" onClick={props.onOpenAppearance} />
    </nav>
    <div className="history-section"><p className="nav-group-label"><History size={13} />History</p><nav className="history-list" aria-label="历史会话">{props.sessions.length === 0 ? <p className="nav-empty">暂无会话</p> : props.sessions.map((session) => <motion.button layout type="button" className={`history-item ${session.sessionId === props.sessionId ? "active" : ""}`} disabled={props.busy} onClick={() => props.onSelectSession(session)} aria-current={session.sessionId === props.sessionId ? "page" : undefined} key={session.sessionId}>{session.sessionId === props.sessionId && <motion.span className="history-active-indicator" layoutId={`history-active-${props.scope}`} />}<span className="history-name">{session.name || "新对话"}</span><small>{session.sessionId === props.sessionId ? "Current" : "Session"}</small></motion.button>)}</nav></div>
  </div>;
}

interface SidebarProps extends Omit<SidebarContentProps, "compact" | "scope"> {
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ collapsed, onToggle, ...contentProps }: SidebarProps) {
  return <aside className={`desktop-sidebar ${collapsed ? "collapsed" : ""}`}><div className="sidebar-toolbar"><span>{collapsed ? "" : "Navigation"}</span><button className="icon-button" onClick={onToggle} aria-label={collapsed ? "展开侧栏" : "收起侧栏"} title={collapsed ? "展开侧栏" : "收起侧栏"}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button></div><SidebarContent {...contentProps} compact={collapsed} scope="desktop" /></aside>;
}
