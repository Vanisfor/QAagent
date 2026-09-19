import { useEffect, useRef, useState } from "react";
import { Globe, ImagePlus, Palette, Settings, SlidersHorizontal, UserRound } from "lucide-react";

import { BackgroundSettingsModal } from "../BackgroundSettingsModal";
import { ModelSettingsModal } from "../ModelSettingsModal";
import { Modal } from "../components/ui/Modal";
import type { LLMSettings } from "../types";
import type { AccountController } from "../user/useAccount";
import type { Appearance, Personalization, UserProfile } from "../user/types";

export type SettingsCategory = "general" | "appearance" | "personalization" | "model" | "account";
const categories = [
  { id: "general", label: "通用", icon: Globe },
  { id: "appearance", label: "外观", icon: Palette },
  { id: "personalization", label: "个性化", icon: SlidersHorizontal },
  { id: "model", label: "模型与 API", icon: Settings },
  { id: "account", label: "账户", icon: UserRound },
] as const;

interface SettingsModalProps {
  category: SettingsCategory | null;
  onCategory: (value: SettingsCategory) => void;
  onClose: () => void;
  user: AccountController;
  token: string;
  onModelChange: (value: LLMSettings) => void;
}

export function SettingsModal({ category, onCategory, onClose, user, token, onModelChange }: SettingsModalProps) {
  const [appearance, setAppearance] = useState<Appearance | null>(null);
  const [preferences, setPreferences] = useState<Personalization | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => { setAppearance(user.appearance); setPreferences(user.personalization); setProfile(user.account?.profile ?? null); }, [user.appearance, user.personalization, user.account?.profile, category]);
  useEffect(() => { setStatus(""); setError(""); }, [category]);

  async function perform(action: () => Promise<void>) {
    setBusy(true); setStatus(""); setError("");
    try { await action(); setStatus("已保存到你的账户。"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请重试。"); }
    finally { setBusy(false); }
  }

  function avatarFile(file: File | undefined) {
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || !/\.(png|jpe?g|webp)$/i.test(file.name)) { setError("请选择 JPG、PNG 或 WebP 图片。"); return; }
    if (file.size > 2 * 1024 * 1024) { setError("头像不能超过 2 MB。"); return; }
    void perform(() => user.uploadAvatar(file));
  }

  const complete = profile && appearance && preferences;
  return <Modal open={category !== null} title="设置" onClose={onClose}>
    <div className="settings-grid">
      <nav className="settings-categories" aria-label="设置分类">{categories.map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-current={category === id ? "page" : undefined} onClick={() => onCategory(id)}><Icon size={16} />{label}</button>)}</nav>
      <div className="settings-section">
        <h3>{categories.find((item) => item.id === category)?.label}</h3>
        {user.error && <div><p className="form-error" role="alert">{user.error}</p><button className="secondary-button" onClick={user.retry}>重试</button></div>}
        {!complete && !user.error && category !== "model" && <p>正在读取账户设置…</p>}
        <fieldset disabled={busy} className="settings-fields">
          {category === "general" && profile && <div className="settings-form"><label>语言偏好<select value={profile.language} onChange={(event) => setProfile({ ...profile, language: event.target.value as UserProfile["language"] })}><option value="auto">跟随提问语言</option><option value="zh">中文</option><option value="en">English</option></select></label><label>时区<input value={profile.timezone} onChange={(event) => setProfile({ ...profile, timezone: event.target.value })} placeholder="Asia/Shanghai" /></label><p className="muted small">当前界面使用中文，语言与时区作为账户偏好供 Agent 参考。</p><button className="primary-button" onClick={() => void perform(() => user.saveProfile(profile))}>保存通用设置</button></div>}
          {category === "appearance" && appearance && <div className="profile-settings"><label>主题<select value={appearance.theme} onChange={(event) => setAppearance({ ...appearance, theme: event.target.value as Appearance["theme"] })}><option value="light">浅色</option><option value="dark">深色</option></select></label><label className="toggle-row">默认折叠侧边栏<input type="checkbox" checked={appearance.sidebar_collapsed} onChange={(event) => setAppearance({ ...appearance, sidebar_collapsed: event.target.checked })} /></label><BackgroundSettingsModal embedded settings={appearance.background} onClose={onClose} onChange={(background) => setAppearance({ ...appearance, background })} /><button className="primary-button" onClick={() => void perform(() => user.saveAppearance(appearance))}>保存外观设置</button></div>}
          {category === "personalization" && preferences && <div className="settings-form"><label>基础风格<select value={preferences.personality} onChange={(event) => setPreferences({ ...preferences, personality: event.target.value as Personalization["personality"] })}><option value="professional">专业</option><option value="friendly">友好</option><option value="direct">直接</option></select></label><label>回答长度<select value={preferences.verbosity} onChange={(event) => setPreferences({ ...preferences, verbosity: event.target.value as Personalization["verbosity"] })}><option value="concise">简洁</option><option value="balanced">适中</option><option value="detailed">详细</option></select></label><label>自定义指令<textarea maxLength={2000} rows={5} value={preferences.custom_instructions} onChange={(event) => setPreferences({ ...preferences, custom_instructions: event.target.value })} placeholder="希望助手了解什么，以及如何回答你？" /></label><label>技术深度<select value={preferences.response_style.technical_depth} onChange={(event) => setPreferences({ ...preferences, response_style: { ...preferences.response_style, technical_depth: event.target.value as Personalization["response_style"]["technical_depth"] } })}><option value="low">易于理解</option><option value="medium">适中</option><option value="high">深入技术细节</option></select></label><label className="toggle-row">使用 Markdown<input type="checkbox" checked={preferences.response_style.markdown} onChange={(event) => setPreferences({ ...preferences, response_style: { ...preferences.response_style, markdown: event.target.checked } })} /></label><label className="toggle-row">允许表情符号<input type="checkbox" checked={preferences.response_style.emoji} onChange={(event) => setPreferences({ ...preferences, response_style: { ...preferences.response_style, emoji: event.target.checked } })} /></label><label className="toggle-row">使用长期记忆<input type="checkbox" checked={preferences.memory_enabled} onChange={(event) => setPreferences({ ...preferences, memory_enabled: event.target.checked })} /></label><p className="muted small">关闭后不再检索或保存长期记忆，已有记忆仍保留。偏好从下一次提问起生效，不能改变系统规则或工具权限。</p><button className="primary-button" onClick={() => void perform(() => user.savePersonalization(preferences))}>保存个性化</button></div>}
          {category === "model" && <ModelSettingsModal embedded userToken={token} onClose={onClose} onChange={onModelChange} />}
          {category === "account" && profile && user.account && <div className="settings-form"><div className="account-avatar"><span className="user-avatar profile-avatar-preview">{user.avatar ? <img src={user.avatar} alt="当前头像" /> : profile.display_name.slice(0, 1)}</span><button type="button" className="secondary-button" onClick={() => fileInput.current?.click()}><ImagePlus size={15} />更改头像</button>{profile.avatar_url && <button className="secondary-button" onClick={() => void perform(user.deleteAvatar)}>恢复默认</button>}<input ref={fileInput} type="file" hidden accept="image/png,image/jpeg,image/webp" onChange={(event) => { avatarFile(event.target.files?.[0]); event.target.value = ""; }} /></div><p className="muted small">支持 JPG、PNG、WebP，最大 2 MB。头像保存在账户中。</p><label>显示名称<input maxLength={50} value={profile.display_name} onChange={(event) => setProfile({ ...profile, display_name: event.target.value })} /></label><label>简介<textarea rows={3} maxLength={500} value={profile.bio} onChange={(event) => setProfile({ ...profile, bio: event.target.value })} /></label><dl><dt>邮箱</dt><dd>{user.account.email}</dd><dt>账户状态</dt><dd>{user.account.status === "active" ? "正常" : "不可用"}</dd><dt>注册时间</dt><dd>{new Date(user.account.created_at).toLocaleDateString()}</dd></dl><p className="muted small">邮箱修改、密码重置和设备管理将在后续开放。</p><button className="primary-button" onClick={() => void perform(() => user.saveProfile(profile))}>保存账户资料</button></div>}
        </fieldset>
        {busy && <p role="status">正在保存…</p>}{status && <p role="status">{status}</p>}{error && <p className="form-error" role="alert">{error}</p>}
      </div>
    </div>
  </Modal>;
}
