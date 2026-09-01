import { LogOut, Moon, PanelLeft, Sun } from "lucide-react";

interface TopBarProps {
  theme: "light" | "dark";
  modelName: string;
  modelConfigured: boolean;
  onToggleSidebar: () => void;
  onOpenSettings: () => void;
  onToggleTheme: () => void;
  onLogout: () => void;
}

export function TopBar(props: TopBarProps) {
  return <header className="top-bar">
    <div className="top-bar-actions"><button className="icon-button" onClick={props.onToggleSidebar} aria-label="切换侧边栏" title="切换侧边栏"><PanelLeft size={18} /></button></div>
    <button className={`model-pill top-bar-center ${props.modelConfigured ? "" : "needs-setup"}`} onClick={props.onOpenSettings} aria-label="模型与 API 设置" title="模型与 API 设置"><span className="model-dot" />{props.modelName}</button>
    <div className="top-bar-actions">
      <button className="icon-button" onClick={props.onToggleTheme} aria-label={props.theme === "light" ? "切换到深色主题" : "切换到浅色主题"} title={props.theme === "light" ? "深色主题" : "浅色主题"}>{props.theme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button>
      <button className="icon-button" onClick={props.onLogout} aria-label="退出登录" title="退出登录"><LogOut size={17} /></button>
    </div>
  </header>;
}
