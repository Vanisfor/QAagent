import type { ChatBackgroundSettings } from "../BackgroundSettingsModal";

export interface UserProfile {
  display_name: string;
  avatar_url: string | null;
  bio: string;
  language: "auto" | "zh" | "en";
  timezone: string;
}

export interface Account {
  id: number;
  email: string;
  status: string;
  created_at: string;
  last_login_at: string | null;
  profile: UserProfile;
}

export interface Appearance {
  theme: "light" | "dark";
  sidebar_collapsed: boolean;
  background: ChatBackgroundSettings;
}

export interface Personalization {
  personality: "professional" | "friendly" | "direct";
  custom_instructions: string;
  verbosity: "concise" | "balanced" | "detailed";
  response_style: { markdown: boolean; emoji: boolean; technical_depth: "low" | "medium" | "high" };
  memory_enabled: boolean;
}
