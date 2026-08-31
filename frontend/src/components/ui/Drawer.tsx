import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { X } from "lucide-react";

interface DrawerProps {
  open: boolean;
  eyebrow?: string;
  title: string;
  side?: "left" | "right";
  className?: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
  onClose: () => void;
}

export function Drawer({ open, eyebrow, title, side = "right", className = "", initialFocusRef, children, onClose }: DrawerProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => (initialFocusRef?.current ?? closeRef.current)?.focus(), 0);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]")];
      if (focusable.length === 0) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => { window.removeEventListener("keydown", handleKeyDown); previousFocusRef.current?.focus(); };
  }, [initialFocusRef, onClose, open]);

  const offset = reduceMotion ? 0 : side === "right" ? 20 : -20;
  return <AnimatePresence>{open && <motion.div className="overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <motion.aside ref={dialogRef} className={`drawer drawer-${side} ${className}`} role="dialog" aria-modal="true" aria-labelledby={`${title.replaceAll(" ", "-")}-title`} initial={{ opacity: 0, x: offset }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: offset }} transition={{ duration: reduceMotion ? 0.1 : 0.22, ease: [0.22, 1, 0.36, 1] }}>
      <header className="drawer-header"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2 id={`${title.replaceAll(" ", "-")}-title`}>{title}</h2></div><button ref={closeRef} className="icon-button" onClick={onClose} aria-label={`关闭${title}`}><X size={18} /></button></header>
      <div className="drawer-content">{children}</div>
    </motion.aside>
  </motion.div>}</AnimatePresence>;
}
