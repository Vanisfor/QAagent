import { useEffect, useRef, useState, type FormEvent } from "react";
import { CheckCircle2, KeyRound, ShieldCheck, Trash2 } from "lucide-react";

import { deleteLLMSettings, getLLMSettings, saveLLMSettings, testLLMSettings } from "./api";
import { Modal } from "./components/ui/Modal";
import type { LLMSettings, LLMSettingsInput } from "./types";

interface ModelSettingsModalProps {
  open: boolean;
  userToken: string;
  onClose: () => void;
  onChange: (settings: LLMSettings) => void;
}

const defaultForm: LLMSettingsInput = {
  provider: "deepseek",
  model: "deepseek-v4-flash",
  base_url: "https://api.deepseek.com",
  temperature: 0.2,
  max_tokens: 2000,
  thinking_enabled: false,
};

function formFromSettings(settings: LLMSettings): LLMSettingsInput {
  return {
    provider: settings.provider,
    model: settings.model,
    base_url: settings.base_url,
    temperature: settings.temperature,
    max_tokens: settings.max_tokens,
    thinking_enabled: settings.thinking_enabled,
  };
}

export function ModelSettingsModal({ open, userToken, onClose, onChange }: ModelSettingsModalProps) {
  const [form, setForm] = useState<LLMSettingsInput>(defaultForm);
  const [maskedKey, setMaskedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const apiKeyRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) { setForm((current) => ({ ...current, api_key: undefined })); return; }
    setStatus(null);
    void getLLMSettings(userToken)
      .then((settings) => { setForm(formFromSettings(settings)); setMaskedKey(settings.api_key_masked); })
      .catch((reason) => setStatus({ kind: "error", text: reason instanceof Error ? reason.message : "无法读取设置" }));
  }, [open, userToken]);

  function payload(): LLMSettingsInput {
    const normalized = { ...form };
    if (!normalized.api_key?.trim()) delete normalized.api_key;
    return normalized;
  }

  async function testConnection() {
    setBusy(true); setStatus(null);
    try { await testLLMSettings(userToken, payload()); setStatus({ kind: "success", text: "连接验证成功，尚未保存更改。" }); }
    catch (reason) { setStatus({ kind: "error", text: reason instanceof Error ? reason.message : "连接验证失败" }); }
    finally { setBusy(false); }
  }

  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setStatus(null);
    try {
      const saved = await saveLLMSettings(userToken, payload());
      setForm(formFromSettings(saved)); setMaskedKey(saved.api_key_masked); onChange(saved);
      setStatus({ kind: "success", text: "验证通过，配置已加密保存。" });
    } catch (reason) { setStatus({ kind: "error", text: reason instanceof Error ? reason.message : "配置保存失败" }); }
    finally { setBusy(false); }
  }

  async function removeCredential() {
    if (!window.confirm("删除后将无法继续聊天，除非重新配置并验证 API Key。确定删除吗？")) return;
    setBusy(true); setStatus(null);
    try {
      await deleteLLMSettings(userToken);
      const empty: LLMSettings = { ...defaultForm, configured: false, api_key_masked: null, validated_at: null };
      setForm(defaultForm); setMaskedKey(null); onChange(empty);
      setStatus({ kind: "success", text: "凭据和模型配置已删除。" });
    } catch (reason) { setStatus({ kind: "error", text: reason instanceof Error ? reason.message : "删除失败" }); }
    finally { setBusy(false); }
  }

  return <Modal open={open} title="模型与 API 设置" onClose={onClose}>
    <div className="security-note"><ShieldCheck size={18} /><div><strong>密钥由服务器加密保存</strong><p>完整 API Key 不会返回浏览器，也不会写入聊天记录、日志或追踪。</p></div></div>
    <form className="settings-form" onSubmit={save}>
      <label htmlFor="provider">API 供应商<select id="provider" value={form.provider} disabled><option value="deepseek">DeepSeek</option></select></label>
      <label htmlFor="base-url">API Base URL<input id="base-url" type="url" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} required /></label>
      <label htmlFor="api-key">API Key<div className="secret-input"><KeyRound size={15} /><input ref={apiKeyRef} id="api-key" type="password" autoComplete="new-password" value={form.api_key ?? ""} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder={maskedKey ? `${maskedKey}（留空则继续使用）` : "输入你的 DeepSeek API Key"} required={!maskedKey} /></div><small>{maskedKey ? `当前凭据：${maskedKey}` : "首次配置必须填写，保存后不可查看原文。"}</small></label>
      <label htmlFor="model">模型<input id="model" value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} required /></label>
      <div className="settings-grid"><label htmlFor="temperature">温度 <b>{form.temperature.toFixed(1)}</b><input id="temperature" type="range" min="0" max="2" step="0.1" value={form.temperature} onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })} /></label><label htmlFor="max-tokens">最大输出 Token<input id="max-tokens" type="number" min="1" max="8192" value={form.max_tokens} onChange={(event) => setForm({ ...form, max_tokens: Number(event.target.value) })} required /></label></div>
      <label className="toggle-row" htmlFor="thinking-enabled"><span><strong>允许推理模式</strong><small>开启后可在聊天框选择 HIGH 或 MAX。</small></span><input id="thinking-enabled" type="checkbox" checked={form.thinking_enabled} onChange={(event) => setForm({ ...form, thinking_enabled: event.target.checked })} /></label>
      {status && <div className={`settings-status ${status.kind}`} role="status">{status.kind === "success" && <CheckCircle2 size={16} />}<span>{status.text}</span></div>}
      <div className="settings-actions"><button type="button" className="secondary-button" onClick={() => void testConnection()} disabled={busy}>测试连接</button><button className="primary-button" disabled={busy}>{busy ? "处理中…" : "验证并保存"}</button></div>
      {maskedKey && <button type="button" className="danger-button" onClick={() => void removeCredential()} disabled={busy}><Trash2 size={15} />删除模型凭据</button>}
    </form>
  </Modal>;
}
