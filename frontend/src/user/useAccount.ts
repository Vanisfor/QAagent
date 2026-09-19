import { useEffect, useState } from "react";

import { apiFetch } from "../auth/transport";
import type { Account, Appearance, Personalization, UserProfile } from "./types";

async function request<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init?.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await apiFetch(`/api/v1/users/me${path}`, { ...init, headers });
  if (!response.ok) {
    const data = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new Error(typeof data.detail === "string" ? data.detail : `操作失败（HTTP ${response.status}），请检查填写内容。`);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

export function useAccount(token: string) {
  const [account, setAccount] = useState<Account | null>(null);
  const [appearance, setAppearance] = useState<Appearance | null>(null);
  const [personalization, setPersonalization] = useState<Personalization | null>(null);
  const [error, setError] = useState("");
  const [avatar, setAvatar] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([request<Account>(token, ""), request<Appearance>(token, "/settings"), request<Personalization>(token, "/personalization")]).then(([user, settings, preferences]) => {
      if (cancelled) return;
      setAccount(user); setAppearance(settings); setPersonalization(preferences); setError("");
      try { localStorage.setItem(`qa-appearance:${user.id}`, JSON.stringify(settings)); } catch { /* Backend remains authoritative. */ }
    }).catch((reason: Error) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, [revision]);

  useEffect(() => {
    const path = account?.profile.avatar_url;
    setAvatar("");
    if (!path) return;
    let cancelled = false;
    let url = "";
    void apiFetch(path, { headers: { Authorization: `Bearer ${token}` } }).then(async (response) => {
      if (!response.ok) throw new Error("头像加载失败。");
      const blob = await response.blob();
      if (!cancelled) { url = URL.createObjectURL(blob); setAvatar(url); }
    }).catch((reason: Error) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [account?.profile.avatar_url]);

  async function saveAppearance(value: Appearance) {
    const saved = await request<Appearance>(token, "/settings", { method: "PATCH", body: JSON.stringify(value) });
    setAppearance(saved);
  }

  async function savePersonalization(value: Personalization) {
    setPersonalization(await request<Personalization>(token, "/personalization", { method: "PATCH", body: JSON.stringify(value) }));
  }

  async function saveProfile(value: UserProfile) {
    const { avatar_url: _avatar, ...editable } = value;
    const saved = await request<UserProfile>(token, "/profile", { method: "PATCH", body: JSON.stringify(editable) });
    setAccount((current) => current ? { ...current, profile: saved } : current);
  }

  async function uploadAvatar(file: File) {
    const body = new FormData(); body.append("file", file);
    const saved = await request<UserProfile>(token, "/avatar", { method: "POST", body });
    setAccount((current) => current ? { ...current, profile: saved } : current);
  }

  async function deleteAvatar() {
    await request<void>(token, "/avatar", { method: "DELETE" });
    setAccount((current) => current ? { ...current, profile: { ...current.profile, avatar_url: null } } : current);
  }

  return { account, appearance, personalization, avatar, error, saveAppearance, savePersonalization, saveProfile, uploadAvatar, deleteAvatar, retry: () => setRevision((value) => value + 1) };
}

export type AccountController = ReturnType<typeof useAccount>;
