import type { ReactNode } from "react";

interface AppShellProps {
  topNav: ReactNode;
  sidebar: ReactNode;
  children: ReactNode;
  context: ReactNode;
}

export function AppShell({ topNav, sidebar, children, context }: AppShellProps) {
  return <div className="app-shell"><div className="top-nav-slot">{topNav}</div><div className="sidebar-slot">{sidebar}</div><main className="main-slot">{children}</main><aside className="context-slot">{context}</aside></div>;
}
