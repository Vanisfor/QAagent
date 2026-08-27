import { useRef, useState, type ChangeEvent } from "react";
import { ImagePlus, Trash2 } from "lucide-react";

import { Drawer } from "./components/ui/Drawer";

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
  accent: "#84cc16",
  intensity: 35,
  speed: 30,
};

const MAX_BACKGROUND_BYTES = 2 * 1024 * 1024;
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

  const presets: Array<{ value: Exclude<BackgroundPreset, "custom">; label: string }> = [
    { value: "aurora", label: "Ambient" },
    { value: "threads", label: "Contours" },
    { value: "grid", label: "Grid" },
    { value: "solid", label: "Solid" },
  ];

  return <Drawer open={open} eyebrow="PREFERENCES" title="Appearance" className="background-studio" onClose={onClose}>
    <section className="preference-section"><header><h3>Background</h3><p>选择克制的技术背景，或上传仅保存在当前浏览器的图片。</p></header><div className="preset-grid">{presets.map((preset) => <button type="button" className={settings.preset === preset.value ? "active" : ""} onClick={() => onChange({ ...settings, preset: preset.value })} key={preset.value}><span className={`preset-preview bg-${preset.value}`} />{preset.label}</button>)}</div></section>
    <section className="preference-section custom-background-control"><header><h3>Custom image</h3><p>支持 JPG、PNG、WebP，最大 2 MB。图片不会上传到服务器。</p></header>{settings.imageDataUrl && <div className="custom-image-preview" style={{ backgroundImage: `url(${settings.imageDataUrl})` }} role="img" aria-label="当前自定义背景预览" />}<input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={selectImage} hidden />{settings.imageDataUrl && settings.preset !== "custom" && <button type="button" className="secondary-button" onClick={() => onChange({ ...settings, preset: "custom" })}><ImagePlus size={15} />使用当前图片</button>}<button type="button" className="secondary-button" onClick={() => fileInputRef.current?.click()}><ImagePlus size={15} />{settings.imageDataUrl ? "更换图片" : "选择图片"}</button>{settings.imageDataUrl && <button type="button" className="danger-button" onClick={() => onChange({ ...settings, preset: "aurora", imageDataUrl: undefined })}><Trash2 size={15} />移除图片</button>}{error && <p className="form-error" role="alert">{error}</p>}{persistenceError && <p className="form-error" role="alert">{persistenceError}</p>}</section>
    <section className="preference-section"><header><h3>Presentation</h3></header>{settings.preset !== "custom" && <label htmlFor="background-accent">Accent<input id="background-accent" type="color" value={settings.accent} onChange={(event) => onChange({ ...settings, accent: event.target.value })} /></label>}<label htmlFor="background-intensity">{settings.preset === "custom" ? "Image intensity" : "Intensity"}<b>{settings.intensity}%</b><input id="background-intensity" type="range" min="0" max="100" value={settings.intensity} onChange={(event) => onChange({ ...settings, intensity: Number(event.target.value) })} /></label>{settings.preset !== "custom" && <label htmlFor="background-speed">Motion<b>{settings.speed}%</b><input id="background-speed" type="range" min="0" max="100" value={settings.speed} onChange={(event) => onChange({ ...settings, speed: Number(event.target.value) })} /></label>}</section>
    <button className="secondary-button full-width" onClick={() => onChange(defaultBackground)}>恢复默认</button>
  </Drawer>;
}
