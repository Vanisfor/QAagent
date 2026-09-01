import { useRef, useState, type ChangeEvent } from "react";
import { ImagePlus, Trash2 } from "lucide-react";

import { Modal } from "./components/ui/Modal";

export type ChatBackgroundPreset = "none" | "green" | "sunset" | "ocean" | "lavender" | "charcoal" | "custom";

export interface ChatBackgroundSettings {
  preset: ChatBackgroundPreset;
  imageDataUrl?: string;
  brightness: number;
  blur: number;
  opacity: number;
}

export const defaultChatBackground: ChatBackgroundSettings = { preset: "none", brightness: 1, blur: 0, opacity: 1 };

export const PRESET_GRADIENTS: Record<string, string> = {
  green: "linear-gradient(160deg, #b9e0ae 0%, #d9f2d3 55%, #f2faf0 100%)",
  sunset: "linear-gradient(160deg, #ffd9a0 0%, #ffb9a8 50%, #ffe6dc 100%)",
  ocean: "linear-gradient(160deg, #a3d8ff 0%, #cfeaff 55%, #f0f8ff 100%)",
  lavender: "linear-gradient(160deg, #d3c4f4 0%, #e9e1fb 55%, #f9f6ff 100%)",
  charcoal: "linear-gradient(160deg, #3c3c42 0%, #2c2c30 55%, #212124 100%)",
};

const MAX_BACKGROUND_BYTES = 2 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const PRESETS: Array<{ value: Exclude<ChatBackgroundPreset, "custom">; label: string }> = [
  { value: "none", label: "无背景" },
  { value: "green", label: "柔绿" },
  { value: "sunset", label: "暖阳" },
  { value: "ocean", label: "海蓝" },
  { value: "lavender", label: "淡紫" },
  { value: "charcoal", label: "墨黑" },
];

interface BackgroundSettingsModalProps {
  open: boolean;
  settings: ChatBackgroundSettings;
  persistenceError?: string;
  onClose: () => void;
  onChange: (settings: ChatBackgroundSettings) => void;
}

export function BackgroundSettingsModal({ open, settings, persistenceError, onClose, onChange }: BackgroundSettingsModalProps) {
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  function selectImage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) { setError("仅支持 JPG、PNG 或 WebP 图片。"); return; }
    if (file.size > MAX_BACKGROUND_BYTES) { setError("图片不能超过 2 MB，请先压缩后再上传。"); return; }
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== "string") { setError("图片读取失败，请重新选择。"); return; }
      setError(""); onChange({ ...settings, preset: "custom", imageDataUrl: reader.result });
    };
    reader.onerror = () => setError("图片读取失败，请重新选择。");
    reader.readAsDataURL(file);
  }

  function selectPreset(preset: Exclude<ChatBackgroundPreset, "custom">) {
    onChange({ ...settings, preset, imageDataUrl: preset === "none" ? undefined : settings.imageDataUrl });
  }

  return <Modal open={open} title="聊天背景" onClose={onClose}>
    <div className="background-studio">
      <section className="preference-section"><header><h3>背景样式</h3><p>选择预设配色，或上传一张图片作为聊天背景。</p></header>
        <div className="preset-grid">{PRESETS.map((preset) => <button type="button" key={preset.value} className={settings.preset === preset.value ? "active" : ""} onClick={() => selectPreset(preset.value)}><span className={`preset-preview ${preset.value === "none" ? "preset-none" : ""}`} style={preset.value === "none" ? undefined : { background: PRESET_GRADIENTS[preset.value] }} />{preset.label}</button>)}</div>
      </section>
      <section className="preference-section"><header><h3>自定义图片</h3><p>支持 JPG、PNG、WebP，最大 2 MB，仅保存在当前浏览器。</p></header>
        {settings.imageDataUrl && <div className="custom-image-preview" style={{ backgroundImage: `url(${settings.imageDataUrl})` }} role="img" aria-label="当前背景预览" />}
        <input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={selectImage} hidden />
        {settings.imageDataUrl && settings.preset !== "custom" && <button type="button" className="secondary-button" onClick={() => onChange({ ...settings, preset: "custom" })}><ImagePlus size={15} />使用当前图片</button>}
        <button type="button" className="secondary-button" onClick={() => fileInputRef.current?.click()}><ImagePlus size={15} />{settings.imageDataUrl ? "更换图片" : "选择图片"}</button>
        {settings.imageDataUrl && <button type="button" className="danger-button" onClick={() => onChange({ ...settings, preset: "none", imageDataUrl: undefined })}><Trash2 size={15} />移除图片</button>}
        {error && <p className="form-error" role="alert">{error}</p>}
        {persistenceError && <p className="form-error" role="alert">{persistenceError}</p>}
      </section>
      <section className="preference-section"><header><h3>调整参数</h3></header>
        <label htmlFor="bg-brightness">亮度<b>{Math.round(settings.brightness * 100)}%</b><input id="bg-brightness" type="range" min="0.4" max="1.5" step="0.05" value={settings.brightness} onChange={(event) => onChange({ ...settings, brightness: Number(event.target.value) })} /></label>
        <label htmlFor="bg-blur">模糊<b>{settings.blur}px</b><input id="bg-blur" type="range" min="0" max="20" step="1" value={settings.blur} onChange={(event) => onChange({ ...settings, blur: Number(event.target.value) })} /></label>
        <label htmlFor="bg-opacity">强度<b>{Math.round(settings.opacity * 100)}%</b><input id="bg-opacity" type="range" min="0.3" max="1" step="0.05" value={settings.opacity} onChange={(event) => onChange({ ...settings, opacity: Number(event.target.value) })} /></label>
      </section>
      <button className="secondary-button full-width" onClick={() => onChange(defaultChatBackground)}>恢复默认</button>
    </div>
  </Modal>;
}
