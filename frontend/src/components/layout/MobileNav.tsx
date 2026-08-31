import { Drawer } from "../ui/Drawer";
import { SidebarContent } from "./Sidebar";
import type { SessionSummary } from "../../types";

interface MobileNavProps {
  open: boolean;
  sessions: SessionSummary[];
  sessionId: string;
  busy: boolean;
  onClose: () => void;
  onChat: () => void;
  onNewSession: () => void;
  onSelectSession: (session: SessionSummary) => void;
  onOpenRuntime: () => void;
  onOpenSettings: () => void;
  onOpenAppearance: () => void;
}

export function MobileNav({ open, onClose, onChat, onNewSession, onSelectSession, onOpenRuntime, onOpenSettings, onOpenAppearance, ...rest }: MobileNavProps) {
  const closeThen = (action: () => void) => () => { onClose(); action(); };
  return <Drawer open={open} side="left" eyebrow="Workspace" title="Navigation" className="mobile-nav-drawer" onClose={onClose}><SidebarContent {...rest} compact={false} scope="mobile" onChat={closeThen(onChat)} onNewSession={closeThen(onNewSession)} onSelectSession={(session) => { onClose(); onSelectSession(session); }} onOpenRuntime={closeThen(onOpenRuntime)} onOpenSettings={closeThen(onOpenSettings)} onOpenAppearance={closeThen(onOpenAppearance)} /></Drawer>;
}
