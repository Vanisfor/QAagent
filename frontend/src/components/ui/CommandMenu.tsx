import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Search } from "lucide-react";

export interface CommandAction {
  id: string;
  label: string;
  group: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
}

interface CommandMenuProps {
  open: boolean;
  actions: CommandAction[];
  onClose: () => void;
}

export function CommandMenu({ open, actions, onClose }: CommandMenuProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const reduceMotion = useReducedMotion();
  useEffect(() => {
    if (!open) { setQuery(""); return; }
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => inputRef.current?.focus(), 0);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled])")];
      if (focusable.length === 0) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => { window.removeEventListener("keydown", handleKeyDown); previousFocusRef.current?.focus(); };
  }, [onClose, open]);
  const filtered = useMemo(() => actions.filter((action) => `${action.group} ${action.label}`.toLowerCase().includes(query.toLowerCase())), [actions, query]);
  function run(action: CommandAction) { onClose(); action.run(); }

  return <AnimatePresence>{open && <motion.div className="command-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <motion.section ref={dialogRef} className="command-menu" role="dialog" aria-modal="true" aria-label="命令菜单" initial={{ opacity: 0, y: reduceMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reduceMotion ? 0 : 6 }} transition={{ duration: reduceMotion ? 0.1 : 0.2 }}>
      <label className="command-search"><Search size={17} /><input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索会话或操作…" aria-label="搜索命令" /><kbd>Esc</kbd></label>
      <div className="command-results">{filtered.length === 0 ? <p className="command-empty">没有匹配的操作</p> : filtered.map((action) => <button key={action.id} type="button" onClick={() => run(action)}><span className="command-icon">{action.icon}</span><span><strong>{action.label}</strong><small>{action.group}</small></span>{action.hint && <kbd>{action.hint}</kbd>}</button>)}</div>
    </motion.section>
  </motion.div>}</AnimatePresence>;
}
