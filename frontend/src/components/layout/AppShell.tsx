import type { ReactNode } from "react";

interface AppShellProps {
  sidebar: ReactNode;
  main: ReactNode;
  context?: ReactNode;
}

export function AppShell({ sidebar, main, context }: AppShellProps) {
  return <div className="app">{sidebar}<div className="main-area">{main}</div>{context ? <div className="run-slot">{context}</div> : null}</div>;
}
