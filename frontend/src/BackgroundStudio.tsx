import { type ChangeEvent, useEffect, useRef, useState } from "react";
import { ImagePlus, Trash2, X } from "lucide-react";

export type BackgroundPreset = "aurora" | "threads" | "grid" | "solid" | "custom";

export interface BackgroundSettings {
  preset: BackgroundPreset;
  accent: string;
  intensity: number;
  speed: number;
  imageDataUrl?: string;
}

export const defaultBackground: BackgroundSettings = {
  preset: "aurora",
  accent: "#22c55e",
  intensity: 55,
  speed: 45,
};

const MAX_BACKGROUND_BYTES = 20 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

interface BackgroundStudioProps {
  open: boolean;
  settings: BackgroundSettings;
  persistenceError?: string;
  onChange: (settings: BackgroundSettings) => void;
  onClose: () => void;
}

export function BackgroundStudio({ open, settings, persistenceError, onChange, onClose }: BackgroundStudioProps) {
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    setError("");
    window.setTimeout(() => closeButtonRef.current?.focus(), 0);
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  function selectImage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
      setError("仅支持 JPG、PNG 或 WebP 图片。");
      return;
    }
    if (file.size > MAX_BACKGROUND_BYTES) {
      setError("图片不能超过 2 MB，请先压缩后再上传。");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== "string") { setError("图片读取失败，请重新选择。"); return; }
      setError("");
      onChange({ ...settings, preset: "custom", imageDataUrl: reader.result });
    };
    reader.onerror = () => setError("图片读取失败，请重新选择。");
    reader.readAsDataURL(file);
  }

  if (!open) return null;
  const presets: Array<{ value: Exclude<BackgroundPreset, "custom">; label: string }> = [
    { value: "aurora", label: "极光" },
    { value: "threads", label: "涟漪" },
    { value: "grid", label: "网格" },
    { value: "solid", label: "纯色" },
  ];

  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="background-studio glass-panel" role="dialog" aria-modal="true" aria-labelledby="background-studio-title">
      <div className="drawer-title"><div><p className="eyebrow">APPEARANCE</p><h2 id="background-studio-title">背景工作室</h2></div><button ref={closeButtonRef} onClick={onClose} aria-label="关闭背景设置"><X /></button></div>
      <label>预设<div className="preset-grid">{presets.map((preset) => <button type="button" className={settings.preset === preset.value ? "active" : ""} onClick={() => onChange({ ...settings, preset: preset.value })} key={preset.value}><span className={`preset-preview bg-${preset.value}`} />{preset.label}</button>)}</div></label>
      <section className="custom-background-control"><div><strong>自定义图片</strong><p>仅保存在当前浏览器，支持 JPG、PNG、WebP，最大 2 MB。</p></div>{settings.imageDataUrl && <div className="custom-image-preview" style={{ backgroundImage: `url(${settings.imageDataUrl})` }} role="img" aria-label="当前自定义背景预览" />}
        <input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={selectImage} hidden />
        {settings.imageDataUrl && settings.preset !== "custom" && <button type="button" className="upload-button" onClick={() => onChange({ ...settings, preset: "custom" })}><ImagePlus size={16} />使用当前图片</button>}
        <button type="button" className="upload-button" onClick={() => fileInputRef.current?.click()}><ImagePlus size={16} />{settings.imageDataUrl ? "更换图片" : "选择图片"}</button>
        {settings.imageDataUrl && <button type="button" className="remove-background-button" onClick={() => onChange({ ...settings, preset: "aurora", imageDataUrl: undefined })}><Trash2 size={15} />移除图片</button>}
        {error && <p className="form-error" role="alert">{error}</p>}
        {persistenceError && <p className="form-error" role="alert">{persistenceError}</p>}
      </section>
      {settings.preset !== "custom" && <label htmlFor="background-accent">强调色<input id="background-accent" type="color" value={settings.accent} onChange={(event) => onChange({ ...settings, accent: event.target.value })} /></label>}
      <label htmlFor="background-intensity">{settings.preset === "custom" ? "图片强度" : "强度"} <b>{settings.intensity}%</b><input id="background-intensity" type="range" min="0" max="100" value={settings.intensity} onChange={(event) => onChange({ ...settings, intensity: Number(event.target.value) })} /></label>
      {settings.preset !== "custom" && <label htmlFor="background-speed">速度 <b>{settings.speed}%</b><input id="background-speed" type="range" min="0" max="100" value={settings.speed} onChange={(event) => onChange({ ...settings, speed: Number(event.target.value) })} /></label>}
      <button className="secondary-button" onClick={() => onChange(defaultBackground)}>恢复默认</button>
    </aside>
  </div>;
}
