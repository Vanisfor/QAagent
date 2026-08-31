import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Activity, BrainCircuit, Command, ExternalLink, LogOut, Menu, Moon, Palette, Settings, Sun, UserRound } from "lucide-react";

interface TopNavProps {
  theme: "dark" | "light";
  modelName: string;
  modelConfigured: boolean;
  onOpenMobile: () => void;
  onOpenCommand: () => void;
  onOpenRuntime: () => void;
  onOpenSettings: () => void;
  onOpenAppearance: () => void;
  onToggleTheme: () => void;
  onLogout: () => void;
}

export function TopNav(props: TopNavProps) {
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!accountOpen) return;
    const close = (event: MouseEvent) => { if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [accountOpen]);

  return <header className="top-nav">
    <div className="top-nav-leading"><button className="icon-button mobile-menu-button" onClick={props.onOpenMobile} aria-label="打开导航" title="Navigation"><Menu size={18} /></button><a className="product-mark" href="#workspace" aria-label="QA Agent 工作区"><span><BrainCircuit size={18} /></span><strong>QA Agent</strong></a><nav className="primary-nav" aria-label="主要导航"><a className="active" href="#workspace">Workspace</a></nav></div>
    <div className="top-nav-actions">
      <button className="command-trigger" onClick={props.onOpenCommand}><Command size={15} /><span>Command</span><kbd>Ctrl K</kbd></button>
      <button className="icon-button runtime-trigger" onClick={props.onOpenRuntime} aria-label="打开运行状态" title="Execution"><Activity size={17} /></button>
      <a className="icon-button" href="https://github.com/Vanisfor/QAagent" target="_blank" rel="noreferrer" aria-label="打开 GitHub 仓库" title="GitHub"><ExternalLink size={17} /></a>
      <button className="icon-button" onClick={props.onToggleTheme} aria-label={props.theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} title={props.theme === "dark" ? "Light theme" : "Dark theme"}>{props.theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button>
      <div className="account-menu" ref={accountRef}><button className={`account-trigger ${props.modelConfigured ? "configured" : ""}`} onClick={() => setAccountOpen((current) => !current)} aria-expanded={accountOpen}><UserRound size={16} /><span className="account-model">{props.modelConfigured ? props.modelName : "Setup required"}</span></button><AnimatePresence>{accountOpen && <motion.div className="account-popover" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.16 }}>
        <button onClick={() => { setAccountOpen(false); props.onOpenSettings(); }}><Settings size={15} />Models & API</button>
        <button onClick={() => { setAccountOpen(false); props.onOpenAppearance(); }}><Palette size={15} />Appearance</button>
        <span className="menu-separator" />
        <button onClick={props.onLogout}><LogOut size={15} />Sign out</button>
      </motion.div>}</AnimatePresence></div>
    </div>
  </header>;
}
