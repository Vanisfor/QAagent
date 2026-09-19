import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronUp, LogOut, Palette, Settings, UserRound } from "lucide-react";

import type { SettingsCategory } from "../../settings/SettingsModal";

interface UserProfileMenuProps {
  userLabel: string;
  displayName: string;
  avatar: string;
  onOpenSettings: (category: SettingsCategory) => void;
  onLogout: () => void;
}

export function UserProfileMenu(props: UserProfileMenuProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 8, bottom: 8, maxHeight: 300 });
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const initials = props.displayName.slice(0, 1).toUpperCase() || "U";

  function closeMenu() { setOpen(false); trigger.current?.focus(); }

  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect || !rect.width) { setOpen(false); return; }
      setPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 288)), bottom: Math.max(8, window.innerHeight - rect.top + 8), maxHeight: Math.max(0, rect.top - 16) });
    };
    update();
    const observer = new ResizeObserver(update);
    if (trigger.current) observer.observe(trigger.current);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !trigger.current?.contains(event.target)) setOpen(false);
    };
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeMenu(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", keyboard);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", keyboard); };
  }, [open]);

  const items = [
    { label: "个性化", icon: Palette, action: () => props.onOpenSettings("personalization") },
    { label: "设置", icon: Settings, action: () => props.onOpenSettings("general") },
    { label: "账户", icon: UserRound, action: () => props.onOpenSettings("account") },
    { label: "退出登录", icon: LogOut, action: props.onLogout },
  ];

  return <>
    <button ref={trigger} type="button" className="profile-trigger" aria-label={`账户菜单：${props.userLabel}`} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} onClick={() => setOpen(!open)} onKeyDown={(event) => { if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); setOpen(true); } }}>
      <span className="user-avatar">{props.avatar ? <img src={props.avatar} alt="" /> : initials}</span><span className="user-name"><strong>{props.displayName}</strong><small>{props.userLabel}</small></span><ChevronUp size={16} className="profile-chevron" />
    </button>
    {open && createPortal(<div ref={menu} id={menuId} className="profile-menu" role="menu" aria-label="账户菜单" style={position} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== trigger.current) setOpen(false); }} onKeyDown={(event) => {
      const buttons = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? []);
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      let next = index;
      if (event.key === "ArrowDown") next = (index + 1) % buttons.length;
      else if (event.key === "ArrowUp") next = (index - 1 + buttons.length) % buttons.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = buttons.length - 1;
      else return;
      event.preventDefault(); buttons[next]?.focus();
    }}>
      <div className="profile-menu-heading" role="presentation"><span className="user-avatar">{props.avatar ? <img src={props.avatar} alt="" /> : initials}</span><strong>{props.displayName}</strong><span>{props.userLabel}</span></div>
      {items.map(({ label, icon: Icon, action }, index) => <button key={label} type="button" role="menuitem" tabIndex={-1} className={index === items.length - 1 ? "profile-logout" : ""} onClick={() => { closeMenu(); action(); }}><Icon size={17} /><span>{label}</span></button>)}
    </div>, document.body)}

  </>;
}
